import json
from dataclasses import dataclass

from openai import AsyncOpenAI, OpenAIError

from src.chatbot.exceptions import AIServiceError
from src.chatbot.clarification.prompts import AMBIGUOUS_MATCH_RESOLUTION_SYSTEM_PROMPT, NOT_FOUND_ITEM_RESOLUTION_SYSTEM_PROMPT
from src.chatbot.openai_messages import openai_chat_history_from_messages
from src.chatbot.schema import Message
from src.config import settings

_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

@dataclass
class AmbiguousMatchResolution:
    confident: bool
    canonical: str | None
    clarification_message: str | None

async def resolve_ambiguous_match(
    candidates: list[str],
    latest_message: str,
    message_history: list[Message] | None = None,
) -> AmbiguousMatchResolution:
    system_content = AMBIGUOUS_MATCH_RESOLUTION_SYSTEM_PROMPT.format(
        candidates=", ".join(f'"{c}"' for c in candidates),
    )
    history = openai_chat_history_from_messages(message_history)
    print(f"history: {history}")
    print(f"latest_message: {latest_message}")
    messages: list[dict] = [
        {"role": "system", "content": system_content},
        *history,
        {"role": "user", "content": latest_message},
    ]

    try:
        response = await _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"},
            max_tokens=120,
        )
    except OpenAIError as e:
        raise AIServiceError(f"OpenAI request failed: {e}") from e

    try:
        raw = response.choices[0].message.content
        print(f"raw: {raw}")
        data = json.loads(raw)
        return AmbiguousMatchResolution(
            confident=data["confident"],
            canonical=data.get("canonical"),
            clarification_message=data.get("clarification_message"),
        )
    except Exception as e:
        raise AIServiceError(f"Failed to parse ambiguous match resolution: {e}") from e


async def resolve_not_found_item(
    item_name: str,
    top_candidates: list[str],
    menu_context: str,
    latest_message: str,
    message_history: list[Message] | None = None,
) -> str:
    candidates_str = ", ".join(f'"{c}"' for c in top_candidates) if top_candidates else "none"
    system_content = NOT_FOUND_ITEM_RESOLUTION_SYSTEM_PROMPT.format(
        item_name=item_name,
        top_candidates=candidates_str,
        menu_context=menu_context,
    )
    history = openai_chat_history_from_messages(message_history)
    messages: list[dict] = [
        {"role": "system", "content": system_content},
        *history,
        {"role": "user", "content": latest_message},
    ]

    try:
        response = await _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0,
            max_tokens=120,
        )
    except OpenAIError as e:
        raise AIServiceError(f"OpenAI request failed: {e}") from e

    content = response.choices[0].message.content
    if not content:
        raise AIServiceError("Empty response from OpenAI for not-found item resolution")
    return content
