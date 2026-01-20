"""GitHub source crawler using the GitHub Search API."""

import os
import re
import base64
from typing import Dict, Generator, List, Optional, Set
from datetime import datetime, timedelta

from utils.rate_limit import rate_limited_request
from utils.logging import get_logger, log_progress
from utils.dedupe import Candidate
from extract.github_extract import GitHubExtractor
from extract.location_extract import LocationExtractor

logger = get_logger(source="github")


# Repo name patterns that indicate tips/guides rather than original projects
SKIP_REPO_PATTERNS = [
    r"^awesome-",
    r"-awesome$",
    r"^cursor-tips",
    r"^cursor-rules",
    r"-tips$",
    r"-tricks$",
    r"-guide$",
    r"-tutorial$",
    r"-cheatsheet$",
    r"^dotfiles",
    r"^\.cursor",
    r"cursorrules$",
    r"^cursor-free",  # License bypass tools
    r"machine-?id",   # License bypass tools
]

# Repo descriptions that indicate non-original content
SKIP_DESCRIPTION_PATTERNS = [
    r"curated list",
    r"awesome list",
    r"collection of",
    r"tips and tricks",
    r"cheat sheet",
    r"reset.*machine.*id",
    r"bypass.*trial",
    r"free.*vip",
]


class GitHubSource:
    """Crawls GitHub for vibe coding candidates."""

    # Search queries targeting vibe coding signals - focused on actual projects
    SEARCH_QUERIES = [
        # Original projects built with AI tools
        '"built with Cursor" -awesome -tips -rules',
        '"made with v0" OR "built with v0" -awesome',
        '"shipped" "prototype" AI -awesome -list',
        '"weekend project" AI OR LLM -awesome',
        '"hackathon" "demo" AI agent -awesome',

        # Actual AI/LLM applications
        '"AI agent" "deployed" OR "live" -awesome -tips',
        '"LLM app" "demo" OR "try it" -awesome',
        'LangChain "production" OR "deployed" -awesome',

        # Fintech projects
        'fintech "prototype" OR "MVP" -awesome',
        'payments "demo" stripe OR plaid -awesome',

        # Founder/startup projects
        '"YC" OR "Y Combinator" "launched" -awesome -list',
        'startup "shipped" "prototype" -awesome',

        # Specific shipping signals
        '"live demo" AI OR LLM -awesome',
        '"try it out" prototype -awesome',
    ]

    def __init__(self, token: Optional[str] = None, max_repos_per_query: int = 30):
        self.token = token or os.environ.get("GITHUB_TOKEN")
        if not self.token:
            logger.warning("No GITHUB_TOKEN set - API rate limits will be restrictive")

        self.max_repos_per_query = max_repos_per_query
        self.extractor = GitHubExtractor()
        self.location_extractor = LocationExtractor()
        self.base_url = "https://api.github.com"
        self._seen_users: Set[str] = set()

        # Compile skip patterns
        self._skip_repo_patterns = [re.compile(p, re.I) for p in SKIP_REPO_PATTERNS]
        self._skip_desc_patterns = [re.compile(p, re.I) for p in SKIP_DESCRIPTION_PATTERNS]

    def _headers(self) -> Dict:
        """Get request headers."""
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _api_request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Make a rate-limited API request."""
        url = f"{self.base_url}{endpoint}"
        try:
            response = rate_limited_request(
                source="github",
                method="GET",
                url=url,
                headers=self._headers(),
                params=params,
            )
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 403:
                logger.warning(f"Rate limit hit or forbidden: {endpoint}")
                return None
            elif response.status_code == 404:
                return None
            else:
                logger.warning(f"GitHub API error {response.status_code}: {endpoint}")
                return None
        except Exception as e:
            logger.error(f"GitHub API request failed: {e}")
            return None

    def _should_skip_repo(self, repo: Dict) -> bool:
        """Check if repo should be skipped based on name/description patterns."""
        repo_name = repo.get("name", "").lower()
        description = repo.get("description", "") or ""

        # Check repo name patterns
        for pattern in self._skip_repo_patterns:
            if pattern.search(repo_name):
                logger.debug(f"Skipping repo {repo_name}: matches skip pattern")
                return True

        # Check description patterns
        for pattern in self._skip_desc_patterns:
            if pattern.search(description):
                logger.debug(f"Skipping repo {repo_name}: description matches skip pattern")
                return True

        return False

    def search_repos(self, query: str, max_results: int = 30) -> List[Dict]:
        """Search for repositories matching query."""
        results = []
        page = 1
        per_page = min(30, max_results)

        while len(results) < max_results:
            params = {
                "q": query,
                "sort": "updated",
                "order": "desc",
                "per_page": per_page,
                "page": page,
            }

            data = self._api_request("/search/repositories", params)
            if not data or "items" not in data:
                break

            items = data["items"]
            if not items:
                break

            for item in items:
                repo = self.extractor.extract_repo(item)
                results.append(repo)
                if len(results) >= max_results:
                    break

            page += 1

            # Respect search result limits
            if page > 10:  # GitHub limits to 1000 results
                break

        return results

    def get_user(self, username: str) -> Optional[Dict]:
        """Get user profile data."""
        data = self._api_request(f"/users/{username}")
        if data:
            return self.extractor.extract_user(data)
        return None

    def get_user_repos(self, username: str, max_repos: int = 10) -> List[Dict]:
        """Get user's recent repos to assess shipping behavior."""
        params = {
            "sort": "updated",
            "direction": "desc",
            "per_page": max_repos,
            "type": "owner",  # Only repos they own, not forks
        }
        data = self._api_request(f"/users/{username}/repos", params)
        if data:
            return [self.extractor.extract_repo(r) for r in data if not r.get("fork")]
        return []

    def get_user_events(self, username: str) -> Optional[List[Dict]]:
        """Get user's recent public events to find email from commits."""
        data = self._api_request(f"/users/{username}/events/public")
        return data if data else None

    def get_readme(self, owner: str, repo: str) -> Optional[Dict]:
        """Get and extract README content."""
        data = self._api_request(f"/repos/{owner}/{repo}/readme")
        if not data:
            return None

        try:
            content = base64.b64decode(data.get("content", "")).decode("utf-8")
            return self.extractor.extract_readme(content)
        except Exception as e:
            logger.debug(f"Failed to decode README for {owner}/{repo}: {e}")
            return None

    def _extract_email_from_events(self, events: List[Dict]) -> Optional[str]:
        """Extract email from push events (commit author)."""
        if not events:
            return None

        for event in events:
            if event.get("type") == "PushEvent":
                payload = event.get("payload", {})
                commits = payload.get("commits", [])
                for commit in commits:
                    author = commit.get("author", {})
                    email = author.get("email")
                    if email and not email.endswith("@users.noreply.github.com"):
                        return email
        return None

    def _extract_linkedin_from_bio(self, bio: str, website: str = None) -> Optional[str]:
        """Extract LinkedIn URL from bio or website."""
        texts_to_check = [bio or ""]
        if website:
            texts_to_check.append(website)

        for text in texts_to_check:
            # Direct LinkedIn URL
            match = re.search(r'(https?://(?:www\.)?linkedin\.com/in/[a-zA-Z0-9_-]+)', text, re.I)
            if match:
                return match.group(1)

            # LinkedIn handle mention
            match = re.search(r'linkedin[:\s]+@?([a-zA-Z0-9_-]+)', text, re.I)
            if match:
                return f"https://linkedin.com/in/{match.group(1)}"

        return None

    def _assess_user_shipping_behavior(self, repos: List[Dict]) -> Dict:
        """Assess user's shipping behavior from their repos."""
        if not repos:
            return {"score": 0, "signals": []}

        signals = []
        score = 0

        # Count repos with homepages (deployed)
        deployed_count = sum(1 for r in repos if r.get("homepage"))
        if deployed_count >= 3:
            score += 10
            signals.append(f"{deployed_count} deployed projects")
        elif deployed_count >= 1:
            score += 5
            signals.append(f"{deployed_count} deployed project(s)")

        # Total stars across repos
        total_stars = sum(r.get("stars", 0) for r in repos)
        if total_stars >= 100:
            score += 10
            signals.append(f"{total_stars} total stars")
        elif total_stars >= 20:
            score += 5
            signals.append(f"{total_stars} total stars")

        # Recent activity (repos updated in last 3 months)
        recent_count = 0
        now = datetime.now()
        for r in repos:
            pushed_at = r.get("pushed_at")
            if pushed_at:
                try:
                    pushed = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
                    if (now.replace(tzinfo=pushed.tzinfo) - pushed).days < 90:
                        recent_count += 1
                except:
                    pass

        if recent_count >= 3:
            score += 10
            signals.append(f"{recent_count} recently active repos")
        elif recent_count >= 1:
            score += 5
            signals.append(f"{recent_count} recently active repo(s)")

        # Variety of languages (indicates breadth)
        languages = set(r.get("language") for r in repos if r.get("language"))
        if len(languages) >= 3:
            score += 5
            signals.append(f"Uses {len(languages)} languages")

        return {"score": score, "signals": signals}

    def crawl(self, limit: int = 300) -> Generator[Candidate, None, None]:
        """
        Crawl GitHub for candidates.

        Args:
            limit: Maximum number of candidates to return

        Yields:
            Candidate objects
        """
        candidates_found = 0
        total_queries = len(self.SEARCH_QUERIES)

        logger.info(f"Starting GitHub crawl with {total_queries} queries, limit={limit}")

        for query_idx, query in enumerate(self.SEARCH_QUERIES):
            if candidates_found >= limit:
                break

            log_progress(logger, query_idx + 1, total_queries, f"Query: {query[:50]}...")

            repos = self.search_repos(query, max_results=self.max_repos_per_query)
            logger.info(f"Found {len(repos)} repos for query")

            for repo in repos:
                if candidates_found >= limit:
                    break

                owner = repo.get("owner_username")
                if not owner or owner in self._seen_users:
                    continue

                self._seen_users.add(owner)

                # Skip forks
                if repo.get("is_fork"):
                    continue

                # Skip repos matching noise patterns
                if self._should_skip_repo(repo):
                    continue

                relevance = self.extractor.score_repo_relevance(repo)
                if relevance < 0.2:
                    continue

                # Get user profile
                user = self.get_user(owner)
                if not user:
                    continue

                # Get user's other repos to assess shipping behavior
                user_repos = self.get_user_repos(owner, max_repos=10)
                shipping_assessment = self._assess_user_shipping_behavior(user_repos)

                # Skip users with very low shipping signals
                if shipping_assessment["score"] < 5 and not user.get("bio"):
                    logger.debug(f"Skipping {owner}: low shipping signals and no bio")
                    continue

                # Get README for evidence
                repo_name = repo.get("name")
                readme = self.get_readme(owner, repo_name) if repo_name else None

                # Try to get email from events if not in profile
                email = user.get("email")
                if not email:
                    events = self.get_user_events(owner)
                    email = self._extract_email_from_events(events)

                # Extract LinkedIn
                linkedin = self._extract_linkedin_from_bio(
                    user.get("bio", ""),
                    user.get("blog")
                )

                # Create candidate
                candidate = self._build_candidate(
                    user, repo, readme, user_repos,
                    shipping_assessment, email, linkedin
                )
                if candidate:
                    candidates_found += 1
                    log_progress(logger, candidates_found, limit, f"Found: {owner}")
                    yield candidate

        logger.info(f"GitHub crawl complete. Found {candidates_found} candidates")

    def _build_candidate(
        self,
        user: Dict,
        repo: Dict,
        readme: Optional[Dict],
        user_repos: List[Dict],
        shipping_assessment: Dict,
        discovered_email: Optional[str],
        linkedin_url: Optional[str],
    ) -> Optional[Candidate]:
        """Build a Candidate object from GitHub data."""
        username = user.get("username")
        if not username:
            return None

        # Extract location
        location_result = self.location_extractor.extract(
            location_field=user.get("location"),
            bio_text=user.get("bio"),
            evidence_url=user.get("html_url"),
        )

        # Build evidence snippets
        evidence = []

        # Add repo description as evidence
        if repo.get("description"):
            evidence.append({
                "text": repo["description"],
                "url": repo.get("html_url"),
                "source": "github_repo",
            })

        # Add README evidence
        if readme:
            for snippet in readme.get("evidence_snippets", [])[:3]:
                evidence.append({
                    "text": snippet,
                    "url": repo.get("html_url"),
                    "source": "github_readme",
                })

        # Add bio as evidence
        if user.get("bio"):
            evidence.append({
                "text": user["bio"],
                "url": user.get("html_url"),
                "source": "github_bio",
            })

        # Add shipping assessment signals as evidence
        for signal in shipping_assessment.get("signals", []):
            evidence.append({
                "text": signal,
                "url": user.get("html_url"),
                "source": "github_activity",
            })

        # Collect demo URLs from all user repos
        demo_urls = []
        if repo.get("homepage"):
            demo_urls.append(repo["homepage"])
        if readme:
            demo_urls.extend(readme.get("demo_links", []))

        # Add homepages from other repos
        for r in user_repos[:5]:
            if r.get("homepage") and r["homepage"] not in demo_urls:
                demo_urls.append(r["homepage"])

        # Calculate total stars across repos
        total_stars = sum(r.get("stars", 0) for r in user_repos) if user_repos else repo.get("stars", 0)

        candidate = Candidate(
            name=user.get("name"),
            github_username=username,
            email=discovered_email or user.get("email"),
            linkedin_url=linkedin_url,
            github_url=user.get("html_url"),
            website=user.get("blog") or None,
            demo_urls=list(set(demo_urls))[:5],
            source_urls=[repo.get("html_url")],
            bio=user.get("bio"),
            evidence_snippets=evidence,
            location_raw=location_result.location_raw,
            country=location_result.country,
            metro_bucket=location_result.metro_bucket,
            location_confidence=location_result.confidence,
            location_evidence_url=location_result.evidence_url,
            sources={"github"},
            last_activity=repo.get("pushed_at"),
            stars_total=total_stars,
            repo_count=user.get("public_repos", 0),
        )

        return candidate
