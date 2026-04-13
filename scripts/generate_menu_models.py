"""Auto-generate src/menu/models.py from live Firestore data.

Usage:
    python scripts/generate_menu_models.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.firebase as firebase_module
from src.firebase import init_firebase
from src.config import settings

OUTPUT_PATH = Path(__file__).parent.parent / "src" / "menu" / "models.py"

HEADER = '''from __future__ import annotations

from typing import Optional

from firedantic import AsyncModel, AsyncSubCollection, AsyncSubModel
from pydantic import BaseModel
'''


# ---------------------------------------------------------------------------
# Schema merging
# ---------------------------------------------------------------------------

def merge_docs(docs: list[dict]) -> dict[str, list]:
    """Union all document field dicts, collecting all observed values per field."""
    merged: dict[str, list] = {}
    for doc in docs:
        for key, value in doc.items():
            merged.setdefault(key, []).append(value)
    return merged


# ---------------------------------------------------------------------------
# Type inference
# ---------------------------------------------------------------------------

def infer_type(values: list, field_name: str, nested_models: dict[str, str], total_docs: int) -> str:
    """Return a Python type annotation string for the observed values.

    Modifies nested_models in-place: {ModelClassName: model_code}.
    total_docs is the number of documents this field was observed in.
    """
    # Determine if the field is optional (missing in some docs)
    optional = len(values) < total_docs or any(v is None for v in values)

    non_none = [v for v in values if v is not None]
    if not non_none:
        return "Optional[object] = None"

    # Check for list-of-dicts → nested model
    if all(isinstance(v, list) for v in non_none):
        inner_items = [item for lst in non_none for item in lst if isinstance(item, dict)]
        if inner_items:
            model_name = _to_pascal_case(field_name) + "Model"
            if model_name not in nested_models:
                nested_models[model_name] = _build_basemodel(model_name, inner_items, nested_models)
            inner_type = f"list[{model_name}]"
            if optional:
                return f"Optional[{inner_type}] = None"
            return f"{inner_type} = []"
        # List of non-dicts (e.g. list[str])
        inner_python = _python_type_of_list_items(non_none)
        inner_type = f"list[{inner_python}]"
        if optional:
            return f"Optional[{inner_type}] = None"
        return f"{inner_type} = []"

    # Check for dict values → dict[str, ...]
    if all(isinstance(v, dict) for v in non_none):
        inner_vals = [iv for d in non_none for iv in d.values()]
        if all(isinstance(iv, dict) for iv in inner_vals) if inner_vals else False:
            inner_type = "dict[str, dict[str, str]]"
        elif all(isinstance(iv, str) for iv in inner_vals) if inner_vals else True:
            inner_type = "dict[str, str]"
        elif all(isinstance(iv, int) for iv in inner_vals) if inner_vals else False:
            inner_type = "dict[str, int]"
        else:
            inner_type = "dict[str, object]"
        if optional:
            return f"Optional[{inner_type}] = None"
        return f"{inner_type} = dict()"

    # Check for datetime / SERVER_TIMESTAMP sentinel
    type_names = {type(v).__name__ for v in non_none}
    if type_names & {"DatetimeWithNanoseconds", "datetime"}:
        return "Optional[object] = None"

    # Primitive types
    if all(isinstance(v, bool) for v in non_none):
        base = "bool"
        default = "False"
    elif all(isinstance(v, int) for v in non_none):
        base = "int"
        default = "0"
    elif all(isinstance(v, float) for v in non_none):
        base = "float"
        default = "0.0"
    elif all(isinstance(v, str) for v in non_none):
        base = "str"
        default = '""'
    else:
        base = "object"
        default = "None"
        optional = True

    if optional:
        if base == "str":
            return f"Optional[str] = None"
        return f"Optional[{base}] = None"
    return f"{base} = {default}"


def _python_type_of_list_items(lists: list[list]) -> str:
    items = [item for lst in lists for item in lst]
    if not items:
        return "object"
    if all(isinstance(i, str) for i in items):
        return "str"
    if all(isinstance(i, int) for i in items):
        return "int"
    if all(isinstance(i, float) for i in items):
        return "float"
    return "object"


def _to_pascal_case(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_"))


# ---------------------------------------------------------------------------
# Model code generation helpers
# ---------------------------------------------------------------------------

def _build_basemodel(class_name: str, docs: list[dict], nested_models: dict[str, str]) -> str:
    merged = merge_docs(docs)
    total = len(docs)
    lines = [f"class {class_name}(BaseModel):"]
    for field, values in merged.items():
        if field == "id":
            continue  # id is handled by firedantic
        annotation = infer_type(values, field, nested_models, total)
        lines.append(f"    {field}: {annotation}")
    if len(lines) == 1:
        lines.append("    pass")
    return "\n".join(lines)


def _build_asyncmodel(class_name: str, base: str, docs: list[dict], nested_models: dict[str, str],
                      collection_tpl: str | None, root_collection: str | None) -> str:
    merged = merge_docs(docs)
    total = len(docs)
    lines = [f"class {class_name}({base}):"]

    if root_collection:
        lines.append(f'    __collection__ = "{root_collection}"')

    for field, values in merged.items():
        if field == "id":
            continue
        annotation = infer_type(values, field, nested_models, total)
        lines.append(f"    {field}: {annotation}")

    if collection_tpl:
        lines.append("")
        lines.append("    class Collection(AsyncSubCollection):")
        lines.append(f'        __collection_tpl__ = "{collection_tpl}"')

    if len(lines) == 1:
        lines.append("    pass")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    if not settings.USER_ID:
        print("ERROR: USER_ID not set")
        sys.exit(1)

    await init_firebase()
    db = firebase_module.firebaseDatabase
    user_id = settings.USER_ID

    # Users root doc
    user_snap = await db.collection("Users").document(user_id).get()
    user_docs = [user_snap.to_dict() or {}]

    # Users/{id}/Inventory (up to 20)
    inv_snaps = await db.collection("Users").document(user_id).collection("Inventory").limit(20).get()
    inv_docs = [s.to_dict() for s in inv_snaps if s.to_dict()]

    # Users/{id}/InventoryIdMaps (up to 5 — usually just "default")
    idmap_snaps = await db.collection("Users").document(user_id).collection("InventoryIdMaps").limit(5).get()
    idmap_docs = [s.to_dict() for s in idmap_snaps if s.to_dict()]

    # menus root doc (synthetic)
    menu_docs = [{"updated_at": None}]

    # Derive categories and items from Inventory (same transformation as sync.py)
    seen_cats: dict[str, str] = {}
    for raw in inv_docs:
        for cat in raw.get("categories", []):
            if isinstance(cat, dict) and cat.get("id"):
                seen_cats[cat["id"]] = cat.get("name", "")
    cat_docs = [{"id": k, "name": v} for k, v in seen_cats.items()]

    item_docs = []
    for raw in inv_docs:
        cats = raw.get("categories", [])
        item_docs.append({
            "id": raw.get("id", ""),
            "name": raw.get("name", ""),
            "category_id": cats[0]["id"] if cats and isinstance(cats[0], dict) else "",
            "category_name": cats[0]["name"] if cats and isinstance(cats[0], dict) else "",
            "price": raw.get("price", 0),
            "description": raw.get("alternateName"),
            "modifier_groups": [
                {"id": g.get("id", ""), "name": g.get("name", ""),
                 "min_required": 0, "max_allowed": 0,
                 "modifiers": [{"id": m.get("id", ""), "name": m.get("name", ""), "price": m.get("price", 0)}
                               for m in g.get("modifiers", []) if isinstance(m, dict)]}
                for g in raw.get("modifierGroups", []) if isinstance(g, dict)
            ],
        })

    if not inv_docs:
        print("WARNING: No inventory documents found")
    if not cat_docs:
        print("WARNING: No category documents found — skipping MenuCategory generation")
    if not item_docs:
        print("WARNING: No item documents found — skipping MenuItem generation")

    # nested_models collects {ClassName: code_string} in insertion order (deepest first)
    nested_models: dict[str, str] = {}

    user_code = _build_asyncmodel(
        "User", "AsyncModel", user_docs, nested_models,
        collection_tpl=None, root_collection="Users",
    )
    inv_code = _build_asyncmodel(
        "InventoryItem", "AsyncSubModel", inv_docs, nested_models,
        collection_tpl="Users/{id}/Inventory", root_collection=None,
    ) if inv_docs else _fallback_inv()
    idmap_code = _build_asyncmodel(
        "InventoryIdMap", "AsyncSubModel", idmap_docs, nested_models,
        collection_tpl="Users/{id}/InventoryIdMaps", root_collection=None,
    ) if idmap_docs else _fallback_idmap()
    menu_code = _build_asyncmodel(
        "Menu", "AsyncModel", menu_docs, nested_models,
        collection_tpl=None, root_collection="menus",
    )
    cat_code = _build_asyncmodel(
        "MenuCategory", "AsyncSubModel", cat_docs, nested_models,
        collection_tpl="menus/{id}/categories", root_collection=None,
    ) if cat_docs else _fallback_category()
    item_code = _build_asyncmodel(
        "MenuItem", "AsyncSubModel", item_docs, nested_models,
        collection_tpl="menus/{id}/items", root_collection=None,
    ) if item_docs else _fallback_item()

    # Build file content — nested BaseModels first, then Users tree, then menus tree
    sections = [HEADER]
    for model_code in nested_models.values():
        sections.append(model_code + "\n")
    sections += [user_code + "\n", inv_code + "\n", idmap_code + "\n",
                 menu_code + "\n", cat_code + "\n", item_code + "\n"]

    OUTPUT_PATH.write_text("\n".join(sections))
    print(f"Written {OUTPUT_PATH}")


def _fallback_inv() -> str:
    return (
        "class InventoryItem(AsyncSubModel):\n"
        "    name: str\n\n"
        "    class Collection(AsyncSubCollection):\n"
        '        __collection_tpl__ = "Users/{id}/Inventory"'
    )


def _fallback_idmap() -> str:
    return (
        "class InventoryIdMap(AsyncSubModel):\n"
        "    updated_at: Optional[object] = None\n\n"
        "    class Collection(AsyncSubCollection):\n"
        '        __collection_tpl__ = "Users/{id}/InventoryIdMaps"'
    )


def _fallback_category() -> str:
    return (
        "class MenuCategory(AsyncSubModel):\n"
        "    name: str\n\n"
        "    class Collection(AsyncSubCollection):\n"
        '        __collection_tpl__ = "menus/{id}/categories"'
    )


def _fallback_item() -> str:
    return (
        "class MenuItem(AsyncSubModel):\n"
        "    name: str\n\n"
        "    class Collection(AsyncSubCollection):\n"
        '        __collection_tpl__ = "menus/{id}/items"'
    )


asyncio.run(main())
