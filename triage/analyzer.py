"""Claude API analysis — sends fetched data and returns categorized triage JSON."""

import json
import re
import sys
import time

import anthropic

MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 8192

SYSTEM_PROMPT_TEMPLATE = """\
You are a daily triage assistant for a Principal ML Software Engineer and open-source project maintainer.

The user is Krishna (Discord: ramkrishna2910, GitHub: ramkrishna2910). He maintains the Lemonade SDK, an open-source local LLM server for AMD Ryzen AI hardware. The project includes integrations with llama.cpp, ONNX Runtime GenAI, and supports multimodal inference (image generation, speech-to-text). Krishna is also active in the ONNX community. He interacts with external contributors, AMD stakeholders, and community members daily.

Your job is to review the Discord messages and GitHub items provided and produce a prioritized triage report in structured JSON.

TRIAGE RULES:

Discord:
- If Krishna has already replied to a thread, mark it as HANDLED unless the thread has continued with new unanswered questions after his reply
- If a question or bug report was adequately answered by another community member, mark it as HANDLED (Krishna does not need to act on every question)
- If a question, bug report, or request has NO adequate response from anyone (Krishna or community), mark it as ACT
- If a long discussion ended without resolution or has unresolved open questions, mark it as ACT
- If a question was partially answered but still has unresolved aspects, mark it as ACT
- If a message is informational (announcement, FYI, resolved discussion), mark it as MONITOR
- Summarize the thread in 1-2 sentences. Do not quote messages verbatim
- Always include the message link

GitHub:
- Issues/PRs updated in the last {lookback_hours} hours are RECENT — prioritize these
- Issues: only mark as ACT if they have significant community engagement (many upvotes/reactions or many comments). Low-activity issues should be MONITOR
- If Krishna has already commented or reviewed and the thread is quiet, mark as HANDLED
- PRs where Krishna is listed as a requested reviewer (review_requested=true): mark as ACT (he has been explicitly asked to review)
- PRs where Krishna previously requested changes AND the author has pushed new commits or resolved comments since his review: mark as ACT (author is waiting for Krishna to re-review)
- PRs where Krishna has not reviewed at all and is not a requested reviewer: mark as MONITOR
- Draft PRs: always mark as MONITOR regardless of Krishna's involvement
- If a PR has been approved or merged by others with no action needed from Krishna, mark as HANDLED
- Summarize each item in 1-2 sentences

OUTPUT FORMAT:
Return only valid JSON with this structure:
{{
  "generated_at": "ISO timestamp",
  "discord": {{
    "act": [TriageItem],
    "monitor": [TriageItem],
    "handled": [TriageItem]
  }},
  "github": {{
    "act": [TriageItem],
    "monitor": [TriageItem],
    "handled": [TriageItem]
  }}
}}

Where TriageItem is:
{{
  "summary": str,         // 1-2 sentence description of what needs attention
  "reason": str,          // Why this was categorized this way (1 sentence)
  "link": str,            // Clickable URL to the message or issue
  "label": str,           // Short tag e.g. "bug", "question", "PR review", "discussion", "draft PR"
  "is_recent": bool       // True if activity is within the last {lookback_hours} hours
}}

Be concise. Do not add commentary outside the JSON. Do not invent information not present in the input."""


def _truncate(text: str, max_len: int = 500) -> str:
    """Truncate text to max_len characters."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _trim_replies(replies: list[dict], max_count: int = 20) -> list:
    """If more than max_count replies, keep first 5 + last 5 with omission note."""
    if len(replies) <= max_count:
        return replies
    omitted = len(replies) - 10
    return (
        replies[:5]
        + [{"_note": f"[... {omitted} messages omitted ...]"}]
        + replies[-5:]
    )


def _prepare_input(discord_data: list[dict], github_data: list[dict]) -> str:
    """Build the user message with truncated data."""
    # Truncate discord message content and replies
    discord_trimmed = []
    for msg in discord_data:
        trimmed = {**msg}
        trimmed["content"] = _truncate(trimmed.get("content", ""))
        trimmed["replies"] = _trim_replies([
            {**r, "content": _truncate(r.get("content", ""))}
            for r in trimmed.get("replies", [])
        ])
        discord_trimmed.append(trimmed)

    # Truncate github bodies and comments
    github_trimmed = []
    for item in github_data:
        trimmed = {**item}
        trimmed["body"] = _truncate(trimmed.get("body", ""))
        trimmed["comments"] = [
            {**c, "body": _truncate(c.get("body", ""))}
            for c in trimmed.get("comments", [])
        ]
        trimmed["reviews"] = [
            {**r, "body": _truncate(r.get("body", ""))}
            for r in trimmed.get("reviews", [])
        ]
        github_trimmed.append(trimmed)

    return json.dumps(
        {"discord_messages": discord_trimmed, "github_items": github_trimmed},
        indent=2,
        default=str,
    )


def _parse_response(text: str) -> dict:
    """Parse JSON from Claude's response, with fallback regex extraction."""
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting JSON block from markdown fences
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding the outermost { ... }
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    print("Error: Could not parse Claude API response as JSON.", file=sys.stderr)
    print(f"Raw response:\n{text[:500]}", file=sys.stderr)
    sys.exit(1)


def analyze(discord_data: list[dict], github_data: list[dict], config: dict) -> dict:
    """
    Send data to Claude API for triage analysis. Returns parsed JSON result.
    Retries once on failure with 5s delay.
    """
    api_key = config.get("env", {}).get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    user_message = _prepare_input(discord_data, github_data)
    lookback = max(
        config.get("discord", {}).get("lookback_hours", 24),
        config.get("github", {}).get("lookback_hours", 24),
    )
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(lookback_hours=lookback)

    # Rough token estimate: ~4 chars per token
    est_input_tokens = (len(system_prompt) + len(user_message)) // 4
    print(f"  System prompt: ~{len(system_prompt) // 4:,} tokens")
    print(f"  User message: ~{len(user_message) // 4:,} tokens ({len(user_message):,} chars)")
    print(f"  Total input: ~{est_input_tokens:,} tokens (estimated)")

    for attempt in range(2):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            usage = response.usage
            print(f"  Actual usage: {usage.input_tokens:,} input + {usage.output_tokens:,} output = {usage.input_tokens + usage.output_tokens:,} total tokens")
            text = response.content[0].text
            return _parse_response(text)
        except Exception as e:
            if attempt == 0:
                print(f"[analyzer] Claude API error: {e}. Retrying in 5s...", file=sys.stderr)
                time.sleep(5)
            else:
                print(f"Error: Claude API failed after retry: {e}", file=sys.stderr)
                sys.exit(1)
