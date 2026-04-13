from src.menu.api.schema import InventoryItemSchema, MenuIngestResponse
from src.menu.infrastructure.repository import menu_repository
from src.menu.infrastructure.sync import sync_inventory_to_firestore


class MenuService:
    async def reload_menu_context(self) -> None:
        await menu_repository.reload()

    async def ingest_inventory(
        self,
        inventory: dict[str, InventoryItemSchema],
    ) -> MenuIngestResponse:
        categories_synced, items_synced, combos_synced = await sync_inventory_to_firestore(inventory)
        await self.reload_menu_context()
        return MenuIngestResponse(
            success=True,
            items_synced=items_synced,
            categories_synced=categories_synced,
            combos_synced=combos_synced,
        )


menu_service = MenuService()
