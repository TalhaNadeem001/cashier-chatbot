import json
from openai import AsyncOpenAI, OpenAIError
from src.chatbot.exceptions import AIServiceError
from src.chatbot.extraction.prompts import APPLY_ORDER_DELTA_SYSTEM_PROMPT, EXTRACT_ORDER_ITEMS_SYSTEM_PROMPT, EXTRACT_ADD_ITEMS_SYSTEM_PROMPT, EXTRACT_MODIFY_ITEMS_SYSTEM_PROMPT, EXTRACT_SWAP_ITEMS_SYSTEM_PROMPT, RESOLVE_CONFIRMATION_SYSTEM_PROMPT, RESOLVE_REMOVE_ITEM_SYSTEM_PROMPT, EXTRACT_PENDING_MOD_SELECTIONS_SYSTEM_PROMPT
from src.chatbot.openai_messages import openai_chat_history_from_messages
from src.chatbot.schema import AddItemsResult, Message, ModifyItem, OrderDeltaResult, OrderItem, SwapItems
from src.config import settings
from src.menu.loader import get_menu_context

_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

_DELTA_HISTORY_TAIL = 6


def build_delta_history_messages(message_history: list[Message] | None) -> list[dict[str, str]]:
    return openai_chat_history_from_messages(message_history, tail=_DELTA_HISTORY_TAIL)


def get_latest_assistant_context(message_history: list[Message] | None) -> str | None:
    if not message_history:
        return None
    for message in reversed(message_history):
        if message.role == "assistant":
            content = str(message.content).strip()
            return content or None
    return None


async def apply_order_delta(
    latest_message: str,
    order_state: dict,
    message_history: list[Message] | None = None,
) -> OrderDeltaResult:
    system = APPLY_ORDER_DELTA_SYSTEM_PROMPT.replace("{order_state}", json.dumps(order_state, ensure_ascii=False))
    history = build_delta_history_messages(message_history)
    latest_assistant_message = get_latest_assistant_context(message_history)
    print("[delta] latest_message:", latest_message)
    print("[delta] stripped_order_state:", order_state)
    print("[delta] history_window_count:", len(history))
    print("[delta] history_window:", history)
    print("[delta] latest_assistant_context:", latest_assistant_message)
    messages = [
        {"role": "system", "content": system},
        {"role": "system", "content": "Respond with valid JSON only."},
    ]
    if latest_assistant_message:
        messages.append(
            {
                "role": "system",
                "content": f"Most recent assistant message for disambiguation: {latest_assistant_message}",
            }
        )
    messages.extend([
        *history,
        {"role": "user", "content": latest_message},
    ])

    try:
        response = await _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=800,
            temperature=0,
            response_format={"type": "json_object"},
        )
    except OpenAIError as e:
        raise AIServiceError(f"OpenAI request failed: {e}") from e

    raw = json.loads(response.choices[0].message.content)
    print("[delta] raw_llm_output:", raw)
    return OrderDeltaResult(
        items=[OrderItem(**item) for item in raw.get("items", [])],
    )


async def extract_order_items(
    latest_message: str,
    message_history: list[Message] | None = None,
) -> list[OrderItem]:
    history = openai_chat_history_from_messages(message_history)
    system = EXTRACT_ORDER_ITEMS_SYSTEM_PROMPT
    messages = [
        {"role": "system", "content": system},
        *history,
        {"role": "user", "content": latest_message},
    ]

    try:
        response = await _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=500,
            temperature=0,
            response_format={"type": "json_object"},
        )
    except OpenAIError as e:
        raise AIServiceError(f"OpenAI request failed: {e}") from e

    raw = json.loads(response.choices[0].message.content)
    return [OrderItem(**item) for item in raw.get("items", [])]


