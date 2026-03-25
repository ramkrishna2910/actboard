"""GitHub REST API client — fetches issues and PRs per repo config."""

import sys
from datetime import datetime, timedelta, timezone

import requests

GITHUB_API = "https://api.github.com"


def _check_rate_limit(resp: requests.Response):
    remaining = resp.headers.get("X-RateLimit-Remaining")
    if remaining is not None and int(remaining) == 0:
        reset_ts = int(resp.headers.get("X-RateLimit-Reset", 0))
        reset_dt = datetime.fromtimestamp(reset_ts, tz=timezone.utc)
        print(f"Error: GitHub rate limit exhausted. Resets at {reset_dt.isoformat()}", file=sys.stderr)
        sys.exit(1)


def _paginate(session: requests.Session, url: str, params: dict | None = None) -> list[dict]:
    results = []
    while url:
        resp = session.get(url, params=params)
        _check_rate_limit(resp)
        resp.raise_for_status()
        results.extend(resp.json())
        url = resp.links.get("next", {}).get("url")
        params = None
    return results


def _get_json(session: requests.Session, url: str) -> dict:
    resp = session.get(url)
    _check_rate_limit(resp)
    resp.raise_for_status()
    return resp.json()


def _fetch_comments(session, owner, repo, number):
    return _paginate(session, f"{GITHUB_API}/repos/{owner}/{repo}/issues/{number}/comments")


def _fetch_reviews(session, owner, repo, number):
    return _paginate(session, f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}/reviews")


def _fetch_review_comments(session, owner, repo, number):
    return _paginate(session, f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}/comments")


def _process_item(session, item, owner, repo, repo_full, github_username, include_reviews, cutoff):
    """Process a single issue/PR into the output schema."""
    updated_at = datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00"))
    is_pr = "pull_request" in item
    number = item["number"]
    is_recent = updated_at >= cutoff

    # Fetch comments
    raw_comments = _fetch_comments(session, owner, repo, number)
    comments = [
        {"author": c["user"]["login"], "body": c.get("body", ""), "created_at": c["created_at"]}
        for c in raw_comments
    ]
    i_commented = any(c["author"].lower() == github_username.lower() for c in comments)

    # Check if requested reviewer
    review_requested = False
    if is_pr:
        pr_resp = _get_json(session, f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}")
        requested_reviewers = [r["login"].lower() for r in pr_resp.get("requested_reviewers", [])]
        review_requested = github_username.lower() in requested_reviewers

    # Fetch reviews for PRs
    reviews = []
    i_reviewed = False
    if is_pr and include_reviews:
        raw_reviews = _fetch_reviews(session, owner, repo, number)
        raw_review_comments = _fetch_review_comments(session, owner, repo, number)
        reviews = [
            {"author": r["user"]["login"], "state": r.get("state", ""), "body": r.get("body", ""), "submitted_at": r.get("submitted_at", "")}
            for r in raw_reviews
        ]
        i_reviewed = any(r["author"].lower() == github_username.lower() for r in reviews)
        if not i_reviewed:
            i_reviewed = any(rc["user"]["login"].lower() == github_username.lower() for rc in raw_review_comments)

    i_requested_changes = any(
        r["author"].lower() == github_username.lower() and r["state"] == "CHANGES_REQUESTED"
        for r in reviews
    )

    item_type = "pr" if is_pr else "issue"
    draft = item.get("draft", False) if is_pr else False
    link = (f"https://github.com/{owner}/{repo}/pull/{number}" if is_pr
            else f"https://github.com/{owner}/{repo}/issues/{number}")

    return {
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
        "reactions": item.get("reactions", {}).get("total_count", 0),
        "comment_count": len(comments),
        "link": link,
        "comments": comments,
        "reviews": reviews,
        "i_commented": i_commented,
        "i_requested_changes": i_requested_changes,
        "i_reviewed": i_reviewed,
        "review_requested": review_requested,
    }


def fetch_github(config: dict) -> dict:
    """
    Fetch GitHub data per repo. Returns a dict keyed by repo config name:
    {"lemonade": [items], "llama.cpp": [items], ...}
    """
    token = config.get("env", {}).get("GITHUB_TOKEN", "")
    if not token:
        print("Error: GITHUB_TOKEN not set.", file=sys.stderr)
        sys.exit(1)

    github_username = config["user"]["github_username"]
    lookback = config["github"].get("lookback_hours", 24)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback)

    session = requests.Session()
    session.headers.update({
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    })

    results = {}

    for repo_cfg in config["github"]["repos"]:
        owner = repo_cfg["owner"]
        repo = repo_cfg["repo"]
        repo_name = repo_cfg.get("name", repo)
        repo_full = f"{owner}/{repo}"
        fetch_mode = repo_cfg.get("fetch", "all")
        include_drafts = repo_cfg.get("include_drafts", False)
        include_reviews = repo_cfg.get("include_code_reviews", True)

        print(f"  Scanning {repo_full} (mode={fetch_mode})...")

        url = f"{GITHUB_API}/repos/{owner}/{repo}/issues"
        params = {"state": "open", "sort": "updated", "direction": "desc", "per_page": 100}
        items = _paginate(session, url, params)

        repo_results = []
        issue_count = 0
        pr_count = 0

        for item in items:
            updated_at = datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00"))

            # Early-stop on lookback cutoff
            if updated_at < cutoff:
                break

            is_pr = "pull_request" in item

            # Skip based on fetch mode
            if fetch_mode == "prs_only" and not is_pr:
                continue
            if fetch_mode == "issues_only" and is_pr:
                continue

            # Skip drafts if configured
            if is_pr and item.get("draft", False) and not include_drafts:
                continue

            processed = _process_item(
                session, item, owner, repo, repo_full,
                github_username, include_reviews, cutoff,
            )

            if is_pr:
                pr_count += 1
                print(f"    PR #{item['number']}: {item['title'][:60]} ({processed['comment_count']} comments, {len(processed['reviews'])} reviews)")
            else:
                issue_count += 1
                print(f"    Issue #{item['number']}: {item['title'][:60]} ({processed['comment_count']} comments)")

            repo_results.append(processed)

        print(f"  {repo_full}: {issue_count} issues, {pr_count} PRs")
        results[repo_name] = repo_results

    return results
