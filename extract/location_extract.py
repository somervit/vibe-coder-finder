"""Location extraction and classification."""

import re
from typing import Dict, Optional, Tuple
from dataclasses import dataclass


@dataclass
class LocationResult:
    """Result of location extraction."""
    location_raw: Optional[str] = None
    country: str = "unknown"  # US / non-US / unknown
    metro_bucket: str = "UNKNOWN"  # SF_BAY_AREA / OTHER_US / NON_US / UNKNOWN
    confidence: float = 0.0
    evidence_url: Optional[str] = None


# San Francisco / Bay Area cities and variations
SF_BAY_AREA_PATTERNS = [
    r"\bsan\s*francisco\b",
    r"\bsf\b(?!\s*\d)",  # SF but not SF followed by numbers
    r"\bbay\s*area\b",
    r"\bsilicon\s*valley\b",
    r"\boakland\b",
    r"\bberkeley\b",
    r"\bsan\s*jose\b",
    r"\bpalo\s*alto\b",
    r"\bmountain\s*view\b",
    r"\bsunnyvale\b",
    r"\bmenlo\s*park\b",
    r"\bredwood\s*city\b",
    r"\bsanta\s*clara\b",
    r"\bfremont\b",
    r"\bsan\s*mateo\b",
    r"\bdaly\s*city\b",
    r"\bsausalito\b",
    r"\bwalnut\s*creek\b",
    r"\bcupertino\b",
    r"\blos\s*altos\b",
    r"\bfoster\s*city\b",
    r"\bsoma\b",  # South of Market, SF
    r"\bmission\s*district\b",
]

# Other major US tech hubs and cities
OTHER_US_PATTERNS = [
    # States
    r"\bcalifornia\b", r"\b,?\s*ca\b",
    r"\bnew\s*york\b", r"\bnyc\b", r"\b,?\s*ny\b",
    r"\btexas\b", r"\b,?\s*tx\b",
    r"\bwashington\b", r"\bseattle\b", r"\b,?\s*wa\b",
    r"\bcolorado\b", r"\bdenver\b", r"\bboulder\b", r"\b,?\s*co\b",
    r"\bmassachusetts\b", r"\bboston\b", r"\bcambridge\b", r"\b,?\s*ma\b",
    r"\bflorida\b", r"\bmiami\b", r"\b,?\s*fl\b",
    r"\bgeorgia\b", r"\batlanta\b", r"\b,?\s*ga\b",
    r"\billinois\b", r"\bchicago\b", r"\b,?\s*il\b",
    r"\barizona\b", r"\bphoenix\b", r"\bscottsdale\b", r"\b,?\s*az\b",
    r"\boregon\b", r"\bportland\b", r"\b,?\s*or\b",
    r"\bminnesota\b", r"\bminneapolis\b", r"\b,?\s*mn\b",
    r"\butah\b", r"\bsalt\s*lake\b", r"\b,?\s*ut\b",
    r"\bnorth\s*carolina\b", r"\braleigh\b", r"\bdurham\b", r"\bcharlotte\b", r"\b,?\s*nc\b",
    r"\bvirginia\b", r"\b,?\s*va\b",
    r"\bnew\s*jersey\b", r"\b,?\s*nj\b",
    r"\bpennsylvania\b", r"\bphiladelphia\b", r"\bpittsburgh\b", r"\b,?\s*pa\b",
    r"\bmichigan\b", r"\bdetroit\b", r"\bann\s*arbor\b", r"\b,?\s*mi\b",
    r"\bohio\b", r"\bcolumbus\b", r"\b,?\s*oh\b",
    r"\bmaryland\b", r"\bbaltimore\b", r"\b,?\s*md\b",
    r"\bwashington\s*d\.?c\.?\b", r"\bdc\b",
    r"\baustin\b", r"\bhouston\b", r"\bdallas\b",
    r"\blos\s*angeles\b", r"\bla\b(?!\s*\d)", r"\bsan\s*diego\b",
    # Generic US
    r"\bunited\s*states\b", r"\busa\b", r"\bu\.?s\.?a?\.?\b",
]

