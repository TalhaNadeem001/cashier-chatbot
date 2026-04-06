#!/usr/bin/env python3
"""Print all menu item names from menu_index.json, grouped by category."""

import json
from pathlib import Path

DATA_FILE = Path(__file__).parent.parent / "src" / "data" / "menu_index.json"

index = json.loads(DATA_FILE.read_text())

by_category: dict[str, list[str]] = {}
for item in index.values():
    by_category.setdefault(item["category"], []).append(item["canonical_name"])

for category, names in by_category.items():
    print(f"\n{category}")
    for name in names:
        print(f"  - {name}")

print(f"\nTotal: {len(index)} items")
