"""Uses Claude Code CLI to draft responses for ACT items using codebase context."""

import json
import subprocess
import sys


def _call_claude_code(question: str, link: str, label: str, repo_path: str, session_id: str | None) -> dict:
    """Call claude CLI to draft a response for a single ACT item."""
    prompt = (
        f"A user asked this on Discord/GitHub ({label}):\n\n"
        f"{question}\n\n"
        f"Link: {link}\n\n"
        "Search the codebase for relevant code and draft a brief, helpful response "
        "(2-4 sentences max). Include specific file paths or code references if relevant. "
        "If you cannot find relevant code, say so briefly."
    )

    cmd = [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--max-turns", "3",
        "--allowedTools", "Read,Glob,Grep",
    ]
    if session_id:
        cmd.extend(["--resume", session_id])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=repo_path, timeout=120)
        if result.returncode != 0:
            print(f"    [responder] claude CLI error for: {label}", file=sys.stderr)
            return {"response": None, "session_id": session_id}
        output = json.loads(result.stdout)
        return {
            "response": output.get("result", "").strip(),
            "session_id": output.get("session_id", session_id),
        }
    except subprocess.TimeoutExpired:
        print(f"    [responder] timeout for: {label}", file=sys.stderr)
        return {"response": None, "session_id": session_id}
    except Exception as e:
        print(f"    [responder] error for {label}: {e}", file=sys.stderr)
        return {"response": None, "session_id": session_id}


def _init_session(repo_path: str, repo_name: str) -> str | None:
    """Initialize a Claude Code session for a repo."""
    prompt = (
        f"Read the project README and understand the codebase structure of {repo_name}. "
        "You will be asked follow-up questions about this codebase. "
        "Just confirm you've loaded the context."
    )
    cmd = [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--max-turns", "5",
        "--allowedTools", "Read,Glob,Grep",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=repo_path, timeout=120)
        if result.returncode != 0:
            return None
        output = json.loads(result.stdout)
        session_id = output.get("session_id")
        print(f"    [responder] Session for {repo_name}: {session_id}")
        return session_id
    except Exception as e:
        print(f"    [responder] Init error for {repo_name}: {e}", file=sys.stderr)
        return None


def generate_responses(triage_result: dict, config: dict) -> dict:
    """
    For each ACT item, use Claude Code to draft a response using the
    appropriate repo's codebase.
    """
    # Build repo_path lookup from config
    repo_paths = {}
    for rc in config.get("github", {}).get("repos", []):
        name = rc.get("name", rc["repo"])
        path = rc.get("repo_path", "")
        if path:
            repo_paths[name] = path

    # Default repo for discord ACT items
    default_repo = None
    for rc in config.get("github", {}).get("repos", []):
        if rc.get("fetch") == "all":
            path = rc.get("repo_path", "")
            name = rc.get("name", rc["repo"])
            if path:
                default_repo = (name, path)
                break

    # Collect ACT items with their repo context
    act_items = []
    for source_key, categories in triage_result.items():
        if source_key in ("generated_at",) or not isinstance(categories, dict):
            continue
        for item in categories.get("act", []):
            if source_key == "discord" and default_repo:
                act_items.append((default_repo[0], default_repo[1], item))
            elif source_key in repo_paths:
                act_items.append((source_key, repo_paths[source_key], item))

    if not act_items:
        print("  No ACT items with available repos to respond to.")
        return triage_result

    print(f"  Generating responses for {len(act_items)} ACT items...")

    # Group by repo and init one session per repo
    sessions = {}  # repo_name -> session_id
    for repo_name, repo_path, item in act_items:
        if repo_name not in sessions:
            print(f"    Initializing session for {repo_name}...")
            sessions[repo_name] = _init_session(repo_path, repo_name)

    # Process items
    for i, (repo_name, repo_path, item) in enumerate(act_items, 1):
        summary = item.get("summary", "")
        link = item.get("link", "")
        label = item.get("label", "")
        print(f"    [{i}/{len(act_items)}] {repo_name}/{label}: {summary[:60]}...")

        result = _call_claude_code(summary, link, label, repo_path, sessions.get(repo_name))
        if result["session_id"]:
            sessions[repo_name] = result["session_id"]
        if result["response"]:
            item["suggested_response"] = result["response"]
            print(f"      Response generated ({len(result['response'])} chars)")
        else:
            print(f"      No response generated")

    return triage_result
