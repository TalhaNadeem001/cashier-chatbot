import asyncio

from src.chatbot.cart import handlers as cart_handlers
from src.chatbot.clarification.fuzzy_matcher import _MatchResult
from src.chatbot.internal_schemas import ComboApplicationResult
from src.chatbot.schema import BotInteractionRequest, OrderDeltaResult, OrderItem
from src.menu.loader import init_menu


def setup_module(module):
    asyncio.run(init_menu())


def test_order_handler_enriches_variable_price_burger(monkeypatch):
    async def fake_apply_order_delta(self, latest_message: str, order_state: dict, message_history=None):
        return OrderDeltaResult(
            items=[OrderItem(name="hot honey burger", quantity=1, modifier="Double")]
        )

    async def fake_match_items_to_menu(self, items: list[dict], request: BotInteractionRequest):
        return [
            _MatchResult(
                item=OrderItem(**items[0]),
                status="confirmed",
                canonical_name="Hot Honey Burger",
            )
        ]

    async def fake_apply_best_combo(order_state: dict, previous_combo=None):
        return ComboApplicationResult(order_state=order_state, combo_event=None)

    async def fake_polish_food_order_reply(order_state: dict, order_outcome: dict, latest_message: str, message_history=None):
        return "ok"

    monkeypatch.setattr(cart_handlers.OrderExtractor, "apply_order_delta", fake_apply_order_delta)
    monkeypatch.setattr(cart_handlers.OrderStateHandler, "_match_items_to_menu", fake_match_items_to_menu)
    monkeypatch.setattr(cart_handlers, "apply_best_combo", fake_apply_best_combo)
    monkeypatch.setattr(cart_handlers, "polish_food_order_reply", fake_polish_food_order_reply)

    response = asyncio.run(
        cart_handlers.OrderStateHandler().handle(
            BotInteractionRequest(
                user_id="user-1",
                latest_message="double hot honey burger",
                message_history=[],
                order_state={"items": []},
            )
        )
    )

    assert response.order_state["items"][0]["name"] == "Hot Honey Burger"
    assert response.order_state["items"][0]["unit_price"] == 1199
    assert response.order_state["items"][0]["item_total"] == 1199
    assert response.order_state["order_total"] == 1199


def test_order_handler_enriches_variable_price_wings(monkeypatch):
    async def fake_apply_order_delta(self, latest_message: str, order_state: dict, message_history=None):
        return OrderDeltaResult(
            items=[OrderItem(name="boneless wings", quantity=12, modifier="Hot Honey, 12")]
        )

    async def fake_match_items_to_menu(self, items: list[dict], request: BotInteractionRequest):
        return [
            _MatchResult(
                item=OrderItem(**items[0]),
                status="confirmed",
                canonical_name="Boneless Wings",
            )
        ]

    async def fake_apply_best_combo(order_state: dict, previous_combo=None):
        return ComboApplicationResult(order_state=order_state, combo_event=None)

    async def fake_polish_food_order_reply(order_state: dict, order_outcome: dict, latest_message: str, message_history=None):
        return "ok"

    monkeypatch.setattr(cart_handlers.OrderExtractor, "apply_order_delta", fake_apply_order_delta)
    monkeypatch.setattr(cart_handlers.OrderStateHandler, "_match_items_to_menu", fake_match_items_to_menu)
    monkeypatch.setattr(cart_handlers, "apply_best_combo", fake_apply_best_combo)
    monkeypatch.setattr(cart_handlers, "polish_food_order_reply", fake_polish_food_order_reply)

    response = asyncio.run(
        cart_handlers.OrderStateHandler().handle(
            BotInteractionRequest(
                user_id="user-1",
                latest_message="12 hot honey boneless wings",
                message_history=[],
                order_state={"items": []},
            )
        )
    )

    assert response.order_state["items"][0]["name"] == "Boneless Wings"
    assert response.order_state["items"][0]["unit_price"] == 1598
    assert response.order_state["items"][0]["item_total"] == 1598
    assert response.order_state["order_total"] == 1598
