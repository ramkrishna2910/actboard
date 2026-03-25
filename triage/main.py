"""Daily triage automation — orchestrates fetch, analyze, render, and publish."""

import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from fetchers.discord_fetcher import fetch_discord
from fetchers.github_fetcher import fetch_github
from fetchers.gh_fetcher import fetch_gh_supplements
from analyzer import analyze
from responder import generate_responses
from notion_writer import write_to_notion


def _git_pull(repo_path: str, name: str):
    """Pull latest changes for a repo."""
    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            capture_output=True, text=True, cwd=repo_path, timeout=60,
        )
        if result.returncode == 0:
            print(f"    {name}: {result.stdout.strip()}")
        else:
            print(f"    {name}: git pull warning: {result.stderr.strip()}", file=sys.stderr)
    except Exception as e:
        print(f"    {name}: git pull failed: {e}", file=sys.stderr)


def sync_repos(config: dict):
    """Git pull all repos that have a local clone path."""
    print("Syncing repos...")
    for repo_cfg in config["github"]["repos"]:
        repo_path = repo_cfg.get("repo_path", "")
        name = repo_cfg.get("name", repo_cfg["repo"])
        if repo_path and Path(repo_path).exists():
            _git_pull(repo_path, name)


def load_config() -> dict:
    """Load config.yaml and .env, validate required keys."""
    config_path = Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    env_path = Path(__file__).parent / ".env"
    load_dotenv(env_path)

    config["env"] = {
        "DISCORD_BOT_TOKEN": os.getenv("DISCORD_BOT_TOKEN", ""),
        "GITHUB_TOKEN": os.getenv("GITHUB_TOKEN", ""),
        "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", ""),
        "NOTION_API_KEY": os.getenv("NOTION_API_KEY", ""),
    }

    missing = [k for k, v in config["env"].items() if not v]
    if missing:
        print(f"Error: Missing API keys: {', '.join(missing)}", file=sys.stderr)
        print("Copy .env.example to .env and fill in all values.", file=sys.stderr)
        sys.exit(1)

    # On Mondays, extend lookback to 72 hours to cover the weekend
    if date.today().weekday() == 0:
        print("Monday detected - extending lookback to 72 hours")
        config["discord"]["lookback_hours"] = 72
        config["github"]["lookback_hours"] = 72

    # Build report title
    today = date.today().isoformat()
    title_fmt = config.get("notion", {}).get("report_title_format", "Daily Triage - {date}")
    config["_report_title"] = title_fmt.format(date=today)

    return config


def main():
    config = load_config()

    # Pull all repos first
    sync_repos(config)

    print("Fetching Discord messages...")
    discord_data = fetch_discord(config)
    print(f"  Found {len(discord_data)} messages")

    print("Fetching GitHub issues/PRs...")
    github_data = fetch_github(config)
    for repo_name, items in github_data.items():
        print(f"  {repo_name}: {len(items)} items")

    print("Checking gh CLI for extras...")
    gh_extras = fetch_gh_supplements(config)

    print("Analyzing with Claude...")
    triage_result = analyze(discord_data, github_data, config, gh_extras)

    print("Generating suggested responses...")
    triage_result = generate_responses(triage_result, config)

    print("Writing to Notion...")
    try:
        page_url = write_to_notion(triage_result, config)
        print(f"Triage board: {page_url}")
    except Exception as e:
        today = date.today().isoformat()
        fallback_path = Path(__file__).parent / f"triage_output_{today}.json"
        with open(fallback_path, "w") as f:
            json.dump(triage_result, f, indent=2, default=str)
        print(f"Error writing to Notion: {e}", file=sys.stderr)
        print(f"Triage JSON saved to: {fallback_path}")


if __name__ == "__main__":
    main()
