import json
import os
from pathlib import Path

from rapidfuzz import fuzz, process, utils

from src import firebase as _firebase
_VARIABLE_PRICE_GROUP_NAMES = {"patties", "quantity"}
_QUANTITY_AS_SELECTION_GROUP_NAMES = {"quantity"}

_items_by_name: dict[str, dict] = {}
_items_by_id: dict[str, dict] = {}
_combos: list[dict] = []
_items_name_set: set[str] = set()


def _top_level_modifier_group_elements(raw: dict) -> list[dict]:
    groups = raw.get("modifierGroups")
    if isinstance(groups, dict):
        elems = groups.get("elements")
        if isinstance(elems, list):
            return elems
    return []


def _top_level_modifier_elements(raw: dict) -> list[dict]:
    modifiers = raw.get("modifiers")
    if isinstance(modifiers, dict):
        elems = modifiers.get("elements")
        if isinstance(elems, list):
            return elems
    root = raw.get("modifierElements")
    if isinstance(root, list):
        return root
    return []


def _clover_item_elements(raw: dict) -> list[dict]:
    """Clover v3 inventory may return ``items.elements`` (legacy) or a top-level ``elements`` list."""
    items = raw.get("items")
    if isinstance(items, dict):
        el = items.get("elements")
        if isinstance(el, list):
            return el
    root = raw.get("elements")
    if isinstance(root, list):
        return root
    return []


def _normalized_modifier_row(modifier: dict) -> dict | None:
    modifier_id = str(modifier.get("id", "")).strip()
    if not modifier_id:
        return None
    return {
        "id": modifier_id,
        "name": str(modifier.get("name", "")).strip(),
        "price": modifier.get("price", 0) or 0,
    }


def _merge_modifier_registry_from_rows(modifiers: list[dict], registry: dict[str, dict]) -> None:
    for modifier in modifiers:
        if not isinstance(modifier, dict):
            continue
        normalized = _normalized_modifier_row(modifier)
        if normalized is None:
            continue
        current = registry.get(normalized["id"])
        if current is None or _modifier_detail_score(normalized) > _modifier_detail_score(current):
            registry[normalized["id"]] = normalized


def _hydrate_modifier_rows_with_registry(rows: list[dict], modifier_registry: dict[str, dict]) -> list[dict]:
    hydrated: list[dict] = []
    for row in rows:
        modifier_id = str(row.get("id", "")).strip()
        if not modifier_id:
            continue
        best = dict(row)
        known = modifier_registry.get(modifier_id)
        if known is not None and _modifier_detail_score(known) > _modifier_detail_score(best):
            best = dict(known)
        hydrated.append(best)
    return hydrated


def _expanded_modifier_elements_from_clover_group(group: dict) -> list[dict]:
    inner = group.get("modifiers")
    if isinstance(inner, dict):
        elems = inner.get("elements")
        if isinstance(elems, list) and elems:
            return [
                row
                for modifier in elems
                if isinstance(modifier, dict)
                and (row := _normalized_modifier_row(modifier)) is not None
            ]
    return []


def _modifier_detail_score(modifier: dict) -> tuple[int, int]:
    name = str(modifier.get("name", "")).strip()
    modifier_id = str(modifier.get("id", "")).strip()
    return (
        int(bool(name and name != modifier_id)),
        int((modifier.get("price", 0) or 0) != 0),
    )


def _merge_modifier_registry_from_group(group: dict, registry: dict[str, dict]) -> None:
    for modifier in _expanded_modifier_elements_from_clover_group(group):
        current = registry.get(modifier["id"])
        if current is None or _modifier_detail_score(modifier) > _modifier_detail_score(current):
            registry[modifier["id"]] = modifier


def _build_modifier_registry(
    item_rows: list[dict],
    top_level_groups: list[dict],
    top_level_modifiers: list[dict],
) -> dict[str, dict]:
    registry: dict[str, dict] = {}
    _merge_modifier_registry_from_rows(top_level_modifiers, registry)
    for group in top_level_groups:
        if isinstance(group, dict):
            _merge_modifier_registry_from_group(group, registry)
    for item in item_rows:
        for group in item.get("modifierGroups", {}).get("elements", []):
            if isinstance(group, dict):
                _merge_modifier_registry_from_group(group, registry)
    return registry


