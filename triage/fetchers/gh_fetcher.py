"""Supplementary GitHub CLI fetcher — catches notifications and review requests."""

import json
import subprocess
import sys


def _run_gh(args: list[str]) -> list[dict]:
    """Run a gh CLI command and return parsed JSON."""
    cmd = ["gh"] + args + ["--json"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return []
        return json.loads(result.stdout) if result.stdout.strip() else []
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return []


def fetch_review_requests(github_username: str) -> list[dict]:
    """Fetch PRs where the user is requested as reviewer across all repos."""
    try:
        result = subprocess.run(
            ["gh", "search", "prs", "--review-requested", f"@{github_username}",
             "--state", "open", "--json", "number,title,repository,url,updatedAt,isDraft"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            print(f"  [gh] review-requested search failed: {result.stderr[:200]}", file=sys.stderr)
            return []
        return json.loads(result.stdout) if result.stdout.strip() else []
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        print(f"  [gh] review-requested error: {e}", file=sys.stderr)
        return []


def fetch_mentions(github_username: str) -> list[dict]:
    """Fetch PRs/issues where the user is mentioned."""
    try:
        result = subprocess.run(
            ["gh", "search", "issues", "--mentions", f"@{github_username}",
             "--state", "open", "--json", "number,title,repository,url,updatedAt,type"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return []
        return json.loads(result.stdout) if result.stdout.strip() else []
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return []


def fetch_gh_supplements(config: dict) -> dict:
    """
    Fetch supplementary data via gh CLI. Returns dict with extra items
    keyed by source type.
    """
    github_username = config["user"]["github_username"]
    tracked_repos = set()
    for repo_cfg in config["github"]["repos"]:
        tracked_repos.add(f"{repo_cfg['owner']}/{repo_cfg['repo']}")

    # PRs requesting Krishna's review (across ALL repos, not just tracked ones)
    print("  Checking gh for review requests...")
    review_prs = fetch_review_requests(github_username)
    extra_reviews = []
    for pr in review_prs:
        repo_name = pr.get("repository", {}).get("nameWithOwner", "")
        if repo_name in tracked_repos:
            continue  # already fetched by REST API
        extra_reviews.append({
            "type": "pr",
            "repo": repo_name,
            "number": pr.get("number"),
            "title": pr.get("title", ""),
            "link": pr.get("url", ""),
            "is_recent": True,
            "draft": pr.get("isDraft", False),
            "source": "gh_review_request",
        })

    if extra_reviews:
        print(f"    Found {len(extra_reviews)} review requests in untracked repos")

    # Mentions (catch things REST API might miss)
    print("  Checking gh for mentions...")
    mentions = fetch_mentions(github_username)
    extra_mentions = []
    for item in mentions:
        repo_name = item.get("repository", {}).get("nameWithOwner", "")
        if repo_name in tracked_repos:
            continue
        extra_mentions.append({
            "type": item.get("type", "issue").lower(),
            "repo": repo_name,
            "number": item.get("number"),
            "title": item.get("title", ""),
            "link": item.get("url", ""),
            "is_recent": True,
            "source": "gh_mention",
        })

    if extra_mentions:
        print(f"    Found {len(extra_mentions)} mentions in untracked repos")

    return {
        "review_requests": extra_reviews,
        "mentions": extra_mentions,
    }
