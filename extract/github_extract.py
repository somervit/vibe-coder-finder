"""GitHub-specific data extraction."""

import re
from typing import Dict, List, Optional
from utils.text import extract_evidence_lines, truncate_text


class GitHubExtractor:
    """Extracts structured data from GitHub API responses."""

    def extract_user(self, user_data: Dict) -> Dict:
        """
        Extract candidate info from GitHub user API response.

        Args:
            user_data: Response from /users/{username}

        Returns:
            Structured user data
        """
        return {
            "username": user_data.get("login"),
            "name": user_data.get("name"),
            "bio": user_data.get("bio"),
            "company": user_data.get("company"),
            "location": user_data.get("location"),
            "email": user_data.get("email"),  # Only if public
            "blog": user_data.get("blog"),
            "twitter_username": user_data.get("twitter_username"),
            "public_repos": user_data.get("public_repos", 0),
            "followers": user_data.get("followers", 0),
            "html_url": user_data.get("html_url"),
            "avatar_url": user_data.get("avatar_url"),
            "created_at": user_data.get("created_at"),
            "updated_at": user_data.get("updated_at"),
        }

    def extract_repo(self, repo_data: Dict) -> Dict:
        """
        Extract relevant info from GitHub repo API response.

        Args:
            repo_data: Response from /repos/{owner}/{repo} or search result

        Returns:
            Structured repo data
        """
        owner = repo_data.get("owner", {})

        return {
            "name": repo_data.get("name"),
            "full_name": repo_data.get("full_name"),
            "description": repo_data.get("description"),
            "html_url": repo_data.get("html_url"),
            "homepage": repo_data.get("homepage"),
            "language": repo_data.get("language"),
            "stars": repo_data.get("stargazers_count", 0),
            "forks": repo_data.get("forks_count", 0),
            "open_issues": repo_data.get("open_issues_count", 0),
            "topics": repo_data.get("topics", []),
            "created_at": repo_data.get("created_at"),
            "updated_at": repo_data.get("updated_at"),
            "pushed_at": repo_data.get("pushed_at"),
            "owner_username": owner.get("login"),
            "owner_url": owner.get("html_url"),
            "is_fork": repo_data.get("fork", False),
        }

    def extract_readme(self, readme_content: str, max_length: int = 3000) -> Dict:
        """
        Extract evidence from README content.

        Args:
            readme_content: Decoded README text
            max_length: Max chars to keep

        Returns:
            {
                "content": str (truncated),
                "evidence_snippets": [str],
                "has_demo_link": bool,
                "demo_links": [str],
            }
        """
        content = truncate_text(readme_content, max_length)
        evidence = extract_evidence_lines(readme_content)

        # Find demo/live links
        demo_patterns = [
            r"(?:demo|live|try it|deployed?)[\s:]*(?:at\s*)?(https?://[^\s\)]+)",
            r"\[(?:demo|live|try it)[^\]]*\]\((https?://[^\)]+)\)",
            r"(https?://[^\s]+(?:vercel|netlify|railway|render|herokuapp)[^\s]*)",
        ]

        demo_links = []
        for pattern in demo_patterns:
            matches = re.findall(pattern, readme_content, re.IGNORECASE)
            demo_links.extend(matches)

        return {
            "content": content,
            "evidence_snippets": evidence,
            "has_demo_link": len(demo_links) > 0,
            "demo_links": list(set(demo_links))[:5],  # Limit to 5
        }

    def extract_search_result(self, item: Dict, result_type: str) -> Dict:
        """
        Extract info from GitHub search API result item.

        Args:
            item: Single item from search results
            result_type: "repositories" or "code"

        Returns:
            Structured search result
        """
        if result_type == "repositories":
            return self.extract_repo(item)

        elif result_type == "code":
            repo = item.get("repository", {})
            return {
                "name": item.get("name"),
                "path": item.get("path"),
                "html_url": item.get("html_url"),
                "repo_name": repo.get("full_name"),
                "repo_url": repo.get("html_url"),
                "repo_description": repo.get("description"),
                "owner_username": repo.get("owner", {}).get("login"),
            }

        return item

    def score_repo_relevance(self, repo: Dict) -> float:
        """
        Score a repo's relevance for vibe coding signals.

        Returns score 0-1.
        """
        score = 0.0
        max_score = 0.0

        # Recent activity (pushed in last 6 months)
        max_score += 0.2
        pushed_at = repo.get("pushed_at", "")
        if pushed_at:
            from datetime import datetime, timezone
            try:
                pushed = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                days_ago = (now - pushed).days
                if days_ago < 30:
                    score += 0.2
                elif days_ago < 90:
                    score += 0.15
                elif days_ago < 180:
                    score += 0.1
            except:
                pass

        # Stars
        max_score += 0.2
        stars = repo.get("stars", 0)
        if stars >= 100:
            score += 0.2
        elif stars >= 50:
            score += 0.15
        elif stars >= 10:
            score += 0.1
        elif stars >= 1:
            score += 0.05

        # Relevant topics
        max_score += 0.3
        topics = [t.lower() for t in repo.get("topics", [])]
        relevant_topics = {
            "ai", "llm", "gpt", "openai", "anthropic", "langchain",
            "cursor", "v0", "replit", "prototype", "mvp", "demo",
            "hackathon", "fintech", "payments", "agent", "chatbot",
        }
        topic_matches = len(set(topics) & relevant_topics)
        if topic_matches >= 3:
            score += 0.3
        elif topic_matches >= 2:
            score += 0.2
        elif topic_matches >= 1:
            score += 0.1

        # Description keywords
        max_score += 0.2
        desc = (repo.get("description") or "").lower()
        vibe_keywords = ["prototype", "mvp", "demo", "ai", "llm", "agent", "cursor", "v0"]
        desc_matches = sum(1 for k in vibe_keywords if k in desc)
        if desc_matches >= 2:
            score += 0.2
        elif desc_matches >= 1:
            score += 0.1

        # Has homepage (deployed)
        max_score += 0.1
        if repo.get("homepage"):
            score += 0.1

        return score / max_score if max_score > 0 else 0
