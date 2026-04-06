from __future__ import annotations

from src.chatbot.schema import Message


def openai_chat_history_from_messages(
    message_history: list[Message] | None,
    *,
    tail: int | None = None,
) -> list[dict[str, str]]:
    """Normalize stored messages into OpenAI Chat Completions `messages` entries.

    Ensures role/content are plain strings with JSON-safe text (no NULs) so request
    bodies serialize reliably.
    """
    if not message_history:
        return []
    msgs = message_history[-tail:] if tail is not None else message_history
    out: list[dict[str, str]] = []
    for m in msgs:
        data = m.model_dump(mode="json")
        role_raw = data.get("role")
        content_raw = data.get("content")
        role = role_raw if isinstance(role_raw, str) else str(role_raw)
        content = content_raw if isinstance(content_raw, str) else str(content_raw)
        content = content.replace("\x00", "")
        out.append({"role": role, "content": content})
    return out
