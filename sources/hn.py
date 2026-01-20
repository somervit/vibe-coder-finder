"""Hacker News source crawler using Algolia HN Search API."""

import re
from typing import Dict, Generator, List, Optional, Set
from datetime import datetime, timezone

from utils.rate_limit import rate_limited_request
from utils.logging import get_logger, log_progress
from utils.dedupe import Candidate
from utils.text import extract_evidence_lines, is_likely_personal_site
from extract.location_extract import LocationExtractor
from extract.html_extract import HTMLExtractor

logger = get_logger(source="hn")


class HackerNewsSource:
    """Crawls Hacker News for Show HN posts with vibe coding signals."""

    # Search queries for HN
    SEARCH_QUERIES = [
        # Show HN + tools
        "Show HN Cursor",
        "Show HN v0",
        "Show HN Replit",
        "Show HN prototype",
        "Show HN MVP",
        "Show HN AI agent",
        "Show HN LLM",
        "Show HN OpenAI",
        "Show HN Anthropic",
        "Show HN Claude",
        "Show HN GPT",
        "Show HN LangChain",

        # Show HN + shipping signals
        "Show HN weekend project",
        "Show HN demo",
        "Show HN built",
        "Show HN launched",

        # Show HN + fintech
        "Show HN fintech",
        "Show HN payments",
        "Show HN banking",

        # Founder signals
        "Show HN YC",
        "Show HN startup",
    ]

    def __init__(self, fetch_personal_sites: bool = True, max_results_per_query: int = 20):
        self.base_url = "https://hn.algolia.com/api/v1"
        self.fetch_personal_sites = fetch_personal_sites
        self.max_results_per_query = max_results_per_query
        self.location_extractor = LocationExtractor()
        self.html_extractor = HTMLExtractor()
        self._seen_authors: Set[str] = set()

    def search(self, query: str, tags: str = "show_hn", max_results: int = 20) -> List[Dict]:
        """
        Search HN using Algolia API.

        Args:
            query: Search query
            tags: HN tags to filter (show_hn, ask_hn, story, etc.)
            max_results: Maximum results to return

        Returns:
            List of story objects
        """
        results = []
        page = 0
        hits_per_page = min(20, max_results)

        while len(results) < max_results:
            params = {
                "query": query,
                "tags": tags,
                "hitsPerPage": hits_per_page,
                "page": page,
            }

            try:
                response = rate_limited_request(
                    source="hn",
                    method="GET",
                    url=f"{self.base_url}/search",
                    params=params,
                )

                if response.status_code != 200:
                    logger.warning(f"HN API error: {response.status_code}")
                    break

                data = response.json()
                hits = data.get("hits", [])

                if not hits:
                    break

                for hit in hits:
                    results.append(self._extract_story(hit))
                    if len(results) >= max_results:
                        break

                page += 1

                # Algolia limits
                if page >= data.get("nbPages", 1):
                    break

            except Exception as e:
                logger.error(f"HN search failed: {e}")
                break

        return results

    def _extract_story(self, hit: Dict) -> Dict:
        """Extract relevant fields from an Algolia hit."""
        return {
            "id": hit.get("objectID"),
            "title": hit.get("title", ""),
            "url": hit.get("url"),
            "author": hit.get("author"),
            "points": hit.get("points", 0),
            "num_comments": hit.get("num_comments", 0),
            "created_at": hit.get("created_at"),
            "story_text": hit.get("story_text"),  # For text posts
        }

    def get_user(self, username: str) -> Optional[Dict]:
        """Get HN user profile."""
        try:
            response = rate_limited_request(
                source="hn",
                method="GET",
                url=f"{self.base_url}/users/{username}",
            )

            if response.status_code == 200:
                data = response.json()
                return {
                    "username": data.get("username"),
                    "about": data.get("about"),
                    "karma": data.get("karma", 0),
                    "created_at": data.get("created_at"),
                }
        except Exception as e:
            logger.debug(f"Failed to get HN user {username}: {e}")

        return None

    def _fetch_page(self, url: str) -> Optional[Dict]:
        """Fetch and extract data from a page URL."""
        if not url or not url.startswith("http"):
            return None

        try:
            response = rate_limited_request(
                source="web",
                method="GET",
                url=url,
                timeout=15,
            )

            if response.status_code != 200:
                return None

            html = response.text
            return self.html_extractor.extract(html, url)

        except Exception as e:
            logger.debug(f"Failed to fetch {url}: {e}")
            return None

    def crawl(self, limit: int = 300) -> Generator[Candidate, None, None]:
        """
        Crawl HN for candidates.

        Args:
            limit: Maximum candidates to return

        Yields:
            Candidate objects
        """
        candidates_found = 0
        total_queries = len(self.SEARCH_QUERIES)

        logger.info(f"Starting HN crawl with {total_queries} queries, limit={limit}")

        for query_idx, query in enumerate(self.SEARCH_QUERIES):
            if candidates_found >= limit:
                break

            log_progress(logger, query_idx + 1, total_queries, f"Query: {query}")

            stories = self.search(query, max_results=self.max_results_per_query)
            logger.info(f"Found {len(stories)} stories")

            for story in stories:
                if candidates_found >= limit:
                    break

                author = story.get("author")
                if not author or author in self._seen_authors:
                    continue

                self._seen_authors.add(author)

                # Skip low engagement stories
                points = story.get("points", 0)
                if points < 5:
                    continue

                # Get user profile
                user = self.get_user(author)

                # Optionally fetch linked page
                page_data = None
                if self.fetch_personal_sites and story.get("url"):
                    url = story["url"]
                    if is_likely_personal_site(url):
                        page_data = self._fetch_page(url)

                # Build candidate
                candidate = self._build_candidate(story, user, page_data)
                if candidate:
                    candidates_found += 1
                    log_progress(logger, candidates_found, limit, f"Found: {author}")
                    yield candidate

        logger.info(f"HN crawl complete. Found {candidates_found} candidates")

    def _build_candidate(
        self,
        story: Dict,
        user: Optional[Dict],
        page_data: Optional[Dict],
    ) -> Optional[Candidate]:
        """Build a Candidate from HN data."""
        author = story.get("author")
        if not author:
            return None

        # Build evidence
        evidence = []

        # Story title is primary evidence
        title = story.get("title", "")
        if title:
            evidence.append({
                "text": title,
                "url": f"https://news.ycombinator.com/item?id={story.get('id')}",
                "source": "hn_title",
            })

        # Story text (for text posts)
        if story.get("story_text"):
            for line in extract_evidence_lines(story["story_text"])[:2]:
                evidence.append({
                    "text": line,
                    "url": f"https://news.ycombinator.com/item?id={story.get('id')}",
                    "source": "hn_text",
                })

        # Page evidence
        if page_data:
            for snippet in page_data.get("evidence_snippets", [])[:2]:
                evidence.append({
                    "text": snippet,
                    "url": page_data.get("url"),
                    "source": "hn_linked_page",
                })

        # Extract location from various sources
        location_result = self.location_extractor.extract(
            bio_text=user.get("about") if user else None,
            about_text=page_data.get("main_content") if page_data else None,
        )

        # If page has better location, use it
        if page_data:
            page_location = self.location_extractor.extract_from_html(
                page_data.get("main_content", ""),
                page_data.get("url"),
            )
            if page_location.confidence > location_result.confidence:
                location_result = page_location

        # Collect links
        demo_urls = []
        source_urls = [f"https://news.ycombinator.com/item?id={story.get('id')}"]

        if story.get("url"):
            demo_urls.append(story["url"])

        if page_data:
            demo_urls.extend(page_data.get("links", [])[:3])

        # Get contact info from page if available
        github_username = None
        website = None
        email = None
        linkedin_url = None
        twitter_handle = None

        if page_data:
            github_username = page_data.get("github_username")
            website = story.get("url") if is_likely_personal_site(story.get("url", "")) else None
            email = page_data.get("email")
            linkedin_url = page_data.get("linkedin_url")
            twitter_handle = page_data.get("twitter_handle")

        candidate = Candidate(
            name=page_data.get("author") if page_data else None,
            hn_username=author,
            github_username=github_username,
            email=email,
            linkedin_url=linkedin_url,
            twitter_handle=twitter_handle,
            website=website,
            demo_urls=list(set(demo_urls))[:5],
            source_urls=source_urls,
            bio=user.get("about") if user else None,
            evidence_snippets=evidence,
            location_raw=location_result.location_raw,
            country=location_result.country,
            metro_bucket=location_result.metro_bucket,
            location_confidence=location_result.confidence,
            location_evidence_url=location_result.evidence_url,
            sources={"hn"},
            last_activity=story.get("created_at"),
        )

        return candidate
