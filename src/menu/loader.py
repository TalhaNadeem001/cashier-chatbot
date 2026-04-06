import json
from pathlib import Path

_MENU_FILE = Path(__file__).parent.parent.parent / "data" / "normalized_menu.json"

with _MENU_FILE.open() as f:
    _MENU_DATA: dict = json.load(f)

async def get_menu_item_names() -> list[str]:
    items = _MENU_DATA.get("menu", {}).get("items", {})
    return list(items.keys())

async def get_menu_item_modifiers_and_add_ons(name: str) -> tuple[list[str], list[str]]:
    items = _MENU_DATA.get("menu", {}).get("items", {})
    key = name.lower().strip()
    item = items.get(key)

    # Support lookups by human-readable names (e.g. "original name", "canonical_name").
    if item is None:
        for v in items.values():
            if v.get("original name", "").lower() == key:
                item = v
                break
            if v.get("canonical_name", "").lower() == key:
                item = v
                break

    if item is None:
        return [], []

    mods: dict = item.get("mods", {}) or {}
    required_keys: list[str] = item.get("requires", []) or []
    optional_keys: list[str] = item.get("optional", []) or []

    modifiers: list[str] = []
    add_ons: list[str] = []

    def _extract_options(mod_key: str, out: list[str]) -> None:
        mod = mods.get(mod_key) or {}
        for opt in mod.get("options", []) or []:
            if isinstance(opt, dict):
                n = opt.get("name")
                if n:
                    out.append(str(n))
            elif isinstance(opt, str):
                out.append(opt)

    # Flatten modifier option names. Keep `add_ons` separated because it can be large.
    for mod_key in required_keys:
        if mod_key == "add_ons":
            _extract_options(mod_key, add_ons)
        else:
            _extract_options(mod_key, modifiers)

    for mod_key in optional_keys:
        if mod_key == "add_ons":
            _extract_options(mod_key, add_ons)
        else:
            _extract_options(mod_key, modifiers)

    # De-dupe while preserving insertion order.
    modifiers = list(dict.fromkeys(modifiers))
    add_ons = list(dict.fromkeys(add_ons))
    return modifiers, add_ons


async def get_item_price(name: str) -> float | None:
    items = _MENU_DATA.get("menu", {}).get("items", {})
    key = name.lower().strip()
    item = items.get(key)
    if item is None:
        for v in items.values():
            if v.get("original name", "").lower() == key:
                item = v
                break
    if item is None:
        return None
    price = item.get("price")
    return float(price) if price is not None else None


def get_item_definition(name: str) -> dict | None:
    items = _MENU_DATA.get("menu", {}).get("items", {})
    key = name.lower().strip()
    item = items.get(key)
    if item is None:
        for v in items.values():
            if v.get("original name", "").lower() == key:
                item = v
                break
            if v.get("canonical_name", "").lower() == key:
                item = v
                break
    return item


def validate_mod_selections(item_name: str, selected_mods: dict) -> tuple[list[str], list[str]]:
    """Returns (validation_errors, missing_required_mod_keys)."""
    item = get_item_definition(item_name)
    if item is None:
        return [], []

    mods: dict = item.get("mods", {}) or {}
    requires: list[str] = item.get("requires", []) or []
    optional: list[str] = item.get("optional", []) or []
    valid_mod_keys = set(requires) | set(optional)

    validation_errors: list[str] = []

    for mod_key, selected_value in selected_mods.items():
        if mod_key not in valid_mod_keys:
            validation_errors.append(f'"{mod_key}" is not a valid modifier for {item_name}.')
            continue

        mod = mods.get(mod_key, {})
        valid_options = [
            opt["name"] for opt in mod.get("options", [])
            if isinstance(opt, dict) and opt.get("name")
        ]
        valid_lower = {o.lower().strip(): o for o in valid_options}

        values = selected_value if isinstance(selected_value, list) else [selected_value]
        for v in values:
            if str(v).lower().strip() not in valid_lower:
                opts_str = ", ".join(valid_options)
                validation_errors.append(
                    f'"{v}" is not a valid option for {mod.get("label", mod_key)}. Valid options: {opts_str}.'
                )

    missing_required = [k for k in requires if k not in selected_mods]
    return validation_errors, missing_required