# Non-US countries and cities
NON_US_PATTERNS = [
    # Countries
    r"\bcanada\b", r"\btoronto\b", r"\bvancouver\b", r"\bmontreal\b",
    r"\bunited\s*kingdom\b", r"\buk\b", r"\blondon\b", r"\bengland\b",
    r"\bgermany\b", r"\bberlin\b", r"\bmunich\b",
    r"\bfrance\b", r"\bparis\b",
    r"\bindia\b", r"\bbangalore\b", r"\bmumbai\b", r"\bdelhi\b", r"\bhyderabad\b",
    r"\bchina\b", r"\bbeijing\b", r"\bshanghai\b", r"\bshenzhen\b",
    r"\bjapan\b", r"\btokyo\b",
    r"\baustralia\b", r"\bsydney\b", r"\bmelbourne\b",
    r"\bsingapore\b",
    r"\bnetherlands\b", r"\bamsterdam\b",
    r"\bsweden\b", r"\bstockholm\b",
    r"\bisrael\b", r"\btel\s*aviv\b",
    r"\bbrazil\b", r"\bsao\s*paulo\b",
    r"\bspain\b", r"\bmadrid\b", r"\bbarcelona\b",
    r"\bitaly\b", r"\bmilan\b", r"\brome\b",
    r"\bireland\b", r"\bdublin\b",
    r"\bpoland\b", r"\bwarsaw\b", r"\bkrakow\b",
    r"\bukraine\b", r"\bkyiv\b",
    r"\bportugal\b", r"\blisbon\b",
    r"\bmexico\b",
    r"\bargentina\b", r"\bbuenos\s*aires\b",
    r"\bsouth\s*korea\b", r"\bseoul\b",
    r"\bindonesia\b", r"\bjakarta\b",
    r"\bvietnam\b", r"\bhanoi\b",
    r"\bthailand\b", r"\bbangkok\b",
    r"\bphilippines\b", r"\bmanila\b",
    r"\bpakistan\b", r"\bkarachi\b",
    r"\bnigeria\b", r"\blagos\b",
    r"\bkenya\b", r"\bnairobi\b",
    r"\bsouth\s*africa\b", r"\bcape\s*town\b", r"\bjohannesburg\b",
    r"\begypt\b", r"\bcairo\b",
    r"\brumania\b", r"\bbucharest\b",
    r"\bczech\b", r"\bprague\b",
    r"\bhungary\b", r"\bbudapest\b",
    r"\baustria\b", r"\bvienna\b",
    r"\bswitzerland\b", r"\bzurich\b", r"\bgeneva\b",
    r"\bbelgium\b", r"\bbrussels\b",
    r"\bdenmark\b", r"\bcopenhagen\b",
    r"\bnorway\b", r"\boslo\b",
    r"\bfinland\b", r"\bhelsinki\b",
    r"\bnew\s*zealand\b", r"\bauckland\b", r"\bwellington\b",
    r"\brussia\b", r"\bmoscow\b",
    r"\bturkey\b", r"\bistanbul\b",
    r"\buae\b", r"\bdubai\b", r"\babu\s*dhabi\b",
]


