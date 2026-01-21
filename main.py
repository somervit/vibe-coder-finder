#!/usr/bin/env python3
"""
Vibe Coder Finder - Discover builders who ship with modern AI tooling.

Usage:
    python main.py search --limit 300
    python main.py score --in results/raw.json --out results/scored.csv
"""

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import List

from utils.logging import setup_logger, get_logger
from utils.dedupe import Candidate, CandidateDeduper
from sources.github import GitHubSource
from sources.hn import HackerNewsSource
from sources.brave_search import BraveSearchSource
from sources.devto import DevToSource
from sources.producthunt import ProductHuntSource
from sources.twitter import TwitterSource
from sources.reddit import RedditSource
from sources.yc import YCSource
from score.rubric import CandidateScorer
from score.llm_scorer import LLMScorer


def setup_results_dir():
    """Ensure results directory exists."""
    results_dir = Path("results")
    results_dir.mkdir(exist_ok=True)
    return results_dir


def run_search(args):
    """Run the search pipeline across all sources."""
    setup_logger(level=args.log_level)
    logger = get_logger(source="main")

    logger.info(f"Starting search with limit={args.limit}")

    # Initialize components
    deduper = CandidateDeduper()
    results_dir = setup_results_dir()

    # Determine which sources to use
    sources_to_run = []

    if args.sources == "all" or "github" in args.sources:
        if os.environ.get("GITHUB_TOKEN"):
            sources_to_run.append(("github", GitHubSource()))
        else:
            logger.warning("Skipping GitHub: GITHUB_TOKEN not set")

    if args.sources == "all" or "hn" in args.sources:
        sources_to_run.append(("hn", HackerNewsSource(fetch_personal_sites=not args.no_fetch)))

    if args.sources == "all" or "brave" in args.sources:
        if os.environ.get("BRAVE_API_KEY"):
            sources_to_run.append(("brave", BraveSearchSource(fetch_pages=not args.no_fetch)))
        else:
            logger.warning("Skipping Brave: BRAVE_API_KEY not set")

    if args.sources == "all" or "devto" in args.sources:
        sources_to_run.append(("devto", DevToSource()))

    if args.sources == "all" or "producthunt" in args.sources:
        sources_to_run.append(("producthunt", ProductHuntSource(fetch_maker_pages=not args.no_fetch)))

    if args.sources == "all" or "twitter" in args.sources:
        if os.environ.get("TWITTER_API_KEY") and os.environ.get("TWITTER_API_SECRET"):
            sources_to_run.append(("twitter", TwitterSource()))
        else:
            logger.warning("Skipping Twitter: TWITTER_API_KEY or TWITTER_API_SECRET not set")

    if args.sources == "all" or "reddit" in args.sources:
        sources_to_run.append(("reddit", RedditSource()))

    if args.sources == "all" or "yc" in args.sources:
        sources_to_run.append(("yc", YCSource(use_browser=not args.no_fetch)))

    if not sources_to_run:
        logger.error("No sources available. Set GITHUB_TOKEN and/or BRAVE_API_KEY.")
        sys.exit(1)

    # Calculate per-source limits
    per_source_limit = args.limit // len(sources_to_run)

    # Crawl each source
    for source_name, source in sources_to_run:
        logger.info(f"Crawling {source_name}...")

        try:
            for candidate in source.crawl(limit=per_source_limit):
                deduper.add(candidate)
        except Exception as e:
            logger.error(f"Error crawling {source_name}: {e}")
            if args.debug:
                raise

    # Get deduplicated candidates
    candidates = deduper.get_all()
    logger.info(f"Found {len(candidates)} unique candidates after deduplication")

    # Save raw results
    raw_path = results_dir / "raw.json"
    save_json(candidates, raw_path)
    logger.info(f"Saved raw results to {raw_path}")

    # Optionally score immediately
    if args.score:
        logger.info("Scoring candidates...")
        scorer = CandidateScorer()
        scored = scorer.score_all(candidates)

        # Optionally enhance with LLM pitches
        if args.llm:
            llm_scorer = LLMScorer(provider=args.llm_provider)
            if llm_scorer.is_available():
                scored = llm_scorer.enhance_candidates(scored, max_candidates=args.llm_limit)
            else:
                logger.warning(f"LLM scoring requested but {args.llm_provider.upper()}_API_KEY not set")

        # Save scored results
        scored_json_path = results_dir / "scored.json"
        scored_csv_path = results_dir / "scored.csv"

        save_json(scored, scored_json_path)
        save_csv(scored, scored_csv_path)

        logger.info(f"Saved scored results to {scored_json_path} and {scored_csv_path}")
        logger.info(f"Top 5 candidates:")
        for c in scored[:5]:
            logger.info(f"  {c.total_score:.1f}: {c.name or c.github_username or c.hn_username}")

    logger.info("Search complete!")


