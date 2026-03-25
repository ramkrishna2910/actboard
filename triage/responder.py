"""Uses Claude Code CLI to draft responses for ACT items using codebase context."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed


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
        "claude",
        "-p", prompt,
        "--output-format", "json",
        "--max-turns", "3",
        "--allowedTools", "Read,Glob,Grep",
    ]

    if session_id:
        cmd.extend(["--resume", session_id])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=repo_path,
            timeout=120,
        )
        if result.returncode != 0:
            print(f"    [responder] claude CLI error for: {label}", file=sys.stderr)
            return {"response": None, "session_id": session_id}

        output = json.loads(result.stdout)
        new_session_id = output.get("session_id", session_id)
        response_text = output.get("result", "").strip()

        return {"response": response_text, "session_id": new_session_id}

    except subprocess.TimeoutExpired:
        print(f"    [responder] timeout for: {label}", file=sys.stderr)
        return {"response": None, "session_id": session_id}
    except (json.JSONDecodeError, Exception) as e:
        print(f"    [responder] error for {label}: {e}", file=sys.stderr)
        return {"response": None, "session_id": session_id}


def _init_session(repo_path: str) -> str | None:
    """Initialize a Claude Code session by loading codebase context once."""
    prompt = (
        "Read the project README and understand the codebase structure. "
        "This is the Lemonade SDK — an open-source local LLM server for AMD Ryzen AI hardware. "
        "You will be asked follow-up questions about this codebase. "
        "Just confirm you've loaded the context."
    )

    cmd = [
        "claude",
        "-p", prompt,
        "--output-format", "json",
        "--max-turns", "5",
        "--allowedTools", "Read,Glob,Grep",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=repo_path,
            timeout=120,
        )
        if result.returncode != 0:
            print("    [responder] Failed to init session", file=sys.stderr)
            return None

        output = json.loads(result.stdout)
        session_id = output.get("session_id")
        print(f"    [responder] Session initialized: {session_id}")
        return session_id

    except Exception as e:
        print(f"    [responder] Init error: {e}", file=sys.stderr)
        return None


def _git_pull(repo_path: str):
    """Pull latest changes from the main branch."""
    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            capture_output=True,
            text=True,
            cwd=repo_path,
            timeout=60,
        )
        if result.returncode == 0:
            print(f"    Repo updated: {result.stdout.strip()}")
        else:
            print(f"    git pull warning: {result.stderr.strip()}", file=sys.stderr)
    except Exception as e:
        print(f"    git pull failed: {e}", file=sys.stderr)


def generate_responses(triage_result: dict, config: dict) -> dict:
    """
    For each ACT item in the triage result, use Claude Code to draft a response.
    Modifies triage_result in place, adding 'suggested_response' to ACT items.
    Returns the modified triage_result.
    """
    repo_path = config.get("responder", {}).get("repo_path", "")
    if not repo_path:
        print("  Skipping responder: responder.repo_path not set in config.yaml")
        return triage_result

    # Collect all ACT items across discord and github
    act_items = []
    for source in ("discord", "github"):
        for item in triage_result.get(source, {}).get("act", []):
            act_items.append((source, item))

    if not act_items:
        print("  No ACT items to respond to.")
        return triage_result

    print(f"  Generating responses for {len(act_items)} ACT items...")

    # Pull latest code
    print("    Syncing repo...")
    _git_pull(repo_path)

    # Init session once to load codebase context
    print("    Initializing Claude Code session...")
    session_id = _init_session(repo_path)

    # Process items sequentially to reuse session context
    for i, (source, item) in enumerate(act_items, 1):
        summary = item.get("summary", "")
        link = item.get("link", "")
        label = item.get("label", "")
        print(f"    [{i}/{len(act_items)}] {label}: {summary[:60]}...")

        result = _call_claude_code(summary, link, label, repo_path, session_id)
        if result["session_id"]:
            session_id = result["session_id"]
        if result["response"]:
            item["suggested_response"] = result["response"]
            print(f"      Response generated ({len(result['response'])} chars)")
        else:
            print(f"      No response generated")

    return triage_result
