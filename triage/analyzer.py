"""LLM analysis — parallel sub-agents per Discord channel and per GitHub repo.
Supports Claude API and any OpenAI-compatible local endpoint."""

import json
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

CLAUDE_MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS_CLAUDE = 8192
MAX_TOKENS_LOCAL = 16384  # Needs room for thinking (~5-8k) + JSON output (~2k)

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

REDDIT_PROMPT_TEMPLATE = CONTEXT + """

Review these Reddit posts from r/{subreddit} and categorize each into ACT, MONITOR, or HANDLED.

{custom_prompt}

Summarize each in 1-2 sentences. Include the post link.

Return only valid JSON:
{{"act": [TriageItem], "monitor": [TriageItem], "handled": [TriageItem]}}

TriageItem = {{"summary": str, "reason": str, "link": str, "label": str, "is_recent": bool}}"""


def _prepare_reddit_posts(posts: list[dict], truncate_len: int = 1000) -> str:
    trimmed = []
    for post in posts:
        t = {**post}
        t["body"] = _truncate(t.get("body", ""), truncate_len)
        trimmed.append(t)
    return json.dumps(trimmed, indent=2, default=str)


def _truncate(text: str, max_len: int = 500) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _trim_replies(replies: list[dict], max_count: int = 20) -> list:
    if len(replies) <= max_count:
        return replies
    omitted = len(replies) - 10
    return replies[:5] + [{"_note": f"[... {omitted} messages omitted ...]"}] + replies[-5:]