def run_score(args):
    """Score candidates from a raw JSON file."""
    setup_logger(level=args.log_level)
    logger = get_logger(source="main")

    # Load input
    input_path = Path(args.input)
    if not input_path.exists():
        logger.error(f"Input file not found: {input_path}")
        sys.exit(1)

    logger.info(f"Loading candidates from {input_path}")
    candidates = load_json(input_path)
    logger.info(f"Loaded {len(candidates)} candidates")

    # Score
    scorer = CandidateScorer()
    scored = scorer.score_all(candidates)
    logger.info(f"Scored {len(scored)} candidates (excluded {len(candidates) - len(scored)} non-US)")

    # Save output
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_path.suffix == ".csv":
        save_csv(scored, output_path)
    else:
        save_json(scored, output_path)

    logger.info(f"Saved scored results to {output_path}")

    # Also save JSON version if CSV
    if output_path.suffix == ".csv":
        json_path = output_path.with_suffix(".json")
        save_json(scored, json_path)
        logger.info(f"Also saved JSON to {json_path}")

    # Print top candidates
    logger.info(f"\nTop 10 candidates:")
    for i, c in enumerate(scored[:10], 1):
        name = c.name or c.github_username or c.hn_username or "Unknown"
        logger.info(f"  {i}. {c.total_score:.1f} - {name} ({c.metro_bucket})")
        if c.recruiter_pitch:
            logger.info(f"     {c.recruiter_pitch}")


def save_json(candidates: List[Candidate], path: Path):
    """Save candidates to JSON file."""
    data = [c.to_dict() if hasattr(c, 'to_dict') else c for c in candidates]
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def load_json(path: Path) -> List[Candidate]:
    """Load candidates from JSON file."""
    with open(path) as f:
        data = json.load(f)
    return [Candidate.from_dict(d) for d in data]


def save_csv(candidates: List[Candidate], path: Path):
    """Save candidates to CSV file."""
    if not candidates:
        return

    # Define CSV columns
    columns = [
        "rank",
        "total_score",
        "name",
        "primary_handle",
        "email",
        "linkedin_url",
        "twitter_handle",
        "github_username",
        "hn_username",
        "reddit_username",
        "location_raw",
        "metro_bucket",
        "location_confidence",
        "github_url",
        "website",
        "demo_urls",
        "sources",
        "shipping_velocity",
        "tooling_signals",
        "founder_fit",
        "fintech_relevance",
        "communication",
        "location_multiplier",
        "recruiter_pitch",
        "evidence_snippets",
    ]

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()

        for rank, c in enumerate(candidates, 1):
            row = {
                "rank": rank,
                "total_score": c.total_score,
                "name": c.name or "",
                "primary_handle": c.github_username or c.hn_username or "",
                "email": c.email or "",
                "linkedin_url": c.linkedin_url or "",
                "twitter_handle": c.twitter_handle or "",
                "github_username": c.github_username or "",
                "hn_username": c.hn_username or "",
                "reddit_username": c.reddit_username or "",
                "location_raw": c.location_raw or "",
                "metro_bucket": c.metro_bucket,
                "location_confidence": f"{c.location_confidence:.2f}",
                "github_url": c.github_url or "",
                "website": c.website or "",
                "demo_urls": "; ".join(c.demo_urls[:3]) if c.demo_urls else "",
                "sources": ", ".join(c.sources) if c.sources else "",
                "shipping_velocity": c.scores.get("shipping_velocity", 0),
                "tooling_signals": c.scores.get("tooling_signals", 0),
                "founder_fit": c.scores.get("founder_fit", 0),
                "fintech_relevance": c.scores.get("fintech_relevance", 0),
                "communication": c.scores.get("communication", 0),
                "location_multiplier": c.scores.get("location_multiplier", 1.0),
                "recruiter_pitch": c.recruiter_pitch or "",
                "evidence_snippets": format_evidence(c.evidence_snippets),
            }
            writer.writerow(row)


