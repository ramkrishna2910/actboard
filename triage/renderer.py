"""Converts triage JSON into Notion API block structures."""

CATEGORY_EMOJI = {
    "act": "\U0001f534",      # 🔴
    "monitor": "\U0001f7e1",  # 🟡
    "handled": "\U0001f7e2",  # 🟢
}

CATEGORY_LABELS = {
    "act": "Act",
    "monitor": "Monitor",
    "handled": "Handled",
}


def _rich_text(text: str, **annotations) -> dict:
    rt = {
        "type": "text",
        "text": {"content": text},
    }
    if annotations:
        rt["annotations"] = annotations
    return rt


def _link_text(text: str, url: str) -> dict:
    return {
        "type": "text",
        "text": {"content": text, "link": {"url": url}},
        "annotations": {"color": "blue"},
    }


def _todo_block(item: dict, emoji: str) -> dict:
    """Compact to-do block: emoji + summary + label + reason + link, all on one line."""
    segments = [
        _rich_text(f"{emoji} "),
        _rich_text(item["summary"]),
    ]

    # Recent badge inline
    if item.get("is_recent"):
        segments.append(_rich_text(" \U0001f550", bold=True))  # 🕐

    # Label as code
    segments.append(_rich_text(" "))
    segments.append(_rich_text(item["label"], code=True))

    # Reason (italic, gray) - brief, inline
    segments.append(_rich_text(f" - {item['reason']}", italic=True, color="gray"))

    # Link
    segments.append(_rich_text(" "))
    segments.append(_link_text("\u2197", item["link"]))  # ↗

    return {
        "object": "block",
        "type": "to_do",
        "to_do": {
            "rich_text": segments,
            "checked": False,
        },
    }


def _heading_block(level: int, text: str) -> dict:
    key = f"heading_{level}"
    return {
        "object": "block",
        "type": key,
        key: {
            "rich_text": [_rich_text(text)],
            "is_toggleable": True,
        },
    }


def _paragraph_block(text: str, **annotations) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [_rich_text(text, **annotations)],
        },
    }


def _divider_block() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def _render_category(items: list[dict], category: str) -> list[dict]:
    emoji = CATEGORY_EMOJI[category]
    label = CATEGORY_LABELS[category]
    count = len(items)
    blocks = []

    blocks.append(_heading_block(3, f"{emoji} {label} ({count})"))

    if not items:
        blocks.append(_paragraph_block("No items.", italic=True, color="gray"))
    else:
        for item in items:
            blocks.append(_todo_block(item, emoji))

    return blocks


def render(triage_result: dict) -> list[dict]:
    blocks = []

    generated_at = triage_result.get("generated_at", "")
    if generated_at:
        blocks.append(_paragraph_block(f"Generated at: {generated_at}", color="gray"))

    blocks.append(_divider_block())

    # Discord
    discord = triage_result.get("discord", {})
    blocks.append(_heading_block(2, "Discord"))
    for category in ("act", "monitor", "handled"):
        blocks.extend(_render_category(discord.get(category, []), category))

    blocks.append(_divider_block())

    # GitHub
    github = triage_result.get("github", {})
    blocks.append(_heading_block(2, "GitHub"))
    for category in ("act", "monitor", "handled"):
        blocks.extend(_render_category(github.get(category, []), category))

    return blocks