def _prepare_discord_channel(messages: list[dict], truncate_len: int = 1000) -> str:
    trimmed = []
    for msg in messages:
        m = {**msg}
        m["content"] = _truncate(m.get("content", ""), truncate_len)
        m["replies"] = _trim_replies([
            {**r, "content": _truncate(r.get("content", ""), truncate_len)} for r in m.get("replies", [])
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


def _call_claude(api_key: str, model: str, system: str, user_msg: str, label: str) -> dict:
    """Call Claude API."""
    import anthropic
    from pipeline_events import emit
    emit("agent_start", f"agent:{label}", label=label)
    client = anthropic.Anthropic(api_key=api_key)
    for attempt in range(2):
        try:
            response = client.messages.create(
                model=model, max_tokens=MAX_TOKENS_CLAUDE, system=system,
                messages=[{"role": "user", "content": user_msg}],
            )
            usage = response.usage
            tokens = usage.input_tokens + usage.output_tokens
            cost = (usage.input_tokens * 3 + usage.output_tokens * 15) / 1_000_000
            print(f"    {label}: {usage.input_tokens:,} in + {usage.output_tokens:,} out = {tokens:,} tokens")
            result = _parse_response(response.content[0].text)
            if result:
                emit("agent_complete", f"agent:{label}", label=label,
                     tokens_in=usage.input_tokens, tokens_out=usage.output_tokens, cost=cost)
                return result
            print(f"    {label}: failed to parse JSON, treating as empty", file=sys.stderr)
            emit("agent_complete", f"agent:{label}", label=label,
                 tokens_in=usage.input_tokens, tokens_out=usage.output_tokens, cost=cost)
            return {"act": [], "monitor": [], "handled": []}
        except Exception as e:
            if attempt == 0:
                print(f"    {label}: error ({e}), retrying in 5s...", file=sys.stderr)
                time.sleep(5)
            else:
                print(f"    {label}: failed after retry ({e}), skipping", file=sys.stderr)
                emit("agent_error", f"agent:{label}", label=label, error=str(e))
                return {"act": [], "monitor": [], "handled": []}


def _call_local(base_url: str, model: str, system: str, user_msg: str, label: str) -> dict:
    """Call any OpenAI-compatible chat completions endpoint."""
    from pipeline_events import emit
    emit("agent_start", f"agent:{label}", label=label)
    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    body = {
        "model": model,
        "max_tokens": MAX_TOKENS_LOCAL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        "cache_prompt": False,
    }
    for attempt in range(2):
        try:
            resp = requests.post(url, json=body, timeout=300)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise RuntimeError(data["error"].get("message", str(data["error"])))
            msg = data["choices"][0]["message"]
            text = msg.get("content", "") or ""
            # If content is empty, check reasoning_content
            if not text.strip() and msg.get("reasoning_content"):
                text = msg["reasoning_content"]
            # Strip <think>...</think> tags that Qwen3.5 may produce
            text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL)
            # Also handle unclosed <think> tags (thinking was truncated)
            text = re.sub(r"<think>.*", "", text, flags=re.DOTALL).strip()
            usage = data.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            if prompt_tokens or completion_tokens:
                print(f"    {label}: {prompt_tokens:,} in + {completion_tokens:,} out = {prompt_tokens + completion_tokens:,} tokens")
            else:
                print(f"    {label}: response received ({len(text)} chars)")
            result = _parse_response(text)
            if result:
                emit("agent_complete", f"agent:{label}", label=label,
                     tokens_in=prompt_tokens, tokens_out=completion_tokens, cost=0.0)
                return result
            print(f"    {label}: failed to parse JSON, treating as empty", file=sys.stderr)
            emit("agent_complete", f"agent:{label}", label=label,
                 tokens_in=prompt_tokens, tokens_out=completion_tokens, cost=0.0)
            return {"act": [], "monitor": [], "handled": []}
        except Exception as e:
            if attempt == 0:
                print(f"    {label}: error ({e}), retrying in 5s...", file=sys.stderr)
                time.sleep(5)
            else:
                print(f"    {label}: failed after retry ({e}), skipping", file=sys.stderr)
                emit("agent_error", f"agent:{label}", label=label, error=str(e))
                return {"act": [], "monitor": [], "handled": []}


def _make_caller(config: dict):
    """Return a callable (system, user_msg, label) -> dict based on inference config."""
    inference = config.get("inference", {})
    backend = inference.get("backend", "claude")

    if backend == "local":
        base_url = inference.get("base_url", "http://localhost:8000")
        model = inference.get("model", "")
        print(f"  Using local LLM: {base_url} (model={model or 'default'})")
        return lambda system, user_msg, label: _call_local(base_url, model, system, user_msg, label)
    else:
        api_key = config.get("env", {}).get("ANTHROPIC_API_KEY", "")
        if not api_key:
            print("Error: ANTHROPIC_API_KEY not set.", file=sys.stderr)
            sys.exit(1)
        model = inference.get("model", CLAUDE_MODEL)
        print(f"  Using Claude API: {model}")
        return lambda system, user_msg, label: _call_claude(api_key, model, system, user_msg, label)


def analyze(discord_data: list[dict], github_data: dict, config: dict,
            gh_extras: dict | None = None, reddit_data: dict | None = None) -> dict:
    """
    Parallel sub-agents: one per Discord channel, one per GitHub repo, one per subreddit.
    github_data is now a dict: {repo_name: [items]}
    Returns: {discord: {...}, repo_name: {...}, ...}
    """
    inference = config.get("inference", {})
    caller = _make_caller(config)
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
    # Split large channels into chunks to fit context window
    is_local = inference.get("backend") == "local"
    max_msgs_per_chunk = 8 if is_local else 200
    discord_trunc = 500 if is_local else 1000
    for channel_name, messages in discord_by_channel.items():
        system = DISCORD_PROMPT_TEMPLATE.format(channel_name=channel_name)
        for i in range(0, len(messages), max_msgs_per_chunk):
            chunk = messages[i:i + max_msgs_per_chunk]
            user_msg = _prepare_discord_channel(chunk, discord_trunc)
            part = f" pt{i // max_msgs_per_chunk + 1}" if len(messages) > max_msgs_per_chunk else ""
            tasks.append(("discord", f"discord/#{channel_name}{part} ({len(chunk)} msgs)", system, user_msg))

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
        default_trunc = 1000 if is_local else (2000 if rc.get("prompt") else 1000)
        truncate_len = rc.get("truncate_len", default_trunc)
        max_items_per_chunk = 5 if inference.get("backend") == "local" else 50
        for i in range(0, len(items), max_items_per_chunk):
            chunk = items[i:i + max_items_per_chunk]
            user_msg = _prepare_github_items(chunk, truncate_len)
            part = f" pt{i // max_items_per_chunk + 1}" if len(items) > max_items_per_chunk else ""
            tasks.append((repo_name, f"github/{repo_name}{part} ({len(chunk)} items)", system, user_msg))

    # gh CLI extras (untracked repos)
    if gh_extras:
        all_extras = gh_extras.get("review_requests", []) + gh_extras.get("mentions", [])
        if all_extras:
            user_msg = json.dumps(all_extras, indent=2, default=str)
            tasks.append(("_gh_extras", f"gh/extras ({len(all_extras)} items)", GH_EXTRAS_PROMPT, user_msg))

    # Reddit subreddits
    reddit_cfg_by_name = {}
    for sub_cfg in config.get("reddit", {}).get("subreddits", []):
        reddit_cfg_by_name[f"r/{sub_cfg['name']}"] = sub_cfg
    if reddit_data:
        for sub_key, posts in reddit_data.items():
            if not posts:
                continue
            sub_cfg = reddit_cfg_by_name.get(sub_key, {})
            custom_prompt = sub_cfg.get("prompt", "Categorize posts as ACT, MONITOR, or HANDLED.")
            subreddit = sub_cfg.get("name", sub_key)
            system = REDDIT_PROMPT_TEMPLATE.format(subreddit=subreddit, custom_prompt=custom_prompt)
            max_posts_per_chunk = 15 if is_local else 100
            for i in range(0, len(posts), max_posts_per_chunk):
                chunk = posts[i:i + max_posts_per_chunk]
                user_msg = _prepare_reddit_posts(chunk, 500 if is_local else 1000)
                part = f" pt{i // max_posts_per_chunk + 1}" if len(posts) > max_posts_per_chunk else ""
                tasks.append((sub_key, f"reddit/{sub_key}{part} ({len(chunk)} posts)", system, user_msg))

    from pipeline_events import emit as _emit
    _emit("analyze_start", "analyzer", task_count=len(tasks), tasks=[t[1] for t in tasks])
    print(f"  Dispatching {len(tasks)} sub-agents in parallel...")

    # Init result structure
    merged = {"discord": {"act": [], "monitor": [], "handled": []}}
    for repo_name in github_data:
        merged[repo_name] = {"act": [], "monitor": [], "handled": []}
    if gh_extras:
        merged["_gh_extras"] = {"act": [], "monitor": [], "handled": []}
    if reddit_data:
        for sub_key in reddit_data:
            merged[sub_key] = {"act": [], "monitor": [], "handled": []}

    # Local LLM can only handle 1-2 concurrent requests; Claude can do many
    max_workers = 1 if inference.get("backend") == "local" else min(len(tasks), 10)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(caller, system, user_msg, label): (output_key, label)
            for output_key, label, system, user_msg in tasks
        }
        for future in as_completed(futures):
            output_key, label = futures[future]
            result = future.result()
            if output_key not in merged:
                merged[output_key] = {"act": [], "monitor": [], "handled": []}
            for cat in ("act", "monitor", "handled"):
                merged[output_key][cat].extend(result.get(cat, []))

    _emit("analyze_complete", "analyzer", total_tokens=0, total_cost=0)
    merged["generated_at"] = datetime.now(timezone.utc).isoformat()
    return merged
