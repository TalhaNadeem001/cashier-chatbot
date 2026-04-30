import json
from dataclasses import dataclass

from src.chatbot.clarification.prompts import (
    AMBIGUOUS_MATCH_RESOLUTION_SYSTEM_PROMPT,
    MODIFIER_RESOLUTION_SYSTEM_PROMPT,
    NOT_FOUND_ITEM_RESOLUTION_SYSTEM_PROMPT,
)
from src.chatbot.llm_client import generate_model, generate_text
from src.chatbot.internal_schemas import ModifierResolutionResult
from src.chatbot.llm_messages import chat_history_from_messages
from src.chatbot.schema import Message
from src.chatbot.structured_schemas import AmbiguousMatchResolutionPayload

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
    history = chat_history_from_messages(message_history)
    print(f"history: {history}")
    print(f"latest_message: {latest_message}")
    messages: list[dict] = [
        {"role": "system", "content": system_content},
        *history,
        {"role": "user", "content": latest_message},
    ]
    data = await generate_model(
        messages,
        AmbiguousMatchResolutionPayload,
        temperature=0,
    )
    print(f"raw: {data.model_dump(mode='json')}")
    return AmbiguousMatchResolution(
        confident=data.confident,
        canonical=data.canonical,
        clarification_message=data.clarification_message,
    )


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
    history = chat_history_from_messages(message_history)
    messages: list[dict] = [
        {"role": "system", "content": system_content},
        *history,
        {"role": "user", "content": latest_message},
    ]
    return await generate_text(messages, temperature=0)


async def resolve_modifiers_for_item(
    details: str,
    item_name: str,
    available_options: list[dict],
    existing_modifiers: list[dict] | None = None,
) -> ModifierResolutionResult:
    slim_options = [
        {
            "modifierId": opt["modifierId"],
            "name": opt["name"],
            "groupId": opt["groupId"],
            "groupName": opt["groupName"],
            "price": opt.get("price", 0),
            "maxAllowed": opt.get("maxAllowed", 0),
        }
        for opt in available_options
    ]
    slim_existing = [
        {"modifierId": m["modifierId"], "name": m["name"], "groupId": m.get("groupId"), "groupName": m.get("groupName")}
        for m in (existing_modifiers or [])
    ]
    system_content = MODIFIER_RESOLUTION_SYSTEM_PROMPT.format(
        item_name=item_name,
        options_json=json.dumps(slim_options, ensure_ascii=False),
        existing_modifiers_json=json.dumps(slim_existing, ensure_ascii=False),
    )
    messages: list[dict] = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": details},
    ]
    return await generate_model(messages, ModifierResolutionResult, temperature=0)
