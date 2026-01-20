"""Rate limiting utilities with exponential backoff."""

import time
import threading
from typing import Dict, Optional
from dataclasses import dataclass, field
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import requests


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting a specific source."""
    requests_per_second: float = 1.0
    max_retries: int = 3
    initial_backoff: float = 1.0
    max_backoff: float = 60.0


# Default configs per source
DEFAULT_CONFIGS: Dict[str, RateLimitConfig] = {
    "github": RateLimitConfig(requests_per_second=1.0, max_retries=5),
    "brave": RateLimitConfig(requests_per_second=1.0, max_retries=3),
    "hn": RateLimitConfig(requests_per_second=2.0, max_retries=3),
    "web": RateLimitConfig(requests_per_second=2.0, max_retries=2),
}


@dataclass
class RateLimiter:
    """Thread-safe rate limiter with per-source tracking."""

    configs: Dict[str, RateLimitConfig] = field(default_factory=lambda: DEFAULT_CONFIGS.copy())
    _last_request: Dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def wait(self, source: str) -> None:
        """Wait if necessary to respect rate limits for the given source."""
        config = self.configs.get(source, RateLimitConfig())
        min_interval = 1.0 / config.requests_per_second

        with self._lock:
            now = time.time()
            last = self._last_request.get(source, 0)
            elapsed = now - last

            if elapsed < min_interval:
                sleep_time = min_interval - elapsed
                time.sleep(sleep_time)

            self._last_request[source] = time.time()

    def get_retry_decorator(self, source: str):
        """Get a tenacity retry decorator configured for the given source."""
        config = self.configs.get(source, RateLimitConfig())

        return retry(
            stop=stop_after_attempt(config.max_retries),
            wait=wait_exponential(
                multiplier=config.initial_backoff,
                max=config.max_backoff
            ),
            retry=retry_if_exception_type((
                requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.HTTPError,
            )),
            reraise=True,
        )


# Global rate limiter instance
_global_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Get or create the global rate limiter."""
    global _global_limiter
    if _global_limiter is None:
        _global_limiter = RateLimiter()
    return _global_limiter


def rate_limited_request(
    source: str,
    method: str,
    url: str,
    **kwargs
) -> requests.Response:
    """Make a rate-limited HTTP request with automatic retries."""
    limiter = get_rate_limiter()
    limiter.wait(source)

    config = limiter.configs.get(source, RateLimitConfig())

    # Set reasonable defaults
    kwargs.setdefault("timeout", 30)
    kwargs.setdefault("headers", {})
    kwargs["headers"].setdefault("User-Agent", "VibeCoder-Finder/1.0 (Recruiting Research Tool)")

    @retry(
        stop=stop_after_attempt(config.max_retries),
        wait=wait_exponential(multiplier=config.initial_backoff, max=config.max_backoff),
        retry=retry_if_exception_type((
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
        )),
        reraise=True,
    )
    def _make_request():
        response = requests.request(method, url, **kwargs)
        # Retry on rate limit responses
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            time.sleep(min(retry_after, config.max_backoff))
            response.raise_for_status()
        elif response.status_code >= 500:
            response.raise_for_status()
        return response

    return _make_request()
