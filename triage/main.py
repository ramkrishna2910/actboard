"""Daily triage automation — orchestrates fetch, analyze, render, and publish."""

import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from fetchers.discord_fetcher import fetch_discord
from fetchers.github_fetcher import fetch_github
from fetchers.gh_fetcher import fetch_gh_supplements
from fetchers.reddit_fetcher import fetch_reddit
from fetchers.outlook_fetcher import fetch_outlook
from analyzer import analyze
from responder import generate_responses
from notion_writer import write_to_notion
from pipeline_events import emit


def _git_pull(repo_path: str, name: str):
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
    print("Syncing repos...")
    for repo_cfg in config["github"]["repos"]:
        repo_path = repo_cfg.get("repo_path", "")
        name = repo_cfg.get("name", repo_cfg["repo"])
        if repo_path and Path(repo_path).exists():
            _git_pull(repo_path, name)


def load_config() -> dict:
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
        "OUTLOOK_FLOW_URL": os.getenv("OUTLOOK_FLOW_URL", ""),
    }

    required_keys = ["DISCORD_BOT_TOKEN", "GITHUB_TOKEN", "NOTION_API_KEY"]
    if config.get("inference", {}).get("backend", "claude") == "claude":
        required_keys.append("ANTHROPIC_API_KEY")
    missing = [k for k in required_keys if not config["env"].get(k)]
    if missing:
        print(f"Error: Missing API keys: {', '.join(missing)}", file=sys.stderr)
        print("Copy .env.example to .env and fill in all values.", file=sys.stderr)
        sys.exit(1)

    if date.today().weekday() == 0:
        print("Monday detected - extending lookback to 72 hours")
        config["discord"]["lookback_hours"] = 72
        config["github"]["lookback_hours"] = 72

    today = date.today().isoformat()
    title_fmt = config.get("notion", {}).get("report_title_format", "Daily Triage - {date}")
    config["_report_title"] = title_fmt.format(date=today)

    return config


def main():
    start = time.time()
    config = load_config()

    sources = ["Discord"] + [r.get("name", r["repo"]) for r in config["github"]["repos"]]
    emit("pipeline_start", "pipeline", config_summary={"sources": sources})

    # Sync repos
    emit("stage_start", "sync_repos")
    sync_repos(config)
    emit("stage_complete", "sync_repos", item_count=len(config["github"]["repos"]))

    # Fetch Discord
    emit("stage_start", "fetch_discord")
    print("Fetching Discord messages...")
    discord_data = fetch_discord(config)
    print(f"  Found {len(discord_data)} messages")
    emit("stage_complete", "fetch_discord", item_count=len(discord_data))

    # Fetch GitHub
    emit("stage_start", "fetch_github")
    print("Fetching GitHub issues/PRs...")
    github_data = fetch_github(config)
    gh_total = sum(len(v) for v in github_data.values())
    for repo_name, items in github_data.items():
        print(f"  {repo_name}: {len(items)} items")
    emit("stage_complete", "fetch_github", item_count=gh_total)

    # Fetch gh CLI extras
    emit("stage_start", "fetch_gh")
    print("Checking gh CLI for extras...")
    gh_extras = fetch_gh_supplements(config)
    gh_extra_count = len(gh_extras.get("review_requests", [])) + len(gh_extras.get("mentions", []))
    emit("stage_complete", "fetch_gh", item_count=gh_extra_count)

    # Fetch Reddit
    emit("stage_start", "fetch_reddit")
    print("Fetching Reddit posts...")
    reddit_data = fetch_reddit(config)
    reddit_total = sum(len(v) for v in reddit_data.values())
    for sub_key, posts in reddit_data.items():
        print(f"  {sub_key}: {len(posts)} posts")
    emit("stage_complete", "fetch_reddit", item_count=reddit_total)

    # Fetch Outlook emails
    emit("stage_start", "fetch_outlook")
    print("Fetching Outlook emails...")
    outlook_data = fetch_outlook(config)
    outlook_total = sum(len(v) for v in outlook_data.values())
    emit("stage_complete", "fetch_outlook", item_count=outlook_total)

    # Analyze
    print("Analyzing...")
    triage_result = analyze(discord_data, github_data, config, gh_extras, reddit_data, outlook_data)

    # Respond
    emit("stage_start", "responder")
    print("Generating suggested responses...")
    triage_result = generate_responses(triage_result, config)
    emit("stage_complete", "responder")

    # Write to Notion
    emit("stage_start", "notion")
    print("Writing to Notion...")
    try:
        page_url = write_to_notion(triage_result, config)
        print(f"Triage board: {page_url}")
        emit("notion_complete", "notion", page_url=page_url)
    except Exception as e:
        today = date.today().isoformat()
        fallback_path = Path(__file__).parent / f"triage_output_{today}.json"
        with open(fallback_path, "w") as f:
            json.dump(triage_result, f, indent=2, default=str)
        print(f"Error writing to Notion: {e}", file=sys.stderr)
        print(f"Triage JSON saved to: {fallback_path}")
        emit("notion_error", "notion", error=str(e))

    duration = time.time() - start
    emit("pipeline_complete", "pipeline", duration=duration)
    print(f"\nDone in {duration:.1f}s")


if __name__ == "__main__":
    main()
