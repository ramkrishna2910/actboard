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

## Step 2 — repo clones

The responder needs each repo cloned locally. This step makes sure every
tracked repo has a clone and writes the absolute path back into
`triage/config.yaml` as `repo_path`.

Verify `git --version` works first. If it doesn't, stop and tell the
user to install Git from <https://git-scm.com/downloads>.

Pick a default base directory once, up front, where missing clones will
land. Suggest `~/actboard-repos/` (resolve `~` to the user's home — e.g.
`C:\Users\<name>\actboard-repos` on Windows, `/Users/<name>/actboard-repos`
on macOS, `/home/<name>/actboard-repos` on Linux). Let the user override
with any absolute path. Create the directory if it doesn't exist
(`mkdir -p`).

Then read `triage/config.yaml`. For each entry in `github.repos`:

1. Show the repo as `<owner>/<repo>`.
2. If the entry already has a `repo_path` and that path exists with a
   `.git/` dir, say "already cloned at `<path>`" and skip to the next
   repo.
3. Otherwise, ask: "Clone <owner>/<repo>? [Y/n/path]"
   - **Y (default)**: clone into `<base>/<repo>` (using the default
     base from above). Run `git clone https://github.com/<owner>/<repo>
     <path>`. If the destination already exists and isn't empty,
     warn — don't clobber. On clone failure (private repo, network,
     etc.), surface the git error and ask the user to fix and re-run.
   - **path**: the user supplies an absolute path. If it exists with a
     `.git/`, just record it. If it doesn't exist, run the same
     `git clone` into that path.
   - **n**: skip — leave `repo_path` unset for this repo. The
     responder will silently skip it on every run.
4. After clone (or path confirm), validate `<path>/.git/` exists.
5. Write `repo_path: <absolute-path>` under that repo's entry in
   `triage/config.yaml`. Don't disturb other fields.

After the loop, summarize: which repos got new clones, which used
existing clones, which were skipped.

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
