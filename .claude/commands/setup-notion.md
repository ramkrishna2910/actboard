---
description: Create a Notion integration, connect it to a parent page, save credentials
---

Walk the user through setting up Notion as the output destination for
actboard. Notion is required.

## Step 1 — create the integration

Tell the user to:
1. Open <https://www.notion.so/profile/integrations>
2. Click **+ New integration**.
3. Pick any name (e.g. "actboard"), associate it with their workspace,
   choose **Internal** type.
4. Under Capabilities, enable: Read content, Update content, Insert
   content. (Read user info / read comments are optional.)
5. Submit, then under **Internal Integration Secret** click Show → Copy.

## Step 2 — save the secret

Ask the user to paste the integration secret. It should start with `ntn_`
(or older `secret_`). If the prefix doesn't match, ask to re-paste.

Write the value into `triage/.env` by replacing the line that starts with
`NOTION_API_KEY=`. Don't print it back to the user — just confirm "saved".

## Step 3 — create or pick the parent page

Tell the user to:
1. Open the Notion page that should contain the daily triage reports
   (or create a new one — e.g. "ActBoard"). Daily pages will be created
   as children under it.
2. On that page, click the `⋯` menu (top right) → **Connections** →
   **+ Add connections** → search and select their integration.
3. Confirm the integration now appears under Connections on that page.

## Step 4 — extract the page ID

Ask for the page URL. Notion URLs look like:

```
https://www.notion.so/<workspace>/<title>-<32-char-id>?<query>
```

Extract the 32-character hex ID (the last `-`-separated segment of the
path before any `?`). Write it into `triage/config.yaml` at
`notion.parent_page_id`.

## Step 5 — verify

Make a `GET https://api.notion.com/v1/users/me` request with headers:
- `Authorization: Bearer <secret>`
- `Notion-Version: 2022-06-28`

If 200 → tell the user "Notion is set up." If 401 → the secret is wrong;
ask to re-paste.

Then `GET https://api.notion.com/v1/pages/<parent_page_id>` with the same
headers. 200 confirms the integration has access to the parent page. 404
means the integration wasn't connected to the page in step 3 — send them
back to that step.
