import asyncio

from src.chatbot.cart import handlers as cart_handlers
from src.chatbot.cart import item_detection_service
from src.chatbot.clarification.fuzzy_matcher import _MatchResult
from src.chatbot.internal_schemas import ComboApplicationResult
from src.chatbot.schema import BotInteractionRequest, OrderDeltaResult, OrderItem


def test_order_handler_strips_invalid_modifier_before_generating_reply(monkeypatch):
    captured: dict = {}

    async def fake_apply_order_delta(self, latest_message: str, order_state: dict, message_history=None):
        return OrderDeltaResult(
            items=[OrderItem(name="chicken sando", quantity=1, modifier="Ranch")]
        )

    async def fake_match_items_to_menu(self, items: list[dict], request: BotInteractionRequest):
        return [
            _MatchResult(
                item=OrderItem(**items[0]),
                status="confirmed",
                canonical_name="Chicken Sando",
            )
        ]

    async def fake_apply_best_combo(order_state: dict, previous_combo=None):
        return ComboApplicationResult(order_state=order_state, combo_event=None)

    async def fake_polish_food_order_reply(order_state: dict, order_outcome: dict, latest_message: str, message_history=None):
        captured["order_state"] = order_state
        captured["order_outcome"] = order_outcome
        return "Ranch is not allowed for Chicken Sando. Allowed options are Spicy."

    monkeypatch.setattr(cart_handlers.OrderExtractor, "apply_order_delta", fake_apply_order_delta)
    monkeypatch.setattr(cart_handlers.OrderStateHandler, "_match_items_to_menu", fake_match_items_to_menu)
    monkeypatch.setattr(cart_handlers, "apply_best_combo", fake_apply_best_combo)
    monkeypatch.setattr(cart_handlers, "polish_food_order_reply", fake_polish_food_order_reply)
    monkeypatch.setattr(cart_handlers, "get_item_id", lambda name: "item-1")
    monkeypatch.setattr(
        cart_handlers,
        "get_order_item_unit_price",
        lambda item: 9.99,
    )
    monkeypatch.setattr(
        cart_handlers,
        "get_order_item_line_total",
        lambda item: 9.99,
    )
    monkeypatch.setattr(
        cart_handlers,
        "resolve_mod_ids_from_string",
        lambda item_name, modifier_str: [],
    )
    monkeypatch.setattr(
        item_detection_service,
        "get_item_definition",
        lambda name: {
            "price": 9.99,
            "modifier_groups": [
                {"modifiers": [{"name": "Spicy"}]},
            ],
        },
    )

    async def fake_get_item_category(name: str):
        return None

    monkeypatch.setattr(
        item_detection_service,
        "get_item_category",
        fake_get_item_category,
    )

    request = BotInteractionRequest(
        user_id="user-1",
        latest_message="add ranch to the chicken sando",
        message_history=[],
        order_state={"items": []},
    )

    response = asyncio.run(cart_handlers.OrderStateHandler().handle(request))

    assert response.chatbot_message == "Ranch is not allowed for Chicken Sando. Allowed options are Spicy."
    assert response.order_state["items"] == [
        {
            "name": "Chicken Sando",
            "quantity": 1,
            "modifier": None,
            "item_id": "item-1",
            "unit_price": 9.99,
            "item_total": 9.99,
        }
    ]
    assert captured["order_outcome"]["invalid_modifiers"] == [
        {
            "item_name": "Chicken Sando",
            "invalid_modifier": "Ranch",
            "allowed_options": ["Spicy"],
        }
    ]
