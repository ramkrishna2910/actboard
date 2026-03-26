"""Notion API client — one database per source, auto-created under parent page."""

import sys
import time
from datetime import date
from pathlib import Path

import httpx
import yaml
from notion_client import Client, APIResponseError

NOTION_API = "https://api.notion.com/v1"
NOTION_HEADERS_BASE = {
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

# Per-source database property schema (no Source column needed — it's implicit)
DATABASE_PROPERTIES = {
    "Summary": {"title": {}},
    "Category": {
        "select": {
            "options": [
                {"name": "ACT", "color": "red"},
                {"name": "MONITOR", "color": "yellow"},
                {"name": "HANDLED", "color": "green"},
            ]
        }
    },
    "Done": {"checkbox": {}},
    "Label": {"select": {"options": []}},
    "Link": {"url": {}},
    "Reason": {"rich_text": {}},
    "Recent": {"checkbox": {}},
    "Suggested Response": {"rich_text": {}},
    "Date": {"date": {}},
}

def _build_source_config(config: dict) -> dict:
    """Build source config dynamically from config.yaml repos + discord."""
    sources = {
        "discord": {"name": "Discord", "icon": "\U0001f4ac", "hide_handled": False},
    }
    for repo_cfg in config.get("github", {}).get("repos", []):
        name = repo_cfg.get("name", repo_cfg["repo"])
        icon = repo_cfg.get("icon", "\U0001f4e6")  # 📦 default
        hide_handled = repo_cfg.get("hide_handled", False)
        sources[name] = {"name": name, "icon": icon, "hide_handled": hide_handled}
    for sub_cfg in config.get("reddit", {}).get("subreddits", []):
        key = f"r/{sub_cfg['name']}"
        icon = sub_cfg.get("icon", "\U0001f4e2")  # 📢 default
        hide = sub_cfg.get("hide_handled", True)
        sources[key] = {"name": key, "icon": icon, "hide_handled": hide}
    sources["_gh_extras"] = {"name": "Other (gh)", "icon": "\U0001f514", "hide_handled": False}
    return sources


def _headers(api_key: str) -> dict:
    return {**NOTION_HEADERS_BASE, "Authorization": f"Bearer {api_key}"}


def _api_post(api_key: str, path: str, body: dict) -> dict:
    resp = httpx.post(f"{NOTION_API}/{path}", headers=_headers(api_key), json=body, timeout=30)
    if resp.status_code != 200:
        print(f"    [notion] {path} error {resp.status_code}: {resp.text[:300]}", file=sys.stderr)
    resp.raise_for_status()
    return resp.json()


def _api_get(api_key: str, path: str) -> dict:
    resp = httpx.get(f"{NOTION_API}/{path}", headers=_headers(api_key), timeout=30)
    resp.raise_for_status()
    return resp.json()


def _create_database(api_key: str, parent_page_id: str, title: str, icon: str) -> str:
    """Create a source database with properties and icon."""
    db = _api_post(api_key, "databases", {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": title}}],
        "icon": {"type": "emoji", "emoji": icon},
        "properties": DATABASE_PROPERTIES,
    })
    print(f"  Created database: {icon} {title}")
    return db["id"]


def _get_existing_links(api_key: str, db_id: str, today: str) -> set[str]:
    """Query database for today's entries, return their links for dedup."""
    links = set()
    cursor = None
    clean_id = db_id.replace("-", "")
    filter_obj = {"property": "Date", "date": {"equals": today}}
    while True:
        body = {"filter": filter_obj}
        if cursor:
            body["start_cursor"] = cursor
        resp = _api_post(api_key, f"databases/{clean_id}/query", body)
        for page in resp.get("results", []):
            url = page.get("properties", {}).get("Link", {}).get("url")
            if url:
                links.add(url)
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return links


def _create_entry(api_key: str, db_id: str, item: dict, category: str, today: str):
    """Create a single database entry."""
    properties = {
        "Summary": {"title": [{"text": {"content": item.get("summary", "")[:2000]}}]},
        "Category": {"select": {"name": category.upper()}},
        "Done": {"checkbox": False},
        "Label": {"select": {"name": item.get("label") or "other"}},
        "Link": {"url": item.get("link", "")},
        "Reason": {"rich_text": [{"text": {"content": item.get("reason", "")[:2000]}}]},
        "Recent": {"checkbox": item.get("is_recent", False)},
        "Date": {"date": {"start": today}},
    }
    suggested = item.get("suggested_response", "")
    if suggested:
        properties["Suggested Response"] = {
            "rich_text": [{"text": {"content": suggested[:2000]}}]
        }
    _api_post(api_key, "pages", {
        "parent": {"database_id": db_id},
        "properties": properties,
    })


def _find_daily_page(api_key: str, parent_page_id: str, title: str) -> str | None:
    """Search for today's page under the parent."""
    resp = _api_post(api_key, "search", {"query": title})
    for page in resp.get("results", []):
        if page.get("object") != "page":
            continue
        page_title = ""
        title_prop = page.get("properties", {}).get("title", {})
        if isinstance(title_prop, dict):
            for t in title_prop.get("title", []):
                page_title += t.get("plain_text", "")
        parent = page.get("parent", {})
        if page_title == title and parent.get("page_id", "").replace("-", "") == parent_page_id.replace("-", ""):
            return page["id"]
    return None


def _create_daily_page(api_key: str, parent_page_id: str, title: str) -> str:
    """Create today's date page under the parent."""
    page = _api_post(api_key, "pages", {
        "parent": {"page_id": parent_page_id},
        "properties": {
            "title": [{"text": {"content": title}}],
        },
        "icon": {"type": "emoji", "emoji": "\U0001f4cb"},  # 📋
    })
    print(f"  Created daily page: {title}")
    return page["id"]


def _find_database_in_page(api_key: str, page_id: str, title: str) -> str | None:
    """Find a database by title under a specific page."""
    # List child blocks to find databases
    clean_id = page_id.replace("-", "")
    cursor = None
    while True:
        path = f"blocks/{clean_id}/children"
        if cursor:
            path += f"?start_cursor={cursor}"
        resp = _api_get(api_key, path)
        for block in resp.get("results", []):
            if block.get("type") == "child_database":
                db_title = block.get("child_database", {}).get("title", "")
                if db_title == title:
                    return block["id"]
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return None


def write_to_notion(triage_result: dict, config: dict) -> str:
    """
    Write triage results to per-source databases under a daily page.
    Structure: Parent Page > Daily Page (date) > Source Databases
    """
    api_key = config["env"]["NOTION_API_KEY"]
    parent_page_id = config["notion"]["parent_page_id"]
    today = date.today().isoformat()
    title_fmt = config.get("notion", {}).get("report_title_format", "Daily Triage - {date}")
    daily_title = title_fmt.format(date=today)

    # Find or create today's page
    daily_page_id = _find_daily_page(api_key, parent_page_id, daily_title)
    if not daily_page_id:
        daily_page_id = _create_daily_page(api_key, parent_page_id, daily_title)
    else:
        print(f"  Found existing daily page: {daily_title}")

    source_config = _build_source_config(config)
    for source_key, source_cfg in source_config.items():
        source_name = source_cfg["name"]
        source_icon = source_cfg["icon"]
        items_by_cat = triage_result.get(source_key, {})

        # All items for this source
        hide_handled = source_cfg.get("hide_handled", False)
        categories = ("act", "monitor") if hide_handled else ("act", "monitor", "handled")
        all_items = []
        for category in categories:
            for item in items_by_cat.get(category, []):
                all_items.append((category, item))

        if not all_items:
            print(f"  {source_icon} {source_name}: no items, skipping")
            continue

        # Find or create database under today's page
        db_id = _find_database_in_page(api_key, daily_page_id, source_name)
        if not db_id:
            db_id = _create_database(api_key, daily_page_id, source_name, source_icon)

        print(f"  {source_icon} {source_name}: {len(all_items)} items")

        # Dedup
        existing_links = set()
        try:
            existing_links = _get_existing_links(api_key, db_id, today)
            if existing_links:
                print(f"    Already have {len(existing_links)} entries for today")
        except Exception:
            pass

        # Add new entries
        new_count = 0
        for category, item in all_items:
            link = item.get("link", "")
            if link in existing_links:
                continue
            _create_entry(api_key, db_id, item, category, today)
            new_count += 1

        print(f"    Added {new_count} new entries")

    return f"https://notion.so/{daily_page_id.replace('-', '')}"
