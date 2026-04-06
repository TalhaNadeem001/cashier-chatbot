import json
from dataclasses import dataclass

from openai import AsyncOpenAI, OpenAIError

from src.chatbot.exceptions import AIServiceError
from src.chatbot.clarification.prompts import AMBIGUOUS_MATCH_RESOLUTION_SYSTEM_PROMPT
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
