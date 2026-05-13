# actboard

Daily triage assistant: pulls activity from Discord, GitHub, Reddit, and
Juejin (掘金), runs it through an LLM, publishes a prioritized
ACT/MONITOR/HANDLED report to Notion. See `README.md` for user-facing docs.

## First-time setup

If `triage/.env` or `triage/config.yaml` doesn't exist, the user is brand
new — point them at `/actboard-setup` rather than improvising.

Slash commands (in `.claude/commands/`):

- `/actboard-setup` — full first-time flow: deps, tokens, config, verify.
- `/actboard-setup-notion` — required output destination.
- `/actboard-setup-inference` — Anthropic API or local OpenAI-compatible LLM.
- `/actboard-setup-discord`, `/actboard-setup-github`,
  `/actboard-setup-reddit` — optional sources. Juejin (掘金) is also
  supported via the `juejin:` section of `config.yaml` (public recommend
  feed, no auth); there's no dedicated setup command yet.
- `/actboard-setup-responder` — optional Claude Code reply drafting for
  ACT items.
- `/actboard-verify` — sanity-check every configured token against its API.

For incremental changes (e.g. adding another repo to triage), invoke the
matching `/actboard-setup-*` command instead of re-running the full
`/actboard-setup`.

## Working in this repo

- All runtime code is under `triage/`. Run with `cd triage && python main.py`
  (or `python dashboard.py` for the live Rich view).
- `triage/.env` and `triage/config.yaml` are gitignored — treat them as
  containing secrets and personal config. Never commit them.
- `triage/.env.example` and `triage/config.example.yaml` are the public
  templates. Keep them in sync whenever you change the env vars or config
  schema.
- No automated tests. Verify changes by running `main.py` end-to-end. If
  Notion publishing fails, the pipeline falls back to writing
  `triage_output_<date>.json`.
- Keep scope tight. This is a small single-purpose tool, not a platform.

## Architecture

`main.py` orchestrates:
1. **Fetchers** (`triage/fetchers/`) — one per source. Each returns empty if
   its config section is missing/blank, so sources are independently
   optional.
2. **Analyzer** (`analyzer.py`) — fans out to one LLM sub-agent per channel,
   per repo, per subreddit, per Juejin category, plus one for `gh` extras.
   Concurrency is 10 for Claude, 1 for local LLMs.
3. **Responder** (`responder.py`, optional) — for ACT items in repos where
   the user set `repo_path`, shells out to the `claude` CLI to draft a
   suggested reply using the local clone.
4. **Notion writer** (`notion_writer.py`) — finds/creates a daily page,
   then one database per source under it. Dedups by item link.

The persona that shapes the LLM's judgment is read from
`config.user.persona` — never hardcode it back into the analyzer.
