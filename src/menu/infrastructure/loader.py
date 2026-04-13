import json
from pathlib import Path

from rapidfuzz import fuzz, process, utils

INVENTORY_PATH = Path(__file__).resolve().parents[3] / "data" / "inventory.json"

_items_by_name: dict[str, dict] = {}
_items_by_id: dict[str, dict] = {}
_combos: list[dict] = []


async def init_menu() -> None:
    global _items_by_name, _items_by_id, _combos
    raw = json.loads(INVENTORY_PATH.read_text())
    by_name: dict[str, dict] = {}
    by_id: dict[str, dict] = {}
    for item_data in raw.values():
        cats = item_data.get("categories", [])
        modifier_groups = [
            {
                "id": g.get("id", ""),
                "name": g.get("name", ""),
                "min_required": 0,
                "max_allowed": 0,
                "modifiers": [
                    {"id": m.get("id", ""), "name": m.get("name", ""), "price": m.get("price", 0)}
                    for m in g.get("modifiers", [])
                ],
            }
            for g in item_data.get("modifierGroups", [])
        ]
        item = {
            "id": item_data["id"],
            "name": item_data["name"],
            "category_id": cats[0]["id"] if cats else "",
            "category_name": cats[0]["name"] if cats else "",
            "price": item_data.get("price", 0),
            "description": item_data.get("alternateName"),
            "modifier_groups": modifier_groups,
        }
        by_name[item["name"].lower()] = item
        by_id[item["id"]] = item
    _items_by_name = by_name
    _items_by_id = by_id
    _combos = []
    print(f"Menu loaded from inventory.json: {len(_items_by_name)} items")


async def get_menu_item_names() -> list[str]:
    return [item["name"] for item in _items_by_name.values()]


async def get_menu_item_modifiers_and_add_ons(name: str) -> tuple[list[str], list[str]]:
    item = _items_by_name.get(name.lower().strip())
    if item is None:
        return [], []

    modifiers: list[str] = []
    add_ons: list[str] = []

    for group in item.get("modifier_groups", []):
        target = modifiers if group.get("min_required", 0) > 0 else add_ons
        for mod in group.get("modifiers", []):
            n = mod.get("name")
            if n:
                target.append(str(n))

    modifiers = list(dict.fromkeys(modifiers))
    add_ons = list(dict.fromkeys(add_ons))
    return modifiers, add_ons


async def get_item_price(name: str) -> float | None:
    item = _items_by_name.get(name.lower().strip())
    if item is None:
        return None
    price = item.get("price")
    return price / 100 if price is not None else None


async def get_item_category(name: str) -> str | None:
    item = _items_by_name.get(name.lower().strip())
    if item is None:
        return None
    return item.get("category_name")


def get_item_definition(name: str) -> dict | None:
    return _items_by_name.get(name.lower().strip())


def get_item_id(name: str) -> str | None:
    item = _items_by_name.get(name.lower().strip())
    return item["id"] if item else None


def resolve_mod_ids(
    item_name: str,
    selected_mods: dict[str, str | list[str]],
) -> list[dict]:
    item = _items_by_name.get(item_name.lower().strip())
    if not item:
        return []
    groups_by_name = {g["name"]: g for g in item.get("modifier_groups", [])}
    result: list[dict] = []
    for group_name, selected_value in selected_mods.items():
        group = groups_by_name.get(group_name)
        if group is None:
            continue
        mods_by_name_lower = {
            m["name"].lower().strip(): m
            for m in group.get("modifiers", [])
            if m.get("name")
        }
        values = selected_value if isinstance(selected_value, list) else [selected_value]
        for v in values:
            matched_mod = mods_by_name_lower.get(str(v).lower().strip())
            if matched_mod:
                result.append({
                    "group_name": group["name"],
                    "group_id": group["id"],
                    "modifier_name": matched_mod["name"],
                    "modifier_id": matched_mod["id"],
                })
    return result


def _mod_scorer(s1: str, s2: str, **kwargs: object) -> float:
    s1p = utils.default_process(s1)
    s2p = utils.default_process(s2)
    return max(
        fuzz.ratio(s1p, s2p, processor=None),
        fuzz.partial_ratio(s1p, s2p, processor=None) * 0.9,
        fuzz.token_sort_ratio(s1p, s2p, processor=None),
        fuzz.partial_token_sort_ratio(s1p, s2p, processor=None) * 0.9,
    )


