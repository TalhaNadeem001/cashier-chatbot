from src.cache import cache_get
from src.chatbot.visibility.constants import (
    RESTAURANT_NAME_KEY,
    RESTAURANT_CITY_KEY,
    RESTAURANT_PHONE_KEY,
    RESTAURANT_TAGLINE_KEY,
    RESTAURANT_GREETING_KEY,
    RESTAURANT_CONTEXT_JSON_KEY,
)
from src.chatbot.schema import BotInteractionRequest, ChatbotResponse
from src.menu.loader import get_order_item_line_total, get_order_item_unit_price, order_item_uses_quantity_selection
import json

async def fetch_restaurant_profile(user_id: str) -> dict:
    profile_json = await _get_restaurant_profile_json(user_id)
    profile_fields = await _get_restaurant_profile_fields(user_id)
    return {**profile_fields, **profile_json}


async def _get_restaurant_profile_fields(user_id: str) -> dict[str, str]:
    name = await cache_get(RESTAURANT_NAME_KEY.format(user_id=user_id))
    city = await cache_get(RESTAURANT_CITY_KEY.format(user_id=user_id))
    phone = await cache_get(RESTAURANT_PHONE_KEY.format(user_id=user_id))
    tagline = await cache_get(RESTAURANT_TAGLINE_KEY.format(user_id=user_id))
    greeting = await cache_get(RESTAURANT_GREETING_KEY.format(user_id=user_id))
    return {
        "restaurantName": name or "",
        "city": city or "",
        "phone": phone or "",
        "tagline": tagline or "",
        "greeting": greeting or "",
    }


async def _get_restaurant_profile_json(user_id: str) -> dict[str, str]:
    raw = await cache_get(RESTAURANT_CONTEXT_JSON_KEY.format(user_id=user_id))
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if isinstance(parsed, dict):
        return {k: str(v) for k, v in parsed.items() if v is not None}
    return {}


async def _build_name_location(profile: dict[str, str]) -> str | None:
    name_location = profile.get("nameLocation")
    if name_location:
        return name_location

    name = profile.get("restaurantName", "").strip()
    city = profile.get("city", "").strip()
    if name and city:
        return f"{name}, {city}"
    if name:
        return name
    return None


async def build_restaurant_context(profile: dict[str, str]) -> str | None:
    lines: list[str] = []
    name = profile.get("restaurantName", "").strip()
    tagline = profile.get("tagline", "").strip()
    phone = profile.get("phone", "").strip()
    city = profile.get("city", "").strip()
    greeting = profile.get("greeting", "").strip()
    name_location = await _build_name_location(profile)

    if name:
        lines.append(f"Restaurant name: {name}")
    if name_location:
        lines.append(f"Location: {name_location}")
    if city:
        lines.append(f"City: {city}")
    if phone:
        lines.append(f"Phone: {phone}")
    if tagline:
        lines.append(f"Tagline: {tagline}")
    if greeting:
        lines.append(f"Greeting: {greeting}")

    if lines:
        return "\n".join(lines)
    return None

async def parse_name_location(value: str) -> tuple[str, str | None]:
    parts = value.split(",", 1)
    name = parts[0].strip()
    location = parts[1].strip() if len(parts) == 2 else None
    return name, location


async def _get_items(request: BotInteractionRequest) -> list[dict]:
    return (request.order_state or {}).get("items", [])


async def _build_order_lines(items: list[dict]) -> tuple[list[str], float]:
    lines: list[str] = []
    total = 0.0

    for item in items:
        line, line_total = await _format_line_item(item)

        if line_total is not None:
            total += line_total

        lines.append(line)

    return lines, total


async def _format_line_item(item: dict) -> tuple[str, float | None]:
    name = item.get("name", "Unknown item")
    modifier = item.get("modifier")

    raw_unit_price = get_order_item_unit_price(item)
    raw_line_total = get_order_item_line_total(item)
    price = raw_unit_price / 100 if raw_unit_price is not None else None
    line_total = raw_line_total / 100 if raw_line_total is not None else None

    label = await _build_item_label(name, price, item, modifier)

    if line_total is None:
        qty_prefix = await _quantity_prefix(item)
        return f"- {qty_prefix}{label}", None

    qty_prefix = await _quantity_prefix(item)
    return f"- {qty_prefix}{label} = ${line_total:.2f}", line_total


async def _build_item_label(name: str, price: float | None, item: dict, modifier: str | None) -> str:
    quantity = int(item.get("quantity", 1) or 1)
    quantity_is_selection = order_item_uses_quantity_selection(item)

    if price is not None:
        if quantity > 1 and not quantity_is_selection:
            label = f"{name} (${price:.2f} each)"
        else:
            label = f"{name} (${price:.2f})"
    else:
        label = name

    if modifier:
        label += f" [{modifier}]"

    return label


async def _quantity_prefix(item: dict) -> str:
    quantity = int(item.get("quantity", 1) or 1)
    if quantity <= 1 or order_item_uses_quantity_selection(item):
        return ""
    return f"{quantity}x "


async def _format_order_message(lines: list[str], total: float) -> str:
    items_text = "\n".join(lines)
    total_line = f"\n\nTotal: ${total:.2f}" if total > 0 else ""

    return (
        f"Great! Your order is:\n"
        f"{items_text}"
        f"{total_line}\n\n"
        f"Thank you for ordering!"
    )


async def _build_empty_order_response(request: BotInteractionRequest) -> ChatbotResponse:
    return ChatbotResponse(
        chatbot_message="It looks like you haven't ordered anything yet — what would you like?",
        order_state=request.order_state,
    )
