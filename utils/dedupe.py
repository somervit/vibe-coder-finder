"""Candidate deduplication and identity linking."""

import re
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
from difflib import SequenceMatcher


@dataclass
class Candidate:
    """Represents a discovered candidate."""

    # Identity
    id: str = ""  # Generated unique ID
    name: Optional[str] = None
    github_username: Optional[str] = None
    hn_username: Optional[str] = None
    reddit_username: Optional[str] = None
    email: Optional[str] = None  # Only if publicly listed
    linkedin_url: Optional[str] = None
    twitter_handle: Optional[str] = None

    # Links
    github_url: Optional[str] = None
    website: Optional[str] = None
    demo_urls: List[str] = field(default_factory=list)
    source_urls: List[str] = field(default_factory=list)

    # Bio and evidence
    bio: Optional[str] = None
    evidence_snippets: List[Dict] = field(default_factory=list)  # [{text, url, source}]

    # Location (populated by location extractor)
    location_raw: Optional[str] = None
    country: str = "unknown"  # US / non-US / unknown
    metro_bucket: str = "UNKNOWN"  # SF_BAY_AREA / OTHER_US / NON_US / UNKNOWN
    location_confidence: float = 0.0
    location_evidence_url: Optional[str] = None

    # Metadata
    sources: Set[str] = field(default_factory=set)  # github, hn, brave, etc.
    last_activity: Optional[str] = None
    stars_total: int = 0
    repo_count: int = 0

    # Scoring (populated by scorer)
    scores: Dict = field(default_factory=dict)
    total_score: float = 0.0
    recruiter_pitch: Optional[str] = None

    def __post_init__(self):
        if not self.id:
            self.id = self._generate_id()

    def _generate_id(self) -> str:
        """Generate a unique ID based on available identifiers."""
        if self.github_username:
            return f"gh:{self.github_username.lower()}"
        if self.hn_username:
            return f"hn:{self.hn_username.lower()}"
        if self.reddit_username:
            return f"reddit:{self.reddit_username.lower()}"
        if self.website:
            domain = self._extract_domain(self.website)
            if domain:
                return f"web:{domain.lower()}"
        if self.email:
            return f"email:{self.email.lower()}"
        if self.name:
            slug = re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")
            return f"name:{slug}"
        return f"unknown:{id(self)}"

    @staticmethod
    def _extract_domain(url: str) -> Optional[str]:
        """Extract domain from URL."""
        match = re.search(r"https?://(?:www\.)?([^/]+)", url)
        return match.group(1) if match else None

    def merge_from(self, other: "Candidate") -> None:
        """Merge another candidate's data into this one."""
        # Prefer non-None values
        if other.name and not self.name:
            self.name = other.name
        if other.github_username and not self.github_username:
            self.github_username = other.github_username
        if other.hn_username and not self.hn_username:
            self.hn_username = other.hn_username
        if other.reddit_username and not self.reddit_username:
            self.reddit_username = other.reddit_username
        if other.email and not self.email:
            self.email = other.email
        if other.linkedin_url and not self.linkedin_url:
            self.linkedin_url = other.linkedin_url
        if other.twitter_handle and not self.twitter_handle:
            self.twitter_handle = other.twitter_handle
        if other.github_url and not self.github_url:
            self.github_url = other.github_url
        if other.website and not self.website:
            self.website = other.website
        if other.bio and (not self.bio or len(other.bio) > len(self.bio)):
            self.bio = other.bio
        if other.location_raw and other.location_confidence > self.location_confidence:
            self.location_raw = other.location_raw
            self.country = other.country
            self.metro_bucket = other.metro_bucket
            self.location_confidence = other.location_confidence
            self.location_evidence_url = other.location_evidence_url

        # Merge lists
        self.demo_urls = list(set(self.demo_urls + other.demo_urls))
        self.source_urls = list(set(self.source_urls + other.source_urls))
        self.evidence_snippets.extend(other.evidence_snippets)
        self.sources.update(other.sources)

        # Take max values
        self.stars_total = max(self.stars_total, other.stars_total)
        self.repo_count = max(self.repo_count, other.repo_count)

        # Keep most recent activity
        if other.last_activity:
            if not self.last_activity or other.last_activity > self.last_activity:
                self.last_activity = other.last_activity

        # Regenerate ID with new info
        self.id = self._generate_id()

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "github_username": self.github_username,
            "hn_username": self.hn_username,
            "reddit_username": self.reddit_username,
            "email": self.email,
            "linkedin_url": self.linkedin_url,
            "twitter_handle": self.twitter_handle,
            "github_url": self.github_url,
            "website": self.website,
            "demo_urls": self.demo_urls,
            "source_urls": self.source_urls,
            "bio": self.bio,
            "evidence_snippets": self.evidence_snippets,
            "location_raw": self.location_raw,
            "country": self.country,
            "metro_bucket": self.metro_bucket,
            "location_confidence": self.location_confidence,
            "location_evidence_url": self.location_evidence_url,
            "sources": list(self.sources),
            "last_activity": self.last_activity,
            "stars_total": self.stars_total,
            "repo_count": self.repo_count,
            "scores": self.scores,
            "total_score": self.total_score,
            "recruiter_pitch": self.recruiter_pitch,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "Candidate":
        """Create from dictionary."""
        data = data.copy()
        data["sources"] = set(data.get("sources", []))
        data["evidence_snippets"] = data.get("evidence_snippets", [])
        data["demo_urls"] = data.get("demo_urls", [])
        data["source_urls"] = data.get("source_urls", [])
        data["scores"] = data.get("scores", {})
        return cls(**data)


