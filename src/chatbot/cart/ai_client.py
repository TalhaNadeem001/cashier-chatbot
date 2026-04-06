import json

from openai import AsyncOpenAI, OpenAIError

from src.chatbot.exceptions import AIServiceError
from src.chatbot.internal_schemas import OrderFinalizationIntent, OrderSupervisionResult
from src.chatbot.prompts import (
    POLISH_FOOD_ORDER_REPLY_SYSTEM_PROMPT,
    RESOLVE_ORDER_FINALIZATION_SYSTEM_PROMPT,
    SUPERVISE_ORDER_STATE_SYSTEM_PROMPT,
)
from src.chatbot.openai_messages import openai_chat_history_from_messages
from src.chatbot.schema import Message
from src.config import settings

_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


async def supervise_order_state(
    proposed_order_state: dict,
    latest_message: str,
    message_history: list[Message] | None = None,
) -> OrderSupervisionResult:
    history = openai_chat_history_from_messages(message_history)
    context_lines = [
        f"Proposed order state: {proposed_order_state}",
    ]
    messages: list[dict] = [
        {"role": "system", "content": SUPERVISE_ORDER_STATE_SYSTEM_PROMPT},
        {"role": "system", "content": "\n".join(context_lines)},
        *history,
        {"role": "user", "content": latest_message},
    ]

    try:
        response = await _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=300,
            temperature=0,
            response_format={"type": "json_object"},
        )
    except OpenAIError as e:
        raise AIServiceError(f"OpenAI request failed: {e}") from e

    try:
        return OrderSupervisionResult(**json.loads(response.choices[0].message.content))
    except Exception as e:
        raise AIServiceError(f"Failed to parse order supervision result: {e}") from e


async def polish_food_order_reply(
    order_state: dict,
    latest_message: str,
    message_history: list[Message] | None = None,
) -> str:
    history = openai_chat_history_from_messages(message_history)
    system = POLISH_FOOD_ORDER_REPLY_SYSTEM_PROMPT.format(order_state=order_state)
    messages: list[dict] = [
        {"role": "system", "content": system},
        *history,
        {"role": "user", "content": latest_message},
    ]

    try:
        response = await _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=200,
            temperature=0.4,
        )
    except OpenAIError as e:
        raise AIServiceError(f"OpenAI request failed: {e}") from e

    return response.choices[0].message.content.strip()


async def resolve_order_finalization(
    latest_message: str,
    order_state: dict,
    message_history: list[Message] | None = None,
) -> OrderFinalizationIntent:
    history = openai_chat_history_from_messages(message_history)
    system = RESOLVE_ORDER_FINALIZATION_SYSTEM_PROMPT.format(order_state=order_state)
    messages: list[dict] = [
        {"role": "system", "content": system},
        *history,
        {"role": "user", "content": latest_message},
    ]

    try:
        response = await _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=40,
            temperature=0,
            response_format={"type": "json_object"},
        )
    except OpenAIError as e:
        raise AIServiceError(f"OpenAI request failed: {e}") from e

    try:
        return OrderFinalizationIntent(**json.loads(response.choices[0].message.content))
    except Exception as e:
        raise AIServiceError(f"Failed to parse order finalization intent: {e}") from e
