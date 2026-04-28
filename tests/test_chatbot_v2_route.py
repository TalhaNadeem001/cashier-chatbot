from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.chatbot.schema import ChatbotV2MessageResponse


def _client() -> TestClient:
    from src.chatbot.router import v2_router

    app = FastAPI()
    app.include_router(v2_router)
    return TestClient(app)


def test_chatbot_v2_message_returns_direct_response(monkeypatch):
    from src.chatbot import router as router_mod

    async def _fake_handle_message(self, request):
        del self
        assert request.user_message == "hello there"
        assert request.session_id == "session-1"
        assert request.merchant_id == "merchant-1"
        return ChatbotV2MessageResponse(
            system_response="stubbed system response",
            session_id=request.session_id,
        )

    monkeypatch.setattr(router_mod.Orchestrator, "handle_message", _fake_handle_message)

    response = _client().post(
        "/chatbot/v2/message",
        json={
            "user_message": "hello there",
            "session_id": "session-1",
            "merchant_id": "merchant-1",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "system_response": "stubbed system response",
        "session_id": "session-1",
    }


def test_chatbot_v2_message_requires_all_fields():
    response = _client().post(
        "/chatbot/v2/message",
        json={
            "session_id": "session-1",
            "merchant_id": "merchant-1",
        },
    )

    assert response.status_code == 422