class LocationExtractor:
    """Extracts and classifies location information."""

    def __init__(self):
        # Compile patterns for efficiency
        self._sf_patterns = [re.compile(p, re.IGNORECASE) for p in SF_BAY_AREA_PATTERNS]
        self._us_patterns = [re.compile(p, re.IGNORECASE) for p in OTHER_US_PATTERNS]
        self._non_us_patterns = [re.compile(p, re.IGNORECASE) for p in NON_US_PATTERNS]

    def extract(
        self,
        location_field: Optional[str] = None,
        bio_text: Optional[str] = None,
        about_text: Optional[str] = None,
        evidence_url: Optional[str] = None,
    ) -> LocationResult:
        """
        Extract location from various text sources.

        Args:
            location_field: Explicit location field (e.g., from GitHub profile)
            bio_text: Bio or description text
            about_text: About page or longer text
            evidence_url: URL where location was found

        Returns:
            LocationResult with classification
        """
        result = LocationResult(evidence_url=evidence_url)

        # Process location field first (highest signal)
        if location_field:
            field_result = self._classify_text(location_field)
            if field_result[1] > 0.5:  # High confidence from explicit field
                result.location_raw = location_field
                result.metro_bucket = field_result[0]
                result.country = self._bucket_to_country(field_result[0])
                result.confidence = min(field_result[1] + 0.2, 1.0)  # Boost for explicit field
                return result

        # Check bio text
        if bio_text:
            bio_result = self._classify_text(bio_text)
            if bio_result[1] > result.confidence:
                result.location_raw = self._extract_location_phrase(bio_text)
                result.metro_bucket = bio_result[0]
                result.country = self._bucket_to_country(bio_result[0])
                result.confidence = bio_result[1]

        # Check about text
        if about_text:
            about_result = self._classify_text(about_text)
            if about_result[1] > result.confidence:
                result.location_raw = self._extract_location_phrase(about_text)
                result.metro_bucket = about_result[0]
                result.country = self._bucket_to_country(about_result[0])
                result.confidence = about_result[1]

        return result

    def _classify_text(self, text: str) -> Tuple[str, float]:
        """
        Classify text and return (metro_bucket, confidence).
        """
        if not text:
            return ("UNKNOWN", 0.0)

        text_lower = text.lower()

        # Check SF Bay Area first (highest priority)
        sf_matches = sum(1 for p in self._sf_patterns if p.search(text_lower))
        if sf_matches > 0:
            confidence = min(0.6 + (sf_matches * 0.15), 1.0)
            return ("SF_BAY_AREA", confidence)

        # Check non-US (before general US to avoid false positives)
        non_us_matches = sum(1 for p in self._non_us_patterns if p.search(text_lower))
        if non_us_matches > 0:
            confidence = min(0.5 + (non_us_matches * 0.15), 1.0)
            return ("NON_US", confidence)

        # Check other US locations
        us_matches = sum(1 for p in self._us_patterns if p.search(text_lower))
        if us_matches > 0:
            confidence = min(0.5 + (us_matches * 0.15), 1.0)
            return ("OTHER_US", confidence)

        return ("UNKNOWN", 0.0)

    def _bucket_to_country(self, bucket: str) -> str:
        """Convert metro bucket to country classification."""
        if bucket == "SF_BAY_AREA":
            return "US"
        elif bucket == "OTHER_US":
            return "US"
        elif bucket == "NON_US":
            return "non-US"
        return "unknown"

    def _extract_location_phrase(self, text: str) -> Optional[str]:
        """Extract a location phrase from text."""
        # Look for common location patterns
        patterns = [
            r"(?:based\s+in|located\s+in|from|living\s+in|@)\s+([^,.]+(?:,\s*[^,.]+)?)",
            r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?,\s*(?:CA|NY|TX|WA|CO|MA|FL|GA|IL|AZ|OR))\b",
            r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?,\s*(?:California|New York|Texas))\b",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()

        return None

    def extract_from_html(self, html_text: str, url: Optional[str] = None) -> LocationResult:
        """Extract location from HTML page content."""
        # Look for location in about sections, footer, contact info
        location_indicators = [
            r"(?:location|based|located|address|headquarters?)[\s:]+([^\n<]{5,50})",
            r"(?:based\s+in|located\s+in)\s+([^\n<]{5,50})",
        ]

        for pattern in location_indicators:
            match = re.search(pattern, html_text, re.IGNORECASE)
            if match:
                location_text = match.group(1).strip()
                return self.extract(location_field=location_text, evidence_url=url)

        # Fall back to classifying the whole text
        return self.extract(about_text=html_text, evidence_url=url)
