#!/usr/bin/env python3
"""Build menu_index.json and menu_validation_map.json from the raw menu JSON."""

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
MENU_FILE = ROOT / "tests" / "smash_n_wings_menu.json"
DATA_DIR = ROOT / "src" / "data"

UPSELL_KEYS = {
    "Recommended Sides And Apps",
    "Recommended Beverages",
    "Recommended Desserts",
}


def build_menu_index(menu_data: dict) -> dict:
    index = {}

    for category in menu_data["menu"]:
        category_name = category["category"]
        for item in category["items"]:
            mod_details = {}
            required_mods = []
            optional_mods = []

            for mod_name, mod_config in item.get("modifications", {}).items():
                if mod_name in UPSELL_KEYS:
                    continue

                mod_key = mod_name.lower().replace(" ", "_")

                options = [
                    {"name": opt["name"], "price": opt["price"]}
                    for opt in mod_config.get("options", [])
                ]

                mod_entry: dict = {
                    "label": mod_name,
                    "type": mod_config["type"],
                    "options": options,
                }

                if "min_select" in mod_config:
                    mod_entry["min_select"] = mod_config["min_select"]
                if "max_select" in mod_config:
                    mod_entry["max_select"] = mod_config["max_select"]

                mod_details[mod_key] = mod_entry

                if mod_config.get("required"):
                    required_mods.append(mod_key)
                else:
                    optional_mods.append(mod_key)

            # Determine price
            if item["base_price"] is not None:
                price = item["base_price"]
            else:
                patties_options = item["modifications"]["Patties"]["options"]
                prices = [opt["price"] for opt in patties_options]
                price = f"{min(prices):.2f}–{max(prices):.2f}"

            item_key = item["name"].lower().strip()
            index[item_key] = {
                "canonical_name": item["name"],
                "category": category_name,
                "description": item.get("description", ""),
                "price": price,
                "requires": required_mods,
                "optional": optional_mods,
                "mods": mod_details,
            }

    return index


def build_validation_map(index: dict) -> dict:
    validation_map = {}

    for item_key, item_data in index.items():
        mod_map = {}
        for mod_key, mod_data in item_data["mods"].items():
            option_names = sorted(
                opt["name"].lower() for opt in mod_data["options"]
            )
            mod_map[mod_key] = option_names
        validation_map[item_key] = mod_map

    return validation_map


if __name__ == "__main__":
    raw = json.loads(MENU_FILE.read_text())
    index = build_menu_index(raw)
    validation_map = build_validation_map(index)

    (DATA_DIR / "menu_index.json").write_text(json.dumps(index, indent=2))
    (DATA_DIR / "menu_validation_map.json").write_text(
        json.dumps(validation_map, indent=2)
    )

    print(f"Indexed {len(index)} items")
    print("Wrote menu_index.json and menu_validation_map.json to src/data/")
