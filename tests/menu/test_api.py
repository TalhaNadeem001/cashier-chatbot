from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from src.app.main import create_app
from src.menu.api.schema import MenuIngestResponse


def build_client() -> TestClient:
    return TestClient(create_app(use_lifespan=False))


def test_menu_ingest_route_uses_menu_service() -> None:
    expected = MenuIngestResponse(
        success=True,
        items_synced=1,
        categories_synced=1,
        combos_synced=0,
    )
    payload = {
        "item-1": {
            "id": "item-1",
            "name": "Classic Burger",
            "price": 999,
            "categories": [{"id": "cat-1", "name": "Burgers"}],
            "modifierGroups": [],
        }
    }

    with patch.object(
        __import__("src.menu.api.router", fromlist=["menu_service"]).menu_service,
        "ingest_inventory",
        AsyncMock(return_value=expected),
    ) as ingest_inventory:
        with build_client() as client:
            response = client.post("/menu/ingest", json=payload)

    assert response.status_code == 200
    assert response.json() == expected.model_dump(mode="json")
    ingest_inventory.assert_awaited_once()
