# Daily Triage Automation — Claude Code Spec

## Overview

A Python CLI tool that runs on demand (initially manually, cron-ready) to:
1. Fetch the last 24 hours of activity from a Discord server
2. Fetch open GitHub issues and PRs with recent activity
3. Analyze everything using Claude API
4. Post a structured daily triage report to a new Notion page

---

## Project Structure

```
triage/
├── config.yaml               # User-editable config (credentials, channels, repos)
├── main.py                   # Entry point — orchestrates the full pipeline
├── fetchers/
│   ├── discord_fetcher.py    # Discord REST API client
│   └── github_fetcher.py     # GitHub REST API client
├── analyzer.py               # Builds Claude prompt and calls Claude API
├── renderer.py               # Formats Claude output into Notion blocks
├── notion_writer.py          # Creates the daily Notion page
├── .env                      # API keys (gitignored)
├── .env.example              # Template for required env vars
└── requirements.txt
```

---

## Config File (config.yaml)

All user-tunable settings live here. Claude Code should never hardcode these values.

```yaml
user:
  discord_username: "ramkrishna2910"   # Used to detect if you have already replied
  github_username: "ramkrishna2910"    # Used to detect your comments/reviews/approvals

discord:
  guild_id: ""                          # Discord server ID
  monitor: "all"                        # "all" OR list of channel IDs to include
  exclude_channels: []                  # Channel IDs to skip (used when monitor is "all")
  lookback_hours: 24

github:
  repos:
    - owner: "lemonade-sdk"
      repo: "lemonade"
  lookback_hours: 24
  include_draft_prs: true               # Fetch drafts but mark as FYI only
  include_code_reviews: true

notion:
  parent_page_id: ""                    # Notion page ID where daily pages are created
  report_title_format: "Daily Triage — {date}"   # e.g. "Daily Triage — 2025-03-11"
```

---

## Environment Variables (.env)

```
DISCORD_BOT_TOKEN=
GITHUB_TOKEN=
ANTHROPIC_API_KEY=
NOTION_API_KEY=
```

---

## Module Specifications

### discord_fetcher.py

**Purpose:** Fetch all messages from all monitored channels in the last N hours.

**Discord bot required permissions:**
- `Read Messages / View Channels`
- `Read Message History`

**Logic:**
1. Use `GET /guilds/{guild_id}/channels` to enumerate all text channels
2. Filter based on `config.discord.monitor` and `config.discord.exclude_channels`
3. For each channel, call `GET /channels/{channel_id}/messages` paginating with `after` timestamp (now - lookback_hours)
4. For each message, also fetch thread replies if the message started a thread
5. Detect whether `config.user.discord_username` appears in the replies of any thread

**Output schema per message:**
```python
{
  "channel_id": str,
  "channel_name": str,
  "message_id": str,
  "author": str,
  "content": str,
  "timestamp": str,             # ISO 8601
  "link": str,                  # https://discord.com/channels/{guild}/{channel}/{message}
  "replies": [
    {
      "author": str,
      "content": str,
      "timestamp": str
    }
  ],
  "i_replied": bool             # True if discord_username appears in replies
}
```

---

### github_fetcher.py

**Purpose:** Fetch open issues and PRs with recent activity.

**Logic:**
1. For each repo in config, call:
   - `GET /repos/{owner}/{repo}/issues?state=open&sort=updated&direction=desc`
   - Filter by `updated_at >= now - lookback_hours` for "recent" flag
   - Note: GitHub issues endpoint returns both issues and PRs; separate them by presence of `pull_request` key
2. For each item, fetch:
   - Full body
   - Comments list (`GET /repos/{owner}/{repo}/issues/{number}/comments`)
   - For PRs: reviews (`GET /repos/{owner}/{repo}/pulls/{number}/reviews`)
   - For PRs: review comments (`GET /repos/{owner}/{repo}/pulls/{number}/comments`)
3. Detect whether `config.user.github_username` appears in any comment or review

**Output schema per item:**
```python
{
  "type": "issue" | "pr",
  "repo": str,
  "number": int,
  "title": str,
  "body": str,
  "state": str,
  "draft": bool,
  "labels": [str],
  "author": str,
  "created_at": str,
  "updated_at": str,
  "is_recent": bool,            # updated within lookback_hours
  "link": str,                  # https://github.com/{owner}/{repo}/issues/{number}
  "comments": [
    {
      "author": str,
      "body": str,
      "created_at": str
    }
  ],
  "reviews": [                  # PRs only
    {
      "author": str,
      "state": str,             # APPROVED, CHANGES_REQUESTED, COMMENTED
      "body": str,
      "submitted_at": str
    }
  ],
  "i_commented": bool,          # True if github_username appears in comments
  "i_reviewed": bool            # True if github_username appears in reviews (PRs only)
}
```

---

### analyzer.py

**Purpose:** Send structured data to Claude API and receive categorized triage output.

**Model:** `claude-sonnet-4-20250514`

**System Prompt:**

