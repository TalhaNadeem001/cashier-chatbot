from fastapi import APIRouter
from src.menu.schema import InventoryIngestRequest, MenuIngestResponse
from src.menu.sync import sync_inventory_to_firestore
from src.menu.loader import init_menu

router = APIRouter(prefix="/menu", tags=["menu"])


@router.post("/ingest", response_model=MenuIngestResponse)
async def ingest_menu(request: InventoryIngestRequest) -> MenuIngestResponse:
    categories_synced, items_synced, combos_synced = await sync_inventory_to_firestore(request.root)
    await init_menu()
    return MenuIngestResponse(
        success=True,
        items_synced=items_synced,
        categories_synced=categories_synced,
        combos_synced=combos_synced,
    )
