"""Dev.to source crawler using the Forem API."""

import re
from typing import Dict, Generator, List, Optional, Set, Union
from datetime import datetime

from utils.rate_limit import rate_limited_request
from utils.logging import get_logger, log_progress
from utils.dedupe import Candidate
from utils.text import extract_evidence_lines
from extract.location_extract import LocationExtractor

logger = get_logger(source="devto")


class DevToSource:
    """Crawls Dev.to for vibe coding candidates via Forem API."""

    # Search tags and queries targeting vibe coders
    SEARCH_TAGS = [
        "cursor",
        "v0",
        "ai",
        "llm",
        "openai",
        "langchain",
        "gpt",
        "anthropic",
        "claude",
        "copilot",
        "prototype",
        "mvp",
        "startup",
        "sidehustle",
        "buildinpublic",
        "shipping",
        "hackathon",
        "fintech",
        "payments",
    ]

    SEARCH_QUERIES = [
        "built with cursor",
        "shipped in a weekend",
        "weekend project",
        "prototype demo",
        "AI agent",
        "LLM app",
        "langchain tutorial",
        "founder journey",
        "startup mvp",
        "fintech app",
    ]

    def __init__(self, max_articles_per_query: int = 15):
        self.base_url = "https://dev.to/api"
        self.max_articles_per_query = max_articles_per_query
        self.location_extractor = LocationExtractor()
        self._seen_authors: Set[str] = set()

    def _api_request(self, endpoint: str, params: Dict = None) -> Optional[Union[Dict, List]]:
        """Make an API request to Dev.to."""
        url = f"{self.base_url}{endpoint}"
        try:
            response = rate_limited_request(
                source="devto",
                method="GET",
                url=url,
                params=params,
                headers={"Accept": "application/json"},
            )
            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"Dev.to API error {response.status_code}: {endpoint}")
                return None
        except Exception as e:
            logger.error(f"Dev.to API request failed: {e}")
            return None

    def search_articles(self, query: str = None, tag: str = None, max_results: int = 15) -> List[Dict]:
        """Search for articles by query or tag."""
        params = {
            "per_page": min(30, max_results),
            "page": 1,
        }

        if tag:
            params["tag"] = tag
        elif query:
            # Dev.to doesn't have a search endpoint in public API
            # Use tag-based search as primary method
            pass

        articles = self._api_request("/articles", params)
        if not articles:
            return []

        results = []
        for article in articles[:max_results]:
            results.append({
                "id": article.get("id"),
                "title": article.get("title", ""),
                "description": article.get("description", ""),
                "url": article.get("url"),
                "canonical_url": article.get("canonical_url"),
                "published_at": article.get("published_at"),
                "positive_reactions_count": article.get("positive_reactions_count", 0),
                "comments_count": article.get("comments_count", 0),
                "reading_time_minutes": article.get("reading_time_minutes", 0),
                "tags": article.get("tag_list", []),
                "user": article.get("user", {}),
            })

        return results

    def get_user(self, username: str) -> Optional[Dict]:
        """Get user profile by username."""
        data = self._api_request(f"/users/by_username", {"url": username})
        if data:
            return {
                "id": data.get("id"),
                "username": data.get("username"),
                "name": data.get("name"),
                "bio": data.get("summary"),
                "location": data.get("location"),
                "website_url": data.get("website_url"),
                "github_username": data.get("github_username"),
                "twitter_username": data.get("twitter_username"),
                "profile_image": data.get("profile_image"),
                "joined_at": data.get("joined_at"),
            }
        return None

    def get_user_articles(self, username: str, max_articles: int = 5) -> List[Dict]:
        """Get recent articles by a user."""
        params = {
            "username": username,
            "per_page": max_articles,
        }
        articles = self._api_request("/articles", params)
        if articles:
            return articles[:max_articles]
        return []

    def _extract_github_from_article(self, article: Dict) -> Optional[str]:
        """Extract GitHub username from article content or links."""
        # Check user's github_username first
        user = article.get("user", {})
        if user.get("github_username"):
            return user["github_username"]

        # Could parse article body for GitHub links if we fetch full article
        return None

    def _score_article_relevance(self, article: Dict) -> float:
        """Score how relevant an article is for vibe coding signals."""
        score = 0.0
        text = f"{article.get('title', '')} {article.get('description', '')}".lower()
        tags = [t.lower() for t in article.get("tags", [])]

        # High-signal keywords
        high_signal = ["shipped", "launched", "built", "prototype", "mvp", "demo", "weekend project"]
        for kw in high_signal:
            if kw in text:
                score += 0.15

        # Tool signals
        tool_signals = ["cursor", "v0", "replit", "copilot", "langchain", "openai", "anthropic", "claude", "gpt"]
        for tool in tool_signals:
            if tool in text or tool in tags:
                score += 0.1

        # Founder signals
        founder_signals = ["founder", "startup", "yc", "bootstrapped", "indie"]
        for signal in founder_signals:
            if signal in text or signal in tags:
                score += 0.1

        # Engagement bonus
        reactions = article.get("positive_reactions_count", 0)
        if reactions >= 50:
            score += 0.15
        elif reactions >= 20:
            score += 0.1
        elif reactions >= 5:
            score += 0.05

        return min(1.0, score)

    def crawl(self, limit: int = 300) -> Generator[Candidate, None, None]:
        """
        Crawl Dev.to for candidates.

        Args:
            limit: Maximum candidates to return

        Yields:
            Candidate objects
        """
        candidates_found = 0
        total_tags = len(self.SEARCH_TAGS)

        logger.info(f"Starting Dev.to crawl with {total_tags} tags, limit={limit}")

        for tag_idx, tag in enumerate(self.SEARCH_TAGS):
            if candidates_found >= limit:
                break

            log_progress(logger, tag_idx + 1, total_tags, f"Tag: {tag}")

            articles = self.search_articles(tag=tag, max_results=self.max_articles_per_query)
            logger.info(f"Found {len(articles)} articles for tag '{tag}'")

            for article in articles:
                if candidates_found >= limit:
                    break

                user = article.get("user", {})
                username = user.get("username")

                if not username or username in self._seen_authors:
                    continue

                self._seen_authors.add(username)

                # Score relevance
                relevance = self._score_article_relevance(article)
                if relevance < 0.2:
                    continue

                # Get full user profile
                user_profile = self.get_user(username)

                # Build candidate
                candidate = self._build_candidate(article, user_profile)
                if candidate:
                    candidates_found += 1
                    log_progress(logger, candidates_found, limit, f"Found: {username}")
                    yield candidate

        logger.info(f"Dev.to crawl complete. Found {candidates_found} candidates")

    def _build_candidate(
        self,
        article: Dict,
        user_profile: Optional[Dict],
    ) -> Optional[Candidate]:
        """Build a Candidate from Dev.to data."""
        user = article.get("user", {})
        username = user.get("username")
        if not username:
            return None

        # Build evidence
        evidence = []

        # Article title and description
        title = article.get("title", "")
        if title:
            evidence.append({
                "text": title,
                "url": article.get("url"),
                "source": "devto_article",
            })

        description = article.get("description", "")
        if description:
            evidence.append({
                "text": description,
                "url": article.get("url"),
                "source": "devto_article",
            })

        # Tags as evidence
        tags = article.get("tags", [])
        if tags:
            evidence.append({
                "text": f"Tags: {', '.join(tags)}",
                "url": article.get("url"),
                "source": "devto_tags",
            })

        # Extract location
        location_text = None
        if user_profile:
            location_text = user_profile.get("location")

        location_result = self.location_extractor.extract(
            location_field=location_text,
            bio_text=user_profile.get("bio") if user_profile else None,
            evidence_url=f"https://dev.to/{username}",
        )

        # Get GitHub and Twitter from profile
        github_username = None
        twitter_handle = None
        website = None
        name = user.get("name")
        bio = None

        if user_profile:
            github_username = user_profile.get("github_username")
            twitter_handle = user_profile.get("twitter_username")
            website = user_profile.get("website_url")
            name = user_profile.get("name") or name
            bio = user_profile.get("bio")

        candidate = Candidate(
            name=name,
            github_username=github_username,
            twitter_handle=twitter_handle,
            website=website,
            demo_urls=[article.get("url")] if article.get("url") else [],
            source_urls=[article.get("url")],
            bio=bio,
            evidence_snippets=evidence,
            location_raw=location_result.location_raw,
            country=location_result.country,
            metro_bucket=location_result.metro_bucket,
            location_confidence=location_result.confidence,
            location_evidence_url=location_result.evidence_url,
            sources={"devto"},
            last_activity=article.get("published_at"),
        )

        return candidate
