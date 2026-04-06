from src.chatbot.schema import BotInteractionRequest
import json

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
