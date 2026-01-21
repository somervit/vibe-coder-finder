# Vibe Coder Finder

A pipeline to discover experienced Product Managers and startup founders who demonstrate "vibe coding" - rapidly shipping prototypes with modern AI tooling like Cursor, v0, Replit, and LLM APIs.

## What It Does

1. **Discovers candidates** from multiple sources (GitHub, Hacker News, Brave Search, Dev.to, ProductHunt, Twitter/X, Reddit, YC Directory)
2. **Extracts structured profiles** with handles, links, bio, email, LinkedIn, and evidence snippets
3. **Deduplicates across sources** linking GitHub ↔ HN ↔ Twitter ↔ LinkedIn accounts
4. **Scores candidates** using a transparent rubric for vibe coding signals, founder fit, and location
5. **Optional LLM enhancement** for better recruiter pitches using Claude or GPT
6. **Outputs ranked results** as CSV and JSON

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Set API keys
export GITHUB_TOKEN="your-github-token"
export BRAVE_API_KEY="your-brave-api-key"

# Run full search pipeline
python main.py search --limit 300 --score

# Results in results/scored.csv and results/scored.json
```

## Requirements

- Python 3.9+
- GitHub Personal Access Token (for GitHub source)
- Brave Search API Key (for Brave source)
- Anthropic or OpenAI API Key (optional, for LLM-powered pitches)

## Installation

```bash
cd vibe-coder-finder
pip install -r requirements.txt
```

## Usage

### Search for Candidates

```bash
# Search all sources for 300 candidates
python main.py search --limit 300

# Search specific sources
python main.py search --sources github,hn,devto --limit 100

# Search and score in one command
python main.py search --limit 200 --score

# Faster search (skip fetching linked pages)
python main.py search --limit 300 --no-fetch

# With LLM-powered recruiter pitches (requires ANTHROPIC_API_KEY)
python main.py search --limit 200 --score --llm

# Use OpenAI instead of Anthropic for LLM pitches
python main.py search --limit 200 --score --llm --llm-provider openai

# Limit LLM enhancement to top N candidates (cost control)
python main.py search --limit 200 --score --llm --llm-limit 25

# Debug mode with verbose logging
python main.py search --limit 100 --debug --log-level 10
```

### YC Directory Scraping

```bash
# One-time setup: install Playwright browser
pip install playwright && playwright install chromium

# Scrape all YC founders from Inactive/Acquired companies (~1,700 companies)
python main.py search --sources yc --limit 5000 --score

