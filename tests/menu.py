import pytest
from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


def test_menu_list():
    """Test menu list endpoint."""
    response = client.get("/menu/")
    assert response.status_code == 200
