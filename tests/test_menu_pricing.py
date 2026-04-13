import asyncio

from src.menu.loader import (
    get_order_item_line_total,
    get_order_item_unit_price,
    init_menu,
    order_item_uses_quantity_selection,
)


def setup_module(module):
    asyncio.run(init_menu())


def test_fixed_price_item_includes_paid_modifier_prices():
    item = {"name": "Chicken Sando", "quantity": 1, "modifier": "Plain Fries"}

    assert get_order_item_unit_price(item) == 1249
    assert get_order_item_line_total(item) == 1249


def test_variable_price_burger_uses_patty_selection_and_order_quantity():
    item = {"name": "Hot Honey Burger", "quantity": 2, "modifier": "Double, Beef Bacon"}

    assert get_order_item_unit_price(item) == 1499
    assert get_order_item_line_total(item) == 2998


def test_variable_price_wings_require_quantity_selection():
    item = {"name": "Boneless Wings", "quantity": 12, "modifier": "Hot Honey"}

    assert get_order_item_unit_price(item) is None
    assert get_order_item_line_total(item) is None
    assert order_item_uses_quantity_selection(item) is False


def test_variable_price_wings_use_quantity_selection_without_multiplier():
    item = {"name": "Boneless Wings", "quantity": 12, "modifier": "Hot Honey, 12"}

    assert get_order_item_unit_price(item) == 1598
    assert get_order_item_line_total(item) == 1598
    assert order_item_uses_quantity_selection(item) is True
