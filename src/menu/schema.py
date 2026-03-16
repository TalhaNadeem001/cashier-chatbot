from pydantic import BaseModel

class MenuIngestRequest(BaseModel):
    """Ingest request schema."""
    menu_items: list[dict]


class MenuIngestResponse(BaseModel):
    """Ingest response schema."""
    success: bool