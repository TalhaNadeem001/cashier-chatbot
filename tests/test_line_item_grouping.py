from src.chatbot.utils import _group_priced_line_items, _pricing_breakdown_from_order


def test_group_distinct_items_unchanged():
    items = [
        {"lineItemId": "a", "name": "Chicken", "quantity": 1, "unitPrice": 899, "modifierPrices": [], "lineTotal": 899},
        {"lineItemId": "b", "name": "Fries", "quantity": 1, "unitPrice": 350, "modifierPrices": [], "lineTotal": 350},
    ]
    grouped = _group_priced_line_items(items)
    assert len(grouped) == 2
    assert grouped[0]["name"] == "Chicken"
    assert grouped[1]["name"] == "Fries"


def test_group_identical_items_merged():
    items = [
        {"lineItemId": "a", "name": "Fish Battered Cod", "quantity": 1, "unitPrice": 1200, "modifierPrices": [], "lineTotal": 1200},
        {"lineItemId": "b", "name": "Fish Battered Cod", "quantity": 1, "unitPrice": 1200, "modifierPrices": [], "lineTotal": 1200},
    ]
    grouped = _group_priced_line_items(items)
    assert len(grouped) == 1
    assert grouped[0]["quantity"] == 2
    assert grouped[0]["lineTotal"] == 2400


def test_group_items_with_different_modifiers_not_merged():
    items = [
        {
            "lineItemId": "a",
            "name": "Chicken Sando",
            "quantity": 1,
            "unitPrice": 899,
            "modifierPrices": [{"modifierId": "m1", "name": "Spicy", "price": 100}],
            "lineTotal": 999,
        },
        {
            "lineItemId": "b",
            "name": "Chicken Sando",
            "quantity": 1,
            "unitPrice": 899,
            "modifierPrices": [],
            "lineTotal": 899,
        },
    ]
    grouped = _group_priced_line_items(items)
    assert len(grouped) == 2


def test_pricing_breakdown_groups_duplicate_clover_line_items():
    order_data = {
        "lineItems": {
            "elements": [
                {"id": "li-1", "name": "Fish Battered Cod", "unitQty": 1000, "price": 1200},
                {"id": "li-2", "name": "Fish Battered Cod", "unitQty": 1000, "price": 1200},
                {"id": "li-3", "name": "Can of Pop", "unitQty": 1000, "price": 200},
                {"id": "li-4", "name": "Can of Pop", "unitQty": 1000, "price": 200},
            ]
        }
    }
    breakdown = _pricing_breakdown_from_order(order_data)
    assert len(breakdown["lineItems"]) == 2
    by_name = {li["name"]: li for li in breakdown["lineItems"]}
    assert by_name["Fish Battered Cod"]["quantity"] == 2
    assert by_name["Fish Battered Cod"]["lineTotal"] == 2400
    assert by_name["Can of Pop"]["quantity"] == 2
    assert by_name["Can of Pop"]["lineTotal"] == 400
