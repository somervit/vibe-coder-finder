"""ProductHunt source crawler."""

import re
from typing import Dict, Generator, List, Optional, Set

from utils.rate_limit import rate_limited_request
from utils.logging import get_logger, log_progress
from utils.dedupe import Candidate
from extract.location_extract import LocationExtractor
from extract.html_extract import HTMLExtractor

logger = get_logger(source="producthunt")


class ProductHuntSource:
    """Crawls ProductHunt for makers of AI/vibe coding products."""

    # Topics/tags to search for
    TOPICS = [
        "artificial-intelligence",
        "ai",
        "developer-tools",
        "saas",
        "productivity",
        "no-code",
        "automation",
        "chatgpt",
        "machine-learning",
        "fintech",
        "payments",
    ]

    # Keywords to look for in product descriptions
    VIBE_KEYWORDS = [
        "built with",
        "cursor",
        "v0",
        "replit",
        "ai-powered",
        "llm",
        "gpt",
        "claude",
        "langchain",
        "prototype",
        "mvp",
        "weekend",
        "shipped",
        "launched",
    ]

    def __init__(self, fetch_maker_pages: bool = True, max_products_per_topic: int = 20):
        self.base_url = "https://www.producthunt.com"
        self.fetch_maker_pages = fetch_maker_pages
        self.max_products_per_topic = max_products_per_topic
        self.location_extractor = LocationExtractor()
        self.html_extractor = HTMLExtractor()
        self._seen_makers: Set[str] = set()

    def _fetch_topic_page(self, topic: str) -> Optional[str]:
        """Fetch a topic page HTML."""
        url = f"{self.base_url}/topics/{topic}"
        try:
            response = rate_limited_request(
                source="producthunt",
                method="GET",
                url=url,
                timeout=15,
            )
            if response.status_code == 200:
                return response.text
            else:
                logger.warning(f"ProductHunt topic page error {response.status_code}: {topic}")
                return None
        except Exception as e:
            logger.error(f"ProductHunt request failed: {e}")
            return None

    def _fetch_product_page(self, product_url: str) -> Optional[Dict]:
        """Fetch and parse a product page."""
        try:
            response = rate_limited_request(
                source="producthunt",
                method="GET",
                url=product_url,
                timeout=15,
            )
            if response.status_code != 200:
                return None

            return self.html_extractor.extract(response.text, product_url)
        except Exception as e:
            logger.debug(f"Failed to fetch product page {product_url}: {e}")
            return None

    def _parse_topic_page(self, html: str, topic: str) -> List[Dict]:
        """Parse products from a topic page."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        products = []

        # Look for product cards/links
        # ProductHunt structure changes, so we use multiple selectors
        product_links = soup.find_all("a", href=re.compile(r"^/posts/[\w-]+$"))

        seen_urls = set()
        for link in product_links:
            href = link.get("href", "")
            if href in seen_urls:
                continue
            seen_urls.add(href)

            product_url = f"{self.base_url}{href}"

            # Try to get product name from link text or nearby elements
            name = link.get_text(strip=True)
            if not name or len(name) > 100:
                # Try parent or sibling elements
                parent = link.find_parent()
                if parent:
                    h_tag = parent.find(["h1", "h2", "h3"])
                    if h_tag:
                        name = h_tag.get_text(strip=True)

            if not name:
                name = href.replace("/posts/", "").replace("-", " ").title()

            # Try to get tagline/description
            tagline = ""
            parent = link.find_parent()
            if parent:
                p_tag = parent.find("p")
                if p_tag:
                    tagline = p_tag.get_text(strip=True)

            products.append({
                "name": name,
                "url": product_url,
                "tagline": tagline,
                "topic": topic,
            })

            if len(products) >= self.max_products_per_topic:
                break

        return products

    def _extract_makers_from_page(self, page_data: Dict, product: Dict) -> List[Dict]:
        """Extract maker information from a product page."""
        makers = []

        # Look for maker usernames in the page content
        content = page_data.get("main_content", "")

        # Common patterns for maker info
        # "Made by @username" or "Built by username"
        maker_patterns = [
            r"(?:made|built|created)\s+by\s+@?(\w+)",
            r"@(\w{3,20})\s+(?:maker|founder|creator)",
        ]

        for pattern in maker_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            for match in matches:
                if match.lower() not in ["the", "a", "an", "this", "that"]:
                    makers.append({
                        "username": match,
                        "source": "producthunt",
                    })

        # Also check for social links
        github = page_data.get("github_username")
        twitter = page_data.get("twitter_handle")

        if github and not any(m.get("github") == github for m in makers):
            makers.append({
                "username": github,
                "github": github,
                "source": "producthunt_github",
            })

        if twitter and not any(m.get("twitter") == twitter for m in makers):
            makers.append({
                "username": twitter,
                "twitter": twitter,
                "source": "producthunt_twitter",
            })

        return makers

    def _score_product_relevance(self, product: Dict, page_data: Optional[Dict] = None) -> float:
        """Score how relevant a product is for vibe coding signals."""
        score = 0.0

        text = f"{product.get('name', '')} {product.get('tagline', '')}".lower()
        if page_data:
            text += f" {page_data.get('main_content', '')[:1000]}".lower()

        # Check for vibe coding keywords
        for keyword in self.VIBE_KEYWORDS:
            if keyword in text:
                score += 0.12

        # Topic bonuses
        topic = product.get("topic", "")
        high_value_topics = ["artificial-intelligence", "ai", "developer-tools", "fintech"]
        if topic in high_value_topics:
            score += 0.1

        return min(1.0, score)

    def crawl(self, limit: int = 300) -> Generator[Candidate, None, None]:
        """
        Crawl ProductHunt for candidates.

        Args:
            limit: Maximum candidates to return

        Yields:
            Candidate objects
        """
        candidates_found = 0
        total_topics = len(self.TOPICS)

        logger.info(f"Starting ProductHunt crawl with {total_topics} topics, limit={limit}")

        for topic_idx, topic in enumerate(self.TOPICS):
            if candidates_found >= limit:
                break

            log_progress(logger, topic_idx + 1, total_topics, f"Topic: {topic}")

            # Fetch topic page
            html = self._fetch_topic_page(topic)
            if not html:
                continue

            # Parse products
            products = self._parse_topic_page(html, topic)
            logger.info(f"Found {len(products)} products for topic '{topic}'")

            for product in products:
                if candidates_found >= limit:
                    break

                # Fetch product page if enabled
                page_data = None
                if self.fetch_maker_pages:
                    page_data = self._fetch_product_page(product["url"])

                # Score relevance
                relevance = self._score_product_relevance(product, page_data)
                if relevance < 0.2:
                    continue

                # Extract makers
                makers = []
                if page_data:
                    makers = self._extract_makers_from_page(page_data, product)

                # Build candidate from product/maker info
                candidate = self._build_candidate(product, page_data, makers)
                if candidate:
                    # Check if we've seen this maker
                    maker_id = candidate.github_username or candidate.twitter_handle or candidate.website
                    if maker_id and maker_id in self._seen_makers:
                        continue
                    if maker_id:
                        self._seen_makers.add(maker_id)

                    candidates_found += 1
                    log_progress(logger, candidates_found, limit, f"Found: {product.get('name', 'Unknown')}")
                    yield candidate

        logger.info(f"ProductHunt crawl complete. Found {candidates_found} candidates")

    def _build_candidate(
        self,
        product: Dict,
        page_data: Optional[Dict],
        makers: List[Dict],
    ) -> Optional[Candidate]:
        """Build a Candidate from ProductHunt data."""
        # Build evidence
        evidence = []

        # Product name and tagline
        name = product.get("name", "")
        if name:
            evidence.append({
                "text": f"ProductHunt: {name}",
                "url": product.get("url"),
                "source": "producthunt_product",
            })

        tagline = product.get("tagline", "")
        if tagline:
            evidence.append({
                "text": tagline,
                "url": product.get("url"),
                "source": "producthunt_tagline",
            })

        # Page evidence
        if page_data:
            for snippet in page_data.get("evidence_snippets", [])[:2]:
                evidence.append({
                    "text": snippet,
                    "url": product.get("url"),
                    "source": "producthunt_page",
                })

        # Extract any maker info
        github_username = None
        twitter_handle = None
        maker_name = None
        email = None
        linkedin_url = None

        for maker in makers:
            if maker.get("github") and not github_username:
                github_username = maker["github"]
            if maker.get("twitter") and not twitter_handle:
                twitter_handle = maker["twitter"]

        if page_data:
            github_username = github_username or page_data.get("github_username")
            twitter_handle = twitter_handle or page_data.get("twitter_handle")
            maker_name = page_data.get("author")
            email = page_data.get("email")
            linkedin_url = page_data.get("linkedin_url")

        # Extract location from page
        location_result = self.location_extractor.extract(
            about_text=page_data.get("main_content") if page_data else tagline,
            evidence_url=product.get("url"),
        )

        candidate = Candidate(
            name=maker_name,
            github_username=github_username,
            twitter_handle=twitter_handle,
            email=email,
            linkedin_url=linkedin_url,
            website=product.get("url"),
            demo_urls=[product.get("url")] if product.get("url") else [],
            source_urls=[product.get("url")],
            bio=tagline,
            evidence_snippets=evidence,
            location_raw=location_result.location_raw,
            country=location_result.country,
            metro_bucket=location_result.metro_bucket,
            location_confidence=location_result.confidence,
            location_evidence_url=location_result.evidence_url,
            sources={"producthunt"},
        )

        return candidate
