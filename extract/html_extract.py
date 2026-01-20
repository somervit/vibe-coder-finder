"""HTML content extraction."""

import re
from typing import Dict, List, Optional
from bs4 import BeautifulSoup

from utils.text import extract_evidence_lines, truncate_text, VIBE_CODING_KEYWORDS, FOUNDER_KEYWORDS


class HTMLExtractor:
    """Extracts structured data from HTML pages."""

    def __init__(self, max_content_length: int = 5000):
        self.max_content_length = max_content_length

    def extract(self, html: str, url: str) -> Dict:
        """
        Extract structured data from HTML.

        Returns:
            {
                "url": str,
                "title": str,
                "author": str or None,
                "description": str or None,
                "main_content": str,
                "evidence_snippets": [str],
                "links": [str],
                "github_username": str or None,
                "twitter_handle": str or None,
                "linkedin_url": str or None,
                "email": str or None,
                "location": str or None,
            }
        """
        soup = BeautifulSoup(html, "lxml")

        # Remove script and style elements
        for element in soup(["script", "style", "nav", "footer", "header"]):
            element.decompose()

        result = {
            "url": url,
            "title": self._extract_title(soup),
            "author": self._extract_author(soup),
            "description": self._extract_description(soup),
            "main_content": "",
            "evidence_snippets": [],
            "links": [],
            "github_username": None,
            "twitter_handle": None,
            "linkedin_url": None,
            "email": None,
            "location": None,
        }

        # Extract main content
        main_content = self._extract_main_content(soup)
        result["main_content"] = truncate_text(main_content, self.max_content_length)

        # Extract evidence
        result["evidence_snippets"] = extract_evidence_lines(main_content)

        # Extract social links
        social = self._extract_social_links(soup, url)
        result["github_username"] = social.get("github")
        result["twitter_handle"] = social.get("twitter")
        result["linkedin_url"] = social.get("linkedin")
        result["links"] = social.get("all_links", [])

        # Extract email
        result["email"] = self._extract_email(soup, main_content)

        # Extract location
        result["location"] = self._extract_location(soup, main_content)

        return result

    def _extract_title(self, soup: BeautifulSoup) -> str:
        """Extract page title."""
        # Try og:title first
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            return og_title["content"]

        # Try regular title
        title_tag = soup.find("title")
        if title_tag:
            return title_tag.get_text(strip=True)

        # Try h1
        h1 = soup.find("h1")
        if h1:
            return h1.get_text(strip=True)

        return ""

    def _extract_author(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract author name."""
        # Try meta author
        meta_author = soup.find("meta", attrs={"name": "author"})
        if meta_author and meta_author.get("content"):
            return meta_author["content"]

        # Try article:author
        og_author = soup.find("meta", property="article:author")
        if og_author and og_author.get("content"):
            return og_author["content"]

        # Try common author class patterns
        author_classes = ["author", "byline", "author-name", "post-author"]
        for cls in author_classes:
            author_el = soup.find(class_=re.compile(cls, re.I))
            if author_el:
                text = author_el.get_text(strip=True)
                if text and len(text) < 100:
                    return text

        return None

    def _extract_description(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract page description."""
        # Try og:description
        og_desc = soup.find("meta", property="og:description")
        if og_desc and og_desc.get("content"):
            return og_desc["content"]

        # Try meta description
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            return meta_desc["content"]

        return None

    def _extract_main_content(self, soup: BeautifulSoup) -> str:
        """Extract main content text."""
        # Try to find main content area
        main_selectors = [
            soup.find("main"),
            soup.find("article"),
            soup.find(class_=re.compile(r"content|post|article", re.I)),
            soup.find(id=re.compile(r"content|post|article", re.I)),
        ]

        for selector in main_selectors:
            if selector:
                text = selector.get_text(separator="\n", strip=True)
                if len(text) > 200:
                    return text

        # Fall back to body
        body = soup.find("body")
        if body:
            return body.get_text(separator="\n", strip=True)

        return soup.get_text(separator="\n", strip=True)

    def _extract_social_links(self, soup: BeautifulSoup, page_url: str) -> Dict:
        """Extract social media links and usernames."""
        result = {
            "github": None,
            "twitter": None,
            "linkedin": None,
            "all_links": [],
        }

        all_links = soup.find_all("a", href=True)
        seen_urls = set()

        for link in all_links:
            href = link["href"]

            # Skip anchors and javascript
            if href.startswith("#") or href.startswith("javascript:"):
                continue

            # Extract GitHub username
            gh_match = re.search(r"github\.com/([a-zA-Z0-9_-]+)(?:/|$)", href)
            if gh_match and not result["github"]:
                username = gh_match.group(1)
                if username not in ["features", "pricing", "enterprise", "topics", "collections", "orgs", "settings"]:
                    result["github"] = username

            # Extract Twitter handle
            tw_match = re.search(r"(?:twitter|x)\.com/([a-zA-Z0-9_]+)", href)
            if tw_match and not result["twitter"]:
                handle = tw_match.group(1)
                if handle not in ["share", "intent", "home", "search"]:
                    result["twitter"] = handle

            # Extract LinkedIn URL
            li_match = re.search(r"(https?://(?:www\.)?linkedin\.com/in/[a-zA-Z0-9_-]+)", href)
            if li_match and not result["linkedin"]:
                result["linkedin"] = li_match.group(1)

            # Collect all external links
            if href.startswith("http") and href not in seen_urls:
                seen_urls.add(href)
                result["all_links"].append(href)

        return result

    def _extract_email(self, soup: BeautifulSoup, text: str) -> Optional[str]:
        """Extract email address from page."""
        # Check mailto links first
        mailto_links = soup.find_all("a", href=re.compile(r"^mailto:", re.I))
        for link in mailto_links:
            href = link.get("href", "")
            match = re.search(r"mailto:([^\?]+)", href)
            if match:
                email = match.group(1).strip()
                if self._is_valid_email(email):
                    return email

        # Search in text content
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        matches = re.findall(email_pattern, text)

        for email in matches:
            if self._is_valid_email(email):
                return email

        return None

    def _is_valid_email(self, email: str) -> bool:
        """Check if email is likely a real personal email."""
        if not email:
            return False

        email = email.lower()

        # Skip common non-personal emails
        skip_patterns = [
            "@example.com",
            "@test.com",
            "@localhost",
            "noreply@",
            "no-reply@",
            "donotreply@",
            "support@",
            "info@",
            "hello@",
            "contact@",
            "admin@",
            "sales@",
            "team@",
        ]

        for pattern in skip_patterns:
            if pattern in email:
                return False

        return True

    def _extract_location(self, soup: BeautifulSoup, text: str) -> Optional[str]:
        """Extract location from page content."""
        # Look for location patterns in text
        patterns = [
            r"(?:based|located|living)\s+(?:in|at)\s+([^.\n,]{3,40})",
            r"(?:from|hometown)\s*:?\s*([^.\n,]{3,40})",
            r"📍\s*([^.\n,]{3,40})",
            r"🌎\s*([^.\n,]{3,40})",
            r"🌍\s*([^.\n,]{3,40})",
            r"location\s*:?\s*([^.\n,]{3,40})",
            r"(?:^|\s)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?,\s*(?:CA|NY|TX|WA|CO|FL|MA|GA|IL|AZ|OR|NC|VA|PA|OH|MI|NJ|United States|USA))\b",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            if match:
                location = match.group(1).strip()
                # Clean up location
                location = re.sub(r'\s+', ' ', location)
                if 3 < len(location) < 50:
                    return location

        # Check meta tags
        geo_tags = [
            soup.find("meta", attrs={"name": "geo.placename"}),
            soup.find("meta", attrs={"name": "geo.region"}),
        ]
        for tag in geo_tags:
            if tag and tag.get("content"):
                return tag["content"]

        return None

    def extract_about_page_location(self, html: str) -> Optional[str]:
        """Extract location specifically from about/bio pages."""
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(separator="\n", strip=True)
        return self._extract_location(soup, text)

    def extract_contact_info(self, html: str, url: str) -> Dict:
        """
        Extract contact information from a page.

        Returns dict with email, linkedin, twitter, github, location.
        """
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(separator="\n", strip=True)

        social = self._extract_social_links(soup, url)

        return {
            "email": self._extract_email(soup, text),
            "linkedin": social.get("linkedin"),
            "twitter": social.get("twitter"),
            "github": social.get("github"),
            "location": self._extract_location(soup, text),
        }
