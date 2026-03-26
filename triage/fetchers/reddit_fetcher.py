"""Reddit fetcher — uses public JSON feeds, no auth required."""

import sys
import time
from datetime import datetime, timedelta, timezone

import requests

REDDIT_BASE = "https://www.reddit.com"
USER_AGENT = "ActBoard Triage Bot 1.0"


def _get_json(session: requests.Session, url: str) -> dict:
    """GET with rate limit handling."""
    resp = session.get(url)
    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", 5))
        print(f"  [reddit] Rate limited, sleeping {retry_after}s", file=sys.stderr)
        time.sleep(retry_after)
        resp = session.get(url)
    if resp.status_code != 200:
        print(f"  [reddit] {url} returned {resp.status_code}", file=sys.stderr)
        return {}
    return resp.json()


def _fetch_subreddit_posts(session: requests.Session, subreddit: str, cutoff: datetime) -> list[dict]:
    """Fetch recent posts from a subreddit's new feed."""
    posts = []
    after = None

    while True:
        url = f"{REDDIT_BASE}/r/{subreddit}/new.json?limit=100"
        if after:
            url += f"&after={after}"

        data = _get_json(session, url)
        listing = data.get("data", {})
        children = listing.get("children", [])

        if not children:
            break

        for child in children:
            post = child.get("data", {})
            created = datetime.fromtimestamp(post.get("created_utc", 0), tz=timezone.utc)

            if created < cutoff:
                return posts

            posts.append({
                "title": post.get("title", ""),
                "body": post.get("selftext", ""),
                "author": post.get("author", ""),
                "score": post.get("score", 0),
                "num_comments": post.get("num_comments", 0),
                "created_at": created.isoformat(),
                "link": f"https://reddit.com{post.get('permalink', '')}",
                "flair": post.get("link_flair_text", ""),
                "is_recent": True,
            })

        after = listing.get("after")
        if not after:
            break

        # Be polite to Reddit
        time.sleep(1)

    return posts


# Default keywords — always included for all subreddits
DEFAULT_KEYWORDS = [
    "lemonade", "lemonade-sdk", "lemonade sdk",
    "amd", "rocm", "ryzen ai", "ryzen-ai", "strix",
    "llama.cpp", "llama cpp", "whisper.cpp", "whisper cpp",
    "onnx", "ort", "genai",
]


def _matches_keywords(post: dict, keywords: list[str]) -> bool:
    """Check if post title, body, or flair contains any keyword (case-insensitive)."""
    text = f"{post.get('title', '')} {post.get('body', '')} {post.get('flair', '')}".lower()
    return any(kw in text for kw in keywords)


def fetch_reddit(config: dict) -> dict:
    """
    Fetch posts from configured subreddits, pre-filtered by keywords.
    Returns: {subreddit_name: [posts]}
    """
    reddit_cfg = config.get("reddit")
    if not reddit_cfg:
        return {}

    subreddits = reddit_cfg.get("subreddits", [])
    if not subreddits:
        return {}

    lookback = reddit_cfg.get("lookback_hours", 24)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    results = {}
    for sub_cfg in subreddits:
        name = sub_cfg["name"]
        extra_keywords = [k.lower() for k in sub_cfg.get("keywords", [])]
        all_keywords = [k.lower() for k in DEFAULT_KEYWORDS] + extra_keywords

        print(f"  Scanning r/{name}...")
        all_posts = _fetch_subreddit_posts(session, name, cutoff)
        filtered = [p for p in all_posts if _matches_keywords(p, all_keywords)]
        print(f"    r/{name}: {len(filtered)}/{len(all_posts)} posts matched keywords")
        results[f"r/{name}"] = filtered

    return results
