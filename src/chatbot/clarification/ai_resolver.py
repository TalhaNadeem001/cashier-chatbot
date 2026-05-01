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
from src.chatbot.structured_schemas import AmbiguousMatchResolutionPayload, SemanticCandidateFilterPayload
from src.chatbot.prompts import SEMANTIC_CANDIDATE_FILTER_SYSTEM_PROMPT

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


async def resolve_semantic_candidate_matches(
    candidates: list[dict],
    user_query: str,
) -> list[dict]:
    """Filter fuzzy/menu candidates by semantic match to the customer's raw request.

    Returns original candidate objects, unchanged and in original order.
    Returns [] when no candidate semantically matches.
    """
    if not candidates:
        return []

    normalized_query = (user_query or "").strip()
    if not normalized_query:
        return []

    slim_candidates = _slim_semantic_candidates(candidates)

    system_content = SEMANTIC_CANDIDATE_FILTER_SYSTEM_PROMPT.format(
        candidates_json=json.dumps(slim_candidates, ensure_ascii=False),
    )

    messages: list[dict] = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": normalized_query},
    ]

    data = await generate_model(
        messages,
        SemanticCandidateFilterPayload,
        temperature=0,
    )

    requested_keys = set(data.matching_candidate_keys)

    # Only accept keys we generated. Ignore unknown keys from the model.
    valid_keys = {f"c{i}" for i in range(len(candidates))}
    selected_keys = requested_keys & valid_keys

    # Preserve original candidate ranking/order.
    return [
        candidate
        for index, candidate in enumerate(candidates)
        if f"c{index}" in selected_keys
    ]


def _candidate_name(candidate: dict | str) -> str:
    if isinstance(candidate, str):
        return candidate

    return (
        candidate.get("name")
        or candidate.get("itemName")
        or candidate.get("title")
        or ""
    )


def _candidate_category(candidate: dict | str) -> str | None:
    if not isinstance(candidate, dict):
        return None

    return (
        candidate.get("category")
        or candidate.get("categoryName")
        or candidate.get("groupName")
        or candidate.get("menuCategory")
    )


def _candidate_description(candidate: dict | str) -> str | None:
    if not isinstance(candidate, dict):
        return None

    return (
        candidate.get("description")
        or candidate.get("desc")
        or candidate.get("details")
    )


def _extract_modifier_like_names(candidate: dict | str, *, limit: int = 80) -> list[str]:
    """Extract modifier/addon names from common candidate shapes.

    Intentionally defensive because menu item dicts may vary between
    Clover normalization, cached menu data, and matcher candidates.
    """
    if not isinstance(candidate, dict):
        return []

    names: list[str] = []

    def add_name(value: object) -> None:
        if isinstance(value, str) and value.strip():
            names.append(value.strip())

    def walk(value: object) -> None:
        if len(names) >= limit:
            return

        if isinstance(value, dict):
            add_name(value.get("name"))
            add_name(value.get("modifierName"))
            add_name(value.get("optionName"))

            for nested_key in (
                "modifiers",
                "modifierGroups",
                "modifier_groups",
                "options",
                "items",
                "addons",
                "addOns",
                "modifierOptions",
            ):
                nested = value.get(nested_key)
                if nested is not None:
                    walk(nested)

        elif isinstance(value, list):
            for item in value:
                if len(names) >= limit:
                    break
                walk(item)

    for key in (
        "modifiers",
        "modifierGroups",
        "modifier_groups",
        "options",
        "addons",
        "addOns",
        "modifierOptions",
    ):
        if key in candidate:
            walk(candidate[key])

    seen: set[str] = set()
    deduped: list[str] = []
    for name in names:
        normalized = name.lower()
        if normalized not in seen:
            seen.add(normalized)
            deduped.append(name)

    return deduped[:limit]


def _slim_semantic_candidates(candidates: list[dict | str]) -> list[dict]:
    slim: list[dict] = []

    for index, candidate in enumerate(candidates):
        candidate_key = f"c{index}"
        name = _candidate_name(candidate)

        slim_candidate: dict = {
            "candidate_key": candidate_key,
            "name": name,
        }

        category = _candidate_category(candidate)
        if category:
            slim_candidate["category"] = category

        description = _candidate_description(candidate)
        if description:
            slim_candidate["description"] = description

        modifier_names = _extract_modifier_like_names(candidate)
        if modifier_names:
            slim_candidate["available_modifier_or_addon_names"] = modifier_names

        if isinstance(candidate, dict):
            aliases = candidate.get("aliases")
            if isinstance(aliases, list) and aliases:
                slim_candidate["aliases"] = [
                    alias for alias in aliases
                    if isinstance(alias, str) and alias.strip()
                ]

            leftover_words = candidate.get("leftover_words")
            if isinstance(leftover_words, list) and leftover_words:
                slim_candidate["leftover_words"] = [
                    word for word in leftover_words
                    if isinstance(word, str) and word.strip()
                ]

        slim.append(slim_candidate)

    return slim
