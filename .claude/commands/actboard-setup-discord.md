---
description: Create a Discord bot, invite it to a server, and configure actboard
---

Walk the user through Discord setup. Skip this command if they don't use
Discord.

## Step 1 — create the bot

Tell the user to:
1. Open <https://discord.com/developers/applications>.
2. Click **New Application**, name it (e.g. "actboard"), Create.
3. Left sidebar → **Bot**. Add Bot if prompted.
4. Under **Privileged Gateway Intents**, enable **Message Content
   Intent**. Save.
5. Click **Reset Token** to reveal the bot token. Copy it.

## Step 2 — save token

Ask the user to paste the token. (Discord bot tokens have no consistent
prefix; trust the paste.) Write to `triage/.env` by replacing the
`DISCORD_BOT_TOKEN=` line. Confirm "saved" without echoing.

## Step 3 — invite bot to server

Tell the user to:
1. Left sidebar → **OAuth2** → **URL Generator**.
2. **Scopes**: check `bot`.
3. **Bot Permissions**: check `Read Messages/View Channels` and
   `Read Message History`.
4. Copy the URL at the bottom. Open it in a browser, pick the server,
   authorize.

## Step 4 — guild ID and username

1. Have the user enable Developer Mode: User Settings → Advanced →
   Developer Mode (on).
2. Right-click the server icon → **Copy Server ID**. Write to
   `triage/config.yaml` as `discord.guild_id` (keep it as a string).
3. Ask for their Discord username (the @handle). Write to
   `triage/config.yaml` as `user.discord_username`.

## Step 5 — channels

Ask whether to monitor all channels or specific ones:
- **All channels**: leave `discord.monitor: all` (the default). Optionally
  ask for channels to skip — by name substring (e.g. "welcome") or ID —
  and update `discord.exclude_channels`.
- **Specific channels only**: ask for channel IDs (right-click each
  channel → Copy Channel ID) and write them as a YAML list at
  `discord.monitor`.

## Step 6 — verify

`GET https://discord.com/api/v10/guilds/<guild_id>` with header
`Authorization: Bot <token>`. 200 → bot has access. 401 → bad token, ask
to re-paste. 403 → bot wasn't invited to the server (back to step 3).
