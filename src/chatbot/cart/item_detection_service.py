import re

from rapidfuzz import process

from src.chatbot.cart.ai_client import resolve_closest_modifier_match
from src.chatbot.clarification.constants import AMBIGUITY_GAP, NOT_FOUND_THRESHOLD
from src.chatbot.clarification.fuzzy_matcher import MODS_CONFIRMED_THRESHOLD, _combined_scorer
from src.chatbot.exceptions import AIServiceError
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


async def validate_order_items(
    order_items: list[dict],
    latest_message: str | None = None,
) -> OrderValidationResult:
    modifier_validation = await validate_order_item_modifiers(
        order_items,
        latest_message=latest_message,
    )
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


async def validate_order_item_modifiers(
    order_items: list[dict],
    latest_message: str | None = None,
) -> OrderValidationResult:
    validated_items: list[dict] = []
    invalid_modifiers: list[ModifierValidationIssue] = []

    for item in order_items:
        item_copy = dict(item)
        name = item_copy.get("name", "")
        if get_item_definition(name) is None:
            validated_items.append(item_copy)
            continue

        item_invalid_modifiers = await detect_mods_allowed(
            item_copy,
            name,
            latest_message=latest_message,
        )
        invalid_modifiers.extend(item_invalid_modifiers)

        validated_items.append(item_copy)

    return OrderValidationResult(
        items=validated_items,
        invalid_modifiers=invalid_modifiers,
    )


async def detect_mods_allowed(
    order_item: dict,
    item_name: str,
    latest_message: str | None = None,
) -> list[ModifierValidationIssue]:
    users_mods = order_item.get("modifier", "").split(",") if order_item.get("modifier") else []
    return await validate_mod_selections(
        item_name,
        users_mods,
        order_item,
        latest_message=latest_message,
    )


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = value.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(value.strip())
    return deduped


async def _resolve_allowed_modifier(
    item_name: str,
    mod_text: str,
    allowed_names: list[str],
    latest_message: str | None = None,
) -> str | None:
    for allowed_name in allowed_names:
        if allowed_name.lower() == mod_text.lower():
            return allowed_name

    top_matches = process.extract(
        mod_text,
        allowed_names,
        scorer=_combined_scorer,
        limit=5,
    )
    if not top_matches or top_matches[0][1] < NOT_FOUND_THRESHOLD:
        return None
    if top_matches and top_matches[0][1] >= MODS_CONFIRMED_THRESHOLD:
        best_score = top_matches[0][1]
        close_matches = [match for match in top_matches if best_score - match[1] <= AMBIGUITY_GAP]
        if len(close_matches) == 1:
            return top_matches[0][0]

    try:
        resolution = await resolve_closest_modifier_match(
            item_name=item_name,
            modifier_text=mod_text,
            allowed_options=allowed_names,
            latest_message=latest_message,
        )
    except AIServiceError as exc:
        print(f"[modifier-validation] ai_fallback_failed: {exc}")
        return None

    if resolution.status != "match":
        return None

    canonical_modifier = str(resolution.canonical_modifier or "").strip()
    allowed_by_lower = {allowed.lower(): allowed for allowed in allowed_names}
    return allowed_by_lower.get(canonical_modifier.lower())


async def validate_mod_selections(
    item_name: str,
    users_mods: list[str],
    order_item: dict,
    latest_message: str | None = None,
) -> list[ModifierValidationIssue]:
    item_data = get_item_definition(item_name) or {}
    allowed_names: list[str] = []
    for group in item_data.get("modifier_groups", []):
        for mod in group.get("modifiers", []):
            name = mod.get("name")
            if name:
                allowed_names.append(str(name))

    allowed_names = _dedupe_preserving_order(allowed_names)
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
        canonical_modifier = await _resolve_allowed_modifier(
            item_name=item_name,
            mod_text=mod_text,
            allowed_names=allowed_names,
            latest_message=latest_message,
        )
        print(f"canonical_modifier: {canonical_modifier}")
        if canonical_modifier is None:
            invalid_modifiers.append(
                ModifierValidationIssue(
                    item_name=item_name,
                    invalid_modifier=mod_text,
                    allowed_options=allowed_names,
                )
            )
        else:
            valid_mods.append(canonical_modifier)

    order_item["modifier"] = ", ".join(_dedupe_preserving_order(valid_mods)) or None
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
        # Piece count comes from the item name (e.g. "6Pc Boneless" → 6) when the
        # menu encodes wing sizes as separate items. OrderItem.quantity is the
        # number of *bundles* the customer is buying (1 unless they doubled up).
        # Only fall back to treating OrderItem.quantity as a piece count for
        # legacy/generic items where the name carries no size (e.g. plain
        # "Boneless Wings" with a Quantity modifier group).
        piece_count = _parse_wings_pieces_from_name(item_name)
        piece_source: str
        if piece_count is not None:
            piece_source = "name"
        else:
            piece_count = ordered_item.get("quantity", 1)
            piece_source = "quantity"

        if piece_count not in [6, 12, 18, 24, 30]:
            # Only emit a wings_quantity follow-up when the size couldn't be
            # determined from the item name. If the item name does carry a
            # valid size, we shouldn't pester the customer to re-pick quantity.
            if piece_source == "quantity":
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
                        "quantity": piece_count,
                        "available_flavors": _WINGS_FLAVORS,
                        "max_flavors": await _max_wings_flavors(piece_count),
                    },
                )
            )
        else:
            max_flavors = await _max_wings_flavors(piece_count)
            if max_flavors is not None:
                flavor_count = len([flavor for flavor in modifier.split(",") if flavor.strip()])
                if flavor_count > max_flavors:
                    follow_up_requirements.append(
                        OrderFollowUpRequirement(
                            kind="wings_flavor_limit",
                            item_name=item_name,
                            details={
                                "quantity": piece_count,
                                "max_flavors": max_flavors,
                                "selected_count": flavor_count,
                            },
                        )
                    )

    return follow_up_requirements


async def _max_wings_flavors(quantity: int) -> int | None:
    return _WINGS_QUANTITY_FLAVORS.get(quantity)


# Matches a piece count attached to "pc" / "piece" in the item name, e.g.:
#   "6Pc Boneless"       → 6
#   "12pc Boneless"      → 12
#   "18 Piece Boneless"  → 18
#   "24-pc Boneless"     → 24
# Returns None when the name has no explicit piece count.
_WINGS_PIECE_IN_NAME_RE = re.compile(
    r"(?<![A-Za-z0-9])(\d+)\s*[-\s]?(?:pc|pcs|piece|pieces)\b",
    re.IGNORECASE,
)


def _parse_wings_pieces_from_name(item_name: str) -> int | None:
    """Extract a piece count from a wings item name, returning None if absent.

    Only recognised piece counts (6/12/18/24/30) are returned so callers can
    trust the result without additional range checks.
    """
    if not item_name:
        return None
    match = _WINGS_PIECE_IN_NAME_RE.search(item_name)
    if match is None:
        return None
    try:
        value = int(match.group(1))
    except (TypeError, ValueError):
        return None
    if value in _WINGS_QUANTITY_FLAVORS:
        return value
    return None


async def is_patties_in_mods(item_mods: str) -> bool:
    patties = ["single", "double", "triple", "quadruple"]
    item_mods = item_mods.lower() if item_mods else ""
    for patty in patties:
        if patty in item_mods:
            return True
    return False
