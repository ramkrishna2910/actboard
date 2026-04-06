"""Twitter/X fetcher — uses Playwright with Chrome user data to scrape search results."""

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


JS_EXTRACT_TWEETS = """
() => {
    const tweets = [];
    document.querySelectorAll('article[data-testid="tweet"]').forEach(el => {
        const textEl = el.querySelector('[data-testid="tweetText"]');
        const text = textEl ? textEl.innerText : '';
        const linkEl = el.querySelector('a[href*="/status/"]');
        const link = linkEl ? linkEl.href : '';
        const timeEl = el.querySelector('time');
        const timestamp = timeEl ? timeEl.getAttribute('datetime') : '';
        const nameEl = el.querySelector('[data-testid="User-Name"]');
        const author = nameEl ? nameEl.innerText.split('\\n')[0] : '';
        if (link) tweets.push({text, link, timestamp, author});
    });
    return tweets;
}
"""


def _scrape_query(page, query: str, cutoff: datetime) -> list[dict]:
    """Search Twitter for a query and extract tweets."""
    search_url = f"https://x.com/search?q={query}&src=typed_query&f=live"
    page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(4000)

    all_tweets = []
    seen_links = set()

    for scroll in range(5):
        try:
            tweets = page.evaluate(JS_EXTRACT_TWEETS)
        except Exception:
            tweets = []

        for tweet in (tweets or []):
            link = tweet.get("link", "")
            if not link or link in seen_links:
                continue
            seen_links.add(link)

            ts = tweet.get("timestamp", "")
            if ts:
                try:
                    tweet_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if tweet_time < cutoff:
                        continue
                except (ValueError, TypeError):
                    pass

            all_tweets.append(tweet)

        page.evaluate("window.scrollBy(0, 2000)")
        page.wait_for_timeout(2000)

    return all_tweets


def fetch_twitter(config: dict) -> dict:
    """
    Fetch tweets matching configured search queries via Playwright.
    Uses Chrome user data directory to preserve login session.
    Returns: {"Twitter": [posts]}
    """
    twitter_cfg = config.get("twitter")
    if not twitter_cfg:
        return {}

    queries = twitter_cfg.get("queries", [])
    if not queries:
        return {}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [twitter] playwright not installed, skipping", file=sys.stderr)
        return {}

    lookback = twitter_cfg.get("lookback_hours", 24)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback)
    headed = twitter_cfg.get("headed", False)

    # Chrome user data path on Windows
    chrome_user_data = twitter_cfg.get(
        "chrome_user_data",
        str(Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data")
    )
    chrome_profile = twitter_cfg.get("chrome_profile", "Default")

    print(f"  Searching Twitter for {len(queries)} queries...")

    seen_links = set()
    posts = []

    import shutil
    import tempfile

    # Copy Chrome profile to a temp dir (Chrome locks its own data dir)
    temp_user_data = Path(tempfile.mkdtemp(prefix="twitter_chrome_"))
    src_profile = Path(chrome_user_data) / chrome_profile
    dst_profile = temp_user_data / chrome_profile

    print(f"    Copying Chrome profile...")
    try:
        shutil.copytree(src_profile, dst_profile, dirs_exist_ok=True,
                       ignore=shutil.ignore_patterns("Cache", "Code Cache", "GPUCache",
                                                      "Service Worker", "*.log", "*.tmp"))
    except Exception as e:
        print(f"    [twitter] Profile copy warning: {e}", file=sys.stderr)

    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(temp_user_data),
                channel="chrome",
                headless=not headed,
                args=[f"--profile-directory={chrome_profile}"],
                timeout=30000,
            )
            page = context.new_page()

            for query in queries:
                print(f"    Searching: {query}")
                try:
                    tweets = _scrape_query(page, query, cutoff)
                    for tweet in tweets:
                        link = tweet.get("link", "")
                        if link in seen_links:
                            continue
                        seen_links.add(link)
                        posts.append({
                            "title": tweet.get("text", "")[:200],
                            "body": tweet.get("text", ""),
                            "author": tweet.get("author", ""),
                            "created_at": tweet.get("timestamp", ""),
                            "link": link,
                            "query": query,
                            "is_recent": True,
                        })
                except Exception as e:
                    print(f"    [twitter] query '{query}' failed: {e}", file=sys.stderr)

            context.close()

    except Exception as e:
        print(f"  [twitter] Playwright error: {e}", file=sys.stderr)
        return {}
    finally:
        shutil.rmtree(temp_user_data, ignore_errors=True)

    print(f"    Found {len(posts)} tweets total")
    return {"Twitter": posts} if posts else {}
