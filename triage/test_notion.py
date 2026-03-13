"""Quick test: create a dummy page in Notion and write a few blocks."""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from notion_client import Client

# Load config
config_path = Path(__file__).parent / "config.yaml"
with open(config_path) as f:
    config = yaml.safe_load(f)

load_dotenv(Path(__file__).parent / ".env")

api_key = os.getenv("NOTION_API_KEY", "")
parent_page_id = config["notion"]["parent_page_id"]

notion = Client(auth=api_key)

# Create a test page
print("Creating test page...")
page = notion.pages.create(
    parent={"page_id": parent_page_id},
    properties={
        "title": [{"text": {"content": "Test Page — DELETE ME"}}],
    },
)
page_id = page["id"]
print(f"Page created: {page['url']}")

# Append a few blocks
print("Appending blocks...")
notion.blocks.children.append(
    block_id=page_id,
    children=[
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": "Test Section"}}]},
        },
        {
            "object": "block",
            "type": "callout",
            "callout": {
                "rich_text": [{"type": "text", "text": {"content": "This is a test callout"}}],
                "icon": {"type": "emoji", "emoji": "\U0001f534"},
                "color": "red_background",
            },
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": "If you see this, Notion writing works!"}}]},
        },
    ],
)

print("Done! Check the page in Notion, then delete it.")
