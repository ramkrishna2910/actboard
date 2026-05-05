---
description: Create a GitHub PAT and configure repos to triage
---

Walk the user through GitHub setup. Skip this command if they don't want
GitHub triage.

## Step 1 — create a fine-grained PAT

Tell the user to:
1. Open <https://github.com/settings/personal-access-tokens/new>.
2. Token name: anything (e.g. "actboard"). Pick a reasonable expiration
   (90 days or longer).
3. **Repository access**: select the specific repos they want to triage
   (or all repos if they prefer broad access).
4. **Permissions → Repository permissions**:
   - Contents: **Read-only**
   - Issues: **Read-only**
   - Pull requests: **Read-only**
   - Metadata: Read-only (added automatically)
5. Generate the token and copy it. Fine-grained tokens start with
   `github_pat_` (older classic tokens start with `ghp_`).

## Step 2 — save token

Validate the prefix (`github_pat_` or `ghp_`). Write to `triage/.env` by
replacing the `GITHUB_TOKEN=` line.

## Step 3 — username

Ask for the user's GitHub @handle. Write to `triage/config.yaml` as
`user.github_username`.

## Step 4 — configure repos

For each repo the user wants triaged, ask:
- `owner` (e.g. `your-org`)
- `repo` (the repo name)
- `name` — display name shown in the Notion output (defaults to the repo
  name)
- `icon` — emoji (optional, e.g. `📦`)
- `fetch` — `all`, `prs_only`, or `issues_only`
- `include_drafts` — true/false (PRs only)
- `repo_path` — optional absolute path to a local clone of this repo. If
  set, two things happen: `main.py` runs `git pull --ff-only` here before
  triaging, and `responder.py` uses Claude Code in this directory to
  draft suggested replies for ACT items. Leave blank if you don't have a
  clone. Validate the path exists and contains `.git/` before saving.
- `prompt` — a short free-text description of what counts as ACT vs
  MONITOR vs HANDLED for *this* repo. Examples:
  - "ACT: PRs where I'm requested as reviewer or where I requested
    changes. Bug reports with no response from me. MONITOR: other open
    issues. HANDLED: merged or closed."

Append each repo as a list entry under `github.repos` in
`triage/config.yaml`. **Remove the placeholder example entry** if the
user kept it from `config.example.yaml`.

## Step 5 — optional gh CLI

Ask if the user has `gh` CLI installed and authenticated. Check with
`gh auth status`. If yes, the pipeline will automatically pick up
review-requests and @mentions across *all* their repos (not just the
ones in `github.repos`) — no extra config. If they want this and don't
have it, point them at <https://cli.github.com/>.

## Step 6 — verify

1. `GET https://api.github.com/user` with `Authorization: token <token>`.
   200 = token works. 401 = bad token.
2. For each configured repo, `GET https://api.github.com/repos/<owner>/<repo>`.
   200 = readable. 404 = the PAT doesn't have access to that repo (back
   to step 1's repo selection).