# Faster YC scrape (skip browser, use API data only - less founder info)
python main.py search --sources yc --limit 5000 --score --no-fetch
```

### Score Existing Results

```bash
# Score candidates from a previous search
python main.py score --in results/raw.json --out results/scored.csv
```

## Output Files

| File | Description |
|------|-------------|
| `results/raw.json` | All discovered candidates with merged evidence |
| `results/scored.json` | Scored candidates (excludes non-US) |
| `results/scored.csv` | Same data in spreadsheet format |

## Output Fields

The CSV output includes:
- `rank`, `total_score` - Ranking and final score
- `name`, `primary_handle` - Identity info
- `email`, `linkedin_url`, `twitter_handle` - Contact info (discovered from profiles and commits)
- `github_username`, `hn_username` - Platform handles
- `location_raw`, `metro_bucket`, `location_confidence` - Location data
- `github_url`, `website`, `demo_urls` - Links
- `sources` - Where they were found (github, hn, brave, devto, producthunt, twitter, reddit, yc)
- Subscore breakdown: `shipping_velocity`, `tooling_signals`, `founder_fit`, `fintech_relevance`, `communication`
- `recruiter_pitch` - Generated pitch (enhanced by LLM if --llm flag used)
- `evidence_snippets` - Key quotes and signals

## Scoring Rubric

Each candidate is scored 0-100 with transparent subscores:

| Category | Max Points | Signals |
|----------|------------|---------|
| Shipping Velocity | 30 | "shipped", "prototype", "MVP", demo URLs, recent activity |
| Tooling Signals | 20 | Cursor, v0, Replit, LangChain, OpenAI, AI agents |
| Founder/PM Fit | 25 | YC, Antler, founder, PM titles, public presence |
| Fintech Relevance | 15 | Fintech, payments, banking keywords |
| Communication | 10 | Bio quality, name available, evidence depth |

### Location Multiplier

- **SF Bay Area**: 1.10x (capped at 100)
- **Other US**: 1.00x
- **Unknown**: 0.80x
- **Non-US**: Excluded from results

## Sources

### GitHub (requires GITHUB_TOKEN)
- Searches repos for vibe coding keywords (excludes tips/guides repos)
- Extracts owner profile, bio, location
- Discovers email from public commit history
- Extracts LinkedIn from bio
- Assesses shipping behavior across user's portfolio
- Parses README for evidence and demo links

### Hacker News
- Searches "Show HN" posts via Algolia API
- Fetches linked personal sites
- Extracts author profiles, email, social links

### Brave Search (requires BRAVE_API_KEY)
- Searches open web for builder content
- Fetches and parses discovered pages
- Extracts author info, contact details, and evidence

### Dev.to
- Searches articles by AI/coding tags
- Extracts author profiles with GitHub/Twitter links
- Scores article relevance for vibe coding signals

### ProductHunt
- Searches AI and developer tool topics
- Extracts maker information from product pages
- Discovers builder social profiles

### Twitter/X (requires TWITTER_API_KEY and TWITTER_API_SECRET)
- Searches for tweets about shipping, prototypes, AI tools
- Extracts user profiles with bio and location
- Discovers GitHub usernames from bios
- Links with other sources via handle matching

### Reddit
- Searches builder communities: r/SideProject, r/startups, r/indiehackers, r/Entrepreneur
- Extracts GitHub, Twitter, LinkedIn from post content
- Discovers location mentions in posts
- No API key required (uses public JSON API)

### YC Directory (requires playwright)
- Scrapes YC's company directory for Inactive and Acquired companies (~1,700 companies)
- Uses Playwright browser automation to extract founder LinkedIn profiles
- Captures founder names, LinkedIn URLs, and company context
- No API key required (uses public yc-oss API + YC website)
- Setup: `pip install playwright && playwright install chromium`

## Project Structure

```
vibe-coder-finder/
├── main.py              # CLI entrypoint
├── requirements.txt     # Python dependencies
├── sources/
│   ├── github.py        # GitHub API crawler
│   ├── hn.py            # Hacker News crawler
│   ├── brave_search.py  # Brave Search crawler
│   ├── devto.py         # Dev.to API crawler
│   ├── producthunt.py   # ProductHunt crawler
│   ├── twitter.py       # Twitter/X API crawler
│   ├── reddit.py        # Reddit API crawler
│   └── yc.py            # YC Directory scraper
├── extract/
│   ├── html_extract.py  # HTML parsing (email, LinkedIn, location)
│   ├── github_extract.py# GitHub-specific extraction
│   └── location_extract.py # Location classification
├── score/
│   ├── rubric.py        # Scoring logic
│   └── llm_scorer.py    # LLM-powered pitch generation
├── utils/
│   ├── rate_limit.py    # Rate limiting + backoff
│   ├── dedupe.py        # Cross-source deduplication
│   ├── logging.py       # Structured logging
│   └── text.py          # Text processing
└── results/             # Output directory
```

## Cross-Source Deduplication

The deduper links candidate profiles across sources using:
- GitHub username
- HN username
- Reddit username
- Twitter handle
- LinkedIn URL
- Email address
- Personal website domain
- Name similarity + common identifiers

When the same person is found on multiple sources, their data is merged and their score benefits from the stronger signal.

## LLM-Powered Pitches

With `--llm` flag, the top candidates get enhanced recruiter pitches generated by Claude or GPT that:
- Analyze all available evidence
- Highlight specific shipping and tooling signals
- Provide confidence level and key signals
- Flag any concerns
- Optionally adjust scores based on evidence quality

## Ethics & Constraints

- Only crawls publicly available pages
- Respects rate limits with exponential backoff
- Stores only necessary public info
- Does not circumvent paywalls or auth
- Location filtering is opt-out for unknown, not strict exclusion
- Email only extracted from public sources (commits, websites)

## Example Output

```csv
rank,total_score,name,primary_handle,email,linkedin_url,metro_bucket,recruiter_pitch
1,78.2,Jane Smith,jsmith,jane@example.com,https://linkedin.com/in/jsmith,SF_BAY_AREA,"Jane Smith shows strong founder/PM signals and is based in the SF Bay Area. Key signals: YC background, uses Cursor/v0, proven shipping track record."
2,72.5,Alex Chen,alexc,,https://linkedin.com/in/alexc,OTHER_US,"Alex Chen is proficient with modern AI tooling and is US-based. Key signals: fintech experience, uses LangChain."
```

## API Keys

### GitHub Token
1. Go to GitHub Settings > Developer Settings > Personal Access Tokens
2. Generate a token with `public_repo` scope
3. Export: `export GITHUB_TOKEN="ghp_..."`

### Brave API Key
1. Sign up at https://brave.com/search/api/
2. Get your API key from the dashboard
3. Export: `export BRAVE_API_KEY="BSA..."`

### Twitter/X API Keys (optional)
1. Go to https://developer.twitter.com/
2. Create a project and app
3. Get your API Key and API Secret from "Keys and tokens"
4. Export both:
   ```bash
   export TWITTER_API_KEY="your-api-key"
   export TWITTER_API_SECRET="your-api-secret"
   ```

### Anthropic API Key (optional, for LLM pitches)
1. Sign up at https://console.anthropic.com/
2. Get your API key
3. Export: `export ANTHROPIC_API_KEY="sk-ant-..."`

### OpenAI API Key (alternative for LLM pitches)
1. Sign up at https://platform.openai.com/
2. Get your API key
3. Export: `export OPENAI_API_KEY="sk-..."`

## Known Limitations

### Brave Search Quality
The Brave Search source can produce false positives by matching content **about** vibe coding rather than content **by** vibe coders. For example:
- Blog posts titled "How to Build with Cursor AI" written by content marketers
- Tutorial articles mentioning AI tools without the author being a builder
- News coverage of startups that mentions founders by name

**Mitigation strategies:**
- Use `--sources github,hn,devto,producthunt,twitter,reddit` to exclude Brave for higher precision
- Rely on the scoring rubric to down-rank weak candidates (content writers typically lack demo URLs, GitHub activity, and multi-source presence)
- Enable `--llm` flag to have AI review and flag concerns for top candidates

### Other Limitations
- **Location inference**: Based on self-reported data in bios/profiles; may reflect office location rather than residence
- **Keyword-based scoring**: Scores keyword presence without semantic context; "shipped" in different contexts scores equally
- **Name deduplication**: Fuzzy name matching (0.85 threshold) may create false positives for common names
- **Rate limits**: Respectful rate limiting means full crawls take time; consider running overnight for large limits
