import json

from src.chatbot.cart.utils import format_money_context_for_prompt
from src.chatbot.gemini_client import generate_model, generate_text
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
from src.chatbot.llm_messages import chat_history_from_messages
from src.chatbot.schema import Message


async def supervise_order_state(
    proposed_order_state: dict,
    latest_message: str,
    message_history: list[Message] | None = None,
) -> OrderSupervisionResult:
    history = chat_history_from_messages(message_history)
    context_lines = [
        f"Proposed order state: {proposed_order_state}",
    ]
    messages: list[dict] = [
        {"role": "system", "content": SUPERVISE_ORDER_STATE_SYSTEM_PROMPT},
        {"role": "system", "content": "\n".join(context_lines)},
        *history,
        {"role": "user", "content": latest_message},
    ]
    return await generate_model(
        messages,
        OrderSupervisionResult,
        temperature=0,
    )


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
    return await generate_model(
        messages,
        ClosestModifierResolution,
        temperature=0,
    )


async def polish_food_order_reply(
    order_state: dict,
    order_outcome: dict,
    latest_message: str,
    message_history: list[Message] | None = None,
) -> str:
    history = chat_history_from_messages(message_history)
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
    response_text = await generate_text(
        messages,
        temperature=0.4,
    )

    print("[reply] order_outcome:", prompt_order_outcome)
    print("[reply] final_order_state_for_prompt:", prompt_order_state)
    print("[reply] raw_cashier_reply:", response_text)
    return response_text


async def resolve_order_finalization(
    latest_message: str,
    order_state: dict,
    message_history: list[Message] | None = None,
) -> OrderFinalizationIntent:
    history = chat_history_from_messages(message_history)
    system = RESOLVE_ORDER_FINALIZATION_SYSTEM_PROMPT.format(order_state=order_state)
    messages: list[dict] = [
        {"role": "system", "content": system},
        *history,
        {"role": "user", "content": latest_message},
    ]
    return await generate_model(
        messages,
        OrderFinalizationIntent,
        temperature=0,
    )
