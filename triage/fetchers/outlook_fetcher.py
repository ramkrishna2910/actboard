"""Outlook email fetcher — triggers Power Automate flow, reads exported emails."""

import json
import os
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import unquote

import requests


# Emails from these senders are typically noise
DEFAULT_SKIP_SENDERS = [
    "noreply", "no-reply", "donotreply", "mailer-daemon",
    "notifications@", "calendar@", "postmaster@",
]

# Subjects matching these patterns are typically noise
DEFAULT_SKIP_SUBJECTS = [
    "password will expire", "out of office", "automatic reply",
    "meeting accepted", "meeting declined", "meeting tentatively",
    "undeliverable", "delivery status",
]


def _decode_email(raw: dict) -> dict:
    """Decode URL-encoded fields from Power Automate export."""
    return {
        "subject": unquote(raw.get("subject", "")),
        "from": unquote(raw.get("from", "")),
        "to": unquote(raw.get("toRecipients", "")),
        "received_at": raw.get("receivedDateTime", ""),
        "link": raw.get("webLink", ""),
        "is_read": raw.get("isRead", False),
        "importance": raw.get("importance", "normal"),
        "body": unquote(raw.get("bodyPreview", "")),
    }


def _is_noise(email: dict, skip_senders: list, skip_subjects: list) -> bool:
    """Filter out automated/noise emails before sending to LLM."""
    sender = email["from"].lower()
    subject = email["subject"].lower()

    for s in skip_senders:
        if s in sender:
            return True
    for s in skip_subjects:
        if s in subject:
            return True
    return False


def _trigger_flow(config: dict) -> bool:
    """Trigger the Power Automate flow to export emails."""
    flow_url = config.get("env", {}).get("OUTLOOK_FLOW_URL", "")
    if not flow_url:
        return False
    try:
        print("  Triggering Power Automate flow...")
        resp = requests.post(flow_url, timeout=30)
        if resp.status_code in (200, 202):
            print("  Flow triggered, waiting for emails...")
            return True
        else:
            print(f"  [outlook] Flow trigger returned {resp.status_code}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"  [outlook] Flow trigger failed: {e}", file=sys.stderr)
        return False


def fetch_outlook(config: dict) -> dict:
    """
    Trigger Power Automate flow, then read exported emails.
    Returns: {"Outlook": [email_dicts]}
    """
    outlook_cfg = config.get("outlook")
    if not outlook_cfg:
        return {}

    base_dir = Path(outlook_cfg.get("folder", ""))
    if not base_dir.exists():
        print(f"  [outlook] Folder not found: {base_dir}", file=sys.stderr)
        return {}

    today = date.today().isoformat()
    today_dir = base_dir / today

    # Trigger flow if today's folder doesn't exist or is empty
    if not today_dir.exists() or not list(today_dir.glob("*.json")):
        triggered = _trigger_flow(config)
        if triggered:
            # Wait for files to appear (flow takes ~10-30s)
            for i in range(12):
                time.sleep(5)
                if today_dir.exists() and list(today_dir.glob("*.json")):
                    print(f"  Emails arrived after {(i + 1) * 5}s")
                    break
            else:
                print("  [outlook] Timed out waiting for emails (60s)")

    if not today_dir.exists():
        print(f"  [outlook] No folder for today: {today_dir}")
        return {}

    # Custom filters from config
    extra_skip_senders = [s.lower() for s in outlook_cfg.get("skip_senders", [])]
    extra_skip_subjects = [s.lower() for s in outlook_cfg.get("skip_subjects", [])]
    skip_senders = DEFAULT_SKIP_SENDERS + extra_skip_senders
    skip_subjects = DEFAULT_SKIP_SUBJECTS + extra_skip_subjects
    keywords = [k.lower() for k in outlook_cfg.get("keywords", [])]

    # Read all JSON files
    all_emails = []
    errors = 0
    for f in today_dir.glob("*.json"):
        try:
            with open(f, encoding="utf-8") as fh:
                raw = json.load(fh)
            email = _decode_email(raw)
            # Build Outlook Web link from message ID (filename without .json)
            msg_id = f.stem
            email["link"] = f"https://outlook.office.com/mail/id/{msg_id}"
            all_emails.append(email)
        except (json.JSONDecodeError, Exception):
            errors += 1

    if errors:
        print(f"  [outlook] {errors} files failed to parse", file=sys.stderr)

    # Filter noise
    filtered = [e for e in all_emails if not _is_noise(e, skip_senders, skip_subjects)]
    skipped = len(all_emails) - len(filtered)

    # Keyword filter (if keywords configured, only keep matching emails)
    if keywords:
        keyword_matched = []
        for e in filtered:
            text = f"{e['subject']} {e['from']} {e['body']}".lower()
            if any(kw in text for kw in keywords):
                keyword_matched.append(e)
        filtered = keyword_matched

    print(f"  Outlook: {len(filtered)}/{len(all_emails)} emails"
          f" ({skipped} noise filtered, {errors} parse errors)")

    # Format for triage pipeline
    posts = []
    for e in filtered:
        posts.append({
            "title": e["subject"],
            "body": e["body"],
            "author": e["from"],
            "created_at": e["received_at"],
            "link": e["link"],
            "is_read": e["is_read"],
            "importance": e["importance"],
            "is_recent": True,
        })

    return {"Outlook": posts} if posts else {}