def format_evidence(evidence: List) -> str:
    """Format evidence snippets for CSV."""
    if not evidence:
        return ""

    lines = []
    for e in evidence[:5]:
        if isinstance(e, dict):
            text = e.get("text", "")[:100]
            lines.append(text)
        else:
            lines.append(str(e)[:100])

    return " | ".join(lines)




def main():
    parser = argparse.ArgumentParser(
        description="Vibe Coder Finder - Discover builders who ship with modern AI tooling",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Search all sources for 300 candidates
    python main.py search --limit 300

    # Search only GitHub and HN
    python main.py search --sources github,hn --limit 100

    # Search and score in one command
    python main.py search --limit 200 --score

    # Score existing raw results
    python main.py score --in results/raw.json --out results/scored.csv

Environment variables:
    GITHUB_TOKEN    - GitHub personal access token (required for GitHub source)
    BRAVE_API_KEY   - Brave Search API key (required for Brave source)
        """,
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode",
    )
    parser.add_argument(
        "--log-level",
        type=int,
        default=20,  # INFO
        help="Logging level (10=DEBUG, 20=INFO, 30=WARNING)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Search command
    search_parser = subparsers.add_parser("search", help="Search for candidates")
    search_parser.add_argument(
        "--limit",
        type=int,
        default=300,
        help="Maximum number of candidates to find (default: 300)",
    )
    search_parser.add_argument(
        "--sources",
        type=str,
        default="all",
        help="Comma-separated sources to use: github,hn,brave,devto,producthunt,twitter,reddit,yc (default: all)",
    )
    search_parser.add_argument(
        "--score",
        action="store_true",
        help="Also score candidates after searching",
    )
    search_parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="Don't fetch linked pages (faster but less data)",
    )
    search_parser.add_argument(
        "--llm",
        action="store_true",
        help="Use LLM to generate better recruiter pitches (requires ANTHROPIC_API_KEY or OPENAI_API_KEY)",
    )
    search_parser.add_argument(
        "--llm-provider",
        type=str,
        default="anthropic",
        choices=["anthropic", "openai"],
        help="LLM provider to use (default: anthropic)",
    )
    search_parser.add_argument(
        "--llm-limit",
        type=int,
        default=50,
        help="Maximum candidates to enhance with LLM (default: 50)",
    )

    # Score command
    score_parser = subparsers.add_parser("score", help="Score existing candidates")
    score_parser.add_argument(
        "--in",
        dest="input",
        type=str,
        default="results/raw.json",
        help="Input JSON file with raw candidates",
    )
    score_parser.add_argument(
        "--out",
        dest="output",
        type=str,
        default="results/scored.csv",
        help="Output file (CSV or JSON based on extension)",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "search":
        run_search(args)
    elif args.command == "score":
        run_score(args)


if __name__ == "__main__":
    main()
