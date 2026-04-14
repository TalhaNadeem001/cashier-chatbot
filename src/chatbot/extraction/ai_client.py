import json
from src.chatbot.gemini_client import generate_model
from src.chatbot.extraction.prompts import APPLY_ORDER_DELTA_SYSTEM_PROMPT, EXTRACT_ORDER_ITEMS_SYSTEM_PROMPT, EXTRACT_ADD_ITEMS_SYSTEM_PROMPT, EXTRACT_MODIFY_ITEMS_SYSTEM_PROMPT, EXTRACT_SWAP_ITEMS_SYSTEM_PROMPT, RESOLVE_CONFIRMATION_SYSTEM_PROMPT, RESOLVE_REMOVE_ITEM_SYSTEM_PROMPT, EXTRACT_PENDING_MOD_SELECTIONS_SYSTEM_PROMPT
from src.chatbot.llm_messages import chat_history_from_messages
from src.chatbot.schema import AddItemsResult, Message, ModifyItem, OrderDeltaResult, OrderItem, SwapItems
from src.menu.loader import get_menu_context
from src.chatbot.structured_schemas import ModifyItemsResult, OrderItemsResult, PendingModifierSelections

_DELTA_HISTORY_TAIL = 6


def build_delta_history_messages(message_history: list[Message] | None) -> list[dict[str, str]]:
    return chat_history_from_messages(message_history, tail=_DELTA_HISTORY_TAIL)


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
    result = await generate_model(
        messages,
        OrderDeltaResult,
        temperature=0,
    )
    print("[delta] raw_llm_output:", result.model_dump(mode="json"))
    return result


async def extract_order_items(
    latest_message: str,
    message_history: list[Message] | None = None,
) -> list[OrderItem]:
    history = chat_history_from_messages(message_history)
    system = EXTRACT_ORDER_ITEMS_SYSTEM_PROMPT
    messages = [
        {"role": "system", "content": system},
        *history,
        {"role": "user", "content": latest_message},
    ]
    result = await generate_model(
        messages,
        OrderItemsResult,
        temperature=0,
    )
    return result.items


async def extract_add_items(
    latest_message: str,
    order_state: dict,
    message_history: list[Message] | None = None,
) -> AddItemsResult:
    history = chat_history_from_messages(message_history)

    system = (
        EXTRACT_ADD_ITEMS_SYSTEM_PROMPT
        .replace("{order_state}", str(order_state))
    )
    messages = [
        {"role": "system", "content": system},
        *history,
        {"role": "user", "content": latest_message},
    ]
    return await generate_model(
        messages,
        AddItemsResult,
        temperature=0,
    )


async def extract_modify_items(
    latest_message: str,
    order_state: dict,
    message_history: list[Message] | None = None,
) -> list[ModifyItem]:
    history = chat_history_from_messages(message_history)
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
    result = await generate_model(
        messages,
        ModifyItemsResult,
        temperature=0,
    )
    return result.items


async def extract_swap_items(
    latest_message: str,
    message_history: list[Message] | None = None,
) -> SwapItems:
    history = chat_history_from_messages(message_history)
    messages = [
        {"role": "system", "content": EXTRACT_SWAP_ITEMS_SYSTEM_PROMPT},
        *history,
        {"role": "user", "content": latest_message},
    ]
    return await generate_model(
        messages,
        SwapItems,
        temperature=0,
    )


async def resolve_remove_item(
    latest_message: str,
    message_history: list[Message] | None = None,
) -> list[OrderItem]:
    history = chat_history_from_messages(message_history)
    messages = [
        {"role": "system", "content": RESOLVE_REMOVE_ITEM_SYSTEM_PROMPT},
        *history,
        {"role": "user", "content": latest_message},
    ]
    result = await generate_model(
        messages,
        OrderItemsResult,
        temperature=0,
    )
    return result.items


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
    result = await generate_model(
        messages,
        PendingModifierSelections,
        temperature=0,
    )
    return result.selected_mods


async def resolve_confirmation(
    latest_message: str,
    message_history: list[Message] | None = None,
) -> list[OrderItem]:
    history = chat_history_from_messages(message_history)
    messages = [
        {"role": "system", "content": RESOLVE_CONFIRMATION_SYSTEM_PROMPT},
        *history,
        {"role": "user", "content": latest_message},
    ]
    result = await generate_model(
        messages,
        OrderItemsResult,
        temperature=0,
    )
    return result.items
