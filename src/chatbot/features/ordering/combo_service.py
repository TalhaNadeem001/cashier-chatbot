from src.chatbot.api.schema import ChatbotResponse
from src.menu.infrastructure.repository import menu_repository

async def detect_and_attach_combo(order_items: list[dict], response: ChatbotResponse) -> ChatbotResponse:
    best_combo = await _find_best_matching_combo(order_items)
    previous_combo = (response.order_state or {}).get("combo")
    is_new_combo = previous_combo is None or previous_combo.get("name") != (best_combo or {}).get("name")
    return await _apply_combo(response, best_combo, is_new_combo)


async def _find_best_matching_combo(order_items: list[dict]) -> dict | None:
    order_counts = await _build_order_counts(order_items)

    matched = [
        combo
        for combo in menu_repository.get_combo_catalog()
        if await _combo_matches_order(combo, order_counts)
    ]
    if not matched:
        return None

    matched.sort(key=lambda c: c.get("price", 0), reverse=True)
    return matched[0]


async def _build_order_counts(order_items: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in order_items:
        key = item["name"].lower()
        counts[key] = counts.get(key, 0) + item["quantity"]
    return counts


async def _combo_matches_order(combo: dict, order_counts: dict[str, int]) -> bool:
    for combo_item in combo.get("items", []):
        name = combo_item.get("item_name", "").lower()
        quantity = combo_item.get("quantity", 1)
        canonical = await _resolve_canonical_name(name)
        if canonical is None or order_counts.get(canonical, 0) < quantity:
            return False
    return True


async def _resolve_canonical_name(name: str) -> str | None:
    item_index = menu_repository.get_item_index()
    if name in item_index:
        return name
    if name.endswith("s") and name[:-1] in item_index:
        return name[:-1]
    return None


async def _apply_combo(response: ChatbotResponse, combo: dict | None, is_new_combo: bool) -> ChatbotResponse:
    if combo is not None:
        return await _attach_combo(response, combo, is_new_combo)
    return await _remove_combo(response)


async def _attach_combo(response: ChatbotResponse, combo: dict, is_new_combo: bool) -> ChatbotResponse:
    updated_order_state = {**(response.order_state or {}), "combo": combo}
    message = response.chatbot_message
    if is_new_combo:
        message += await _build_combo_acknowledgement(combo)
    return response.model_copy(update={
        "order_state": updated_order_state,
        "chatbot_message": message,
    })


async def _remove_combo(response: ChatbotResponse) -> ChatbotResponse:
    order_state = response.order_state
    if not order_state or "combo" not in order_state:
        return response
    updated_order_state = {k: v for k, v in order_state.items() if k != "combo"}
    return response.model_copy(update={"order_state": updated_order_state})


async def _build_combo_acknowledgement(combo: dict) -> str:
    price = combo.get("price", 0)
    return f"\n\nBy the way, that's our {combo['name']} combo deal at ${price / 100:.2f}!"
