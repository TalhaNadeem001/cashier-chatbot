from src.menu.loader import _MENU_DATA
from src.chatbot.schema import ChatbotResponse

async def detect_and_attach_combo(order_items: list[dict], response: ChatbotResponse) -> ChatbotResponse:
    best_combo = await _find_best_matching_combo(order_items)
    return await _apply_combo(response, best_combo)


async def _find_best_matching_combo(order_items: list[dict]) -> dict | None:
    combos: dict = _MENU_DATA.get("menu", {}).get("combos", {})
    canonical_names: set[str] = set(_MENU_DATA.get("menu", {}).get("items", {}).keys())
    order_counts = await _build_order_counts(order_items)

    matched = [
        combo_info
        for combo_key, combo_info in combos.items()
        if await _combo_matches_order(combo_key, order_counts, canonical_names)
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


async def _combo_matches_order(combo_key: str, order_counts: dict[str, int], canonical_names: set[str]) -> bool:
    combo_items = await _parse_combo_key(combo_key)
    if not combo_items:
        return False
    for qty, name in combo_items:
        canonical = await _resolve_canonical_name(name, canonical_names)
        if canonical is None or order_counts.get(canonical, 0) < qty:
            return False
    return True


async def _resolve_canonical_name(name: str, canonical_names: set[str]) -> str | None:
    if name in canonical_names:
        return name
    if name.endswith("s") and name[:-1] in canonical_names:
        return name[:-1]
    return None


async def _parse_combo_key(combo_key: str) -> list[tuple[int, str]]:
    parts: list[tuple[int, str]] = []
    for segment in combo_key.split(","):
        tokens = segment.strip().split(" ", 1)
        if len(tokens) == 2:
            try:
                parts.append((int(tokens[0]), tokens[1].strip().lower()))
            except ValueError:
                pass
    return parts


async def _apply_combo(response: ChatbotResponse, combo: dict | None) -> ChatbotResponse:
    if combo is not None:
        return await _attach_combo(response, combo)
    return await _remove_combo(response)


async def _attach_combo(response: ChatbotResponse, combo: dict) -> ChatbotResponse:
    updated_order_state = {**(response.order_state or {}), "combo": combo}
    return response.model_copy(update={
        "order_state": updated_order_state,
        "chatbot_message": response.chatbot_message + await _build_combo_acknowledgement(combo),
    })


async def _remove_combo(response: ChatbotResponse) -> ChatbotResponse:
    order_state = response.order_state
    if not order_state or "combo" not in order_state:
        return response
    updated_order_state = {k: v for k, v in order_state.items() if k != "combo"}
    return response.model_copy(update={"order_state": updated_order_state})


async def _build_combo_acknowledgement(combo: dict) -> str:
    return f"\n\nBy the way, that's our {combo['original name']} combo deal at ${combo['price']:.2f}!"
