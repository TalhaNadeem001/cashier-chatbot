import re

from src.chatbot.internal_schemas import ClarificationIssue

_NAME_PATTERNS = (
    re.compile(r"\border\s+for\s+([a-z][a-z\s'.-]{0,30})\b", re.IGNORECASE),
    re.compile(r"\b(?:it'?s|its)\s+for\s+([a-z][a-z\s'.-]{0,30})\b", re.IGNORECASE),
    re.compile(r"\b(?:name\s+is|under\s+the\s+name)\s+([a-z][a-z\s'.-]{0,30})\b", re.IGNORECASE),
)

_NOTE_KEYWORDS = ("napkin", "utensil", "fork", "spoon", "ketchup", "plates")
_ORDER_HINTS = (
    "add ",
    "remove ",
    "swap ",
    "burger",
    "wings",
    "fries",
    "coke",
    "sprite",
    "soda",
    "drink",
)


def extract_context_note_update(
    latest_message: str,
    existing_order_state: dict | None = None,
) -> tuple[dict, list[ClarificationIssue], bool]:
    message = (latest_message or "").strip()
    lowered = message.lower()
    if not message:
        return {}, [], False

    metadata: dict = {}
    issues: list[ClarificationIssue] = []
    existing_label = str((existing_order_state or {}).get("customer_label", "")).strip()

    for pattern in _NAME_PATTERNS:
        match = pattern.search(message)
        if not match:
            continue
        label = _normalize_label(match.group(1))
        if not label:
            continue
        metadata["customer_label"] = label
        break

    if any(keyword in lowered for keyword in _NOTE_KEYWORDS):
        note_text = _extract_short_note(message)
        if note_text:
            metadata["notes"] = _merge_note_lines([], [note_text])

    note_only = bool(metadata) and not any(hint in lowered for hint in _ORDER_HINTS)
    return metadata, issues, note_only


def merge_order_metadata(existing_order_state: dict | None, context_update: dict) -> dict:
    existing = dict(existing_order_state or {})
    merged = dict(existing)
    if "customer_label" in context_update:
        merged["customer_label"] = context_update["customer_label"]
    if "notes" in context_update:
        prior = existing.get("notes", [])
        prior_list = prior if isinstance(prior, list) else [str(prior)]
        merged["notes"] = _merge_note_lines(prior_list, context_update["notes"])
    return merged


def _extract_short_note(message: str) -> str:
    cleaned = " ".join(message.strip().split())
    return cleaned[:120] if cleaned else ""


def _normalize_label(raw: str) -> str:
    label = " ".join(str(raw or "").strip().split())
    return label.strip(" .,!?:;")


def _merge_note_lines(existing: list[str], new_lines: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for line in [*existing, *new_lines]:
        text = str(line).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(text)
    return merged
