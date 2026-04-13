import json
from src.chatbot.schema import BotInteractionRequest

_PROMPT_CENTS_KEYS = {"unit_price", "item_total", "order_total", "combo_price"}


def _item_key(item: dict) -> tuple:
    return (
        item.get("name"),
        item.get("modifier"),
    )


def _dedupe_modifier_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        text = str(value).strip()
        normalized = text.lower()
        if not text or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(text)
    return deduped


def _modifier_from_item(item: dict) -> str | None:
    modifier = item.get("modifier")
    if modifier is not None:
        text = str(modifier).strip()
        if text:
            return text

    selected_mods = item.get("selected_mods") or {}
    if not isinstance(selected_mods, dict):
        return None

    flattened: list[str] = []
    for value in selected_mods.values():
        if isinstance(value, list):
            flattened.extend(str(v).strip() for v in value if str(v).strip())
        elif value is not None and str(value).strip():
            flattened.append(str(value).strip())

    flattened = _dedupe_modifier_values(flattened)
    if not flattened:
        return None
    return ", ".join(flattened)


def strip_order_state_for_delta(order_state: dict | None) -> dict:
    items: list[dict] = []
    for raw_item in (order_state or {}).get("items", []):
        name = str(raw_item.get("name", "")).strip()
        quantity = int(raw_item.get("quantity", 0) or 0)
        if not name or quantity <= 0:
            continue
        item = {
            "name": name,
            "quantity": quantity,
            "modifier": _modifier_from_item(raw_item),
        }
        items.append(item)
    return {"items": items}


def normalize_order_items(items: list[dict]) -> list[dict]:
    merged: dict[tuple, dict] = {}

    for raw_item in items:
        name = str(raw_item.get("name", "")).strip()
        quantity = int(raw_item.get("quantity", 0) or 0)
        if not name or quantity <= 0:
            continue

        modifier = raw_item.get("modifier")
        modifier_text = str(modifier).strip() if modifier is not None else ""

        item = {
            "name": name,
            "quantity": quantity,
            "modifier": modifier_text or None,
        }

        key = _item_key(item)
        if key in merged:
            merged[key]["quantity"] += quantity
        else:
            merged[key] = item

    return list(merged.values())


def build_order_update_message(items: list[dict]) -> str:
    if not items:
        return "Your order is now empty. What would you like to order?"

    parts: list[str] = []
    for item in items:
        quantity = item.get("quantity", 1)
        name = item.get("name", "item")
        modifier = item.get("modifier")
        label = f"{quantity}x {name}"
        if modifier:
            label += f" ({modifier})"
        parts.append(label)

    return f"Got it! Your order is now {', '.join(parts)}. Is that all?"


def format_money_context_for_prompt(value, parent_key: str | None = None):
    if isinstance(value, dict):
        formatted: dict = {}
        for key, raw_value in value.items():
            if (
                isinstance(raw_value, (int, float))
                and not isinstance(raw_value, bool)
                and (key in _PROMPT_CENTS_KEYS or (key == "price" and parent_key == "combo"))
            ):
                formatted[key] = f"${raw_value / 100:.2f}"
            else:
                formatted[key] = format_money_context_for_prompt(raw_value, parent_key=key)
        return formatted

    if isinstance(value, list):
        return [format_money_context_for_prompt(item, parent_key=parent_key) for item in value]

    return value

async def extract_items(request: BotInteractionRequest) -> list[dict]:
        items = (request.order_state or {}).get("items", [])
        expanded: list[dict] = []
        for item in items:
            quantity = item.get("quantity", 1)
            base = {k: v for k, v in item.items() if k != "quantity"}
            for _ in range(quantity):
                expanded.append(dict(base))
        return expanded

async def merge_modifier_items(items: list[dict]) -> list[dict]:
    groups: dict[tuple, dict] = {}
    counts: dict[tuple, int] = {}
    for item in items:
        key = (
            item.get("name"),
            item.get("modifier"),
            json.dumps(item.get("selected_mods"), sort_keys=True),
        )
        if key not in groups:
            groups[key] = dict(item)
            counts[key] = 0
        counts[key] += 1
    result = []
    for key, base in groups.items():
        base["quantity"] = counts[key]
        result.append(base)
    return result
