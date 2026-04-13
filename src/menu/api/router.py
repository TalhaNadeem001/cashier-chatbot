from fastapi import APIRouter
from src.menu.api.schema import InventoryIngestRequest, MenuIngestResponse
from src.menu.application.service import menu_service

router = APIRouter(prefix="/menu", tags=["menu"])


@router.post("/ingest", response_model=MenuIngestResponse)
async def ingest_menu(request: InventoryIngestRequest) -> MenuIngestResponse:
    return await menu_service.ingest_inventory(request.root)
