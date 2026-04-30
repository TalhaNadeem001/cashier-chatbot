from src.cache import cache_delete, cache_set
from src.chatbot.constants import (
    CONVERSATION_SUMMARY_TTL,
    SUMMARIZATION_TAIL_MESSAGES,
    SUMMARIZATION_THRESHOLD,
)
from src.chatbot.exceptions import AIServiceError
from src.chatbot.llm_client import generate_text
from src.chatbot.llm_messages import chat_history_from_messages
from src.chatbot.schema import Message

_SUMMARY_KEY = "conversation_summary:{user_id}"

_SYSTEM_PROMPT = """You are a conversation summarizer for a restaurant cashier chatbot.

Produce a compact factual summary of the conversation so far. It will be injected as a system message to give the AI context about what has already been discussed.

Capture:
- Customer's name if mentioned
- Items ordered, removed, or swapped (with quantities and modifiers)
- Dietary preferences or constraints mentioned
- Questions the customer asked and how they were answered
- Current confirmed order contents
- Any pending or unresolved questions

Rules:
1. Write in third person (e.g. "The customer ordered...").
2. Be concise — 3 to 6 sentences maximum.
3. Omit greetings, small talk, and filler.
4. Do not speculate — only include what was explicitly said.
5. Return a single plain-text paragraph. No JSON, no bullet points."""


async def _summarize(messages: list[Message]) -> str:
    payload: list[dict] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        *chat_history_from_messages(messages),
        {"role": "user", "content": "Summarize the conversation above in 3–6 sentences."},
    ]
    return await generate_text(
        payload,
        temperature=0,
    )


async def compress_history_if_needed(
    user_id: str,
    message_history: list[Message] | None,
) -> list[Message] | None:
    # Fresh session — clear any stale summary
    if not message_history:
        await cache_delete(_SUMMARY_KEY.format(user_id=user_id))
        return message_history

    # Short enough — no compression needed
    if len(message_history) <= SUMMARIZATION_THRESHOLD:
        return message_history

    # Compress: summarize all but the last N messages
    tail = message_history[-SUMMARIZATION_TAIL_MESSAGES:]
    to_summarize = message_history[:-SUMMARIZATION_TAIL_MESSAGES]

    try:
        summary_text = await _summarize(to_summarize)
        await cache_set(
            _SUMMARY_KEY.format(user_id=user_id),
            summary_text,
            ttl=CONVERSATION_SUMMARY_TTL,
        )
    except AIServiceError:
        # Graceful fallback — return full history if summarization fails
        return message_history

    summary_msg = Message(
        role="system",
        content=f"[Conversation summary so far]: {summary_text}",
    )
    return [summary_msg, *tail]
