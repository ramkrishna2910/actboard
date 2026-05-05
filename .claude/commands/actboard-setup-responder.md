---
description: Configure suggested-reply drafting for ACT items via Claude Code
---

The responder feature drafts a suggested reply for each ACT item by
spawning `claude -p` inside a local clone of the repo the item came from
and letting Claude search the codebase before answering. It's optional —
skip it and the pipeline still works, just without auto-drafted replies.

## Step 1 — smoke test

Run `claude --version`. If it exits 0, you're done with auth — the user
is running this from inside Claude Code so the binary on PATH inherits
authentication. If it fails (rare — usually a stale or duplicated
install on PATH), tell the user the responder will silently skip until
they fix it. Don't block setup.

Note: if the user is using `lemonade launch claude`, that wrapper hands
the same authenticated `claude` binary back, so the smoke test still
passes — they just get Lemonade-served replies instead of Claude ones.

## Step 2 — repo paths

Read `triage/config.yaml`. For each entry in `github.repos`:

1. Show the repo as `<owner>/<repo>`.
2. Ask if the user has a local clone of it. If no → skip to next repo.
3. If yes → ask for the absolute path. Validate that the path exists and
   contains a `.git/` directory. Re-prompt on failure.
4. Write the path back as `repo_path` under that repo's entry in
   `triage/config.yaml`. Don't disturb other fields.

If no repo ends up with a `repo_path`, the responder will print
"No ACT items with available repos to respond to" each run and skip
silently. That's fine — no error.

## Step 3 — Discord fallback

`responder.py` uses the *first* repo with `fetch: all` AND a `repo_path`
as the default codebase for Discord ACT items. If the user has multiple
`fetch: all` repos with paths, mention which one will be picked (the
first in `github.repos` order) and ask if they want to reorder so the
right one is first. No code change — just edit the order of entries.

## Step 4 — confirm

Tell the user: "Responder is configured. After your next pipeline run,
ACT items in repos with `repo_path` set will get a 'Suggested Response'
field populated in their Notion entry, drafted by Claude after searching
the local clone."
