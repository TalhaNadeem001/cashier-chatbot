from google.cloud.firestore_v1 import SERVER_TIMESTAMP

from src.firebase import firebaseDatabase
from src.config import settings
from src.menu.schema import InventoryItemSchema


async def sync_inventory_to_firestore(inventory: dict[str, InventoryItemSchema]) -> tuple[int, int, int]:
    """Sync a flat inventory payload to Firestore.

    Returns (categories_synced, items_synced, combos_synced).
    """
    db = firebaseDatabase
    restaurant_id = settings.RESTAURANT_ID
    menu_ref = db.collection("menus").document(restaurant_id)

    await menu_ref.set({
        "updated_at": SERVER_TIMESTAMP,
    })

    # Deduplicate categories by ID
    categories: dict[str, str] = {}
    for item in inventory.values():
        for cat in item.categories:
            categories[cat.id] = cat.name

    categories_synced = 0
    for cat_id, cat_name in categories.items():
        cat_ref = menu_ref.collection("categories").document(cat_id)
        await cat_ref.set({
            "id": cat_id,
            "name": cat_name,
        })
        categories_synced += 1

    items_synced = 0
    for item in inventory.values():
        category_id = item.categories[0].id if item.categories else ""
        category_name = item.categories[0].name if item.categories else ""
        modifier_groups = [
            {
                "id": group.id,
                "name": group.name,
                "min_required": 0,
                "max_allowed": 0,
                "modifiers": [
                    {"id": mod.id, "name": mod.name, "price": mod.price}
                    for mod in group.modifiers
                ],
            }
            for group in item.modifierGroups
        ]
        item_ref = menu_ref.collection("items").document(item.id)
        await item_ref.set({
            "id": item.id,
            "name": item.name,
            "category_id": category_id,
            "category_name": category_name,
            "price": item.price,
            "description": item.alternateName,
            "modifier_groups": modifier_groups,
        })
        items_synced += 1

    return categories_synced, items_synced, 0
