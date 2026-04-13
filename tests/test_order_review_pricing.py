import asyncio

from src.chatbot.schema import BotInteractionRequest
from src.chatbot.visibility.handlers import StateHandlerFactory
from src.chatbot.visibility.utils import _build_order_lines
from src.menu.loader import init_menu


def setup_module(module):
    asyncio.run(init_menu())


def test_build_order_lines_uses_shared_variable_pricing():
    lines, total = asyncio.run(
        _build_order_lines(
            [
                {"name": "Hot Honey Burger", "quantity": 1, "modifier": "Double"},
                {"name": "Boneless Wings", "quantity": 12, "modifier": "Hot Honey, 12"},
            ]
        )
    )

    assert lines == [
        "- Hot Honey Burger ($11.99) [Double] = $11.99",
        "- Boneless Wings ($15.98) [Hot Honey, 12] = $15.98",
    ]
    assert total == 27.97


def test_order_review_shows_shared_pricing_totals():
    response = asyncio.run(
        StateHandlerFactory()._handle_order_review(
            BotInteractionRequest(
                user_id="user-1",
                latest_message="review my order",
                message_history=[],
                order_state={
                    "items": [
                        {"name": "Hot Honey Burger", "quantity": 1, "modifier": "Double"},
                        {"name": "Boneless Wings", "quantity": 12, "modifier": "Hot Honey, 12"},
                    ]
                },
            )
        )
    )

    assert "- Hot Honey Burger [Double] ($11.99) = $11.99" in response.chatbot_message
    assert "- Boneless Wings [Hot Honey, 12] ($15.98) = $15.98" in response.chatbot_message
    assert "Running total: $27.97" in response.chatbot_message
