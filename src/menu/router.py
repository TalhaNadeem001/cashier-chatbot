from fastapi import APIRouter
from src.menu.schema import MenuIngestRequest, MenuIngestResponse

router = APIRouter(prefix="/menu", tags=["menu"])


@router.post("/ingest", response_model=MenuIngestResponse)
async def ingest_menu(request: MenuIngestRequest) -> MenuIngestResponse:
    ...
