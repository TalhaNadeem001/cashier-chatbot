from rapidfuzz import process

from src.menu.loader import _MENU_DATA
from src.menu.loader import get_item_category
from src.chatbot.schema import ChatbotResponse
from src.chatbot.clarification.fuzzy_matcher import _combined_scorer, CONFIRMED_THRESHOLD, MODS_CONFIRMED_THRESHOLD

async def validate_order_items(order_items: list[dict], response: ChatbotResponse) -> ChatbotResponse:
    for item in order_items:
        name = item.get("name", "")
        if name in _MENU_DATA.get("menu", {}).get("items", {}).keys():
            response = await detect_non_default_items(item, name, response)
            response = await detect_mods_allowed(item, name, response)
        else:
            response.chatbot_message += f"\n\nSorry, this is not an allowed modifier"
    return response

async def detect_mods_allowed(order_items: dict, item_name: str, response: ChatbotResponse) -> ChatbotResponse:
    users_mods = order_items.get("modifier", "").split(",") if order_items.get("modifier") else []
    response = await validate_mod_selections(item_name, users_mods, order_items, response)
    return response

async def validate_mod_selections(item_name: str, users_mods: list[str], order_item: dict, response: ChatbotResponse) -> ChatbotResponse:
    item_data = _MENU_DATA.get("menu", {}).get("items", {}).get(item_name, {})
    allowed_names: list[str] = []
    for mod in item_data.get("mods", {}).values():
        allowed_names.extend(opt["name"] for opt in mod.get("options", []))
    print(f"allowed_names: {allowed_names}")
    if not allowed_names:
        return response

    valid_mods: list[str] = []

    for mod_text in users_mods:
        print(f"mod_text: {mod_text}")
        mod_text = mod_text.strip()
        if not mod_text:
            continue
        result = process.extractOne(mod_text, allowed_names, scorer=_combined_scorer)
        print(f"result: {result}")
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
    category = await get_item_category(item_name)
    modifier = ordered_item.get("modifier", "")
    print(f"name: {item_name}, category: {category}, modifier: {modifier}")
    if category == "Smash Burgers" and not await is_patties_in_mods(modifier):
        response.chatbot_message += (
            f"\n\nFor your {item_name}, how many patties would you like?\n"
            "• Single — $8.99\n"
            "• Double — $11.99\n"
            "• Triple — $14.99\n"
            "• Quadruple — $16.99"
        )
    if category in ["Boneless Wings", "Bone-In Breaded Wings"]:
        ...
    return response
    

async def is_patties_in_mods(item_mods: str) -> bool:
    patties = ["single", "double", "triple", "quadruple"]
    item_mods = item_mods.lower() if item_mods else ""
    for patty in patties:
        if patty in item_mods:
            return True
    return False