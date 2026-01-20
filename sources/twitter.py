"""Twitter/X source crawler using the Twitter API v2."""

import os
import re
import base64
from typing import Dict, Generator, List, Optional, Set

from utils.rate_limit import rate_limited_request
from utils.logging import get_logger, log_progress
from utils.dedupe import Candidate
from extract.location_extract import LocationExtractor

logger = get_logger(source="twitter")


class TwitterSource:
    """Crawls Twitter/X for vibe coding candidates using API v2."""

    # Search queries targeting vibe coders
    SEARCH_QUERIES = [
        '"built with Cursor" -is:retweet',
        '"shipped" "prototype" -is:retweet',
        '"launched" "AI" "app" -is:retweet',
        '"weekend project" "shipped" -is:retweet',
        '"v0.dev" OR "vercel v0" -is:retweet',
        '"LangChain" "deployed" -is:retweet',
        '"AI agent" "built" -is:retweet',
        '"MVP" "shipped" "demo" -is:retweet',
        '"founder" "launched" "AI" -is:retweet',
        '"YC" "shipped" -is:retweet',
        '"fintech" "prototype" -is:retweet',
        '"Cursor AI" "built" -is:retweet',
        '"OpenAI API" "shipped" -is:retweet',
        '"Claude" "built" "app" -is:retweet',
    ]

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        max_tweets_per_query: int = 20,
    ):
        self.api_key = api_key or os.environ.get("TWITTER_API_KEY")
        self.api_secret = api_secret or os.environ.get("TWITTER_API_SECRET")

        if not self.api_key or not self.api_secret:
            logger.warning("Twitter API credentials not set - Twitter source disabled")

        self.max_tweets_per_query = max_tweets_per_query
        self.base_url = "https://api.twitter.com/2"
        self.location_extractor = LocationExtractor()
        self._seen_users: Set[str] = set()
        self._bearer_token: Optional[str] = None

    def _get_bearer_token(self) -> Optional[str]:
        """Get OAuth 2.0 Bearer token using API key and secret."""
        if self._bearer_token:
            return self._bearer_token

        if not self.api_key or not self.api_secret:
            return None

        # Encode credentials
        credentials = f"{self.api_key}:{self.api_secret}"
        encoded = base64.b64encode(credentials.encode()).decode()

        headers = {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        }

        try:
            response = rate_limited_request(
                source="twitter",
                method="POST",
                url="https://api.twitter.com/oauth2/token",
                headers=headers,
                data={"grant_type": "client_credentials"},
                timeout=30,
            )

            if response.status_code == 200:
                data = response.json()
                self._bearer_token = data.get("access_token")
                logger.info("Successfully obtained Twitter Bearer token")
                return self._bearer_token
            else:
                logger.error(f"Failed to get Twitter Bearer token: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"Error getting Twitter Bearer token: {e}")
            return None

    def _api_request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Make an authenticated API request."""
        bearer_token = self._get_bearer_token()
        if not bearer_token:
            return None

        headers = {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
        }

        url = f"{self.base_url}{endpoint}"

        try:
            response = rate_limited_request(
                source="twitter",
                method="GET",
                url=url,
                headers=headers,
                params=params,
                timeout=30,
            )

            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                logger.warning("Twitter rate limit hit")
                return None
            else:
                logger.warning(f"Twitter API error {response.status_code}: {response.text[:200]}")
                return None
        except Exception as e:
            logger.error(f"Twitter API request failed: {e}")
            return None

    def search_tweets(self, query: str, max_results: int = 20) -> List[Dict]:
        """
        Search for recent tweets matching query.

        Note: Basic API access only allows 10 requests per month with very limited results.
        Pro access ($100/month) is needed for meaningful search volume.
        """
        params = {
            "query": query,
            "max_results": min(max_results, 100),  # API max is 100
            "tweet.fields": "author_id,created_at,public_metrics,entities",
            "expansions": "author_id",
            "user.fields": "name,username,description,location,url,public_metrics,verified",
        }

        data = self._api_request("/tweets/search/recent", params)
        if not data:
            return []

        tweets = data.get("data", [])
        users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}

        results = []
        for tweet in tweets:
            author_id = tweet.get("author_id")
            user = users.get(author_id, {})

            results.append({
                "tweet_id": tweet.get("id"),
                "text": tweet.get("text"),
                "created_at": tweet.get("created_at"),
                "metrics": tweet.get("public_metrics", {}),
                "user_id": author_id,
                "username": user.get("username"),
                "name": user.get("name"),
                "bio": user.get("description"),
                "location": user.get("location"),
                "url": user.get("url"),
                "user_metrics": user.get("public_metrics", {}),
                "verified": user.get("verified", False),
            })

        return results

    def get_user(self, username: str) -> Optional[Dict]:
        """Get user profile by username."""
        params = {
            "user.fields": "name,username,description,location,url,public_metrics,verified,created_at",
        }

        data = self._api_request(f"/users/by/username/{username}", params)
        if data and "data" in data:
            return data["data"]
        return None

    def _extract_links_from_tweet(self, tweet: Dict) -> List[str]:
        """Extract URLs from tweet text and entities."""
        links = []

        # From entities
        text = tweet.get("text", "")

        # Find URLs in text
        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
        matches = re.findall(url_pattern, text)
        links.extend(matches)

        return list(set(links))

    def _extract_github_from_bio(self, bio: str, url: str = None) -> Optional[str]:
        """Extract GitHub username from bio or URL."""
        texts = [bio or ""]
        if url:
            texts.append(url)

        for text in texts:
            match = re.search(r'github\.com/([a-zA-Z0-9_-]+)', text, re.I)
            if match:
                username = match.group(1)
                if username.lower() not in ["features", "pricing", "enterprise"]:
                    return username
        return None

    def _score_tweet_relevance(self, tweet: Dict) -> float:
        """Score how relevant a tweet is for vibe coding signals."""
        score = 0.0
        text = tweet.get("text", "").lower()

        # High-signal keywords
        high_signal = ["shipped", "launched", "built", "prototype", "mvp", "demo"]
        for kw in high_signal:
            if kw in text:
                score += 0.12

        # Tool signals
        tools = ["cursor", "v0", "replit", "langchain", "openai", "claude", "gpt", "ai agent"]
        for tool in tools:
            if tool in text:
                score += 0.1

        # Founder signals
        founder_signals = ["founder", "yc", "startup", "ceo", "cto"]
        for signal in founder_signals:
            if signal in text:
                score += 0.08

        # Engagement bonus
        metrics = tweet.get("metrics", {})
        likes = metrics.get("like_count", 0)
        retweets = metrics.get("retweet_count", 0)

        if likes >= 50 or retweets >= 10:
            score += 0.15
        elif likes >= 10 or retweets >= 3:
            score += 0.08

        return min(1.0, score)

    def crawl(self, limit: int = 300) -> Generator[Candidate, None, None]:
        """
        Crawl Twitter for candidates.

        Args:
            limit: Maximum candidates to return

        Yields:
            Candidate objects
        """
        if not self._get_bearer_token():
            logger.error("Cannot crawl Twitter: Failed to authenticate")
            return

        candidates_found = 0
        total_queries = len(self.SEARCH_QUERIES)

        logger.info(f"Starting Twitter crawl with {total_queries} queries, limit={limit}")

        for query_idx, query in enumerate(self.SEARCH_QUERIES):
            if candidates_found >= limit:
                break

            log_progress(logger, query_idx + 1, total_queries, f"Query: {query[:40]}...")

            tweets = self.search_tweets(query, max_results=self.max_tweets_per_query)
            logger.info(f"Found {len(tweets)} tweets")

            for tweet in tweets:
                if candidates_found >= limit:
                    break

                username = tweet.get("username")
                if not username or username in self._seen_users:
                    continue

                self._seen_users.add(username)

                # Score relevance
                relevance = self._score_tweet_relevance(tweet)
                if relevance < 0.2:
                    continue

                # Build candidate
                candidate = self._build_candidate(tweet)
                if candidate:
                    candidates_found += 1
                    log_progress(logger, candidates_found, limit, f"Found: @{username}")
                    yield candidate

        logger.info(f"Twitter crawl complete. Found {candidates_found} candidates")

    def _build_candidate(self, tweet: Dict) -> Optional[Candidate]:
        """Build a Candidate from Twitter data."""
        username = tweet.get("username")
        if not username:
            return None

        # Build evidence
        evidence = []

        # Tweet text as evidence
        text = tweet.get("text", "")
        if text:
            evidence.append({
                "text": text[:500],
                "url": f"https://twitter.com/{username}/status/{tweet.get('tweet_id')}",
                "source": "twitter_tweet",
            })

        # Bio as evidence
        bio = tweet.get("bio", "")
        if bio:
            evidence.append({
                "text": bio,
                "url": f"https://twitter.com/{username}",
                "source": "twitter_bio",
            })

        # Extract GitHub from bio/URL
        github_username = self._extract_github_from_bio(bio, tweet.get("url"))

        # Extract location
        location_result = self.location_extractor.extract(
            location_field=tweet.get("location"),
            bio_text=bio,
            evidence_url=f"https://twitter.com/{username}",
        )

        # Extract links from tweet
        demo_urls = self._extract_links_from_tweet(tweet)

        # User metrics as evidence
        user_metrics = tweet.get("user_metrics", {})
        followers = user_metrics.get("followers_count", 0)
        if followers >= 1000:
            evidence.append({
                "text": f"{followers:,} Twitter followers",
                "url": f"https://twitter.com/{username}",
                "source": "twitter_metrics",
            })

        candidate = Candidate(
            name=tweet.get("name"),
            twitter_handle=username,
            github_username=github_username,
            website=tweet.get("url"),
            demo_urls=demo_urls[:5],
            source_urls=[f"https://twitter.com/{username}"],
            bio=bio,
            evidence_snippets=evidence,
            location_raw=location_result.location_raw,
            country=location_result.country,
            metro_bucket=location_result.metro_bucket,
            location_confidence=location_result.confidence,
            location_evidence_url=location_result.evidence_url,
            sources={"twitter"},
            last_activity=tweet.get("created_at"),
        )

        return candidate
