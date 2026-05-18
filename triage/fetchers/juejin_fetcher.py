"""Juejin fetcher — uses public recommend feed API, no auth required."""

import sys
import time
from datetime import datetime, timedelta, timezone

import requests

JUEJIN_FEED_URL = "https://api.juejin.cn/recommend_api/v1/article/recommend_cate_feed"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36 ActBoard/1.0"
)

# sort_type: 300 returns the recent/trending feed (items within the last few days).
# sort_type=200 is labeled "newest" in some docs but empirically returns a stale
# recommendation stream spanning months, so it's unusable for daily triage.
SORT_RECENT = 300

# Cap on pages walked per category. The feed is only roughly time-ordered, so we
# can't safely stop on the first older item — instead, page until we run out of
# data or hit this ceiling.
MAX_PAGES = 10


def _post_json(session: requests.Session, url: str, body: dict) -> dict:
    """POST with rate limit handling."""
    resp = session.post(url, json=body, timeout=30)
    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", 5))
        print(f"  [juejin] Rate limited, sleeping {retry_after}s", file=sys.stderr)
        time.sleep(retry_after)
        resp = session.post(url, json=body, timeout=30)
    if resp.status_code != 200:
        print(f"  [juejin] {url} returned {resp.status_code}", file=sys.stderr)
        return {}
    data = resp.json()
    if data.get("err_no", 0) != 0:
        print(f"  [juejin] API error: {data.get('err_msg')}", file=sys.stderr)
        return {}
    return data


def _fetch_category_posts(session: requests.Session, cate_id: str, cutoff: datetime) -> list[dict]:
    """Fetch posts from a category's recent feed, filtered by cutoff."""
    posts = []
    cursor = "0"

    for _ in range(MAX_PAGES):
        body = {
            "id_type": 2,
            "sort_type": SORT_RECENT,
            "cate_id": str(cate_id),
            "cursor": cursor,
            "limit": 20,
        }
        data = _post_json(session, JUEJIN_FEED_URL, body)
        items = data.get("data", []) or []

        if not items:
            break

        for item in items:
            article = item.get("article_info", {}) or {}
            author = item.get("author_user_info", {}) or {}

            ctime_raw = article.get("ctime") or article.get("mtime") or "0"
            try:
                created = datetime.fromtimestamp(int(ctime_raw), tz=timezone.utc)
            except (TypeError, ValueError):
                continue

            if created < cutoff:
                continue

            article_id = article.get("article_id") or item.get("article_id", "")
            tags = [t.get("tag_name", "") for t in (item.get("tags") or [])]

            posts.append({
                "title": article.get("title", ""),
                "body": article.get("brief_content", ""),
                "author": author.get("user_name", ""),
                "score": article.get("digg_count", 0),
                "num_comments": article.get("comment_count", 0),
                "created_at": created.isoformat(),
                "link": f"https://juejin.cn/post/{article_id}",
                "tags": tags,
                "is_recent": True,
            })

        if not data.get("has_more"):
            break
        next_cursor = data.get("cursor")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor

        time.sleep(1)

    return posts


def _matches_keywords(post: dict, keywords: list[str]) -> bool:
    """Check if post title, body, or tags contain any keyword (case-insensitive)."""
    tags_text = " ".join(post.get("tags", []) or [])
    text = f"{post.get('title', '')} {post.get('body', '')} {tags_text}".lower()
    return any(kw in text for kw in keywords)


def fetch_juejin(config: dict) -> dict:
    """
    Fetch posts from configured Juejin categories, pre-filtered by keywords.
    Returns: {"juejin/<name>": [posts]}
    """
    juejin_cfg = config.get("juejin")
    if not juejin_cfg:
        return {}

    categories = juejin_cfg.get("categories", [])
    if not categories:
        return {}

    lookback = juejin_cfg.get("lookback_hours", 24)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback)

    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Origin": "https://juejin.cn",
        "Referer": "https://juejin.cn/",
    })

    global_keywords = [k.lower() for k in juejin_cfg.get("keywords", [])]

    results = {}
    for cat_cfg in categories:
        name = cat_cfg["name"]
        cate_id = cat_cfg.get("cate_id", "")
        if not cate_id:
            print(f"  [juejin] {name}: missing cate_id, skipping", file=sys.stderr)
            continue
        per_cat_keywords = [k.lower() for k in cat_cfg.get("keywords", [])]
        all_keywords = global_keywords + per_cat_keywords

        print(f"  Scanning juejin/{name}...")
        all_posts = _fetch_category_posts(session, cate_id, cutoff)
        if all_keywords:
            filtered = [p for p in all_posts if _matches_keywords(p, all_keywords)]
            print(f"    juejin/{name}: {len(filtered)}/{len(all_posts)} posts matched keywords")
        else:
            filtered = all_posts
            print(f"    juejin/{name}: {len(filtered)} posts (no keyword filter)")
        results[f"juejin/{name}"] = filtered

    return results
