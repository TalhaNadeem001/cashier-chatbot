"""
Build name -> POS id maps from data/inventory.json and write them to Firestore.

Before writing, all documents under Users/{USER_ID}/InventoryIdMaps are deleted,
then recreated (currently a single `default` document).

Maps are stored at:
  Users/{USER_ID}/InventoryIdMaps/default

Fields:
  - items, categories, modifier_groups: flat display name -> id
  - modifiers_by_group: modifier group name -> { modifier name -> modifier id }

Requires USER_ID and Firebase credentials in .env (see src.config.Config).

Usage:
  uv run python scripts/sync_inventory_id_maps.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from google.cloud.firestore_v1 import SERVER_TIMESTAMP

import src.firebase as firebase_module
from src.config import settings
from src.firebase import init_firebase

INVENTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "inventory.json"


def _merge_id(
    target: dict[str, str],
    name: str | None,
    id_: str | None,
    conflicts: list[tuple[str, str, str]],
) -> None:
    if not name or not id_:
        return
    if name in target and target[name] != id_:
        conflicts.append((name, target[name], id_))
    target[name] = id_


def build_maps_from_inventory(
    data: dict[str, object],
) -> tuple[
    dict[str, str],
    dict[str, str],
    dict[str, str],
    dict[str, dict[str, str]],
    list[tuple[str, str, str]],
]:
    items: dict[str, str] = {}
    categories: dict[str, str] = {}
    modifier_groups: dict[str, str] = {}
    modifiers_by_group: dict[str, dict[str, str]] = {}
    conflicts: list[tuple[str, str, str]] = []

    for _doc_id, payload in data.items():
        if not isinstance(payload, dict):
            continue
        item_name = payload.get("name")
        item_id = payload.get("id")
        _merge_id(
            items,
            str(item_name) if item_name is not None else None,
            str(item_id) if item_id is not None else None,
            conflicts,
        )

        for cat in payload.get("categories") or []:
            if isinstance(cat, dict):
                _merge_id(
                    categories,
                    str(cat["name"]) if cat.get("name") is not None else None,
                    str(cat["id"]) if cat.get("id") is not None else None,
                    conflicts,
                )

        for group in payload.get("modifierGroups") or []:
            if not isinstance(group, dict):
                continue
            g_name = group.get("name")
            g_id = group.get("id")
            g_name_s = str(g_name) if g_name is not None else None
            g_id_s = str(g_id) if g_id is not None else None
            _merge_id(modifier_groups, g_name_s, g_id_s, conflicts)

            if g_name_s:
                inner = modifiers_by_group.setdefault(g_name_s, {})
                for mod in group.get("modifiers") or []:
                    if isinstance(mod, dict):
                        _merge_id(
                            inner,
                            str(mod["name"]) if mod.get("name") is not None else None,
                            str(mod["id"]) if mod.get("id") is not None else None,
                            conflicts,
                        )

    return items, categories, modifier_groups, modifiers_by_group, conflicts


async def main() -> None:
    if not settings.USER_ID:
        sys.exit("USER_ID must be set in .env for Firestore path Users/{USER_ID}/...")

    if not INVENTORY_PATH.is_file():
        sys.exit(f"Missing inventory file: {INVENTORY_PATH}")

    with INVENTORY_PATH.open(encoding="utf-8") as f:
        raw = json.load(f)

    if not isinstance(raw, dict):
        sys.exit("inventory.json must be a JSON object (doc id -> item payload)")

    items, categories, modifier_groups, modifiers_by_group, conflicts = (
        build_maps_from_inventory(raw)
    )

    for name, old_id, new_id in conflicts:
        print(
            f"Warning: duplicate name {name!r}: had {old_id!r}, overwriting with {new_id!r}"
        )

    await init_firebase()
    db = firebase_module.firebaseDatabase
    if db is None:
        sys.exit("Firebase client not initialized")

    collection_ref = (
        db.collection("Users").document(settings.USER_ID).collection("InventoryIdMaps")
    )
    existing = await collection_ref.get()
    for doc_snap in existing:
        await doc_snap.reference.delete()
    if existing:
        print(f"Deleted {len(existing)} document(s) from InventoryIdMaps")

    doc_ref = collection_ref.document("default")
    await doc_ref.set(
        {
            "items": items,
            "categories": categories,
            "modifier_groups": modifier_groups,
            "modifiers_by_group": modifiers_by_group,
            "updated_at": SERVER_TIMESTAMP,
        }
    )

    nested_mod_count = sum(len(m) for m in modifiers_by_group.values())
    print(
        f"Wrote InventoryIdMaps/default for user {settings.USER_ID}: "
        f"{len(items)} items, {len(categories)} categories, "
        f"{len(modifier_groups)} modifier groups, {nested_mod_count} modifiers nested by group"
    )


if __name__ == "__main__":
    asyncio.run(main())
