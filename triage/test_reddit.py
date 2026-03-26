"""Test: fetch Reddit, analyze, and write to today's Notion page."""

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from fetchers.reddit_fetcher import fetch_reddit
from analyzer import analyze
from notion_writer import write_to_notion

config_path = Path(__file__).parent / "config.yaml"
with open(config_path) as f:
    config = yaml.safe_load(f)

load_dotenv(Path(__file__).parent / ".env")
config["env"] = {
    "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", ""),
    "NOTION_API_KEY": os.getenv("NOTION_API_KEY", ""),
}

today = date.today().isoformat()
title_fmt = config["notion"]["report_title_format"]
config["_report_title"] = title_fmt.format(date=today)

print("Fetching Reddit posts...")
reddit_data = fetch_reddit(config)
for sub_key, posts in reddit_data.items():
    print(f"  {sub_key}: {len(posts)} posts")

if not any(reddit_data.values()):
    print("No Reddit posts matched keywords. Done.")
    exit(0)

print("Analyzing with Claude...")
triage_result = analyze([], {}, config, reddit_data=reddit_data)

# Remove empty discord key
print("Writing to Notion...")
page_url = write_to_notion(triage_result, config)
print(f"Done: {page_url}")
