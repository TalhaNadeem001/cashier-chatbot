from src.chatbot.internal_schemas import ComboApplicationResult, ComboEvent
from src.menu.loader import _combos, _items_by_name


async def apply_best_combo(
    order_state: dict,
    previous_combo: dict | None = None,
) -> ComboApplicationResult:
    order_items = order_state.get("items", [])
    best_combo = await _find_best_matching_combo(order_items)

    previous_combo_name = (previous_combo or {}).get("name")
    current_combo_name = (best_combo or {}).get("name")

    combo_event: ComboEvent | None = None
    updated_order_state = dict(order_state)

    if best_combo is not None:
        updated_order_state["combo"] = best_combo
        if previous_combo_name != current_combo_name:
            combo_event = ComboEvent(
                kind="attached",
                combo_name=best_combo["name"],
                combo_price=best_combo.get("price"),
            )
    else:
        updated_order_state = {k: v for k, v in updated_order_state.items() if k != "combo"}
        if previous_combo_name:
            combo_event = ComboEvent(
                kind="removed",
                combo_name=previous_combo_name,
                combo_price=(previous_combo or {}).get("price"),
            )

    return ComboApplicationResult(
        order_state=updated_order_state,
        combo_event=combo_event,
    )


async def _find_best_matching_combo(order_items: list[dict]) -> dict | None:
    order_counts = await _build_order_counts(order_items)

    matched = [
        combo
        for combo in _combos
        if await _combo_matches_order(combo, order_counts)
    ]
    if not matched:
        return None

    matched.sort(key=lambda combo: combo.get("price", 0), reverse=True)
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
    if name in _items_by_name:
        return name
    if name.endswith("s") and name[:-1] in _items_by_name:
        return name[:-1]
    return None
