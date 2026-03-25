"""Claude API analysis — parallel sub-agents per Discord channel and per GitHub repo."""

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

CONTEXT = """\
You are a daily triage assistant for Krishna (Discord: ramkrishna2910, GitHub: ramkrishna2910), \
a Principal ML Software Engineer who maintains the Lemonade SDK — an open-source local LLM server \
for AMD Ryzen AI hardware (llama.cpp, ONNX Runtime GenAI, multimodal inference). \
He interacts with external contributors, AMD stakeholders, and community members daily."""

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

REPO_PROMPT_TEMPLATE = CONTEXT + """

Review these GitHub items from {repo_name} ({repo_full}) and categorize each into ACT, MONITOR, or HANDLED.

{custom_prompt}

Items updated in the last {lookback_hours} hours are RECENT (is_recent=true).
Summarize each in 1-2 sentences.

Return only valid JSON:
{{"act": [TriageItem], "monitor": [TriageItem], "handled": [TriageItem]}}

TriageItem = {{"summary": str, "reason": str, "link": str, "label": str, "is_recent": bool}}"""

GH_EXTRAS_PROMPT = CONTEXT + """

These are GitHub items from repos NOT in Krishna's tracked list, but where he was
either requested as a reviewer or @mentioned. Categorize each:

- ACT: Review requests, direct mentions needing a response
- MONITOR: FYI mentions, informational
- HANDLED: Already resolved

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
    return replies[:5] + [{"_note": f"[... {omitted} messages omitted ...]"}] + replies[-5:]


def _prepare_discord_channel(messages: list[dict]) -> str:
    trimmed = []
    for msg in messages:
        m = {**msg}
        m["content"] = _truncate(m.get("content", ""), 1000)
        m["replies"] = _trim_replies([
            {**r, "content": _truncate(r.get("content", ""), 1000)} for r in m.get("replies", [])
        ])
        trimmed.append(m)
    return json.dumps(trimmed, indent=2, default=str)


def _prepare_github_items(items: list[dict], truncate_len: int = 1000) -> str:
    trimmed = []
    for item in items:
        t = {**item}
        t["body"] = _truncate(t.get("body", ""), truncate_len)
        t["comments"] = [{**c, "body": _truncate(c.get("body", ""), truncate_len)} for c in t.get("comments", [])]
        t["reviews"] = [{**r, "body": _truncate(r.get("body", ""), truncate_len)} for r in t.get("reviews", [])]
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
    for attempt in range(2):
        try:
            response = client.messages.create(
                model=MODEL, max_tokens=MAX_TOKENS, system=system,
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


def analyze(discord_data: list[dict], github_data: dict, config: dict, gh_extras: dict | None = None) -> dict:
    """
    Parallel sub-agents: one per Discord channel, one per GitHub repo.
    github_data is now a dict: {repo_name: [items]}
    Returns: {discord: {...}, repo_name: {...}, ...}
    """
    api_key = config.get("env", {}).get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    lookback = config["github"].get("lookback_hours", 24)

    # Build repo config lookup
    repo_cfg_by_name = {}
    for rc in config["github"]["repos"]:
        repo_cfg_by_name[rc.get("name", rc["repo"])] = rc

    # Build sub-agent tasks
    tasks = []  # (output_key, label, system, user_msg)

    # Discord channels
    discord_by_channel = defaultdict(list)
    for msg in discord_data:
        discord_by_channel[msg["channel_name"]].append(msg)
    for channel_name, messages in discord_by_channel.items():
        system = DISCORD_PROMPT_TEMPLATE.format(channel_name=channel_name)
        user_msg = _prepare_discord_channel(messages)
        tasks.append(("discord", f"discord/#{channel_name} ({len(messages)} msgs)", system, user_msg))

    # Per-repo GitHub
    for repo_name, items in github_data.items():
        if not items:
            continue
        rc = repo_cfg_by_name.get(repo_name, {})
        custom_prompt = rc.get("prompt", "Categorize items as ACT, MONITOR, or HANDLED.")
        repo_full = f"{rc.get('owner', '')}/{rc.get('repo', '')}"
        system = REPO_PROMPT_TEMPLATE.format(
            repo_name=repo_name, repo_full=repo_full,
            custom_prompt=custom_prompt, lookback_hours=lookback,
        )
        truncate_len = rc.get("truncate_len", 2000 if rc.get("prompt") else 1000)
        user_msg = _prepare_github_items(items, truncate_len)
        tasks.append((repo_name, f"github/{repo_name} ({len(items)} items)", system, user_msg))

    # gh CLI extras (untracked repos)
    if gh_extras:
        all_extras = gh_extras.get("review_requests", []) + gh_extras.get("mentions", [])
        if all_extras:
            user_msg = json.dumps(all_extras, indent=2, default=str)
            tasks.append(("_gh_extras", f"gh/extras ({len(all_extras)} items)", GH_EXTRAS_PROMPT, user_msg))

    print(f"  Dispatching {len(tasks)} sub-agents in parallel...")

    # Init result structure
    merged = {"discord": {"act": [], "monitor": [], "handled": []}}
    for repo_name in github_data:
        merged[repo_name] = {"act": [], "monitor": [], "handled": []}
    if gh_extras:
        merged["_gh_extras"] = {"act": [], "monitor": [], "handled": []}

    with ThreadPoolExecutor(max_workers=min(len(tasks), 10)) as pool:
        futures = {
            pool.submit(_call_claude, client, system, user_msg, label): (output_key, label)
            for output_key, label, system, user_msg in tasks
        }
        for future in as_completed(futures):
            output_key, label = futures[future]
            result = future.result()
            if output_key not in merged:
                merged[output_key] = {"act": [], "monitor": [], "handled": []}
            for cat in ("act", "monitor", "handled"):
                merged[output_key][cat].extend(result.get(cat, []))

    merged["generated_at"] = datetime.now(timezone.utc).isoformat()
    return merged
