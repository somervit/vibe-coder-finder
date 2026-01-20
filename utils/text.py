"""Text processing utilities."""

import re
from typing import List, Set, Tuple

# Keywords for vibe coding signals
VIBE_CODING_KEYWORDS = {
    # Tools
    "cursor", "cursor ai", "cursor.sh",
    "v0", "v0.dev", "vercel v0",
    "replit", "repl.it",
    "copilot", "github copilot",
    "supabase",
    "next.js", "nextjs", "next js",
    "openai", "gpt-4", "gpt4", "chatgpt",
    "anthropic", "claude",
    "langchain", "langgraph",
    "llamaindex", "llama index",
    "huggingface", "hugging face",
    "streamlit",
    "gradio",

    # Shipping signals
    "shipped", "ship it", "shipping",
    "prototype", "prototyped", "prototyping",
    "mvp", "minimum viable",
    "demo", "demoed",
    "hack", "hacked", "hackathon",
    "built with", "building with",
    "weekend project", "side project",
    "launched", "launch",
    "prompt-to-app", "prompt to app",
    "ai agent", "ai agents", "agent",
    "llm app", "llm application",
    "built in a weekend", "shipped in a weekend",
    "24 hours", "48 hours",
}

# Founder/incubator signals
FOUNDER_KEYWORDS = {
    "founder", "co-founder", "cofounder",
    "yc", "y combinator", "ycombinator",
    "antler",
    "entrepreneur first", "ef",
    "techstars",
    "500 startups", "500startups",
    "on deck",
    "startup", "startups",
    "incubator", "accelerator",
    "seed round", "series a",
    "bootstrapped", "bootstrap",
    "product manager", "pm", "product lead",
    "head of product",
}

# Fintech signals
FINTECH_KEYWORDS = {
    "fintech", "fin-tech",
    "payments", "payment",
    "banking", "neobank",
    "crypto", "cryptocurrency", "web3",
    "defi", "decentralized finance",
    "trading", "trader",
    "investment", "investing",
    "sofi", "stripe", "plaid", "square",
    "financial", "finance",
    "credit", "lending", "loan",
    "insurance", "insurtech",
}


def normalize_text(text: str) -> str:
    """Normalize text for comparison."""
    if not text:
        return ""
    # Lowercase and normalize whitespace
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def truncate_text(text: str, max_length: int = 500, suffix: str = "...") -> str:
    """Truncate text to max_length, adding suffix if truncated."""
    if not text or len(text) <= max_length:
        return text or ""
    return text[: max_length - len(suffix)] + suffix


def extract_keywords(text: str, keyword_set: Set[str] = None) -> List[Tuple[str, str]]:
    """
    Extract matching keywords from text.

    Returns list of (keyword, context) tuples where context is
    the surrounding text snippet.
    """
    if keyword_set is None:
        keyword_set = VIBE_CODING_KEYWORDS | FOUNDER_KEYWORDS | FINTECH_KEYWORDS

    if not text:
        return []

    normalized = normalize_text(text)
    matches = []

    for keyword in keyword_set:
        # Use word boundaries for single words, looser matching for phrases
        if " " in keyword:
            pattern = re.escape(keyword)
        else:
            pattern = r"\b" + re.escape(keyword) + r"\b"

        for match in re.finditer(pattern, normalized, re.IGNORECASE):
            start = max(0, match.start() - 50)
            end = min(len(normalized), match.end() + 50)
            context = normalized[start:end].strip()
            if start > 0:
                context = "..." + context
            if end < len(normalized):
                context = context + "..."
            matches.append((keyword, context))

    # Dedupe by keyword
    seen = set()
    unique_matches = []
    for keyword, context in matches:
        if keyword not in seen:
            seen.add(keyword)
            unique_matches.append((keyword, context))

    return unique_matches


def extract_evidence_lines(text: str, max_lines: int = 8) -> List[str]:
    """Extract lines containing evidence keywords."""
    if not text:
        return []

    all_keywords = VIBE_CODING_KEYWORDS | FOUNDER_KEYWORDS | FINTECH_KEYWORDS
    lines = text.split("\n")
    evidence = []

    for line in lines:
        line = line.strip()
        if not line or len(line) < 10:
            continue

        normalized = normalize_text(line)
        for keyword in all_keywords:
            if keyword in normalized:
                evidence.append(truncate_text(line, 200))
                break

        if len(evidence) >= max_lines:
            break

    return evidence


def clean_html_text(text: str) -> str:
    """Clean text extracted from HTML."""
    if not text:
        return ""

    # Remove excessive whitespace
    text = re.sub(r"\s+", " ", text)
    # Remove common HTML artifacts
    text = re.sub(r"<!--.*?-->", "", text)
    text = text.strip()
    return text


def extract_urls(text: str) -> List[str]:
    """Extract URLs from text."""
    if not text:
        return []

    url_pattern = r"https?://[^\s<>\"')\]]+[^\s<>\"')\].,;:!?]"
    urls = re.findall(url_pattern, text)
    return list(set(urls))


def is_likely_personal_site(url: str) -> bool:
    """Check if URL is likely a personal site or blog."""
    if not url:
        return False

    personal_indicators = [
        ".me/", ".io/", ".dev/",
        "blog.", "about.",
        "/about", "/blog",
        "substack.com", "medium.com/@",
        "github.io",
    ]

    url_lower = url.lower()
    return any(ind in url_lower for ind in personal_indicators)
