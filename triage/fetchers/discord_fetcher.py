"""Discord REST API client — fetches messages from monitored channels."""

import sys
import time
from datetime import datetime, timedelta, timezone

import requests

DISCORD_API = "https://discord.com/api/v10"
DISCORD_EPOCH = 1420070400000  # ms


def datetime_to_snowflake(dt: datetime) -> str:
    """Convert a datetime to a Discord snowflake ID for pagination."""
    unix_ms = int(dt.timestamp() * 1000)
    return str((unix_ms - DISCORD_EPOCH) << 22)


def _headers(token: str) -> dict:
    return {"Authorization": f"Bot {token}"}


def _handle_rate_limit(resp: requests.Response):
    """Sleep on 429, raise on other errors (except 403 which callers handle)."""
    if resp.status_code == 429:
        retry_after = resp.json().get("retry_after", 1)
        print(f"[discord] Rate limited, sleeping {retry_after}s", file=sys.stderr)
        time.sleep(retry_after)
        return True
    return False


def _get(session: requests.Session, url: str, params: dict | None = None) -> requests.Response:
    """GET with automatic 429 retry."""
    while True:
        resp = session.get(url, params=params)
        if not _handle_rate_limit(resp):
            return resp


def _fetch_guild_channels(session: requests.Session, guild_id: str) -> list[dict]:
    """Enumerate all text channels in the guild."""
    resp = _get(session, f"{DISCORD_API}/guilds/{guild_id}/channels")
    if resp.status_code == 401:
        print("Error: Invalid Discord bot token.", file=sys.stderr)
        sys.exit(1)
    resp.raise_for_status()
    # Type 0 = text channel
    return [ch for ch in resp.json() if ch.get("type") in (0, 5)]


def _filter_channels(channels: list[dict], config: dict) -> list[dict]:
    """Apply monitor/exclude filters from config. Supports IDs or names."""
    monitor = config["discord"].get("monitor", "all")
    exclude_raw = config["discord"].get("exclude_channels", [])
    exclude_ids = set()
    exclude_names = set()
    for c in exclude_raw:
        s = str(c)
        if s.isdigit():
            exclude_ids.add(s)
        else:
            exclude_names.add(s.lower())

    if monitor == "all":
        return [
            ch for ch in channels
            if str(ch["id"]) not in exclude_ids
            and not any(ex in ch.get("name", "").lower() for ex in exclude_names)
        ]
    else:
        monitor_ids = set(str(c) for c in monitor)
        return [ch for ch in channels if str(ch["id"]) in monitor_ids]


def _fetch_channel_messages(
    session: requests.Session,
    channel_id: str,
    after_snowflake: str,
) -> list[dict]:
    """Paginate messages in a channel after the given snowflake."""
    messages = []
    after = after_snowflake

    while True:
        resp = _get(
            session,
            f"{DISCORD_API}/channels/{channel_id}/messages",
            params={"after": after, "limit": 100},
        )
        if resp.status_code == 403:
            print(f"[discord] No access to channel {channel_id}, skipping", file=sys.stderr)
            return []
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        messages.extend(batch)
        if len(batch) < 100:
            break
        # Messages are returned newest-first; use the newest ID to paginate forward
        after = max(batch, key=lambda m: m["id"])["id"]

    return messages


def _fetch_active_threads(session: requests.Session, guild_id: str) -> list[dict]:
    """Fetch all active threads in the guild."""
    resp = _get(session, f"{DISCORD_API}/guilds/{guild_id}/threads/active")
    if resp.status_code == 403:
        return []
    resp.raise_for_status()
    return resp.json().get("threads", [])


def _fetch_thread_messages(
    session: requests.Session,
    thread_id: str,
    after_snowflake: str,
) -> list[dict]:
    """Fetch messages in a thread after the given snowflake."""
    return _fetch_channel_messages(session, thread_id, after_snowflake)


def fetch_discord(config: dict) -> list[dict]:
    """
    Main entry point. Returns a list of message dicts matching the spec schema.
    """
    token = config.get("env", {}).get("DISCORD_BOT_TOKEN", "")
    if not token:
        print("Error: DISCORD_BOT_TOKEN not set.", file=sys.stderr)
        sys.exit(1)

    guild_id = str(config["discord"]["guild_id"])
    if not guild_id:
        print("Error: discord.guild_id not set in config.yaml.", file=sys.stderr)
        sys.exit(1)

    lookback = config["discord"].get("lookback_hours", 24)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback)
    after_snowflake = datetime_to_snowflake(cutoff)
    discord_username = config["user"]["discord_username"]

    session = requests.Session()
    session.headers.update(_headers(token))

    # 1. Get channels
    all_channels = _fetch_guild_channels(session, guild_id)
    channels = _filter_channels(all_channels, config)
    channel_map = {str(ch["id"]): ch["name"] for ch in channels}
    monitored_ids = set(channel_map.keys())

    # 2. Fetch messages from each channel
    print(f"  Scanning {len(channels)} channels...")
    seen_ids = set()
    raw_messages = []
    for ch in channels:
        ch_id = str(ch["id"])
        msgs = _fetch_channel_messages(session, ch_id, after_snowflake)
        count = 0
        for m in msgs:
            if m["id"] not in seen_ids:
                seen_ids.add(m["id"])
                m["_channel_id"] = ch_id
                m["_channel_name"] = channel_map[ch_id]
                raw_messages.append(m)
                count += 1
        if count > 0:
            print(f"    #{channel_map[ch_id]}: {count} messages")
        else:
            print(f"    #{channel_map[ch_id]}: 0 messages")

    # 3. Fetch active threads in monitored channels
    active_threads = _fetch_active_threads(session, guild_id)
    thread_parent_map = {}  # thread_id -> parent_channel_id
    for thread in active_threads:
        parent_id = str(thread.get("parent_id", ""))
        if parent_id in monitored_ids:
            thread_parent_map[str(thread["id"])] = parent_id

    # 4. Fetch thread messages and group as replies
    thread_replies = {}  # parent message id -> list of reply dicts
    for thread_id, parent_ch_id in thread_parent_map.items():
        thread_msgs = _fetch_thread_messages(session, thread_id, after_snowflake)
        for tm in thread_msgs:
            if tm["id"] in seen_ids:
                continue
            seen_ids.add(tm["id"])
            # The thread ID itself is typically the parent message ID
            parent_msg_id = thread_id
            thread_replies.setdefault(parent_msg_id, []).append({
                "author": tm["author"]["username"],
                "content": tm.get("content", ""),
                "timestamp": tm["timestamp"],
            })

    # 5. Build output
    results = []
    for m in raw_messages:
        msg_id = m["id"]
        ch_id = m["_channel_id"]
        replies = thread_replies.get(msg_id, [])

        # Check if user replied in thread
        i_replied = any(
            r["author"].lower() == discord_username.lower() for r in replies
        )

        results.append({
            "channel_id": ch_id,
            "channel_name": m["_channel_name"],
            "message_id": msg_id,
            "author": m["author"]["username"],
            "content": m.get("content", ""),
            "timestamp": m["timestamp"],
            "link": f"https://discord.com/channels/{guild_id}/{ch_id}/{msg_id}",
            "replies": replies,
            "i_replied": i_replied,
        })

    return results