async def extract_add_items(
    latest_message: str,
    order_state: dict,
    message_history: list[Message] | None = None,
) -> AddItemsResult:
    history = openai_chat_history_from_messages(message_history)

    system = (
        EXTRACT_ADD_ITEMS_SYSTEM_PROMPT
        .replace("{order_state}", str(order_state))
    )
    messages = [
        {"role": "system", "content": system},
        *history,
        {"role": "user", "content": latest_message},
    ]

    try:
        response = await _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=500,
            temperature=0,
            response_format={"type": "json_object"},
        )
    except OpenAIError as e:
        raise AIServiceError(f"OpenAI request failed: {e}") from e

    raw = json.loads(response.choices[0].message.content)
    return AddItemsResult(
        new_items=[OrderItem(**item) for item in raw.get("new_items", [])],
    )


async def extract_modify_items(
    latest_message: str,
    order_state: dict,
    message_history: list[Message] | None = None,
) -> list[ModifyItem]:
    history = openai_chat_history_from_messages(message_history)
    system = (
        EXTRACT_MODIFY_ITEMS_SYSTEM_PROMPT
        .replace("{order_state}", str(order_state))
        .replace("{menu_context}", get_menu_context())
    )
    messages = [
        {"role": "system", "content": system},
        *history,
        {"role": "user", "content": latest_message},
    ]

    try:
        response = await _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=400,
            temperature=0,
            response_format={"type": "json_object"},
        )
    except OpenAIError as e:
        raise AIServiceError(f"OpenAI request failed: {e}") from e

    raw = json.loads(response.choices[0].message.content)
    return [ModifyItem(**item) for item in raw.get("items", [])]


async def extract_swap_items(
    latest_message: str,
    message_history: list[Message] | None = None,
) -> SwapItems:
    history = openai_chat_history_from_messages(message_history)
    messages = [
        {"role": "system", "content": EXTRACT_SWAP_ITEMS_SYSTEM_PROMPT},
        *history,
        {"role": "user", "content": latest_message},
    ]

    try:
        response = await _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=200,
            temperature=0,
            response_format={"type": "json_object"},
        )
    except OpenAIError as e:
        raise AIServiceError(f"OpenAI request failed: {e}") from e

    raw = json.loads(response.choices[0].message.content)
    return SwapItems(
        remove=[OrderItem(**item) for item in raw.get("remove", [])],
        add=[OrderItem(**item) for item in raw.get("add", [])],
    )


async def resolve_remove_item(
    latest_message: str,
    message_history: list[Message] | None = None,
) -> list[OrderItem]:
    history = openai_chat_history_from_messages(message_history)
    messages = [
        {"role": "system", "content": RESOLVE_REMOVE_ITEM_SYSTEM_PROMPT},
        *history,
        {"role": "user", "content": latest_message},
    ]

    try:
        response = await _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=200,
            temperature=0,
            response_format={"type": "json_object"},
        )
    except OpenAIError as e:
        raise AIServiceError(f"OpenAI request failed: {e}") from e

    raw = json.loads(response.choices[0].message.content)
    return [OrderItem(**item) for item in raw.get("items", [])]


async def extract_pending_mod_selections(
    latest_message: str,
    item_name: str,
    missing_mod_groups_text: str,
) -> dict:
    system = (
        EXTRACT_PENDING_MOD_SELECTIONS_SYSTEM_PROMPT
        .replace("{item_name}", item_name)
        .replace("{missing_mod_groups}", missing_mod_groups_text)
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": latest_message},
    ]
    try:
        response = await _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=200,
            temperature=0,
            response_format={"type": "json_object"},
        )
    except OpenAIError as e:
        raise AIServiceError(f"OpenAI request failed: {e}") from e

    raw = json.loads(response.choices[0].message.content)
    return raw.get("selected_mods") or {}


async def resolve_confirmation(
    latest_message: str,
    message_history: list[Message] | None = None,
) -> list[OrderItem]:
    history = openai_chat_history_from_messages(message_history)
    messages = [
        {"role": "system", "content": RESOLVE_CONFIRMATION_SYSTEM_PROMPT},
        *history,
        {"role": "user", "content": latest_message},
    ]

    try:
        response = await _client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=200,
            temperature=0,
            response_format={"type": "json_object"},
        )
    except OpenAIError as e:
        raise AIServiceError(f"OpenAI request failed: {e}") from e

    raw = json.loads(response.choices[0].message.content)
    return [OrderItem(**item) for item in raw.get("items", [])]
