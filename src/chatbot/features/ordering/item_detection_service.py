from rapidfuzz import process

from src.chatbot.api.schema import ChatbotResponse
from src.chatbot.features.clarification.constants import MODS_CONFIRMED_THRESHOLD
from src.chatbot.features.clarification.fuzzy_matcher import _combined_scorer
from src.menu.infrastructure.repository import menu_repository

_WINGS_FLAVORS_STR = (
    "• Naked\n• Lemon Pepper Seasoning\n• Nashville Seasoning\n• Honey Mustard\n"
    "• Garlic Parm\n• Spicy Garlic Parm\n• Hot Honey\n• Sweet n Spicy\n• BBQ\n• Buffalo\n• Chili Mango"
)

_WINGS_QUANTITY_FLAVORS = {6: 1, 12: 2, 18: 3, 24: 4, 30: 5}

async def validate_order_items(order_items: list[dict], response: ChatbotResponse) -> ChatbotResponse:
    for item in order_items:
        name = item.get("name", "")
        if menu_repository.get_item_definition(name) is not None:
            response = await detect_non_default_items(item, name, response)
            response = await detect_mods_allowed(item, name, response)
        else:
            response.chatbot_message += "\n\nSorry, this is not an allowed modifier"
    return response

async def detect_mods_allowed(order_items: dict, item_name: str, response: ChatbotResponse) -> ChatbotResponse:
    users_mods = order_items.get("modifier", "").split(",") if order_items.get("modifier") else []
    response = await validate_mod_selections(item_name, users_mods, order_items, response)
    return response

async def validate_mod_selections(item_name: str, users_mods: list[str], order_item: dict, response: ChatbotResponse) -> ChatbotResponse:
    item_data = menu_repository.get_item_definition(item_name) or {}
    allowed_names: list[str] = []
    for group in item_data.get("modifier_groups", []):
        for mod in group.get("modifiers", []):
            n = mod.get("name")
            if n:
                allowed_names.append(str(n))
    if not allowed_names:
        return response

    valid_mods: list[str] = []

    for mod_text in users_mods:
        mod_text = mod_text.strip()
        if not mod_text:
            continue
        result = process.extractOne(mod_text, allowed_names, scorer=_combined_scorer)
        if result is None or result[1] < MODS_CONFIRMED_THRESHOLD:
            response.chatbot_message += (
                f'\n\n"{mod_text}" is not a valid modifier for {item_name}. '
                f"Allowed options are: {', '.join(allowed_names)}"
            )
        else:
            valid_mods.append(mod_text)

    order_item["modifier"] = ", ".join(valid_mods)
    return response

async def detect_non_default_items(ordered_item: dict, item_name: str, response: ChatbotResponse) -> ChatbotResponse:
    category = await menu_repository.get_item_category(item_name)
    modifier = ordered_item.get("modifier", "")
    if category == "Smash Burgers" and not await is_patties_in_mods(modifier):
        response.chatbot_message += (
            f"\n\nFor your {item_name}, how many patties would you like?\n"
            "• Single — $8.99\n"
            "• Double — $11.99\n"
            "• Triple — $14.99\n"
            "• Quadruple — $16.99"
        )
    if category in ["Boneless Wings", "Bone-In Breaded Wings"]:
        quantity = ordered_item.get("quantity", 1)
        if quantity not in [6, 12, 18, 24, 30]:
            response.chatbot_message += (
                f"\n\nPlease specify the quantity of {item_name} you would like. "
                "Allowed quantities are 6, 12, 18, 24, 30"
                "\n\nFor the 6 piece you can choose 1 flavor, 12 piece you can choose 2 flavors, "
                "18 piece you can choose 3 flavors, 24 piece you can choose 4 flavors, 30 piece you can choose 5 flavors"
                f"\n\nAvailable flavors:\n{_WINGS_FLAVORS_STR}"
            )
        elif not modifier:
            response.chatbot_message += (
                f"\n\nWhat flavor(s) would you like for your {item_name}?\n\n{_WINGS_FLAVORS_STR}"
            )
        else:
            max_flavors = await _max_wings_flavors(quantity)
            if max_flavors is not None:
                flavor_count = len([f for f in modifier.split(",") if f.strip()])
                if flavor_count > max_flavors:
                    response.chatbot_message += (
                        f"\n\nYou can only choose {max_flavors} flavor(s) for a {quantity} piece {item_name}. "
                        f"You selected {flavor_count}. Please reduce your selection."
                    )
    return response


async def _max_wings_flavors(quantity: int) -> int | None:
    return _WINGS_QUANTITY_FLAVORS.get(quantity)


async def is_patties_in_mods(item_mods: str) -> bool:
    patties = ["single", "double", "triple", "quadruple"]
    item_mods = item_mods.lower() if item_mods else ""
    for patty in patties:
        if patty in item_mods:
            return True
    return False
