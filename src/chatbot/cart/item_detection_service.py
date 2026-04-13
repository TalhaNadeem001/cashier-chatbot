from rapidfuzz import process

from src.chatbot.clarification.fuzzy_matcher import MODS_CONFIRMED_THRESHOLD, _combined_scorer
from src.chatbot.internal_schemas import (
    ModifierValidationIssue,
    OrderFollowUpRequirement,
    OrderValidationResult,
)
from src.menu.loader import get_item_category, get_item_definition

_WINGS_FLAVORS = [
    "Naked",
    "Lemon Pepper Seasoning",
    "Nashville Seasoning",
    "Honey Mustard",
    "Garlic Parm",
    "Spicy Garlic Parm",
    "Hot Honey",
    "Sweet n Spicy",
    "BBQ",
    "Buffalo",
    "Chili Mango",
]

_WINGS_QUANTITY_FLAVORS = {6: 1, 12: 2, 18: 3, 24: 4, 30: 5}

_BURGER_PATTY_OPTIONS = [
    {"label": "Single", "price": 8.99},
    {"label": "Double", "price": 11.99},
    {"label": "Triple", "price": 14.99},
    {"label": "Quadruple", "price": 16.99},
]


async def validate_order_items(order_items: list[dict]) -> OrderValidationResult:
    modifier_validation = await validate_order_item_modifiers(order_items)
    follow_up_requirements: list[OrderFollowUpRequirement] = []

    for item in modifier_validation.items:
        name = item.get("name", "")
        if get_item_definition(name) is None:
            continue

        item_follow_ups = await detect_non_default_items(item, name)
        follow_up_requirements.extend(item_follow_ups)

    return OrderValidationResult(
        items=modifier_validation.items,
        invalid_modifiers=modifier_validation.invalid_modifiers,
        follow_up_requirements=follow_up_requirements,
    )


async def validate_order_item_modifiers(order_items: list[dict]) -> OrderValidationResult:
    validated_items: list[dict] = []
    invalid_modifiers: list[ModifierValidationIssue] = []

    for item in order_items:
        item_copy = dict(item)
        name = item_copy.get("name", "")
        if get_item_definition(name) is None:
            validated_items.append(item_copy)
            continue

        item_invalid_modifiers = await detect_mods_allowed(item_copy, name)
        invalid_modifiers.extend(item_invalid_modifiers)

        validated_items.append(item_copy)

    return OrderValidationResult(
        items=validated_items,
        invalid_modifiers=invalid_modifiers,
    )


async def detect_mods_allowed(order_item: dict, item_name: str) -> list[ModifierValidationIssue]:
    users_mods = order_item.get("modifier", "").split(",") if order_item.get("modifier") else []
    return await validate_mod_selections(item_name, users_mods, order_item)


async def validate_mod_selections(
    item_name: str,
    users_mods: list[str],
    order_item: dict,
) -> list[ModifierValidationIssue]:
    item_data = get_item_definition(item_name) or {}
    allowed_names: list[str] = []
    for group in item_data.get("modifier_groups", []):
        for mod in group.get("modifiers", []):
            name = mod.get("name")
            if name:
                allowed_names.append(str(name))

    print(f"allowed_names: {allowed_names}")
    if not allowed_names:
        return []

    valid_mods: list[str] = []
    invalid_modifiers: list[ModifierValidationIssue] = []

    for mod_text in users_mods:
        print(f"mod_text: {mod_text}")
        mod_text = mod_text.strip()
        if not mod_text:
            continue
        result = process.extractOne(mod_text, allowed_names, scorer=_combined_scorer)
        print(f"result: {result}")
        if result is None or result[1] < MODS_CONFIRMED_THRESHOLD:
            invalid_modifiers.append(
                ModifierValidationIssue(
                    item_name=item_name,
                    invalid_modifier=mod_text,
                    allowed_options=allowed_names,
                )
            )
        else:
            valid_mods.append(mod_text)

    order_item["modifier"] = ", ".join(valid_mods) or None
    return invalid_modifiers


async def detect_non_default_items(ordered_item: dict, item_name: str) -> list[OrderFollowUpRequirement]:
    category = await get_item_category(item_name)
    modifier = ordered_item.get("modifier", "")
    print(f"name: {item_name}, category: {category}, modifier: {modifier}")

    follow_up_requirements: list[OrderFollowUpRequirement] = []

    if category == "Smash Burgers" and not await is_patties_in_mods(modifier):
        follow_up_requirements.append(
            OrderFollowUpRequirement(
                kind="burger_patties",
                item_name=item_name,
                details={"options": _BURGER_PATTY_OPTIONS},
            )
        )

    if category in ["Boneless Wings", "Bone-In Breaded Wings"]:
        quantity = ordered_item.get("quantity", 1)
        if quantity not in [6, 12, 18, 24, 30]:
            follow_up_requirements.append(
                OrderFollowUpRequirement(
                    kind="wings_quantity",
                    item_name=item_name,
                    details={
                        "allowed_quantities": [6, 12, 18, 24, 30],
                        "flavors_per_quantity": _WINGS_QUANTITY_FLAVORS,
                        "available_flavors": _WINGS_FLAVORS,
                    },
                )
            )
        elif not modifier:
            follow_up_requirements.append(
                OrderFollowUpRequirement(
                    kind="wings_flavor",
                    item_name=item_name,
                    details={
                        "quantity": quantity,
                        "available_flavors": _WINGS_FLAVORS,
                        "max_flavors": await _max_wings_flavors(quantity),
                    },
                )
            )
        else:
            max_flavors = await _max_wings_flavors(quantity)
            if max_flavors is not None:
                flavor_count = len([flavor for flavor in modifier.split(",") if flavor.strip()])
                if flavor_count > max_flavors:
                    follow_up_requirements.append(
                        OrderFollowUpRequirement(
                            kind="wings_flavor_limit",
                            item_name=item_name,
                            details={
                                "quantity": quantity,
                                "max_flavors": max_flavors,
                                "selected_count": flavor_count,
                            },
                        )
                    )

    return follow_up_requirements


async def _max_wings_flavors(quantity: int) -> int | None:
    return _WINGS_QUANTITY_FLAVORS.get(quantity)


async def is_patties_in_mods(item_mods: str) -> bool:
    patties = ["single", "double", "triple", "quadruple"]
    item_mods = item_mods.lower() if item_mods else ""
    for patty in patties:
        if patty in item_mods:
            return True
    return False
