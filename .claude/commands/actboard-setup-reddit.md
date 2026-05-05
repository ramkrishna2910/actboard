---
description: Configure subreddits to monitor (no auth required)
---

Reddit uses public JSON feeds — no API token needed. This command just
collects which subreddits to scan.

## Step 1 — pick subreddits

Ask which subreddits the user wants monitored. For each, collect:
- `name` — without the `r/` prefix (e.g. `LocalLLaMA`)
- `icon` — emoji, optional (default `📢`)
- `keywords` — list of substrings; if non-empty, only posts whose title /
  body / flair contain at least one keyword survive. Empty = keep all.
- `prompt` — short free-text description of ACT/MONITOR/HANDLED for this
  subreddit. Optional but strongly recommended.
- `hide_handled` — true/false (default true; HANDLED items aren't shown
  in the Notion output)

## Step 2 — write config

Append each subreddit as a list entry under `reddit.subreddits` in
`triage/config.yaml`.

If the user wants a single keyword filter applied across **all**
subreddits (in addition to per-subreddit filters), ask for the list and
write it to `reddit.keywords`.

Set `reddit.lookback_hours` (default 24) if they want a different window.

## Step 3 — verify

`GET https://www.reddit.com/r/<one-of-their-subs>/new.json?limit=1` with
header `User-Agent: actboard-verify`. 200 confirms Reddit is reachable
and the subreddit name is valid. 404 means the subreddit name is wrong
or private.
