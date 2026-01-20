"""Brave Search source crawler."""

import os
import re
from typing import Dict, Generator, List, Optional, Set
from itertools import product

from utils.rate_limit import rate_limited_request
from utils.logging import get_logger, log_progress
from utils.dedupe import Candidate
from utils.text import is_likely_personal_site
from extract.location_extract import LocationExtractor
from extract.html_extract import HTMLExtractor

logger = get_logger(source="brave")


class BraveSearchSource:
    """Crawls the open web using Brave Search API."""

    # Query components for building search queries
    TOOL_TERMS = [
        '"built with Cursor" OR "Cursor AI"',
        '"v0.dev" OR "Vercel v0"',
        'Replit',
        '"AI agent" OR "LLM app"',
        'OpenAI API OR Anthropic API',
        'LangChain OR LlamaIndex',
    ]

    SHIPPING_TERMS = [
        'prototype OR MVP OR demo',
        '"shipped" OR "launched" OR "built"',
        '"weekend project" OR "side project"',
        'hackathon',
    ]

    FOUNDER_TERMS = [
        'founder OR "co-founder"',
        '"YC" OR "Y Combinator"',
        'Antler OR "Entrepreneur First"',
        '"product manager" OR "PM"',
        'startup',
    ]

    FINTECH_TERMS = [
        'fintech OR payments',
        'banking OR "financial services"',
    ]

    # Location bias terms (added to some queries)
    SF_BIAS = '"San Francisco" OR "SF" OR "Bay Area" OR "Silicon Valley"'
    US_BIAS = '"United States" OR "USA" OR California OR "New York"'

    # Negative keywords to exclude tutorial/guide content
    NEGATIVE_KEYWORDS = [
        '-tutorial',
        '-guide',
        '-"how to"',
        '-"what is"',
        '-course',
        '-"learn to"',
        '-"best practices"',
        '-"tips and tricks"',
        '-"cheat sheet"',
    ]
    NEGATIVE_TERMS_STR = " ".join(NEGATIVE_KEYWORDS)

    # Additional negative keywords for LinkedIn queries
    LINKEDIN_NEGATIVE_KEYWORDS = [
        '-hiring',
        '-"we are"',
        '-"our team"',
        '-"job opportunity"',
        '-recruiter',
    ]

    # First-person builder signals (indicate actual builder)
    BUILDER_SIGNALS = [
        r"\b(i|we) (built|shipped|launched|created|made|deployed)\b",
        r"\bmy (project|app|startup|prototype|mvp|demo|side project)\b",
        r"\bour (launch|product|app|startup)\b",
        r"\bi('m| am) (building|shipping|working on)\b",
        r"\bjust (shipped|launched|deployed|released)\b",
        r"\bhere('s| is) (my|our) (project|app|demo)\b",
    ]

    # Third-person content writer signals (indicate tutorial/guide author)
    WRITER_SIGNALS = [
        r"\bhow to (build|create|make|use|get started)\b",
        r"\b(guide|tutorial|introduction) (to|for|on)\b",
        r"\blearn (how|to)\b",
        r"\bbest practices (for|when|to)\b",
        r"\btips (for|and|to)\b",
        r"\bstep[- ]?by[- ]?step\b",
        r"\bin this (article|post|guide|tutorial)\b",
        r"\b(what is|what are)\b.*\?",
        r"\b(beginner|getting started|101)\b",
    ]

    # Domains that suggest content marketing vs personal
    CONTENT_MARKETING_DOMAINS = [
        "medium.com/",  # Note: medium.com/@username is OK
        "freecodecamp.org",
        "geeksforgeeks.org",
        "towardsdatascience.com",
        "hackernoon.com",
    ]

    PERSONAL_DOMAIN_INDICATORS = [
        ".me/",
        ".io/",
        ".dev/",
        "github.io",
        "vercel.app",
        "netlify.app",
    ]

    def __init__(
        self,
        api_key: Optional[str] = None,
        max_results_per_query: int = 20,
        fetch_pages: bool = True,
    ):
        self.api_key = api_key or os.environ.get("BRAVE_API_KEY")
        if not self.api_key:
            logger.warning("No BRAVE_API_KEY set - Brave Search will not work")

        self.max_results_per_query = max_results_per_query
        self.fetch_pages = fetch_pages
        self.base_url = "https://api.search.brave.com/res/v1/web/search"
        self.location_extractor = LocationExtractor()
        self.html_extractor = HTMLExtractor()
        self._seen_domains: Set[str] = set()

    def _build_queries(self, max_queries: int = 30) -> List[str]:
        """Build a diverse set of search queries with negative keywords to filter tutorials."""
        queries = []
        neg = self.NEGATIVE_TERMS_STR

        # Tool + Shipping combinations (with negative keywords)
        for tool, ship in product(self.TOOL_TERMS[:4], self.SHIPPING_TERMS[:2]):
            queries.append(f"({tool}) ({ship}) {neg}")

        # Tool + Founder combinations
        for tool, founder in product(self.TOOL_TERMS[:3], self.FOUNDER_TERMS[:3]):
            queries.append(f"({tool}) ({founder}) {neg}")

        # Fintech + Tool combinations
        for fintech, tool in product(self.FINTECH_TERMS, self.TOOL_TERMS[:3]):
            queries.append(f"({fintech}) ({tool}) {neg}")

        # Add some with location bias
        location_queries = [
            f'({self.TOOL_TERMS[0]}) ({self.SHIPPING_TERMS[0]}) ({self.SF_BIAS}) {neg}',
            f'({self.FOUNDER_TERMS[0]}) ({self.TOOL_TERMS[1]}) ({self.US_BIAS}) {neg}',
            f'({self.FINTECH_TERMS[0]}) prototype ({self.SF_BIAS}) {neg}',
        ]
        queries.extend(location_queries)

        # LinkedIn-specific queries with stricter first-person requirements
        # Reduced from 5 to 3 queries due to lower signal quality
        linkedin_neg = " ".join(self.NEGATIVE_KEYWORDS + self.LINKEDIN_NEGATIVE_KEYWORDS)
        linkedin_queries = [
            f'site:linkedin.com ("I built" OR "I shipped" OR "launched my") prototype {linkedin_neg}',
            f'site:linkedin.com "my project" OR "my startup" MVP {linkedin_neg}',
            f'site:linkedin.com founder ("I launched" OR "just shipped") {linkedin_neg}',
        ]
        queries.extend(linkedin_queries)

        # Dedupe and limit
        seen = set()
        unique_queries = []
        for q in queries:
            if q not in seen:
                seen.add(q)
                unique_queries.append(q)
                if len(unique_queries) >= max_queries:
                    break

        return unique_queries

    def search(self, query: str, count: int = 20) -> List[Dict]:
        """
        Search using Brave Search API.

        Args:
            query: Search query
            count: Number of results to request

        Returns:
            List of search result objects
        """
        if not self.api_key:
            return []

        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self.api_key,
        }

        params = {
            "q": query,
            "count": min(count, 20),  # Brave limits to 20 per request
            "safesearch": "moderate",
        }

        try:
            response = rate_limited_request(
                source="brave",
                method="GET",
                url=self.base_url,
                headers=headers,
                params=params,
            )

            if response.status_code != 200:
                logger.warning(f"Brave API error: {response.status_code}")
                return []

            data = response.json()
            web_results = data.get("web", {}).get("results", [])

            results = []
            for item in web_results:
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "description": item.get("description", ""),
                    "age": item.get("age"),
                    "language": item.get("language"),
                })

            return results

        except Exception as e:
            logger.error(f"Brave search failed: {e}")
            return []

    def _fetch_page(self, url: str) -> Optional[Dict]:
        """Fetch and extract data from a page."""
        if not url or not url.startswith("http"):
            return None

        # Skip known non-personal domains
        skip_domains = [
            "github.com/topics", "github.com/collections",
            "twitter.com", "x.com",
            "linkedin.com", "facebook.com",
            "youtube.com", "reddit.com",
            "stackoverflow.com",
            "wikipedia.org",
            "amazon.com", "google.com",
        ]

        for skip in skip_domains:
            if skip in url:
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

            # Skip if too large
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > 1_000_000:
                return None

            html = response.text
            return self.html_extractor.extract(html, url)

        except Exception as e:
            logger.debug(f"Failed to fetch {url}: {e}")
            return None

    def _extract_domain(self, url: str) -> Optional[str]:
        """Extract domain from URL."""
        match = re.search(r"https?://(?:www\.)?([^/]+)", url)
        return match.group(1) if match else None

    def _is_likely_builder(
        self,
        result: Dict,
        page_data: Optional[Dict],
    ) -> tuple:
        """
        Determine if result is likely from an actual builder vs content writer.

        Returns:
            (is_builder, confidence, signals)
            - is_builder: True if likely a builder
            - confidence: 0.0-1.0 score
            - signals: List of detected signal strings for debugging
        """
        signals = []
        score = 0.5  # Start neutral

        # Collect all text to analyze
        texts_to_check = []

        title = result.get("title", "")
        desc = result.get("description", "")
        url = result.get("url", "")

        texts_to_check.extend([title, desc])

        if page_data:
            main_content = page_data.get("main_content", "")
            # Limit content to first 2000 chars for performance
            texts_to_check.append(main_content[:2000] if main_content else "")
            for snippet in page_data.get("evidence_snippets", [])[:3]:
                texts_to_check.append(snippet if isinstance(snippet, str) else "")

        combined_text = " ".join(texts_to_check).lower()

        # Check first-person builder signals (positive)
        for pattern in self.BUILDER_SIGNALS:
            if re.search(pattern, combined_text, re.IGNORECASE):
                score += 0.15
                signals.append(f"+builder: matched '{pattern[:30]}...'")

        # Check third-person writer signals (negative)
        for pattern in self.WRITER_SIGNALS:
            if re.search(pattern, combined_text, re.IGNORECASE):
                score -= 0.2
                signals.append(f"-writer: matched '{pattern[:30]}...'")

        # Domain analysis
        for domain in self.CONTENT_MARKETING_DOMAINS:
            if domain in url:
                # Special case: medium.com/@username is personal
                if "medium.com/@" in url:
                    score += 0.05
                    signals.append("+personal: medium.com/@username")
                else:
                    score -= 0.15
                    signals.append(f"-marketing: {domain}")
                break

        for indicator in self.PERSONAL_DOMAIN_INDICATORS:
            if indicator in url:
                score += 0.1
                signals.append(f"+personal: {indicator}")
                break

        # Check for author presence (personal sites often have author)
        if page_data and page_data.get("author"):
            score += 0.05
            signals.append("+author: page has author")

        # Title patterns
        if re.match(r"^(how to|guide|tutorial|learn|tips)", title.lower()):
            score -= 0.25
            signals.append("-title: starts with tutorial pattern")

        if re.search(r"\b(show hn|launched|shipped|built|i made)\b", title.lower()):
            score += 0.15
            signals.append("+title: contains shipping signal")

        # Clamp score
        score = max(0.0, min(1.0, score))

        # Decision threshold
        is_builder = score >= 0.4

        return is_builder, score, signals

    def crawl(self, limit: int = 300) -> Generator[Candidate, None, None]:
        """
        Crawl web using Brave Search.

        Args:
            limit: Maximum candidates to return

        Yields:
            Candidate objects
        """
        if not self.api_key:
            logger.error("Cannot crawl: BRAVE_API_KEY not set")
            return

        candidates_found = 0
        queries = self._build_queries()
        total_queries = len(queries)

        logger.info(f"Starting Brave crawl with {total_queries} queries, limit={limit}")

        for query_idx, query in enumerate(queries):
            if candidates_found >= limit:
                break

            log_progress(logger, query_idx + 1, total_queries, f"Query: {query[:60]}...")

            results = self.search(query, count=self.max_results_per_query)
            logger.info(f"Found {len(results)} results")

            for result in results:
                if candidates_found >= limit:
                    break

                url = result.get("url")
                if not url:
                    continue

                # Dedupe by domain
                domain = self._extract_domain(url)
                if domain in self._seen_domains:
                    continue
                self._seen_domains.add(domain)

                # Fetch page if enabled
                page_data = None
                if self.fetch_pages:
                    page_data = self._fetch_page(url)

                # Pre-filter: Check if this is likely a builder vs content writer
                is_builder, confidence, signals = self._is_likely_builder(result, page_data)

                if not is_builder:
                    logger.debug(
                        f"Skipping {domain}: likely content writer "
                        f"(confidence={confidence:.2f}, signals={signals[:3]})"
                    )
                    continue

                # Build candidate
                candidate = self._build_candidate(result, page_data)
                if candidate:
                    # Store builder confidence as evidence for transparency
                    candidate.evidence_snippets.append({
                        "text": f"Builder confidence: {confidence:.2f}",
                        "url": url,
                        "source": "brave_builder_detection",
                    })

                    candidates_found += 1
                    log_progress(logger, candidates_found, limit, f"Found: {domain} (conf={confidence:.2f})")
                    yield candidate

        logger.info(f"Brave crawl complete. Found {candidates_found} candidates")

    def _build_candidate(
        self,
        result: Dict,
        page_data: Optional[Dict],
    ) -> Optional[Candidate]:
        """Build a Candidate from Brave search result."""
        url = result.get("url")
        if not url:
            return None

        # Build evidence
        evidence = []

        # Search result title/description
        title = result.get("title", "")
        desc = result.get("description", "")

        if title:
            evidence.append({
                "text": title,
                "url": url,
                "source": "brave_title",
            })

        if desc:
            evidence.append({
                "text": desc,
                "url": url,
                "source": "brave_description",
            })

        # Page evidence
        if page_data:
            for snippet in page_data.get("evidence_snippets", [])[:3]:
                evidence.append({
                    "text": snippet,
                    "url": url,
                    "source": "brave_page",
                })

        # Extract location
        location_result = self.location_extractor.extract(
            about_text=page_data.get("main_content") if page_data else desc,
            evidence_url=url,
        )

        # Get name and handles from page
        name = None
        github_username = None
        twitter_handle = None
        linkedin_url = None
        email = None
        website = None

        if page_data:
            name = page_data.get("author")
            github_username = page_data.get("github_username")
            twitter_handle = page_data.get("twitter_handle")
            linkedin_url = page_data.get("linkedin_url")
            email = page_data.get("email")

            if is_likely_personal_site(url):
                website = url

        # Collect links
        demo_urls = [url]
        if page_data:
            # Add any demo links found on the page
            for link in page_data.get("links", [])[:5]:
                if any(x in link for x in ["vercel", "netlify", "railway", "demo", "app"]):
                    demo_urls.append(link)

        candidate = Candidate(
            name=name,
            github_username=github_username,
            email=email,
            linkedin_url=linkedin_url,
            twitter_handle=twitter_handle,
            website=website,
            demo_urls=list(set(demo_urls))[:5],
            source_urls=[url],
            bio=page_data.get("description") if page_data else desc,
            evidence_snippets=evidence,
            location_raw=location_result.location_raw,
            country=location_result.country,
            metro_bucket=location_result.metro_bucket,
            location_confidence=location_result.confidence,
            location_evidence_url=location_result.evidence_url,
            sources={"brave"},
        )

        return candidate
