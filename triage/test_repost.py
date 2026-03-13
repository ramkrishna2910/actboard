"""Re-render and post a saved triage JSON to Notion (no Claude API cost)."""

import json
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

from renderer import render
from notion_writer import write_to_notion

config_path = Path(__file__).parent / "config.yaml"
with open(config_path) as f:
    config = yaml.safe_load(f)

load_dotenv(Path(__file__).parent / ".env")
config["env"] = {
    "NOTION_API_KEY": os.getenv("NOTION_API_KEY", ""),
}

from datetime import date
title_fmt = config["notion"]["report_title_format"]
config["_report_title"] = title_fmt.format(date=date.today().isoformat())

# Load saved JSON
json_path = Path(__file__).parent / "triage_output_2026-03-13.json"
with open(json_path) as f:
    triage_result = json.load(f)

print("Rendering blocks...")
blocks = render(triage_result)
print(f"  {len(blocks)} blocks to write")

print("Writing to Notion...")
page_url = write_to_notion(blocks, config)
print(f"Done: {page_url}")