def _modifier_elements_from_clover_group(group: dict, modifier_registry: dict[str, dict]) -> list[dict]:
    """Normalise modifiers from expanded ``modifiers.elements`` or ``modifierIds`` CSV."""
    elems = _hydrate_modifier_rows_with_registry(
        _expanded_modifier_elements_from_clover_group(group),
        modifier_registry,
    )
    if elems:
        return elems

    mids = group.get("modifierIds")
    if isinstance(mids, str) and mids.strip():
        resolved: list[dict] = []
        for part in mids.split(","):
            modifier_id = part.strip()
            if not modifier_id:
                continue
            known = modifier_registry.get(modifier_id)
            if known is not None:
                resolved.append(dict(known))
            else:
                resolved.append({"id": modifier_id, "name": modifier_id, "price": 0})
        return resolved
    return []


def _modifier_group_detail_score(group: dict, modifier_registry: dict[str, dict]) -> tuple[int, int, int]:
    modifiers = _modifier_elements_from_clover_group(group, modifier_registry)
    named_modifiers = sum(1 for modifier in modifiers if modifier.get("name") and modifier["name"] != modifier["id"])
    return (named_modifiers, len(modifiers), int(bool(group.get("name"))))


def _merge_embedded_modifier_groups(
    items: list[dict],
    registry: dict[str, dict],
    modifier_registry: dict[str, dict],
) -> None:
    """Fill registry from per-item modifierGroups (Clover paginated items API shape)."""
    for item in items:
        for g in item.get("modifierGroups", {}).get("elements", []):
            gid = g.get("id")
            if not gid:
                continue
            elems = _modifier_elements_from_clover_group(g, modifier_registry)
            merged = {
                "id": gid,
                "name": g.get("name", ""),
                "minRequired": g.get("minRequired", 0),
                "maxAllowed": g.get("maxAllowed", 0),
                "modifiers": {"elements": elems},
            }
            prev = registry.get(gid)
            if prev is None:
                registry[gid] = merged
                continue
            if _modifier_group_detail_score(merged, modifier_registry) > _modifier_group_detail_score(prev, modifier_registry):
                registry[gid] = merged


def _group_row_for_item(group_def: dict, ref: dict, modifier_registry: dict[str, dict]) -> dict:
    elems = _modifier_elements_from_clover_group(group_def, modifier_registry)
    if not elems:
        elems = _modifier_elements_from_clover_group(ref, modifier_registry)
    return {
        "id": group_def.get("id", "") or ref.get("id", ""),
        "name": group_def.get("name", "") or ref.get("name", ""),
        "min_required": group_def.get("minRequired", ref.get("minRequired", 0)),
        "max_allowed": group_def.get("maxAllowed", ref.get("maxAllowed", 0)),
        "modifiers": [
            {"id": m.get("id", ""), "name": m.get("name", ""), "price": m.get("price", 0)}
            for m in elems
        ],
    }


def build_normalized_items(raw: dict) -> list[dict]:
    """Parse raw Clover menu JSON into normalized item rows."""
    item_rows = _clover_item_elements(raw)
    top_level_groups = _top_level_modifier_group_elements(raw)
    top_level_modifiers = _top_level_modifier_elements(raw)
    modifier_registry = _build_modifier_registry(item_rows, top_level_groups, top_level_modifiers)
    mod_groups_by_id: dict[str, dict] = {
        g["id"]: g
        for g in top_level_groups
        if isinstance(g, dict) and g.get("id")
    }
    _merge_embedded_modifier_groups(item_rows, mod_groups_by_id, modifier_registry)

    items: list[dict] = []
    for item_data in item_rows:
        cats = item_data.get("categories", {}).get("elements", [])

        modifier_groups: list[dict] = []
        for ref in item_data.get("modifierGroups", {}).get("elements", []):
            gid = ref.get("id", "")
            group_def = mod_groups_by_id.get(gid) if gid else None
            if group_def is None and gid:
                group_def = {
                    "id": gid,
                    "name": ref.get("name", ""),
                    "minRequired": ref.get("minRequired", 0),
                    "maxAllowed": ref.get("maxAllowed", 0),
                    "modifiers": {"elements": _modifier_elements_from_clover_group(ref, modifier_registry)},
                }
            if group_def is None:
                continue
            modifier_groups.append(_group_row_for_item(group_def, ref, modifier_registry))

        item = {
            "id": item_data["id"],
            "name": item_data["name"],
            "category_id": cats[0]["id"] if cats else "",
            "category_name": cats[0]["name"] if cats else "",
            "price": item_data.get("price", 0),
            "description": item_data.get("alternateName"),
            "modifier_groups": modifier_groups,
            "available": item_data.get("available", True),
            "hidden": bool(item_data.get("hidden", False)),
            "deleted": bool(item_data.get("deleted", False)),
        }
        items.append(item)

    return items