class CandidateDeduper:
    """Deduplicates and merges candidates across sources."""

    def __init__(self, similarity_threshold: float = 0.85):
        self.similarity_threshold = similarity_threshold
        self.candidates: Dict[str, Candidate] = {}  # id -> Candidate
        self._github_index: Dict[str, str] = {}  # github_username -> id
        self._hn_index: Dict[str, str] = {}  # hn_username -> id
        self._reddit_index: Dict[str, str] = {}  # reddit_username -> id
        self._twitter_index: Dict[str, str] = {}  # twitter_handle -> id
        self._linkedin_index: Dict[str, str] = {}  # linkedin_url -> id
        self._domain_index: Dict[str, str] = {}  # domain -> id
        self._email_index: Dict[str, str] = {}  # email -> id

    def add(self, candidate: Candidate) -> Candidate:
        """Add a candidate, merging if duplicate found."""
        existing_id = self._find_existing(candidate)

        if existing_id:
            existing = self.candidates[existing_id]
            existing.merge_from(candidate)
            self._update_indices(existing)
            return existing
        else:
            self.candidates[candidate.id] = candidate
            self._update_indices(candidate)
            return candidate

    def _find_existing(self, candidate: Candidate) -> Optional[str]:
        """Find existing candidate ID if this is a duplicate."""
        # Check exact matches first - prioritized by reliability
        if candidate.github_username:
            key = candidate.github_username.lower()
            if key in self._github_index:
                return self._github_index[key]

        if candidate.hn_username:
            key = candidate.hn_username.lower()
            if key in self._hn_index:
                return self._hn_index[key]

        if candidate.reddit_username:
            key = candidate.reddit_username.lower()
            if key in self._reddit_index:
                return self._reddit_index[key]

        if candidate.email:
            key = candidate.email.lower()
            if key in self._email_index:
                return self._email_index[key]

        if candidate.twitter_handle:
            key = candidate.twitter_handle.lower()
            if key in self._twitter_index:
                return self._twitter_index[key]

        if candidate.linkedin_url:
            key = self._normalize_linkedin_url(candidate.linkedin_url)
            if key and key in self._linkedin_index:
                return self._linkedin_index[key]

        if candidate.website:
            domain = self._extract_domain(candidate.website)
            if domain and domain in self._domain_index:
                return self._domain_index[domain]

        # Check name + any common identifier similarity
        if candidate.name:
            for existing in self.candidates.values():
                if existing.name:
                    name_sim = self._name_similarity(candidate.name, existing.name)
                    if name_sim > self.similarity_threshold:
                        # Additional check: same domain or handle
                        if self._has_common_identifier(candidate, existing):
                            return existing.id

        # Cross-reference check: same name with high confidence and overlapping sources
        # e.g., if found on both GitHub and Dev.to with same name
        if candidate.name and len(candidate.name) > 5:
            for existing in self.candidates.values():
                if existing.name and self._name_similarity(candidate.name, existing.name) > 0.9:
                    # Check if they have complementary identifiers that could be same person
                    if self._likely_same_person(candidate, existing):
                        return existing.id

        return None

    def _likely_same_person(self, c1: Candidate, c2: Candidate) -> bool:
        """Determine if two candidates are likely the same person based on cross-signals."""
        # Same website domain
        if c1.website and c2.website:
            d1 = self._extract_domain(c1.website)
            d2 = self._extract_domain(c2.website)
            if d1 and d2 and d1 == d2:
                return True

        # Same demo URLs
        if c1.demo_urls and c2.demo_urls:
            c1_domains = {self._extract_domain(u) for u in c1.demo_urls if u}
            c2_domains = {self._extract_domain(u) for u in c2.demo_urls if u}
            if c1_domains & c2_domains:  # Intersection
                return True

        # One has GitHub username that matches the other's Twitter/HN handle
        handles1 = {h.lower() for h in [c1.github_username, c1.hn_username, c1.twitter_handle] if h}
        handles2 = {h.lower() for h in [c2.github_username, c2.hn_username, c2.twitter_handle] if h}
        if handles1 & handles2:
            return True

        return False

    def _update_indices(self, candidate: Candidate) -> None:
        """Update lookup indices for a candidate."""
        if candidate.github_username:
            self._github_index[candidate.github_username.lower()] = candidate.id

        if candidate.hn_username:
            self._hn_index[candidate.hn_username.lower()] = candidate.id

        if candidate.reddit_username:
            self._reddit_index[candidate.reddit_username.lower()] = candidate.id

        if candidate.twitter_handle:
            self._twitter_index[candidate.twitter_handle.lower()] = candidate.id

        if candidate.linkedin_url:
            key = self._normalize_linkedin_url(candidate.linkedin_url)
            if key:
                self._linkedin_index[key] = candidate.id

        if candidate.email:
            self._email_index[candidate.email.lower()] = candidate.id

        if candidate.website:
            domain = self._extract_domain(candidate.website)
            if domain:
                self._domain_index[domain] = candidate.id

    @staticmethod
    def _normalize_linkedin_url(url: str) -> Optional[str]:
        """Extract LinkedIn username from URL for indexing."""
        if not url:
            return None
        match = re.search(r"linkedin\.com/in/([a-zA-Z0-9_-]+)", url, re.I)
        return match.group(1).lower() if match else None

    @staticmethod
    def _extract_domain(url: str) -> Optional[str]:
        """Extract domain from URL, excluding common platforms."""
        if not url:
            return None

        match = re.search(r"https?://(?:www\.)?([^/]+)", url)
        if not match:
            return None

        domain = match.group(1).lower()

        # Skip common platforms that don't identify individuals
        skip_domains = {
            "github.com", "twitter.com", "x.com", "linkedin.com",
            "medium.com", "substack.com", "youtube.com",
        }

        if domain in skip_domains:
            return None

        return domain

    @staticmethod
    def _name_similarity(name1: str, name2: str) -> float:
        """Calculate similarity between two names."""
        n1 = name1.lower().strip()
        n2 = name2.lower().strip()
        return SequenceMatcher(None, n1, n2).ratio()

    @staticmethod
    def _has_common_identifier(c1: Candidate, c2: Candidate) -> bool:
        """Check if two candidates share any identifier."""
        if c1.github_username and c2.github_username:
            if c1.github_username.lower() == c2.github_username.lower():
                return True

        if c1.hn_username and c2.hn_username:
            if c1.hn_username.lower() == c2.hn_username.lower():
                return True

        if c1.twitter_handle and c2.twitter_handle:
            if c1.twitter_handle.lower() == c2.twitter_handle.lower():
                return True

        if c1.email and c2.email:
            if c1.email.lower() == c2.email.lower():
                return True

        if c1.linkedin_url and c2.linkedin_url:
            # Compare LinkedIn usernames
            l1 = re.search(r"linkedin\.com/in/([a-zA-Z0-9_-]+)", c1.linkedin_url, re.I)
            l2 = re.search(r"linkedin\.com/in/([a-zA-Z0-9_-]+)", c2.linkedin_url, re.I)
            if l1 and l2 and l1.group(1).lower() == l2.group(1).lower():
                return True

        return False

    def get_all(self) -> List[Candidate]:
        """Get all deduplicated candidates."""
        return list(self.candidates.values())

    def get_count(self) -> int:
        """Get count of unique candidates."""
        return len(self.candidates)
