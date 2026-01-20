"""Transparent scoring rubric for candidates."""

import re
from typing import Dict, List, Tuple
from dataclasses import dataclass

from utils.dedupe import Candidate
from utils.text import normalize_text


@dataclass
class ScoreBreakdown:
    """Detailed breakdown of candidate score."""
    shipping_velocity: float = 0.0  # 0-30
    tooling_signals: float = 0.0    # 0-20
    founder_fit: float = 0.0        # 0-25
    fintech_relevance: float = 0.0  # 0-15
    communication: float = 0.0      # 0-10

    raw_total: float = 0.0          # 0-100
    location_multiplier: float = 1.0
    final_total: float = 0.0        # 0-100

    explanations: Dict[str, List[str]] = None

    def __post_init__(self):
        if self.explanations is None:
            self.explanations = {}


class CandidateScorer:
    """Scores candidates using a transparent rubric."""

    # Shipping velocity keywords and weights
    SHIPPING_KEYWORDS = {
        # High signal (3 points each)
        "shipped in a weekend": 3,
        "built in a weekend": 3,
        "launched": 3,
        "shipped": 3,
        "demo": 2,
        "prototype": 2,
        "mvp": 2,
        "hack": 1,
        "hackathon": 2,
        "weekend project": 3,
        "side project": 1,
        "24 hours": 3,
        "48 hours": 3,
    }

    # Tooling keywords and weights
    TOOLING_KEYWORDS = {
        # AI/LLM tools (high signal)
        "cursor": 4,
        "cursor ai": 4,
        "v0": 4,
        "v0.dev": 4,
        "vercel v0": 4,
        "replit": 3,
        "copilot": 2,
        "github copilot": 2,

        # AI/LLM frameworks
        "langchain": 3,
        "langgraph": 3,
        "llamaindex": 3,
        "openai": 2,
        "anthropic": 2,
        "claude": 2,
        "gpt-4": 2,
        "gpt4": 2,

        # Modern stack
        "supabase": 2,
        "next.js": 1,
        "nextjs": 1,
        "vercel": 1,
        "streamlit": 2,
        "gradio": 2,

        # AI agent signals
        "ai agent": 4,
        "llm app": 3,
        "prompt-to-app": 4,
        "agent": 2,
    }

    # Founder/incubator keywords
    FOUNDER_KEYWORDS = {
        # Strong founder signals
        "founder": 4,
        "co-founder": 5,
        "cofounder": 5,
        "ceo": 3,
        "cto": 3,

        # Incubators/accelerators
        "y combinator": 5,
        "yc": 4,
        "ycombinator": 5,
        "antler": 4,
        "entrepreneur first": 4,
        "ef": 2,
        "techstars": 3,
        "500 startups": 3,
        "on deck": 3,

        # Startup signals
        "startup": 2,
        "bootstrapped": 3,
        "seed round": 3,
        "series a": 2,

        # PM signals
        "product manager": 3,
        "product lead": 3,
        "head of product": 4,
        "pm": 1,
    }

    # Fintech keywords
    FINTECH_KEYWORDS = {
        "fintech": 4,
        "payments": 3,
        "banking": 3,
        "neobank": 4,
        "trading": 2,
        "investing": 2,
        "crypto": 2,
        "defi": 2,
        "web3": 1,
        "stripe": 2,
        "plaid": 3,
        "sofi": 3,
        "financial": 2,
        "finance": 2,
        "credit": 2,
        "lending": 2,
        "insurance": 2,
        "insurtech": 3,
    }

    def __init__(self):
        pass

    def score(self, candidate: Candidate) -> Candidate:
        """
        Score a candidate and update their scores field.

        Returns the candidate with updated scores.
        """
        breakdown = ScoreBreakdown()
        all_text = self._collect_text(candidate)

        # Calculate each subscore
        breakdown.shipping_velocity, breakdown.explanations["shipping"] = \
            self._score_shipping(all_text, candidate)

        breakdown.tooling_signals, breakdown.explanations["tooling"] = \
            self._score_tooling(all_text)

        breakdown.founder_fit, breakdown.explanations["founder"] = \
            self._score_founder(all_text, candidate)

        breakdown.fintech_relevance, breakdown.explanations["fintech"] = \
            self._score_fintech(all_text)

        breakdown.communication, breakdown.explanations["communication"] = \
            self._score_communication(candidate)

        # Calculate raw total
        breakdown.raw_total = (
            breakdown.shipping_velocity +
            breakdown.tooling_signals +
            breakdown.founder_fit +
            breakdown.fintech_relevance +
            breakdown.communication
        )

        # Apply location multiplier
        breakdown.location_multiplier = self._get_location_multiplier(candidate)
        breakdown.final_total = min(100, breakdown.raw_total * breakdown.location_multiplier)

        # Update candidate
        candidate.scores = {
            "shipping_velocity": round(breakdown.shipping_velocity, 1),
            "tooling_signals": round(breakdown.tooling_signals, 1),
            "founder_fit": round(breakdown.founder_fit, 1),
            "fintech_relevance": round(breakdown.fintech_relevance, 1),
            "communication": round(breakdown.communication, 1),
            "raw_total": round(breakdown.raw_total, 1),
            "location_multiplier": round(breakdown.location_multiplier, 2),
            "explanations": breakdown.explanations,
        }
        candidate.total_score = round(breakdown.final_total, 1)

        # Generate recruiter pitch
        candidate.recruiter_pitch = self._generate_pitch(candidate, breakdown)

        return candidate

    def _collect_text(self, candidate: Candidate) -> str:
        """Collect all text from candidate for analysis."""
        parts = []

        if candidate.bio:
            parts.append(candidate.bio)

        for evidence in candidate.evidence_snippets:
            if isinstance(evidence, dict):
                parts.append(evidence.get("text", ""))
            else:
                parts.append(str(evidence))

        return normalize_text(" ".join(parts))

    def _score_shipping(self, text: str, candidate: Candidate) -> Tuple[float, List[str]]:
        """Score shipping velocity signals (0-30 points)."""
        points = 0
        explanations = []

        # Keyword matches
        for keyword, weight in self.SHIPPING_KEYWORDS.items():
            if keyword in text:
                points += weight
                explanations.append(f"+{weight}: found '{keyword}'")

        # Bonus for having demo URLs
        if candidate.demo_urls:
            bonus = min(len(candidate.demo_urls) * 2, 6)
            points += bonus
            explanations.append(f"+{bonus}: has {len(candidate.demo_urls)} demo URL(s)")

        # Bonus for recent activity
        if candidate.last_activity:
            from datetime import datetime, timezone
            try:
                last = datetime.fromisoformat(candidate.last_activity.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                days_ago = (now - last).days

                if days_ago < 30:
                    points += 4
                    explanations.append("+4: active in last 30 days")
                elif days_ago < 90:
                    points += 2
                    explanations.append("+2: active in last 90 days")
            except:
                pass

        # Bonus for GitHub stars
        if candidate.stars_total >= 100:
            points += 3
            explanations.append(f"+3: {candidate.stars_total}+ stars on GitHub")
        elif candidate.stars_total >= 10:
            points += 1
            explanations.append(f"+1: {candidate.stars_total}+ stars on GitHub")

        return min(30, points), explanations

    def _score_tooling(self, text: str) -> Tuple[float, List[str]]:
        """Score tooling signals (0-20 points)."""
        points = 0
        explanations = []

        for keyword, weight in self.TOOLING_KEYWORDS.items():
            if keyword in text:
                points += weight
                explanations.append(f"+{weight}: uses '{keyword}'")

        return min(20, points), explanations

    def _score_founder(self, text: str, candidate: Candidate) -> Tuple[float, List[str]]:
        """Score founder/incubation fit (0-25 points)."""
        points = 0
        explanations = []

        for keyword, weight in self.FOUNDER_KEYWORDS.items():
            if keyword in text:
                points += weight
                explanations.append(f"+{weight}: found '{keyword}'")

        # Bonus for multiple sources (shows public presence)
        if len(candidate.sources) >= 2:
            bonus = min(len(candidate.sources) * 2, 6)
            points += bonus
            explanations.append(f"+{bonus}: found on {len(candidate.sources)} sources")

        # Bonus for having a personal website
        if candidate.website:
            points += 3
            explanations.append("+3: has personal website")

        return min(25, points), explanations

    def _score_fintech(self, text: str) -> Tuple[float, List[str]]:
        """Score fintech/payments relevance (0-15 points)."""
        points = 0
        explanations = []

        for keyword, weight in self.FINTECH_KEYWORDS.items():
            if keyword in text:
                points += weight
                explanations.append(f"+{weight}: mentions '{keyword}'")

        return min(15, points), explanations

    def _score_communication(self, candidate: Candidate) -> Tuple[float, List[str]]:
        """Score communication clarity (0-10 points)."""
        points = 0
        explanations = []

        # Has bio
        if candidate.bio and len(candidate.bio) > 20:
            points += 3
            explanations.append("+3: has bio")

        # Has name
        if candidate.name:
            points += 2
            explanations.append("+2: name available")

        # Has multiple evidence snippets
        if len(candidate.evidence_snippets) >= 3:
            points += 3
            explanations.append("+3: multiple evidence snippets")
        elif len(candidate.evidence_snippets) >= 1:
            points += 1
            explanations.append("+1: has evidence")

        # Has contact info (GitHub, website)
        if candidate.github_url or candidate.website:
            points += 2
            explanations.append("+2: contactable")

        return min(10, points), explanations

    def _get_location_multiplier(self, candidate: Candidate) -> float:
        """Get location multiplier based on metro bucket."""
        if candidate.metro_bucket == "SF_BAY_AREA":
            return 1.10
        elif candidate.metro_bucket == "OTHER_US":
            return 1.00
        elif candidate.metro_bucket == "UNKNOWN":
            return 0.80
        elif candidate.metro_bucket == "NON_US":
            return 0.0  # Will be filtered out
        return 0.80

    def _generate_pitch(self, candidate: Candidate, breakdown: ScoreBreakdown) -> str:
        """Generate a 2-sentence recruiter pitch."""
        parts = []

        # First sentence: who they are
        name = candidate.name or candidate.github_username or candidate.hn_username or "This candidate"

        if breakdown.founder_fit >= 15:
            parts.append(f"{name} shows strong founder/PM signals")
        elif breakdown.tooling_signals >= 12:
            parts.append(f"{name} is proficient with modern AI tooling")
        elif breakdown.shipping_velocity >= 20:
            parts.append(f"{name} demonstrates strong shipping velocity")
        else:
            parts.append(f"{name} shows vibe coding signals")

        # Add location if known
        if candidate.metro_bucket == "SF_BAY_AREA":
            parts[0] += " and is based in the SF Bay Area"
        elif candidate.country == "US":
            parts[0] += " and is US-based"

        parts[0] += "."

        # Second sentence: evidence
        evidence_parts = []
        if breakdown.fintech_relevance >= 8:
            evidence_parts.append("fintech experience")
        if "yc" in str(breakdown.explanations.get("founder", [])).lower():
            evidence_parts.append("YC background")
        if breakdown.shipping_velocity >= 15:
            evidence_parts.append("proven shipping track record")
        if breakdown.tooling_signals >= 10:
            top_tools = []
            for exp in breakdown.explanations.get("tooling", [])[:2]:
                if "cursor" in exp.lower():
                    top_tools.append("Cursor")
                elif "v0" in exp.lower():
                    top_tools.append("v0")
                elif "langchain" in exp.lower():
                    top_tools.append("LangChain")
            if top_tools:
                evidence_parts.append(f"uses {'/'.join(top_tools)}")

        if evidence_parts:
            parts.append(f"Key signals: {', '.join(evidence_parts[:3])}.")
        else:
            parts.append("Worth exploring for incubation lab potential.")

        return " ".join(parts)

    def score_all(self, candidates: List[Candidate]) -> List[Candidate]:
        """Score all candidates and return sorted by score."""
        scored = []
        for candidate in candidates:
            # Skip NON_US candidates
            if candidate.metro_bucket == "NON_US":
                continue

            self.score(candidate)
            scored.append(candidate)

        # Sort by total score descending
        scored.sort(key=lambda c: c.total_score, reverse=True)
        return scored