def build_items_by_name(raw: dict) -> dict:
    """Parse raw Clover menu JSON and return an items_by_name dict.

    Keys are lowercase item names; values are item dicts with fields:
    id, name, category_id, category_name, price, description, modifier_groups.

    Accepts legacy shape (``items`` + top-level ``modifierGroups``) and v3 paginated
    inventory shape (top-level ``elements`` only, modifier metadata on each item).

    This is a pure function with no side effects on module globals.
    """
    by_name: dict[str, dict] = {}
    for item in build_normalized_items(raw):
        by_name[item["name"].lower()] = item

    return by_name


def _hydrate_menu_from_raw(raw: dict) -> None:
    """Populate module globals from raw Clover menu JSON (see build_items_by_name)."""
    global _items_by_name, _items_by_id, _combos, _items_name_set
    by_name = build_items_by_name(raw)
    by_id: dict[str, dict] = {}
    for item in by_name.values():
        by_id[item["id"]] = item
    _items_by_name = by_name
    _items_by_id = by_id
    _combos = []
    _items_name_set = {item["name"] for item in _items_by_name.values()}


async def init_menu() -> None:
    """Load the in-memory menu used by cart, pricing, and prompts.

    Order: optional ``CLOVER_MENU_JSON_PATH`` file (local/tests), else Clover API
    using ``Users/{RESTAURANT_ID}/Integrations/Clover`` in Firestore. On missing
    credentials or errors, the menu starts empty and the app still boots.
    """
    bootstrap = os.environ.get("CLOVER_MENU_JSON_PATH")
    if bootstrap and Path(bootstrap).is_file():
        raw = json.loads(Path(bootstrap).read_text())
        _hydrate_menu_from_raw(raw)
        print(f"Menu loaded from CLOVER_MENU_JSON_PATH ({bootstrap}): {len(_items_by_name)} items")
        return

    db = _firebase.firebaseDatabase
    if db is None:
        print("init_menu: Firebase client not ready; menu empty")
        _hydrate_menu_from_raw({"items": {"elements": []}, "modifierGroups": {"elements": []}})
        return

    print("init_menu: no RESTAURANT_ID at startup; menu will be loaded per-request")
    _hydrate_menu_from_raw({"items": {"elements": []}, "modifierGroups": {"elements": []}})


async def get_menu_item_names() -> list[str]:
    return [item["name"] for item in _items_by_name.values()]


async def get_menu_item_name_aliases() -> list[tuple[str, str]]:
    """Return (alias_text, canonical_name) pairs for fuzzy item matching.

    Emits the name itself plus the description as an alternate alias mapping back
    to the same canonical name, so queries like "chicken strips" can match items
    whose display name uses a synonym ("2 Chicken Tenders & Fries") when the
    description contains the customer's wording.
    """
    aliases: list[tuple[str, str]] = []
    for item in _items_by_name.values():
        name = item["name"]
        aliases.append((name, name))
        description = item.get("description")
        if description and str(description).strip():
            aliases.append((str(description).strip(), name))
    return aliases


async def get_menu_item_names_set() -> set[str]:
    """Return the set of all menu item display names for O(1) membership tests.

    Prefer this over get_menu_item_names() when you only need to check whether
    a name exists on the menu (e.g. exact-match checks). Use get_menu_item_names()
    when you need an ordered list for fuzzy matching.
    """
    return _items_name_set


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


def _resolved_mods_for_order_item(order_item: dict, item_definition: dict) -> list[dict]:
    resolved_mods = order_item.get("resolved_mods")
    if isinstance(resolved_mods, list):
        return resolved_mods

    modifier_str = str(order_item.get("modifier") or "").strip()
    if not modifier_str:
        return []

    return resolve_mod_ids_from_string(item_definition.get("name", ""), modifier_str)


