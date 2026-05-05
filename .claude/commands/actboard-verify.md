---
description: Sanity-check every configured token + service against its API
---

Read `triage/.env` and `triage/config.yaml`, then ping every service the
user has configured. Report a one-line `[ok]` or `[fail]` per check.

**Never print secrets.** When reporting failures, name the service and
the HTTP status, not the token.

## Checks

Run only those whose credentials are present.

### Notion (required)
- Need: `NOTION_API_KEY`, `notion.parent_page_id`.
- `GET https://api.notion.com/v1/users/me` with
  `Authorization: Bearer <key>` and `Notion-Version: 2022-06-28`.
  200 → token ok.
- `GET https://api.notion.com/v1/pages/<parent_page_id>` with same
  headers. 200 → integration has access to the page. 404 → integration
  not connected to the page (run `/actboard-setup-notion` step 3 again).

### Inference (required)
- If `inference.backend == "claude"` (or unset) and `ANTHROPIC_API_KEY`
  is set: `POST https://api.anthropic.com/v1/messages` with body
  `{"model":"claude-haiku-4-5","max_tokens":5,"messages":[{"role":"user","content":"ping"}]}`.
  Headers: `x-api-key`, `anthropic-version: 2023-06-01`,
  `content-type: application/json`. 200 = working.
- If `inference.backend == "local"`: `GET <base_url>/v1/models`. 200 =
  reachable. Connection refused = endpoint not running.

### Discord (optional)
- Need: `DISCORD_BOT_TOKEN`, `discord.guild_id`.
- `GET https://discord.com/api/v10/guilds/<guild_id>` with
  `Authorization: Bot <token>`. 200 = bot is in the server.

### GitHub (optional)
- Need: `GITHUB_TOKEN`.
- `GET https://api.github.com/user` with `Authorization: token <token>`.
  200 = token ok.
- For each entry in `github.repos`:
  `GET https://api.github.com/repos/<owner>/<repo>`. 200 = readable.

### gh CLI (optional)
- If the `gh` binary is on PATH, run `gh auth status`. Print whatever
  state it reports.

### Reddit (optional)
- For the first subreddit in `reddit.subreddits`:
  `GET https://www.reddit.com/r/<name>/new.json?limit=1` with
  `User-Agent: actboard-verify`. 200 = subreddit reachable.

## Output

After all checks, print a summary:

```
[ok] Notion
[ok] Notion parent page
[ok] Anthropic
[fail] Discord: 401 (bad token — re-run /actboard-setup-discord)
[ok] GitHub
[ok] GitHub repo your-org/your-repo
[ok] Reddit r/LocalLLaMA

6/7 checks passed
```

If any check fails, suggest the matching `/actboard-setup-*` command to fix it.