```
You are a daily triage assistant for a Principal ML Software Engineer and open-source project maintainer.

The user is Krishna (Discord: ramkrishna2910, GitHub: ramkrishna2910). He maintains the Lemonade SDK, an open-source local LLM server for AMD Ryzen AI hardware. The project includes integrations with llama.cpp, ONNX Runtime GenAI, and supports multimodal inference (image generation, speech-to-text). Krishna is also active in the ONNX community. He interacts with external contributors, AMD stakeholders, and community members daily.

Your job is to review the Discord messages and GitHub items provided and produce a prioritized triage report in structured JSON.

TRIAGE RULES:

Discord:
- If Krishna has already replied to a thread, mark it as HANDLED unless the thread has continued with new unanswered questions after his reply
- If a message is a question, bug report, or request that Krishna has NOT replied to, mark it as ACT
- If a message is informational (announcement, FYI, resolved discussion), mark it as MONITOR
- Summarize the thread in 1-2 sentences. Do not quote messages verbatim
- Always include the message link

GitHub:
- Issues/PRs updated in the last 24 hours are RECENT — prioritize these
- If Krishna has already commented or reviewed and the thread is quiet, mark as HANDLED
- If an issue is a bug report with no response from Krishna, mark as ACT
- If a PR needs review and Krishna has not reviewed, mark as ACT
- Draft PRs: always mark as MONITOR regardless of Krishna's involvement
- If a PR has been approved or merged by others with no action needed from Krishna, mark as HANDLED
- Summarize each item in 1-2 sentences

OUTPUT FORMAT:
Return only valid JSON with this structure:
{
  "generated_at": "ISO timestamp",
  "discord": {
    "act": [TriageItem],
    "monitor": [TriageItem],
    "handled": [TriageItem]
  },
  "github": {
    "act": [TriageItem],
    "monitor": [TriageItem],
    "handled": [TriageItem]
  }
}

Where TriageItem is:
{
  "summary": str,         // 1-2 sentence description of what needs attention
  "reason": str,          // Why this was categorized this way (1 sentence)
  "link": str,            // Clickable URL to the message or issue
  "label": str,           // Short tag e.g. "bug", "question", "PR review", "discussion", "draft PR"
  "is_recent": bool       // True if activity is within the last 24 hours
}

Be concise. Do not add commentary outside the JSON. Do not invent information not present in the input.
```

**User message format:**

Serialize the fetched Discord messages and GitHub items as clean JSON and pass as the user message.

Truncate individual message bodies to 500 characters to stay within context limits. If a thread has more than 20 replies, include only the first 5 and last 5 with a `[... N messages omitted ...]` note.

**Output:** Parsed JSON object matching the schema above.

---

### renderer.py

**Purpose:** Convert the analyzer JSON output into Notion block format.

**Notion page structure:**

```
# Daily Triage — {date}
Generated at: {time}

---

## Discord

### 🔴 Act ({count})
[callout block per item]
  Summary text
  Reason: ...
  Label: ...
  → View Message (link)

### 🟡 Monitor ({count})
[callout block per item]

### 🟢 Handled ({count})
[callout block per item]

---

## GitHub

### 🔴 Act ({count})
[callout block per item]
  Summary text
  Reason: ...
  Label: ...
  🕐 Recent (badge if is_recent)
  → View on GitHub (link)

### 🟡 Monitor ({count})
[callout block per item]

### 🟢 Handled ({count})
[callout block per item]
```

Use Notion callout blocks with emoji icons per category:
- ACT: red background callout, 🔴
- MONITOR: yellow background callout, 🟡
- HANDLED: green background callout, 🟢

Each item should render the link as a Notion external URL button/text — clickable inline.

---

### notion_writer.py

**Purpose:** Create a new Notion page under the configured parent page.

**Logic:**
1. Use Notion API `POST /v1/pages` to create a new child page under `config.notion.parent_page_id`
2. Page title: formatted from `config.notion.report_title_format` with today's date
3. Append all blocks from renderer.py in a single `PATCH /v1/blocks/{page_id}/children` call (batch up to 100 blocks per call as required by Notion API limits)
4. Print the new page URL to stdout on success

---

## main.py — Orchestration

```python
# Pseudocode flow
config = load_config("config.yaml")
load_dotenv()

discord_data = fetch_discord(config)
github_data = fetch_github(config)

triage_result = analyze(discord_data, github_data, config)

notion_blocks = render(triage_result)
page_url = write_to_notion(notion_blocks, config)

print(f"Triage report published: {page_url}")
```

Run with: `python main.py`

---

## Error Handling

- If Discord token is missing or invalid: print clear error and exit with code 1
- If a Discord channel returns 403 (Missing Access): skip silently, log to stderr
- If GitHub rate limit is hit: print warning with reset time and exit
- If Claude API call fails: retry once with 5-second delay, then exit with error
- If Notion write fails: save the JSON output to `./triage_output_{date}.json` as fallback and print path

---

## Requirements.txt

```
requests
python-dotenv
pyyaml
anthropic
notion-client
```

---

## Setup Instructions (to include in README.md)

1. **Create a Discord bot** at discord.com/developers, enable `Message Content Intent`, invite to your server with `Read Messages` + `Read Message History` scopes
2. **Generate a GitHub personal access token** with `repo` read scope
3. **Create a Notion integration** at notion.so/my-integrations, connect it to your target parent page
4. **Get an Anthropic API key** from console.anthropic.com
5. Copy `.env.example` to `.env` and fill in all keys
6. Fill in `config.yaml` with your Discord `guild_id` and Notion `parent_page_id`
7. Run `pip install -r requirements.txt`
8. Run `python main.py`

---

## Future Enhancements (out of scope for v1)

- `--since` CLI flag to override the lookback window
- n8n cron trigger (call `python main.py` via Execute Command node at 8AM)
- Slack DM delivery as alternative to Notion
- Per-channel priority weighting in config
- Multi-server Discord support
