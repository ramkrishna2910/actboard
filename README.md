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

## Setup

The recommended path is [Claude Code](https://claude.com/claude-code).
Clone the repo, open it in Claude Code, and run:

```
/actboard-setup
```

That walks you through Python deps, creates the tokens for each service
(Notion, Anthropic, Discord, GitHub, Reddit) with the right URLs, and
fills in `.env` and `config.yaml` for you. When it's done, run
`/actboard-verify` to confirm every token works.

For incremental changes later — adding a repo, swapping inference
backend, configuring a new subreddit — use the focused commands instead
of redoing the full setup:

| Command | What it does |
| --- | --- |
| `/actboard-setup-notion` | Notion integration + parent page (required) |
| `/actboard-setup-inference` | Anthropic API or local OpenAI-compatible LLM |
| `/actboard-setup-discord` | Discord bot, server invite, channel filters |
| `/actboard-setup-github` | GitHub PAT and repo list |
| `/actboard-setup-reddit` | Subreddit list + keyword filters |
| `/actboard-verify` | Ping every configured service |

### Without Claude Code

The manual path is just:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r triage/requirements.txt
cp triage/.env.example triage/.env
cp triage/config.example.yaml triage/config.yaml
```

Then fill in `triage/.env` with tokens and edit `triage/config.yaml` to
describe yourself (`user.persona`) and the sources you want. Each
slash-command markdown file in `.claude/commands/` is a self-contained
walkthrough of one service — the URLs, exact click paths, and required
permissions are there. Read `actboard-setup-notion.md`,
`actboard-setup-discord.md`, etc. as if they were the docs.

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
│   └── commands/                # /actboard-setup, /actboard-setup-*, /actboard-verify
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
