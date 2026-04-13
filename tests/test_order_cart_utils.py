from src.chatbot.cart.utils import (
    build_order_update_message,
    format_money_context_for_prompt,
    normalize_order_items,
    strip_order_state_for_delta,
)


def test_strip_order_state_for_delta_removes_derived_fields_and_flattens_selected_mods():
    order_state = {
        "items": [
            {
                "name": "Chicken Sando",
                "quantity": 2,
                "modifier": None,
                "selected_mods": {
                    "Spice": "Spicy",
                    "Combo": ["Plain Fries"],
                },
                "item_id": "abc",
                "resolved_mods": [{"modifier_id": "1"}],
                "unit_price": 1299,
                "item_total": 2598,
            }
        ],
        "combo": {"name": "Lunch Combo"},
        "order_total": 2598,
    }

    assert strip_order_state_for_delta(order_state) == {
        "items": [
            {
                "name": "Chicken Sando",
                "quantity": 2,
                "modifier": "Spicy, Plain Fries",
            }
        ]
    }


def test_strip_order_state_for_delta_dedupes_duplicate_selected_mod_values():
    order_state = {
        "items": [
            {
                "name": "Hot Honey Burger",
                "quantity": 3,
                "modifier": None,
                "selected_mods": {
                    "Lettuce Bun": "No Mods",
                    "Make It a Combo With Fries": ["No Mods"],
                },
            }
        ]
    }

    assert strip_order_state_for_delta(order_state) == {
        "items": [
            {
                "name": "Hot Honey Burger",
                "quantity": 3,
                "modifier": "No Mods",
            }
        ]
    }


def test_normalize_order_items_merges_identical_rows_only():
    items = [
        {"name": "Chicken Sando", "quantity": 1, "modifier": "spicy"},
        {"name": "Chicken Sando", "quantity": 2, "modifier": "spicy"},
        {"name": "Chicken Sando", "quantity": 1, "modifier": None},
        {"name": "Chicken Sando", "quantity": 1, "modifier": "plain"},
        {"name": "Chicken Sando", "quantity": 0, "modifier": "plain"},
    ]

    assert normalize_order_items(items) == [
        {"name": "Chicken Sando", "quantity": 3, "modifier": "spicy"},
        {"name": "Chicken Sando", "quantity": 1, "modifier": None},
        {"name": "Chicken Sando", "quantity": 1, "modifier": "plain"},
    ]


def test_build_order_update_message_handles_empty_and_non_empty_orders():
    assert build_order_update_message([]) == "Your order is now empty. What would you like to order?"
    assert (
        build_order_update_message(
            [
                {"name": "Chicken Sando", "quantity": 2, "modifier": "spicy"},
                {"name": "Coke", "quantity": 1, "modifier": None},
            ]
        )
        == "Got it! Your order is now 2x Chicken Sando (spicy), 1x Coke. Is that all?"
    )


def test_format_money_context_for_prompt_formats_cent_fields_only():
    payload = {
        "items": [
            {
                "name": "Chicken Sando",
                "unit_price": 1299,
                "item_total": 2598,
            }
        ],
        "order_total": 2598,
        "combo": {"name": "Lunch Combo", "price": 2198},
        "combo_event": {"combo_name": "Lunch Combo", "combo_price": 2198},
        "options": [{"label": "Single", "price": 8.99}],
    }

    assert format_money_context_for_prompt(payload) == {
        "items": [
            {
                "name": "Chicken Sando",
                "unit_price": "$12.99",
                "item_total": "$25.98",
            }
        ],
        "order_total": "$25.98",
        "combo": {"name": "Lunch Combo", "price": "$21.98"},
        "combo_event": {"combo_name": "Lunch Combo", "combo_price": "$21.98"},
        "options": [{"label": "Single", "price": 8.99}],
    }
