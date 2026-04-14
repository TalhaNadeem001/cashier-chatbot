from __future__ import annotations

from collections.abc import Sequence
from typing import TypedDict

from src.chatbot.schema import Message


class LLMMessage(TypedDict):
    role: str
    content: str


def chat_history_from_messages(
    message_history: list[Message] | None,
    *,
    tail: int | None = None,
) -> list[LLMMessage]:
    """Normalize stored messages into provider-neutral role/content pairs."""
    if not message_history:
        return []
    msgs = message_history[-tail:] if tail is not None else message_history
    out: list[LLMMessage] = []
    for m in msgs:
        data = m.model_dump(mode="json")
        role_raw = data.get("role")
        content_raw = data.get("content")
        role = role_raw if isinstance(role_raw, str) else str(role_raw)
        content = content_raw if isinstance(content_raw, str) else str(content_raw)
        out.append({"role": role, "content": content.replace("\x00", "")})
    return out


def split_system_instruction(
    messages: Sequence[LLMMessage],
) -> tuple[str | None, list[LLMMessage]]:
    """Collect system messages for Gemini's system_instruction field."""
    system_parts: list[str] = []
    conversational_messages: list[LLMMessage] = []

    for message in messages:
        role = str(message["role"])
        content = str(message["content"]).replace("\x00", "")
        if role == "system":
            system_parts.append(content)
            continue
        conversational_messages.append({"role": role, "content": content})

    system_instruction = "\n\n".join(system_parts).strip() or None
    return system_instruction, conversational_messages
