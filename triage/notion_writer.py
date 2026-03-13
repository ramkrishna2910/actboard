"""Notion API client — creates a daily triage page and appends blocks."""

import time

from notion_client import Client, APIResponseError


def _retry(fn, retries=3, delay=5):
    """Retry a function on 502/503/429 errors."""
    for attempt in range(retries):
        try:
            return fn()
        except APIResponseError as e:
            if e.status in (502, 503, 429) and attempt < retries - 1:
                wait = delay * (attempt + 1)
                print(f"  Notion {e.status}, retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise


def write_to_notion(blocks: list[dict], config: dict) -> str:
    """
    Create a new Notion page under the configured parent and append triage blocks.
    Returns the page URL.
    """
    api_key = config["env"]["NOTION_API_KEY"]
    parent_page_id = config["notion"]["parent_page_id"]
    title = config["_report_title"]

    notion = Client(auth=api_key)

    # Create the page
    page = _retry(lambda: notion.pages.create(
        parent={"page_id": parent_page_id},
        properties={
            "title": [{"text": {"content": title}}],
        },
    ))
    page_id = page["id"]

    # Append blocks in batches of 100 (Notion API limit)
    for i in range(0, len(blocks), 100):
        batch = blocks[i : i + 100]
        _retry(lambda batch=batch: notion.blocks.children.append(
            block_id=page_id, children=batch
        ))

    return page["url"]
