"""Claude API analysis — parallel sub-agents per Discord channel and GitHub type."""

import json
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import anthropic

MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 4096

# --- Shared context preamble (included in every sub-agent call) ---

CONTEXT = """\
You are a daily triage assistant for Krishna (Discord: ramkrishna2910, GitHub: ramkrishna2910), \
a Principal ML Software Engineer who maintains the Lemonade SDK — an open-source local LLM server \
for AMD Ryzen AI hardware (llama.cpp, ONNX Runtime GenAI, multimodal inference). \
He interacts with external contributors, AMD stakeholders, and community members daily."""

# --- Per-source system prompts ---

DISCORD_PROMPT_TEMPLATE = CONTEXT + """

Review the Discord messages from the #{channel_name} channel and categorize each into ACT, MONITOR, or HANDLED.

RULES:
- HANDLED: Krishna already replied, OR another community member adequately answered the question/bug
- ACT: A question, bug report, or request with NO adequate response from anyone. Also: long discussions left unresolved
- MONITOR: Informational messages, announcements, FYI, resolved discussions
- Summarize each thread in 1-2 sentences. Do not quote verbatim. Always include the message link.

Return only valid JSON:
{{"act": [TriageItem], "monitor": [TriageItem], "handled": [TriageItem]}}

TriageItem = {{"summary": str, "reason": str, "link": str, "label": str, "is_recent": bool}}"""

GITHUB_ISSUES_PROMPT_TEMPLATE = CONTEXT + """

Review these GitHub issues and categorize each into ACT, MONITOR, or HANDLED.

RULES:
- ACT: Issues with significant community engagement (many reactions/upvotes or many comments) that need Krishna's attention
- MONITOR: Low-activity issues, informational updates
- HANDLED: Krishna has already commented and the thread is quiet, or the issue is resolved
- Issues updated in the last {lookback_hours} hours are RECENT (is_recent=true)
- Summarize each in 1-2 sentences

Return only valid JSON:
{{"act": [TriageItem], "monitor": [TriageItem], "handled": [TriageItem]}}

TriageItem = {{"summary": str, "reason": str, "link": str, "label": str, "is_recent": bool}}"""

GITHUB_PRS_PROMPT_TEMPLATE = CONTEXT + """

Review these GitHub pull requests and categorize each into ACT, MONITOR, or HANDLED.

RULES:
- ACT: PRs where Krishna is a requested reviewer (review_requested=true). PRs where Krishna previously requested changes and the author has responded since (i_requested_changes=true + new activity)
- MONITOR: PRs Krishna has not reviewed and is not a requested reviewer. Draft PRs (always MONITOR)
- HANDLED: PRs Krishna already reviewed and the thread is quiet. PRs approved/merged by others with no action needed
- PRs updated in the last {lookback_hours} hours are RECENT (is_recent=true)
- Summarize each in 1-2 sentences

Return only valid JSON:
{{"act": [TriageItem], "monitor": [TriageItem], "handled": [TriageItem]}}

TriageItem = {{"summary": str, "reason": str, "link": str, "label": str, "is_recent": bool}}"""


def _truncate(text: str, max_len: int = 500) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _trim_replies(replies: list[dict], max_count: int = 20) -> list:
    if len(replies) <= max_count:
        return replies
    omitted = len(replies) - 10
    return (
        replies[:5]
        + [{"_note": f"[... {omitted} messages omitted ...]"}]
        + replies[-5:]
    )


def _prepare_discord_channel(messages: list[dict]) -> str:
    trimmed = []
    for msg in messages:
        m = {**msg}
        m["content"] = _truncate(m.get("content", ""))
        m["replies"] = _trim_replies([
            {**r, "content": _truncate(r.get("content", ""))}
            for r in m.get("replies", [])
        ])
        trimmed.append(m)
    return json.dumps(trimmed, indent=2, default=str)


def _prepare_github_items(items: list[dict]) -> str:
    trimmed = []
    for item in items:
        t = {**item}
        t["body"] = _truncate(t.get("body", ""))
        t["comments"] = [
            {**c, "body": _truncate(c.get("body", ""))}
            for c in t.get("comments", [])
        ]
        t["reviews"] = [
            {**r, "body": _truncate(r.get("body", ""))}
            for r in t.get("reviews", [])
        ]
        trimmed.append(t)
    return json.dumps(trimmed, indent=2, default=str)


def _parse_response(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _call_claude(client: anthropic.Anthropic, system: str, user_msg: str, label: str) -> dict:
    """Make a single Claude API call with retry. Returns parsed JSON or empty categories."""
    for attempt in range(2):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user_msg}],
            )
            usage = response.usage
            tokens = usage.input_tokens + usage.output_tokens
            print(f"    {label}: {usage.input_tokens:,} in + {usage.output_tokens:,} out = {tokens:,} tokens")

            result = _parse_response(response.content[0].text)
            if result:
                return result
            print(f"    {label}: failed to parse JSON, treating as empty", file=sys.stderr)
            return {"act": [], "monitor": [], "handled": []}
        except Exception as e:
            if attempt == 0:
                print(f"    {label}: error ({e}), retrying in 5s...", file=sys.stderr)
                time.sleep(5)
            else:
                print(f"    {label}: failed after retry ({e}), skipping", file=sys.stderr)
                return {"act": [], "monitor": [], "handled": []}


def analyze(discord_data: list[dict], github_data: list[dict], config: dict) -> dict:
    """Split data into focused sub-agent calls, run in parallel, merge results."""
    api_key = config.get("env", {}).get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    lookback = max(
        config.get("discord", {}).get("lookback_hours", 24),
        config.get("github", {}).get("lookback_hours", 24),
    )

    # Group discord messages by channel
    discord_by_channel = defaultdict(list)
    for msg in discord_data:
        discord_by_channel[msg["channel_name"]].append(msg)

    # Split github into issues and PRs
    github_issues = [i for i in github_data if i["type"] == "issue"]
    github_prs = [i for i in github_data if i["type"] == "pr"]

    # Build all sub-agent tasks
    tasks = []
    for channel_name, messages in discord_by_channel.items():
        system = DISCORD_PROMPT_TEMPLATE.format(channel_name=channel_name)
        user_msg = _prepare_discord_channel(messages)
        tasks.append(("discord", f"discord/#{channel_name} ({len(messages)} msgs)", system, user_msg))

    if github_issues:
        system = GITHUB_ISSUES_PROMPT_TEMPLATE.format(lookback_hours=lookback)
        user_msg = _prepare_github_items(github_issues)
        tasks.append(("github", f"github/issues ({len(github_issues)})", system, user_msg))

    if github_prs:
        system = GITHUB_PRS_PROMPT_TEMPLATE.format(lookback_hours=lookback)
        user_msg = _prepare_github_items(github_prs)
        tasks.append(("github", f"github/PRs ({len(github_prs)})", system, user_msg))

    print(f"  Dispatching {len(tasks)} sub-agents in parallel...")

    # Run all calls in parallel
    merged = {
        "discord": {"act": [], "monitor": [], "handled": []},
        "github": {"act": [], "monitor": [], "handled": []},
    }
    total_tokens = 0

    with ThreadPoolExecutor(max_workers=min(len(tasks), 8)) as pool:
        futures = {
            pool.submit(_call_claude, client, system, user_msg, label): (source, label)
            for source, label, system, user_msg in tasks
        }
        for future in as_completed(futures):
            source, label = futures[future]
            result = future.result()
            for cat in ("act", "monitor", "handled"):
                merged[source][cat].extend(result.get(cat, []))

    merged["generated_at"] = datetime.now(timezone.utc).isoformat()
    return merged
