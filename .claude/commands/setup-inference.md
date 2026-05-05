---
description: Choose Anthropic API or a local OpenAI-compatible LLM endpoint
---

Ask the user which inference backend they want.

## Option A — Anthropic API (default, recommended)

1. Send them to <https://console.anthropic.com/settings/keys>.
2. Click **Create Key**, give it a name, copy the value.
3. The key starts with `sk-ant-`. If the prefix doesn't match, warn and
   ask to re-paste.
4. Write the value to `triage/.env` by replacing the line starting with
   `ANTHROPIC_API_KEY=`.
5. Confirm `inference.backend: claude` in `triage/config.yaml` (this is
   the default; only change if they previously picked local).

Verify with a tiny `messages.create` call:

```
POST https://api.anthropic.com/v1/messages
Headers: x-api-key: <key>, anthropic-version: 2023-06-01, content-type: application/json
Body: {"model": "claude-haiku-4-5", "max_tokens": 5, "messages": [{"role": "user", "content": "ping"}]}
```

200 → working. 401 → bad key, re-paste. 400 with "model not found" → try
`claude-sonnet-4-20250514` instead (the default in `analyzer.py`).

## Option B — local OpenAI-compatible endpoint

For users running llama.cpp server, vLLM, LM Studio, etc.

1. Ask for the base URL (e.g. `http://localhost:8000`) and the model name.
2. Update `triage/config.yaml`:
   - `inference.backend: local`
   - `inference.base_url: <url>`
   - `inference.model: <model-name>`
3. Verify with `GET <base_url>/v1/models`. If that fails, try a tiny
   `POST <base_url>/v1/chat/completions` with `{"model": "<name>",
   "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5}`.
4. Note for the user: the analyzer drops concurrency to 1 and trims
   prompts harder for local backends. Expect runs to take longer.
