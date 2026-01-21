"""YC Directory source crawler for Inactive/Acquired companies."""

import os
import re
from typing import Dict, Generator, List, Optional, Set

from utils.rate_limit import rate_limited_request
from utils.logging import get_logger, log_progress
from utils.dedupe import Candidate
from extract.location_extract import LocationExtractor

logger = get_logger(source="yc")


class YCSource:
    """Crawls YC Directory for founders of Inactive/Acquired companies."""

    # YC OSS API endpoint (free, no auth required)
    API_URL = "https://yc-oss.github.io/api/companies/all.json"

    # YC company page base URL
    COMPANY_PAGE_BASE = "https://www.ycombinator.com/companies/"

    # Status values we're interested in
    TARGET_STATUSES = {"Inactive", "Acquired"}

    def __init__(
        self,
        use_browser: bool = True,
        max_companies: int = 0,  # 0 = no limit
    ):
        """
        Initialize YC source.

        Args:
            use_browser: Whether to use Playwright for JS rendering
            max_companies: Maximum companies to process (0 = no limit)
        """
        self.use_browser = use_browser
        self.max_companies = max_companies
        self.location_extractor = LocationExtractor()
        self._browser = None
        self._page = None

    def _fetch_companies(self) -> List[Dict]:
        """Fetch all companies from YC OSS API."""
        try:
            response = rate_limited_request(
                source="yc",
                method="GET",
                url=self.API_URL,
                timeout=30,
            )

            if response.status_code != 200:
                logger.error(f"Failed to fetch YC companies: {response.status_code}")
                return []

            companies = response.json()
            logger.info(f"Fetched {len(companies)} total companies from YC API")

            # Filter to target statuses
            filtered = [
                c for c in companies
                if c.get("status") in self.TARGET_STATUSES
            ]

            logger.info(
                f"Found {len(filtered)} companies with status in {self.TARGET_STATUSES}"
            )

            # Apply max_companies limit if set
            if self.max_companies > 0:
                return filtered[:self.max_companies]
            return filtered

        except Exception as e:
            logger.error(f"Error fetching YC companies: {e}")
            return []

    def _init_browser(self):
        """Initialize Playwright browser."""
        if self._browser is not None:
            return

        try:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
            self._page = self._browser.new_page()
            logger.info("Playwright browser initialized")

        except ImportError:
            logger.error(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )
            raise
        except Exception as e:
            logger.error(f"Failed to initialize browser: {e}")
            raise

    def _close_browser(self):
        """Close Playwright browser."""
        if self._browser:
            self._browser.close()
            self._playwright.stop()
            self._browser = None
            self._page = None

    def _scrape_company_page(self, company: Dict) -> List[Dict]:
        """
        Scrape founder info from YC company page.

        Args:
            company: Company dict from API

        Returns:
            List of founder dicts with name, title, linkedin, etc.
        """
        slug = company.get("slug")
        if not slug:
            return []

        url = f"{self.COMPANY_PAGE_BASE}{slug}"

        try:
            self._page.goto(url, wait_until="networkidle", timeout=15000)

            # Extract founders by finding LinkedIn profile links and their surrounding context
            founders = self._page.evaluate("""
                () => {
                    const founders = [];
                    const seen = new Set();

                    // Find all LinkedIn profile links (not company pages)
                    document.querySelectorAll('a[href*="linkedin.com/in/"]').forEach(link => {
                        // Skip if we've already processed this LinkedIn URL
                        if (seen.has(link.href)) return;
                        seen.add(link.href);

                        // Walk up the DOM to find the name
                        let parent = link.closest('div');
                        let name = null;
                        let title = null;

                        for (let i = 0; i < 5 && parent; i++) {
                            const text = parent.innerText || '';
                            const lines = text.split('\\n').filter(l => l.trim());

                            // First line is likely the name if it's reasonable length
                            if (lines[0] && lines[0].length < 50 && lines[0].length > 2) {
                                name = lines[0].trim();
                                // Second line might be title
                                if (lines[1] && lines[1].length < 100) {
                                    title = lines[1].trim();
                                }
                                break;
                            }
                            parent = parent.parentElement;
                        }

                        if (name) {
                            // Check for Twitter link nearby
                            let twitter = null;
                            const twitterLink = link.closest('div')?.querySelector('a[href*="twitter.com"], a[href*="x.com"]');
                            if (twitterLink) {
                                twitter = twitterLink.href;
                            }

                            founders.push({
                                name: name,
                                title: title,
                                linkedin: link.href,
                                twitter: twitter,
                            });
                        }
                    });

                    return founders;
                }
            """)

            logger.debug(f"Found {len(founders)} founders for {company.get('name')}")
            return founders

        except Exception as e:
            logger.debug(f"Failed to scrape {url}: {e}")
            return []

    def _extract_founders_from_api(self, company: Dict) -> List[Dict]:
        """
        Extract founder info from API data (limited info available).

        Args:
            company: Company dict from API

        Returns:
            List of founder dicts (may be empty if no founder data in API)
        """
        founders = []

        # The API has limited founder info, but check what's available
        if company.get("founders"):
            for f in company.get("founders", []):
                founders.append({
                    "name": f.get("name"),
                    "linkedin": f.get("linkedin"),
                    "twitter": f.get("twitter"),
                })

        return founders

    def _build_candidate(
        self,
        founder: Dict,
        company: Dict,
    ) -> Optional[Candidate]:
        """Build a Candidate from founder and company info."""
        name = founder.get("name")
        if not name:
            return None

        # Extract LinkedIn username from URL
        linkedin_url = founder.get("linkedin")
        linkedin_username = None
        if linkedin_url:
            match = re.search(r"linkedin\.com/in/([^/\?]+)", linkedin_url)
            if match:
                linkedin_username = match.group(1)

        # Extract Twitter handle from URL
        twitter_handle = None
        twitter_url = founder.get("twitter")
        if twitter_url:
            match = re.search(r"(?:twitter\.com|x\.com)/([^/\?]+)", twitter_url)
            if match:
                twitter_handle = match.group(1)

        # Build evidence
        company_name = company.get("name", "Unknown")
        company_status = company.get("status", "")
        batch = company.get("batch", "")
        one_liner = company.get("one_liner", "")

        evidence = [
            {
                "text": f"Co-founder at {company_name} (YC {batch}) - {company_status}",
                "url": f"{self.COMPANY_PAGE_BASE}{company.get('slug', '')}",
                "source": "yc_directory",
            }
        ]

        if one_liner:
            evidence.append({
                "text": f"Company description: {one_liner}",
                "url": f"{self.COMPANY_PAGE_BASE}{company.get('slug', '')}",
                "source": "yc_directory",
            })

        if founder.get("title"):
            evidence.append({
                "text": f"Title: {founder.get('title')}",
                "url": linkedin_url or "",
                "source": "yc_directory",
            })

        # Build bio
        bio_parts = [f"YC {batch} founder"]
        if company_status == "Acquired":
            bio_parts.append(f"({company_name} was acquired)")
        elif company_status == "Inactive":
            bio_parts.append(f"(previously built {company_name})")

        bio = " ".join(bio_parts)

        # Location from company data
        location_raw = company.get("location")
        location_result = self.location_extractor.extract(
            about_text=location_raw,
            evidence_url=f"{self.COMPANY_PAGE_BASE}{company.get('slug', '')}",
        ) if location_raw else None

        candidate = Candidate(
            name=name,
            linkedin_url=linkedin_url,
            twitter_handle=twitter_handle,
            bio=bio,
            evidence_snippets=evidence,
            source_urls=[f"{self.COMPANY_PAGE_BASE}{company.get('slug', '')}"],
            demo_urls=[company.get("website")] if company.get("website") else [],
            location_raw=location_raw,
            country=location_result.country if location_result else None,
            metro_bucket=location_result.metro_bucket if location_result else "UNKNOWN",
            location_confidence=location_result.confidence if location_result else 0.0,
            sources={"yc"},
        )

        return candidate

    def crawl(self, limit: int = 200) -> Generator[Candidate, None, None]:
        """
        Crawl YC Directory for founders of Inactive/Acquired companies.

        Args:
            limit: Maximum candidates to return

        Yields:
            Candidate objects
        """
        logger.info(f"Starting YC Directory crawl, limit={limit}")

        # Fetch companies from API
        companies = self._fetch_companies()

        if not companies:
            logger.warning("No companies found from YC API")
            return

        candidates_found = 0
        seen_names: Set[str] = set()

        # Initialize browser if needed
        if self.use_browser:
            try:
                self._init_browser()
            except Exception as e:
                logger.warning(f"Browser init failed, using API-only mode: {e}")
                self.use_browser = False

        try:
            for idx, company in enumerate(companies):
                if candidates_found >= limit:
                    break

                log_progress(
                    logger, idx + 1, len(companies),
                    f"Processing: {company.get('name', 'Unknown')}"
                )

                # Try to get founders
                founders = []

                # First try browser scraping
                if self.use_browser:
                    founders = self._scrape_company_page(company)

                # Fallback to API data
                if not founders:
                    founders = self._extract_founders_from_api(company)

                # Build candidates from founders
                for founder in founders:
                    if candidates_found >= limit:
                        break

                    name = founder.get("name")
                    if not name or name in seen_names:
                        continue

                    seen_names.add(name)

                    candidate = self._build_candidate(founder, company)
                    if candidate:
                        candidates_found += 1
                        log_progress(
                            logger, candidates_found, limit,
                            f"Found: {name} ({company.get('name')})"
                        )
                        yield candidate

        finally:
            if self.use_browser:
                self._close_browser()

        logger.info(f"YC crawl complete. Found {candidates_found} candidates")
