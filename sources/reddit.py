"""Reddit source crawler using the public JSON API."""

import re
from typing import Dict, Generator, List, Optional, Set
from datetime import datetime

from utils.rate_limit import rate_limited_request
from utils.logging import get_logger, log_progress
from utils.dedupe import Candidate
from extract.location_extract import LocationExtractor

logger = get_logger(source="reddit")


class RedditSource:
    """Crawls Reddit for vibe coding candidates via public JSON API."""

    # Subreddits with high builder density
    SUBREDDITS = [
        "SideProject",
        "startups",
        "indiehackers",
        "Entrepreneur",
        "buildinpublic",
        "webdev",
        "reactjs",
        "nextjs",
        "artificial",
        "MachineLearning",
        "LocalLLaMA",
        "ChatGPT",
        "ClaudeAI",
    ]

    # Search queries for vibe coding signals
    SEARCH_QUERIES = [
        "shipped my first",
        "launched my",
        "built with cursor",
        "built with v0",
        "weekend project",
        "side project launch",
        "Show HN",
        "MVP feedback",
        "just shipped",
        "prototype feedback",
        "built this with AI",
        "built using Claude",
        "built using GPT",
    ]

    def __init__(self):
        self.base_url = "https://www.reddit.com"
        self.location_extractor = LocationExtractor()
        self._seen_authors: Set[str] = set()

    def _api_request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Make a request to Reddit's JSON API."""
        url = f"{self.base_url}{endpoint}.json"
        headers = {
            "User-Agent": "VibeCoder/1.0 (candidate discovery tool)",
        }

        try:
            response = rate_limited_request(
                source="reddit",
                method="GET",
                url=url,
                params=params,
                headers=headers,
            )
            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"Reddit API error {response.status_code}: {endpoint}")
                return None
        except Exception as e:
            logger.error(f"Reddit API request failed: {e}")
            return None

    def search_subreddit(self, subreddit: str, query: str = None, limit: int = 25) -> List[Dict]:
        """Search a subreddit for posts."""
        params = {
            "limit": min(100, limit),
            "sort": "relevance" if query else "hot",
            "t": "month",  # Time filter: hour, day, week, month, year, all
        }

        if query:
            params["q"] = query
            endpoint = f"/r/{subreddit}/search"
            params["restrict_sr"] = "on"
        else:
            endpoint = f"/r/{subreddit}/hot"

        data = self._api_request(endpoint, params)
        if not data:
            return []

        posts = []
        children = data.get("data", {}).get("children", [])

        for child in children[:limit]:
            post = child.get("data", {})
            if post.get("is_self", True):  # Self posts have more content
                posts.append({
                    "id": post.get("id"),
                    "title": post.get("title", ""),
                    "selftext": post.get("selftext", ""),
                    "author": post.get("author"),
                    "subreddit": post.get("subreddit"),
                    "url": f"https://reddit.com{post.get('permalink', '')}",
                    "external_url": post.get("url") if not post.get("is_self") else None,
                    "score": post.get("score", 0),
                    "num_comments": post.get("num_comments", 0),
                    "created_utc": post.get("created_utc"),
                    "link_flair_text": post.get("link_flair_text"),
                })

        return posts

    def get_user_info(self, username: str) -> Optional[Dict]:
        """Get user profile info."""
        if username in ("[deleted]", "AutoModerator", None):
            return None

        data = self._api_request(f"/user/{username}/about")
        if not data:
            return None

        user = data.get("data", {})
        return {
            "username": user.get("name"),
            "created_utc": user.get("created_utc"),
            "link_karma": user.get("link_karma", 0),
            "comment_karma": user.get("comment_karma", 0),
            "subreddit": user.get("subreddit", {}),
        }

    def _extract_links_from_text(self, text: str) -> Dict[str, Optional[str]]:
        """Extract GitHub, Twitter, LinkedIn, and other links from text."""
        result = {
            "github_username": None,
            "twitter_handle": None,
            "linkedin_url": None,
            "website": None,
            "demo_urls": [],
        }

        if not text:
            return result

        # GitHub
        gh_match = re.search(r'github\.com/([a-zA-Z0-9_-]+)', text, re.I)
        if gh_match:
            username = gh_match.group(1)
            if username.lower() not in ("features", "explore", "topics", "trending", "collections"):
                result["github_username"] = username

        # Twitter/X
        tw_match = re.search(r'(?:twitter|x)\.com/([a-zA-Z0-9_]+)', text, re.I)
        if tw_match:
            handle = tw_match.group(1)
            if handle.lower() not in ("home", "explore", "search", "intent"):
                result["twitter_handle"] = handle

        # LinkedIn
        li_match = re.search(r'linkedin\.com/in/([a-zA-Z0-9_-]+)', text, re.I)
        if li_match:
            result["linkedin_url"] = f"https://linkedin.com/in/{li_match.group(1)}"

        # Demo/app URLs
        demo_patterns = [
            r'(https?://[a-zA-Z0-9_-]+\.vercel\.app[^\s\)]*)',
            r'(https?://[a-zA-Z0-9_-]+\.netlify\.app[^\s\)]*)',
            r'(https?://[a-zA-Z0-9_-]+\.railway\.app[^\s\)]*)',
            r'(https?://[a-zA-Z0-9_-]+\.herokuapp\.com[^\s\)]*)',
            r'(https?://[a-zA-Z0-9_-]+\.streamlit\.app[^\s\)]*)',
        ]
        for pattern in demo_patterns:
            matches = re.findall(pattern, text, re.I)
            result["demo_urls"].extend(matches[:2])

        # Personal website (simple heuristic)
        website_match = re.search(r'(?:my (?:site|website|portfolio)|check out)\s*:?\s*(https?://[^\s\)]+)', text, re.I)
        if website_match:
            result["website"] = website_match.group(1)

        return result

    def _score_post_relevance(self, post: Dict) -> float:
        """Score how relevant a post is for vibe coding signals."""
        score = 0.0
        text = f"{post.get('title', '')} {post.get('selftext', '')}".lower()

        # High-signal keywords
        high_signal = ["shipped", "launched", "built", "prototype", "mvp", "demo", "weekend project", "side project"]
        for kw in high_signal:
            if kw in text:
                score += 0.15

        # Tool signals
        tool_signals = ["cursor", "v0", "replit", "copilot", "langchain", "openai", "anthropic", "claude", "gpt", "llm"]
        for tool in tool_signals:
            if tool in text:
                score += 0.1

        # Founder signals
        founder_signals = ["founder", "startup", "yc", "bootstrapped", "indie", "solopreneur"]
        for signal in founder_signals:
            if signal in text:
                score += 0.1

        # Engagement bonus
        post_score = post.get("score", 0)
        if post_score >= 100:
            score += 0.2
        elif post_score >= 50:
            score += 0.15
        elif post_score >= 20:
            score += 0.1
        elif post_score >= 5:
            score += 0.05

        return min(1.0, score)

    def crawl(self, limit: int = 300) -> Generator[Candidate, None, None]:
        """
        Crawl Reddit for candidates.

        Args:
            limit: Maximum candidates to return

        Yields:
            Candidate objects
        """
        candidates_found = 0
        posts_per_subreddit = max(10, limit // len(self.SUBREDDITS))

        logger.info(f"Starting Reddit crawl with {len(self.SUBREDDITS)} subreddits, limit={limit}")

        # First, search subreddits with queries
        for query_idx, query in enumerate(self.SEARCH_QUERIES[:7]):  # Limit queries to avoid rate limits
            if candidates_found >= limit:
                break

            log_progress(logger, query_idx + 1, min(7, len(self.SEARCH_QUERIES)), f"Query: {query[:30]}")

            # Search across key subreddits
            for subreddit in ["SideProject", "startups", "indiehackers", "webdev"]:
                if candidates_found >= limit:
                    break

                posts = self.search_subreddit(subreddit, query=query, limit=10)

                for post in posts:
                    if candidates_found >= limit:
                        break

                    author = post.get("author")
                    if not author or author in self._seen_authors or author == "[deleted]":
                        continue

                    self._seen_authors.add(author)

                    # Score relevance
                    relevance = self._score_post_relevance(post)
                    if relevance < 0.2:
                        continue

                    candidate = self._build_candidate(post)
                    if candidate:
                        candidates_found += 1
                        log_progress(logger, candidates_found, limit, f"Found: u/{author}")
                        yield candidate

        # Then browse hot posts from key subreddits
        for sub_idx, subreddit in enumerate(self.SUBREDDITS):
            if candidates_found >= limit:
                break

            log_progress(logger, sub_idx + 1, len(self.SUBREDDITS), f"Subreddit: r/{subreddit}")

            posts = self.search_subreddit(subreddit, limit=posts_per_subreddit)
            logger.info(f"Found {len(posts)} posts in r/{subreddit}")

            for post in posts:
                if candidates_found >= limit:
                    break

                author = post.get("author")
                if not author or author in self._seen_authors or author == "[deleted]":
                    continue

                self._seen_authors.add(author)

                # Score relevance
                relevance = self._score_post_relevance(post)
                if relevance < 0.25:  # Slightly higher threshold for non-search results
                    continue

                candidate = self._build_candidate(post)
                if candidate:
                    candidates_found += 1
                    log_progress(logger, candidates_found, limit, f"Found: u/{author}")
                    yield candidate

        logger.info(f"Reddit crawl complete. Found {candidates_found} candidates")

    def _build_candidate(self, post: Dict) -> Optional[Candidate]:
        """Build a Candidate from Reddit post data."""
        author = post.get("author")
        if not author or author == "[deleted]":
            return None

        # Get user info
        user_info = self.get_user_info(author)

        # Build evidence from post
        evidence = []
        title = post.get("title", "")
        selftext = post.get("selftext", "")

        if title:
            evidence.append({
                "text": title[:300],
                "url": post.get("url"),
                "source": "reddit_post",
            })

        if selftext:
            # Get first meaningful paragraph
            paragraphs = [p.strip() for p in selftext.split("\n\n") if p.strip()]
            if paragraphs:
                evidence.append({
                    "text": paragraphs[0][:300],
                    "url": post.get("url"),
                    "source": "reddit_post",
                })

        # Extract links from post content
        full_text = f"{title} {selftext}"
        extracted = self._extract_links_from_text(full_text)

        # Try to extract location from user profile or post
        location_text = None
        # Reddit doesn't expose location directly, but sometimes users mention it
        location_patterns = [
            r'\b(?:based in|from|living in|located in)\s+([A-Za-z\s,]+?)(?:\.|,|\s+and|\s+working)',
            r'\b(SF|San Francisco|NYC|New York|LA|Los Angeles|Seattle|Austin|Boston|Chicago)\b',
        ]
        for pattern in location_patterns:
            match = re.search(pattern, full_text, re.I)
            if match:
                location_text = match.group(1).strip()
                break

        location_result = self.location_extractor.extract(
            location_field=location_text,
            evidence_url=post.get("url"),
        )

        candidate = Candidate(
            name=None,  # Reddit doesn't provide real names
            reddit_username=author,
            github_username=extracted.get("github_username"),
            twitter_handle=extracted.get("twitter_handle"),
            linkedin_url=extracted.get("linkedin_url"),
            website=extracted.get("website"),
            demo_urls=extracted.get("demo_urls", [])[:5],
            source_urls=[post.get("url")],
            bio=selftext[:500] if selftext else None,
            evidence_snippets=evidence,
            location_raw=location_result.location_raw,
            country=location_result.country,
            metro_bucket=location_result.metro_bucket,
            location_confidence=location_result.confidence,
            location_evidence_url=location_result.evidence_url,
            sources={"reddit"},
            last_activity=datetime.fromtimestamp(post.get("created_utc", 0)).isoformat() if post.get("created_utc") else None,
        )

        return candidate
