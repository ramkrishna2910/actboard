"""Daily triage automation — orchestrates fetch, analyze, render, and publish."""

import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from fetchers.discord_fetcher import fetch_discord
from fetchers.github_fetcher import fetch_github
from analyzer import analyze
from responder import generate_responses
from notion_writer import write_to_notion


def load_config() -> dict:
    """Load config.yaml and .env, validate required keys."""
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Load .env from the triage/ directory
    env_path = Path(__file__).parent / ".env"
    load_dotenv(env_path)

    # Attach env vars to config for easy access
    config["env"] = {
        "DISCORD_BOT_TOKEN": os.getenv("DISCORD_BOT_TOKEN", ""),
        "GITHUB_TOKEN": os.getenv("GITHUB_TOKEN", ""),
        "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", ""),
        "NOTION_API_KEY": os.getenv("NOTION_API_KEY", ""),
    }

    # Validate all API keys are present
    missing = [k for k, v in config["env"].items() if not v]
    if missing:
        print(f"Error: Missing API keys: {', '.join(missing)}", file=sys.stderr)
        print("Copy .env.example to .env and fill in all values.", file=sys.stderr)
        sys.exit(1)

    # On Mondays, extend lookback to 72 hours to cover the weekend
    if date.today().weekday() == 0:  # 0 = Monday
        print("Monday detected — extending lookback to 72 hours")
        config["discord"]["lookback_hours"] = 72
        config["github"]["lookback_hours"] = 72

    # Build report title
    today = date.today().isoformat()
    title_fmt = config.get("notion", {}).get("report_title_format", "Daily Triage - {date}")
    config["_report_title"] = title_fmt.format(date=today)

    return config


def main():
    config = load_config()

    print("Fetching Discord messages...")
    discord_data = fetch_discord(config)
    print(f"  Found {len(discord_data)} messages")

    print("Fetching GitHub issues/PRs...")
    github_data = fetch_github(config)
    print(f"  Found {len(github_data)} items")

    print("Analyzing with Claude...")
    triage_result = analyze(discord_data, github_data, config)

    print("Generating suggested responses...")
    triage_result = generate_responses(triage_result, config)

    print("Writing to Notion...")
    try:
        page_url = write_to_notion(triage_result, config)
        print(f"Triage board: {page_url}")
    except Exception as e:
        # Fallback: save JSON to file
        today = date.today().isoformat()
        fallback_path = Path(__file__).parent / f"triage_output_{today}.json"
        with open(fallback_path, "w") as f:
            json.dump(triage_result, f, indent=2, default=str)
        print(f"Error writing to Notion: {e}", file=sys.stderr)
        print(f"Triage JSON saved to: {fallback_path}")


if __name__ == "__main__":
    main()
