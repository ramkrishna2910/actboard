---
description: First-time setup — installs deps, walks through tokens for each service, populates .env and config.yaml
---

You are guiding a brand-new user through setting up actboard end-to-end.
They have just cloned the repo for the first time and may know nothing
about the project. Be friendly, concrete, and tight — one or two sentences
per step. Wait for the user's actual answer before assuming.

Throughout:
- Never echo a pasted secret back to the user. Confirm "saved" without
  printing the value.
- Edit `triage/.env` by replacing the value on the matching `KEY=` line —
  don't reformat the file or add new keys.
- When editing `triage/config.yaml`, preserve the user's existing values
  and only fill in fields they're configuring right now.
- If a pasted secret doesn't match the expected prefix (e.g. Anthropic
  keys start with `sk-ant-`), warn and ask to re-paste before saving.

## Step 0 — detect environment

Detect the OS (Windows / macOS / Linux) and confirm Python 3.10+ is
available. Try `python --version`; fall back to `python3` if needed. If
Python is missing or older than 3.10, stop and tell the user to install
Python from <https://www.python.org/downloads/> first.

## Step 1 — install dependencies

1. If `.venv/` doesn't exist, create it: `python -m venv .venv`.
2. Show the user the activation command for their shell:
   - Windows PowerShell: `.\.venv\Scripts\Activate.ps1`
   - Windows cmd: `.venv\Scripts\activate.bat`
   - macOS/Linux: `source .venv/bin/activate`
3. Install requirements: `pip install -r triage/requirements.txt`. Run it
   directly via Bash using the venv's pip path (`.venv/Scripts/pip` on
   Windows, `.venv/bin/pip` on Unix). If that fails, ask the user to
   activate the venv themselves and run the command.

## Step 2 — bootstrap config files

If `triage/.env` doesn't exist, copy `triage/.env.example` to it. If
`triage/config.yaml` doesn't exist, copy `triage/config.example.yaml` to
it. Don't overwrite existing files.

## Step 3 — persona

Ask the user to describe themselves in 2–3 sentences: their role, what
project or area they own, and who they interact with. Write the answer
into `triage/config.yaml` at `user.persona`. The analyzer uses this to
decide what counts as ACT for *this* user.

## Step 4 — required services

Run these in order. For each, read the matching command file under
`.claude/commands/` and execute its instructions inline (do NOT ask the
user to type the slash command themselves):

1. `setup-notion.md` — output destination, required.
2. `setup-inference.md` — Anthropic API or local LLM, required.

## Step 5 — optional sources

Ask the user which of the following they want to enable. For each "yes",
read and execute the matching command file:

- Discord — `setup-discord.md`
- GitHub — `setup-github.md`
- Reddit — `setup-reddit.md`

If the user skips a source, leave its config section as-is (blank/empty
in the example). The pipeline silently skips sources with empty config.

## Step 6 — verify

Read and execute `.claude/commands/verify.md` to ping each configured
service. Report green/red per source. If any fail, suggest the matching
`setup-*` command to fix it.

## Step 7 — first run

When verification is clean, tell the user:

```
cd triage
python main.py
```

Or `python dashboard.py` for the live Rich dashboard. Mention that the
first run takes longer (LLM calls per channel/repo/subreddit) and that
Notion will get a new daily page on success. Point them at the README's
"Schedule" section for cron / Task Scheduler setup.