def _modifier_price_lookup(item_definition: dict) -> dict[tuple[str, str], int | float]:
    prices: dict[tuple[str, str], int | float] = {}
    for group in item_definition.get("modifier_groups", []):
        group_id = str(group.get("id", "")).strip()
        group_name = str(group.get("name", "")).strip()
        for modifier in group.get("modifiers", []):
            modifier_name = str(modifier.get("name", "")).strip()
            if not modifier_name:
                continue
            price = modifier.get("price")
            if isinstance(price, (int, float)) and not isinstance(price, bool):
                if group_id:
                    prices[(group_id, modifier_name.lower())] = price
                if group_name:
                    prices[(group_name.lower(), modifier_name.lower())] = price
    return prices


def _variable_price_group_selections(item_definition: dict, resolved_mods: list[dict]) -> list[tuple[str, int | float]]:
    prices = _modifier_price_lookup(item_definition)
    selections: list[tuple[str, int | float]] = []

    for resolved_mod in resolved_mods:
        modifier_name = str(resolved_mod.get("modifier_name", "")).strip()
        if not modifier_name:
            continue

        group_name = str(resolved_mod.get("group_name", "")).strip().lower()
        if group_name not in _VARIABLE_PRICE_GROUP_NAMES:
            continue

        group_id = str(resolved_mod.get("group_id", "")).strip()
        price = None
        if group_id:
            price = prices.get((group_id, modifier_name.lower()))
        if price is None and group_name:
            price = prices.get((group_name, modifier_name.lower()))
        if isinstance(price, (int, float)) and not isinstance(price, bool):
            selections.append((group_name, price))

    return selections


def get_order_item_unit_price(order_item: dict) -> int | float | None:
    item_definition = get_item_definition(str(order_item.get("name", "")))
    if item_definition is None:
        return None

    base_price = item_definition.get("price")
    if base_price is not None and not isinstance(base_price, (int, float)):
        base_price = None

    resolved_mods = _resolved_mods_for_order_item(order_item, item_definition)
    modifier_prices = _modifier_price_lookup(item_definition)
    variable_price_selections = _variable_price_group_selections(item_definition, resolved_mods)

    selected_modifier_total = 0
    for resolved_mod in resolved_mods:
        modifier_name = str(resolved_mod.get("modifier_name", "")).strip()
        if not modifier_name:
            continue

        group_name = str(resolved_mod.get("group_name", "")).strip().lower()
        group_id = str(resolved_mod.get("group_id", "")).strip()

        price = None
        if group_id:
            price = modifier_prices.get((group_id, modifier_name.lower()))
        if price is None and group_name:
            price = modifier_prices.get((group_name, modifier_name.lower()))
        if not isinstance(price, (int, float)) or isinstance(price, bool):
            continue

        if group_name in _VARIABLE_PRICE_GROUP_NAMES:
            continue

        selected_modifier_total += price

    if isinstance(base_price, (int, float)) and base_price > 0:
        return base_price + selected_modifier_total + sum(price for _, price in variable_price_selections)

    if variable_price_selections:
        return sum(price for _, price in variable_price_selections) + selected_modifier_total

    has_variable_price_group = any(
        str(group.get("name", "")).strip().lower() in _VARIABLE_PRICE_GROUP_NAMES
        for group in item_definition.get("modifier_groups", [])
    )
    if has_variable_price_group:
        return None

    if isinstance(base_price, (int, float)):
        return base_price + selected_modifier_total

    return None


def get_order_item_line_total(order_item: dict) -> int | float | None:
    unit_price = get_order_item_unit_price(order_item)
    if unit_price is None:
        return None

    item_definition = get_item_definition(str(order_item.get("name", "")))
    resolved_mods = _resolved_mods_for_order_item(order_item, item_definition or {})
    variable_price_selections = _variable_price_group_selections(item_definition or {}, resolved_mods)
    uses_quantity_as_selection = any(
        group_name in _QUANTITY_AS_SELECTION_GROUP_NAMES
        for group_name, _ in variable_price_selections
    )
    if uses_quantity_as_selection:
        return unit_price

    quantity = int(order_item.get("quantity", 1) or 1)
    return unit_price * quantity


def order_item_uses_quantity_selection(order_item: dict) -> bool:
    item_definition = get_item_definition(str(order_item.get("name", "")))
    if item_definition is None:
        return False

    resolved_mods = _resolved_mods_for_order_item(order_item, item_definition)
    variable_price_selections = _variable_price_group_selections(item_definition, resolved_mods)
    return any(
        group_name in _QUANTITY_AS_SELECTION_GROUP_NAMES
        for group_name, _ in variable_price_selections
    )


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
