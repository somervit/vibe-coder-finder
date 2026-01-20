"""LLM-powered scoring for better recruiter pitches and nuanced analysis."""

import os
import json
from typing import Dict, List, Optional

from utils.rate_limit import rate_limited_request
from utils.logging import get_logger
from utils.dedupe import Candidate

logger = get_logger(source="llm_scorer")


class LLMScorer:
    """Uses LLM to generate recruiter pitches and refine candidate analysis."""

    PITCH_PROMPT = """You are a technical recruiter at a fintech startup looking for "vibe coders" - people who rapidly ship prototypes using modern AI tooling like Cursor, v0, Replit, LangChain, etc.

Analyze this candidate and write a 2-3 sentence recruiter pitch. Focus on:
1. Their shipping velocity and builder mentality
2. Their use of modern AI/LLM tools
3. Any founder/PM/startup experience
4. Fintech or relevant domain experience

Candidate Profile:
- Name: {name}
- GitHub: {github_username}
- HN: {hn_username}
- Location: {location} ({metro_bucket})
- Bio: {bio}
- Website: {website}
- Demo URLs: {demo_urls}
- Sources found on: {sources}
- Evidence snippets:
{evidence}

Current scores:
- Shipping Velocity: {shipping_velocity}/30
- Tooling Signals: {tooling_signals}/20
- Founder Fit: {founder_fit}/25
- Fintech Relevance: {fintech_relevance}/15
- Communication: {communication}/10
- Total: {total_score}/100

Write a compelling, specific pitch that highlights what makes this person stand out. Be concise and factual - only mention things you can see in the evidence. If there's limited evidence, acknowledge that.

Output JSON format:
{{
    "pitch": "2-3 sentence pitch",
    "confidence": "high/medium/low",
    "key_signals": ["signal1", "signal2", "signal3"],
    "concerns": ["concern1"] or [],
    "adjusted_score": null or number (only if evidence strongly suggests score should be different)
}}"""

    def __init__(self, provider: str = "anthropic", model: str = None):
        """
        Initialize LLM scorer.

        Args:
            provider: "anthropic" or "openai"
            model: Model to use (default: claude-3-haiku or gpt-4o-mini)
        """
        self.provider = provider

        if provider == "anthropic":
            self.api_key = os.environ.get("ANTHROPIC_API_KEY")
            self.model = model or "claude-3-haiku-20240307"
            self.base_url = "https://api.anthropic.com/v1/messages"
        elif provider == "openai":
            self.api_key = os.environ.get("OPENAI_API_KEY")
            self.model = model or "gpt-4o-mini"
            self.base_url = "https://api.openai.com/v1/chat/completions"
        else:
            raise ValueError(f"Unknown provider: {provider}")

        if not self.api_key:
            logger.warning(f"No API key for {provider} - LLM scoring disabled")

    def is_available(self) -> bool:
        """Check if LLM scoring is available."""
        return bool(self.api_key)

    def generate_pitch(self, candidate: Candidate) -> Optional[Dict]:
        """
        Generate an LLM-powered pitch for a candidate.

        Returns dict with pitch, confidence, key_signals, concerns, adjusted_score
        """
        if not self.api_key:
            return None

        # Format evidence
        evidence_text = ""
        for i, e in enumerate(candidate.evidence_snippets[:8], 1):
            if isinstance(e, dict):
                text = e.get("text", "")[:200]
                source = e.get("source", "unknown")
                evidence_text += f"{i}. [{source}] {text}\n"
            else:
                evidence_text += f"{i}. {str(e)[:200]}\n"

        # Build prompt
        prompt = self.PITCH_PROMPT.format(
            name=candidate.name or "Unknown",
            github_username=candidate.github_username or "N/A",
            hn_username=candidate.hn_username or "N/A",
            location=candidate.location_raw or "Unknown",
            metro_bucket=candidate.metro_bucket,
            bio=candidate.bio[:500] if candidate.bio else "N/A",
            website=candidate.website or "N/A",
            demo_urls=", ".join(candidate.demo_urls[:3]) if candidate.demo_urls else "N/A",
            sources=", ".join(candidate.sources) if candidate.sources else "N/A",
            evidence=evidence_text or "No evidence snippets",
            shipping_velocity=candidate.scores.get("shipping_velocity", 0),
            tooling_signals=candidate.scores.get("tooling_signals", 0),
            founder_fit=candidate.scores.get("founder_fit", 0),
            fintech_relevance=candidate.scores.get("fintech_relevance", 0),
            communication=candidate.scores.get("communication", 0),
            total_score=candidate.total_score,
        )

        try:
            if self.provider == "anthropic":
                return self._call_anthropic(prompt)
            else:
                return self._call_openai(prompt)
        except Exception as e:
            logger.error(f"LLM pitch generation failed: {e}")
            return None

    def _call_anthropic(self, prompt: str) -> Optional[Dict]:
        """Call Anthropic API."""
        headers = {
            "x-api-key": self.api_key,
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        }

        data = {
            "model": self.model,
            "max_tokens": 500,
            "messages": [
                {"role": "user", "content": prompt}
            ],
        }

        response = rate_limited_request(
            source="anthropic",
            method="POST",
            url=self.base_url,
            headers=headers,
            json=data,
            timeout=30,
        )

        if response.status_code != 200:
            logger.warning(f"Anthropic API error: {response.status_code}")
            return None

        result = response.json()
        content = result.get("content", [{}])[0].get("text", "")

        return self._parse_response(content)

    def _call_openai(self, prompt: str) -> Optional[Dict]:
        """Call OpenAI API."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        data = {
            "model": self.model,
            "max_tokens": 500,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "response_format": {"type": "json_object"},
        }

        response = rate_limited_request(
            source="openai",
            method="POST",
            url=self.base_url,
            headers=headers,
            json=data,
            timeout=30,
        )

        if response.status_code != 200:
            logger.warning(f"OpenAI API error: {response.status_code}")
            return None

        result = response.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")

        return self._parse_response(content)

    def _parse_response(self, content: str) -> Optional[Dict]:
        """Parse LLM response JSON."""
        try:
            # Try to extract JSON from response
            # Handle case where LLM wraps JSON in markdown code blocks
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            data = json.loads(content.strip())
            return {
                "pitch": data.get("pitch", ""),
                "confidence": data.get("confidence", "medium"),
                "key_signals": data.get("key_signals", []),
                "concerns": data.get("concerns", []),
                "adjusted_score": data.get("adjusted_score"),
            }
        except (json.JSONDecodeError, IndexError) as e:
            logger.debug(f"Failed to parse LLM response: {e}")
            # Fall back to using raw content as pitch
            if content and len(content) > 20:
                return {
                    "pitch": content[:300],
                    "confidence": "low",
                    "key_signals": [],
                    "concerns": [],
                    "adjusted_score": None,
                }
            return None

    def enhance_candidates(
        self,
        candidates: List[Candidate],
        max_candidates: int = 50,
    ) -> List[Candidate]:
        """
        Enhance top candidates with LLM-generated pitches.

        Args:
            candidates: List of scored candidates
            max_candidates: Maximum candidates to enhance (to control costs)

        Returns:
            Candidates with enhanced pitches
        """
        if not self.is_available():
            logger.warning("LLM scoring not available - skipping enhancement")
            return candidates

        logger.info(f"Enhancing top {max_candidates} candidates with LLM pitches")

        enhanced_count = 0
        for candidate in candidates[:max_candidates]:
            result = self.generate_pitch(candidate)

            if result and result.get("pitch"):
                candidate.recruiter_pitch = result["pitch"]

                # Store additional LLM analysis in scores
                candidate.scores["llm_confidence"] = result.get("confidence", "medium")
                candidate.scores["llm_key_signals"] = result.get("key_signals", [])
                candidate.scores["llm_concerns"] = result.get("concerns", [])

                # Optionally adjust score if LLM suggests it
                adjusted = result.get("adjusted_score")
                if adjusted is not None and isinstance(adjusted, (int, float)):
                    # Only allow small adjustments (+/- 10 points)
                    diff = adjusted - candidate.total_score
                    if -10 <= diff <= 10:
                        candidate.total_score = round(adjusted, 1)
                        candidate.scores["llm_adjusted"] = True

                enhanced_count += 1

        logger.info(f"Enhanced {enhanced_count} candidates with LLM pitches")

        # Re-sort by score in case adjustments were made
        candidates.sort(key=lambda c: c.total_score, reverse=True)

        return candidates
