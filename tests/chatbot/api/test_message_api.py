from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from src.app.main import create_app
from src.chatbot.api.schema import ChatbotResponse
from src.chatbot.exceptions import AIServiceError


def build_client(*, raise_server_exceptions: bool = True) -> TestClient:
    return TestClient(
        create_app(use_lifespan=False),
        raise_server_exceptions=raise_server_exceptions,
    )


def build_payload() -> dict:
    return {
        "user_id": "test-user",
        "latest_message": "Can I get a burger?",
        "message_history": [],
    }


def test_message_route_returns_current_chatbot_response_contract() -> None:
    expected = ChatbotResponse(
        chatbot_message="One burger added.",
        order_state={"items": [{"name": "burger", "quantity": 1}]},
        previous_state="food_order",
        previous_food_order_state="new_order",
        customer_name="Taylor",
    )

    with patch("src.chatbot.api.router.ChatReplyService") as service_cls:
        service = service_cls.return_value
        service.interpret_and_respond = AsyncMock(return_value=expected)

        with build_client(raise_server_exceptions=False) as client:
            response = client.post("/api/bot/message", json=build_payload())

    assert response.status_code == 200
    assert response.json() == expected.model_dump(mode="json")
    service.interpret_and_respond.assert_awaited_once()


def test_message_route_uses_registered_exception_handler() -> None:
    with patch("src.chatbot.api.router.ChatReplyService") as service_cls:
        service = service_cls.return_value
        service.interpret_and_respond = AsyncMock(side_effect=AIServiceError("boom"))

        with build_client(raise_server_exceptions=False) as client:
            response = client.post("/api/bot/message", json=build_payload())

    assert response.status_code == 503
    assert response.json() == {"detail": "boom"}
