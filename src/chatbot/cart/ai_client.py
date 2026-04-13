import json

from openai import AsyncOpenAI, OpenAIError

from src.chatbot.cart.utils import format_money_context_for_prompt
from src.chatbot.exceptions import AIServiceError
from src.chatbot.internal_schemas import (
    ClosestModifierResolution,
    OrderFinalizationIntent,
    OrderSupervisionResult,
)
from src.chatbot.prompts import (
    POLISH_FOOD_ORDER_REPLY_SYSTEM_PROMPT,
    RESOLVE_CLOSEST_MODIFIER_SYSTEM_PROMPT,
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


async def resolve_closest_modifier_match(
    item_name: str,
    modifier_text: str,
    allowed_options: list[str],
    latest_message: str | None = None,
) -> ClosestModifierResolution:
    system = RESOLVE_CLOSEST_MODIFIER_SYSTEM_PROMPT.format(
        item_name=item_name,
        modifier_text=modifier_text,
        allowed_options=", ".join(allowed_options),
    )
    context_lines = []
    if latest_message:
        context_lines.append(f"Latest user message: {latest_message}")

    messages: list[dict] = [{"role": "system", "content": system}]
    if context_lines:
        messages.append({"role": "system", "content": "\n".join(context_lines)})
    messages.append({"role": "user", "content": modifier_text})

    try:
        response = await _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=120,
            temperature=0,
            response_format={"type": "json_object"},
        )
    except OpenAIError as e:
        raise AIServiceError(f"OpenAI request failed: {e}") from e

    try:
        return ClosestModifierResolution(**json.loads(response.choices[0].message.content))
    except Exception as e:
        raise AIServiceError(f"Failed to parse closest modifier resolution: {e}") from e


async def polish_food_order_reply(
    order_state: dict,
    order_outcome: dict,
    latest_message: str,
    message_history: list[Message] | None = None,
) -> str:
    history = openai_chat_history_from_messages(message_history)
    prompt_order_state = format_money_context_for_prompt(order_state)
    prompt_order_outcome = format_money_context_for_prompt(order_outcome)
    system = POLISH_FOOD_ORDER_REPLY_SYSTEM_PROMPT.format(
        order_state=json.dumps(prompt_order_state, ensure_ascii=False),
        order_outcome=json.dumps(prompt_order_outcome, ensure_ascii=False),
    )
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

    print("[reply] order_outcome:", prompt_order_outcome)
    print("[reply] final_order_state_for_prompt:", prompt_order_state)
    print("[reply] raw_cashier_reply:", response.choices[0].message.content.strip())
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