def resolve_mod_ids_from_string(item_name: str, modifier_str: str) -> list[dict]:
    """Fuzzy-match each comma-separated token in modifier_str against every modifier group."""
    item = _items_by_name.get(item_name.lower().strip())
    if not item:
        return []
    tokens = [t.strip() for t in modifier_str.split(",") if t.strip()]
    result: list[dict] = []
    seen: set[str] = set()
    for group in item.get("modifier_groups", []):
        mod_names = [m["name"] for m in group.get("modifiers", []) if m.get("name")]
        mods_by_name = {m["name"]: m for m in group.get("modifiers", []) if m.get("name")}
        for token in tokens:
            match = process.extractOne(token, mod_names, scorer=_mod_scorer, score_cutoff=70)
            if match:
                canonical_name = match[0]
                key = f"{group['id']}:{canonical_name}"
                if key not in seen:
                    seen.add(key)
                    matched_mod = mods_by_name[canonical_name]
                    result.append({
                        "group_name": group["name"],
                        "group_id": group["id"],
                        "modifier_name": matched_mod["name"],
                        "modifier_id": matched_mod["id"],
                    })
    return result


def validate_mod_selections(item_name: str, selected_mods: dict) -> tuple[list[str], list[str]]:
    """Returns (validation_errors, missing_required_group_names).

    selected_mods maps modifier group name → selected modifier name(s).
    """
    item = get_item_definition(item_name)
    if item is None:
        return [], []

    groups = item.get("modifier_groups", [])
    groups_by_name = {g["name"]: g for g in groups}

    validation_errors: list[str] = []

    for group_name, selected_value in selected_mods.items():
        group = groups_by_name.get(group_name)
        if group is None:
            validation_errors.append(f'"{group_name}" is not a valid modifier group for {item_name}.')
            continue

        valid_options = [m["name"] for m in group.get("modifiers", []) if m.get("name")]
        valid_lower = {o.lower().strip(): o for o in valid_options}
        values = selected_value if isinstance(selected_value, list) else [selected_value]
        for v in values:
            if str(v).lower().strip() not in valid_lower:
                opts_str = ", ".join(valid_options)
                validation_errors.append(
                    f'"{v}" is not a valid option for {group_name}. Valid options: {opts_str}.'
                )

    required_groups = [g["name"] for g in groups if g.get("min_required", 0) > 0]
    missing_required = [g for g in required_groups if g not in selected_mods]
    return validation_errors, missing_required


def detect_mods_allowed(order_items: list[dict]) -> bool:
    for item in order_items:
        if item.get("modifier") is not None:
            return True
    return False


def detect_add_ons_allowed(order_items: list[dict]) -> bool:
    for item in order_items:
        if item.get("add_on") is not None:
            return True
    return False


def get_menu_context() -> str:
    categories: dict[str, list[dict]] = {}
    for item in _items_by_name.values():
        cat = item.get("category_name", "Other")
        categories.setdefault(cat, []).append(item)

    lines: list[str] = []
    for cat, cat_items in categories.items():
        lines.append(f"\nCategory: {cat}")
        for item in cat_items:
            price = item.get("price")
            try:
                price_str = f"${price / 100:.2f}" if price is not None else "price varies"
            except (TypeError, ValueError):
                price_str = "price varies"
            lines.append(f"  - {item['name']} ({price_str})")
            if item.get("description"):
                lines.append(f"    {item['description']}")
            for group in item.get("modifier_groups", []):
                label = group.get("name", "")
                tag = "required" if group.get("min_required", 0) > 0 else "optional"
                opts = ", ".join(
                    f"{m['name']}" + (f" +${m['price'] / 100:.2f}" if m.get("price") else "")
                    for m in group.get("modifiers", [])
                    if m.get("name")
                )
                lines.append(f"    [{tag}] {label}: {opts}")

    if _combos:
        lines.append("\nCategory: Combos")
        for combo in _combos:
            price = combo.get("price")
            try:
                price_str = f"${price / 100:.2f}" if price is not None else "price varies"
            except (TypeError, ValueError):
                price_str = "price varies"
            lines.append(f"  - {combo['name']} ({price_str})")

    return "\n".join(lines).strip()
