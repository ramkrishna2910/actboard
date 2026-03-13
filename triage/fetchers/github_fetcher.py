"""GitHub REST API client — fetches issues and PRs with recent activity."""

import sys
import time
from datetime import datetime, timedelta, timezone

import requests

GITHUB_API = "https://api.github.com"


def _check_rate_limit(resp: requests.Response):
    """Exit early if rate limit is exhausted."""
    remaining = resp.headers.get("X-RateLimit-Remaining")
    if remaining is not None and int(remaining) == 0:
        reset_ts = int(resp.headers.get("X-RateLimit-Reset", 0))
        reset_dt = datetime.fromtimestamp(reset_ts, tz=timezone.utc)
        print(
            f"Error: GitHub rate limit exhausted. Resets at {reset_dt.isoformat()}",
            file=sys.stderr,
        )
        sys.exit(1)


def _paginate(session: requests.Session, url: str, params: dict | None = None) -> list[dict]:
    """Paginate GitHub API results via Link header."""
    results = []
    while url:
        resp = session.get(url, params=params)
        _check_rate_limit(resp)
        resp.raise_for_status()
        results.extend(resp.json())
        url = resp.links.get("next", {}).get("url")
        params = None  # params are already embedded in the next URL
    return results


def _fetch_comments(session: requests.Session, owner: str, repo: str, number: int) -> list[dict]:
    """Fetch issue/PR comments."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/issues/{number}/comments"
    return _paginate(session, url)


def _fetch_reviews(session: requests.Session, owner: str, repo: str, number: int) -> list[dict]:
    """Fetch PR reviews."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}/reviews"
    return _paginate(session, url)


def _fetch_review_comments(session: requests.Session, owner: str, repo: str, number: int) -> list[dict]:
    """Fetch PR review comments (inline code comments)."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}/comments"
    return _paginate(session, url)


def fetch_github(config: dict) -> list[dict]:
    """
    Main entry point. Returns a list of item dicts matching the spec schema.
    """
    token = config.get("env", {}).get("GITHUB_TOKEN", "")
    if not token:
        print("Error: GITHUB_TOKEN not set.", file=sys.stderr)
        sys.exit(1)

    github_username = config["user"]["github_username"]
    lookback = config["github"].get("lookback_hours", 24)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback)
    include_drafts = config["github"].get("include_draft_prs", True)
    include_reviews = config["github"].get("include_code_reviews", True)

    session = requests.Session()
    session.headers.update({
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    })

    results = []

    for repo_cfg in config["github"]["repos"]:
        owner = repo_cfg["owner"]
        repo = repo_cfg["repo"]
        repo_full = f"{owner}/{repo}"

        # Fetch open issues (includes PRs)
        print(f"  Scanning {repo_full}...")
        url = f"{GITHUB_API}/repos/{owner}/{repo}/issues"
        params = {"state": "open", "sort": "updated", "direction": "desc", "per_page": 100}
        items = _paginate(session, url, params)

        issue_count = 0
        pr_count = 0
        for item in items:
            updated_at = datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00"))

            # Early-stop: items are sorted by updated_at desc
            if updated_at < cutoff:
                break

            is_pr = "pull_request" in item
            number = item["number"]
            is_recent = updated_at >= cutoff

            # Skip drafts if configured
            draft = False
            if is_pr:
                draft = item.get("draft", False)
                if draft and not include_drafts:
                    continue

            # Fetch comments
            raw_comments = _fetch_comments(session, owner, repo, number)
            comments = [
                {
                    "author": c["user"]["login"],
                    "body": c.get("body", ""),
                    "created_at": c["created_at"],
                }
                for c in raw_comments
            ]

            i_commented = any(
                c["author"].lower() == github_username.lower() for c in comments
            )

            # Fetch reviews for PRs
            reviews = []
            i_reviewed = False
            if is_pr and include_reviews:
                raw_reviews = _fetch_reviews(session, owner, repo, number)
                raw_review_comments = _fetch_review_comments(session, owner, repo, number)
                reviews = [
                    {
                        "author": r["user"]["login"],
                        "state": r.get("state", ""),
                        "body": r.get("body", ""),
                        "submitted_at": r.get("submitted_at", ""),
                    }
                    for r in raw_reviews
                ]
                i_reviewed = any(
                    r["author"].lower() == github_username.lower() for r in reviews
                )
                # Also consider inline review comments
                if not i_reviewed:
                    i_reviewed = any(
                        rc["user"]["login"].lower() == github_username.lower()
                        for rc in raw_review_comments
                    )

            item_type = "pr" if is_pr else "issue"
            if is_pr:
                pr_count += 1
                print(f"    PR #{number}: {item['title'][:60]} ({len(comments)} comments, {len(reviews)} reviews)")
            else:
                issue_count += 1
                print(f"    Issue #{number}: {item['title'][:60]} ({len(comments)} comments)")
            link = (
                f"https://github.com/{owner}/{repo}/pull/{number}"
                if is_pr
                else f"https://github.com/{owner}/{repo}/issues/{number}"
            )

            results.append({
                "type": item_type,
                "repo": repo_full,
                "number": number,
                "title": item["title"],
                "body": item.get("body", "") or "",
                "state": item["state"],
                "draft": draft,
                "labels": [l["name"] for l in item.get("labels", [])],
                "author": item["user"]["login"],
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
                "is_recent": is_recent,
                "link": link,
                "comments": comments,
                "reviews": reviews,
                "i_commented": i_commented,
                "i_reviewed": i_reviewed,
            })

        print(f"  {repo_full}: {issue_count} issues, {pr_count} PRs")

    return results