def _parse_combo_key(combo_key: str) -> list[tuple[int, str]]:
    """'2 chicken sandos, 1 regular fries' → [(2, 'chicken sandos'), (1, 'regular fries')]"""
    parts = []
    for segment in combo_key.split(","):
        segment = segment.strip()
        tokens = segment.split(" ", 1)
        if len(tokens) == 2:
            try:
                parts.append((int(tokens[0]), tokens[1].strip().lower()))
            except ValueError:
                pass
    return parts


def detect_combo(order_items: list[dict]) -> dict | None:
    """
    Returns the best-matching combo dict {key, original_name, price, description}
    if the order is a superset of any combo, else None.
    """
    combos = _MENU_DATA.get("menu", {}).get("combos", {})
    canonical_names = set(_MENU_DATA.get("menu", {}).get("items", {}).keys())

    order_counts: dict[str, int] = {}
    for item in order_items:
        k = item["name"].lower()
        order_counts[k] = order_counts.get(k, 0) + item["quantity"]

    matched: list[tuple[str, dict]] = []
    for combo_key, combo_info in combos.items():
        combo_items = _parse_combo_key(combo_key)
        if not combo_items:
            continue
        all_match = True
        for qty, name in combo_items:
            canonical = name if name in canonical_names else (
                name[:-1] if name.endswith("s") and name[:-1] in canonical_names else None
            )
            if canonical is None or order_counts.get(canonical, 0) < qty:
                all_match = False
                break
        if all_match:
            matched.append((combo_key, combo_info))

    if not matched:
        return None

    matched.sort(key=lambda x: x[1].get("price", 0), reverse=True)
    best_key, best_info = matched[0]
    return {
        "key": best_key,
        "original_name": best_info.get("original name"),
        "price": best_info.get("price"),
        "description": best_info.get("description"),
    }


def get_menu_context() -> str:
    items = _MENU_DATA.get("menu", {}).get("items", {})
    # Group items by category, preserving insertion order
    categories: dict[str, list[tuple[str, dict]]] = {}
    for key, item in items.items():
        cat = item.get("category", "Other")
        categories.setdefault(cat, []).append((key, item))

    lines: list[str] = []
    for cat, cat_items in categories.items():
        lines.append(f"\nCategory: {cat}")
        for key, item in cat_items:
            display_name = item.get("original name") or key
            price = item.get("price")
            try:
                price_str = f"${float(price):.2f}" if price is not None else "price varies"
            except (TypeError, ValueError):
                price_str = "price varies"
            lines.append(f"  - {display_name} ({price_str})")
            if item.get("description"):
                lines.append(f"    {item['description']}")
            mods = item.get("mods", {})
            for mod_key in item.get("requires", []):
                mod = mods.get(mod_key, {})
                label = mod.get("label", mod_key)
                opts = ", ".join(
                    f"{o['name']}" + (f" +${o['price']:.2f}" if o.get("price") else "")
                    for o in mod.get("options", [])
                    if isinstance(o, dict)
                )
                lines.append(f"    [required] key={mod_key} ({label}): {opts}")
            for mod_key in item.get("optional", []):
                mod = mods.get(mod_key, {})
                label = mod.get("label", mod_key)
                opts = ", ".join(
                    f"{o['name']}" + (f" +${o['price']:.2f}" if o.get("price") else "")
                    for o in mod.get("options", [])
                    if isinstance(o, dict)
                )
                lines.append(f"    [optional] key={mod_key} ({label}): {opts}")
    combos = _MENU_DATA.get("menu", {}).get("combos", {})
    if combos:
        lines.append("\nCategory: Combos")
        for key, item in combos.items():
            display_name = item.get("original name") or key
            price = item.get("price")
            try:
                price_str = f"${float(price):.2f}" if price is not None else "price varies"
            except (TypeError, ValueError):
                price_str = "price varies"
            lines.append(f"  - {display_name} ({price_str})")
            if item.get("description"):
                lines.append(f"    {item['description']}")
    return "\n".join(lines).strip()
