# actboard

A daily triage assistant that pulls activity from the places you actually live —
Discord, GitHub, and Reddit — runs it through an LLM, and publishes a
prioritized **ACT / MONITOR / HANDLED** report to a Notion page.

Built for open-source maintainers and engineers who get pulled in too many
directions to keep up by hand. Configure once, run on a schedule, read one
page in the morning.

## What it does

For each source you enable, actboard:

1. Pulls the last N hours of activity (default 24, 72 on Mondays).
2. Dispatches one LLM sub-agent per channel / repo / subreddit in parallel.
3. Categorizes every item as **ACT** (needs you), **MONITOR** (FYI), or
   **HANDLED** (already done).
4. For ACT items in repos you've cloned locally, optionally drafts a suggested
   reply by reading the codebase via Claude Code.
5. Writes one Notion database per source under a dated daily page.

| Source | What's pulled | Auth |
| --- | --- | --- |
| Discord | Messages + thread replies in monitored channels | Bot token |
| GitHub (REST) | Open issues / PRs in configured repos with recent activity | PAT (`repo` read) |
| GitHub (`gh` CLI) | Review requests + @mentions across *all* repos | `gh auth login` |
| Reddit | Posts in configured subreddits, optional keyword filter | None (public JSON) |

Every source is optional. Leave its config section empty (or omit it) and
that source is skipped.

## Quickstart with Claude Code

If you have [Claude Code](https://claude.com/claude-code), the fastest path
is to clone the repo, open it in Claude Code, and run:

```
/setup
```

The setup flow walks you through Python deps, creating the tokens for each
service (Notion, Anthropic, Discord, GitHub, Reddit) with the right URLs,
and populating `.env` and `config.yaml` for you. When it's done, run
`/verify` to confirm every token works.

If you'd rather set things up by hand, the manual instructions follow below.

## Requirements

- **Python 3.10+**
- **Notion** account (output destination)
- **Anthropic API key** (or any OpenAI-compatible local LLM endpoint)
- Optional: **`gh` CLI** authenticated, for cross-repo review requests / mentions
- Optional: **Claude Code CLI** (`claude`) on `PATH`, only if you want
  suggested-response drafting for ACT items via local repo clones

## Install

```bash
git clone https://github.com/<your-fork>/actboard.git
cd actboard
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r triage/requirements.txt
```

## Configure

### 1. Create a `.env`

```bash
cp triage/.env.example triage/.env
```

Then fill in only the keys for the sources you'll use:

```
DISCORD_BOT_TOKEN=          # Discord
GITHUB_TOKEN=               # GitHub REST
ANTHROPIC_API_KEY=          # Inference (skip if backend: local)
NOTION_API_KEY=             # Required — output destination
```

### 2. Create your `config.yaml`

```bash
cp triage/config.example.yaml triage/config.yaml
```

`config.yaml` is gitignored. Open it and fill in the sections for the sources
you want — leave others blank or remove them.

The most important field is `user.persona`: a short free-text description of
who you are and what you maintain. The LLM uses it to decide what counts as
ACT for *you*. Example:

```yaml
user:
  persona: |
    You are a daily triage assistant for Jane Doe (GitHub: @janedoe), an
    SRE on the platform team at Example Corp. She owns the deployment
    pipeline and the on-call rotation for Tier-1 services.
```

### 3. Set up each source

<details>
<summary><b>Discord</b></summary>

1. Create a bot at <https://discord.com/developers/applications>.
2. Enable the **Message Content Intent** under *Bot → Privileged Gateway Intents*.
3. Invite the bot to your server with the `bot` scope and these permissions:
   `Read Messages/View Channels`, `Read Message History`.
4. Enable **Developer Mode** in Discord (Settings → Advanced), right-click the
   server icon → **Copy ID**. That's your `discord.guild_id`.
5. Put the bot token in `.env` as `DISCORD_BOT_TOKEN`.
</details>

<details>
<summary><b>GitHub</b></summary>

1. Create a fine-grained personal access token at
   <https://github.com/settings/tokens?type=beta> with **read** access to
   the repos you want to triage.
2. Put it in `.env` as `GITHUB_TOKEN`.
3. (Optional) Install and authenticate the `gh` CLI for the bonus
   review-requests + mentions sweep across *all* repos:
   ```bash
   gh auth login
   ```
</details>

<details>
<summary><b>Notion</b></summary>

1. Create an internal integration at <https://www.notion.so/my-integrations>
   and copy the secret — that's your `NOTION_API_KEY`.
2. In Notion, open the page where you want daily reports to live, click
   **... → Connections → Add connection** and pick your integration.
3. Copy the page ID from the URL: the 32-char string after the last slash
   and before the `?`. That's your `notion.parent_page_id`.
</details>

<details>
<summary><b>Reddit</b></summary>

No auth required — uses the public JSON feeds. Just list subreddits and
optional keyword filters in `config.yaml`. Posts are rate-limited politely.
</details>

<details>
<summary><b>Anthropic vs local LLM</b></summary>

Default is Claude via the Anthropic API. To use a local model instead, in
`config.yaml`:

```yaml
inference:
  backend: local
  base_url: http://localhost:8000
  model: your-model-name
```

Any OpenAI-compatible `/v1/chat/completions` endpoint works
(llama.cpp server, vLLM, LM Studio, etc.). The analyzer reduces concurrency
to 1 and trims prompts more aggressively for local backends.
</details>

## Run

```bash
cd triage
python main.py            # plain output
python dashboard.py       # live Rich dashboard
```

On success it prints the URL of the daily Notion page. If Notion writes fail,
the raw triage JSON is dropped at `triage_output_<date>.json` so you don't
lose the analysis.

## Schedule

There's no built-in scheduler. Run it from cron / Task Scheduler / launchd —
e.g. on Windows:

```
schtasks /create /sc daily /tn ActBoard /tr "C:\path\to\.venv\Scripts\python.exe C:\path\to\actboard\triage\main.py" /st 08:00
```

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `Missing API keys` on startup | `.env` not in `triage/` or required key blank for the configured sources |
| Discord channels return 0 messages | Bot lacks `View Channel` permission, or **Message Content Intent** not enabled |
| `gh search prs` fails | `gh` CLI not installed or not logged in — that source is then skipped silently |
| Notion `validation_error` | Integration not connected to the parent page, or `parent_page_id` is wrong |
| GitHub rate-limit exit | Use a token (or a different one) — unauthenticated quota is tiny |

## Project layout

```
.
├── LICENSE
├── README.md
├── CLAUDE.md                    # Claude Code project context
├── .claude/
│   └── commands/                # /setup, /setup-*, /verify slash commands
└── triage/
    ├── .env.example
    ├── config.example.yaml
    ├── main.py                  # orchestrator
    ├── dashboard.py             # rich live dashboard
    ├── analyzer.py              # parallel sub-agents (Claude / OpenAI-compatible)
    ├── responder.py             # optional Claude Code reply drafting
    ├── notion_writer.py         # per-source databases under a daily page
    ├── pipeline_events.py
    ├── requirements.txt
    └── fetchers/
        ├── discord_fetcher.py
        ├── github_fetcher.py    # REST API
        ├── gh_fetcher.py        # gh CLI bonuses
        └── reddit_fetcher.py
```

## Contributing

Issues and PRs welcome. Keep the scope tight — this is meant to stay a small,
single-purpose tool.

## License

[Apache-2.0](LICENSE).
