import asyncio

from src.chatbot.cart import item_detection_service
from src.chatbot.internal_schemas import ClosestModifierResolution


def test_validate_order_items_reports_invalid_modifier_and_keeps_valid_ones(monkeypatch):
    monkeypatch.setattr(
        item_detection_service,
        "get_item_definition",
        lambda name: {
            "modifier_groups": [
                {"modifiers": [{"name": "Spicy"}, {"name": "Mild"}]},
            ]
        },
    )

    async def fake_get_item_category(name: str):
        return None

    monkeypatch.setattr(item_detection_service, "get_item_category", fake_get_item_category)

    result = asyncio.run(
        item_detection_service.validate_order_items(
            [{"name": "Chicken Sando", "quantity": 1, "modifier": "Spicy, Ranch"}]
        )
    )

    assert result.items == [{"name": "Chicken Sando", "quantity": 1, "modifier": "Spicy"}]
    assert len(result.invalid_modifiers) == 1
    assert result.invalid_modifiers[0].item_name == "Chicken Sando"
    assert result.invalid_modifiers[0].invalid_modifier == "Ranch"
    assert result.follow_up_requirements == []


def test_validate_order_item_modifiers_resolves_three_patties_to_triple(monkeypatch):
    monkeypatch.setattr(
        item_detection_service,
        "get_item_definition",
        lambda name: {
            "modifier_groups": [
                {"modifiers": [{"name": "Single"}, {"name": "Double"}, {"name": "Triple"}, {"name": "Quadruple"}]},
            ],
        },
    )

    async def fake_resolve_closest_modifier_match(item_name, modifier_text, allowed_options, latest_message=None):
        assert item_name == "Hot Honey Burger"
        assert modifier_text == "3 patties"
        assert allowed_options == ["Single", "Double", "Triple", "Quadruple"]
        return ClosestModifierResolution(status="match", canonical_modifier="Triple")

    monkeypatch.setattr(
        item_detection_service,
        "resolve_closest_modifier_match",
        fake_resolve_closest_modifier_match,
    )

    result = asyncio.run(
        item_detection_service.validate_order_item_modifiers(
            [{"name": "Hot Honey Burger", "quantity": 1, "modifier": "3 patties"}],
            latest_message="hot honey burger with 3 patties",
        )
    )

    assert result.items == [{"name": "Hot Honey Burger", "quantity": 1, "modifier": "Triple"}]
    assert result.invalid_modifiers == []


def test_validate_order_item_modifiers_resolves_one_pattie_to_single(monkeypatch):
    monkeypatch.setattr(
        item_detection_service,
        "get_item_definition",
        lambda name: {
            "modifier_groups": [
                {"modifiers": [{"name": "Single"}, {"name": "Double"}, {"name": "Triple"}, {"name": "Quadruple"}]},
            ],
        },
    )

    async def fake_resolve_closest_modifier_match(item_name, modifier_text, allowed_options, latest_message=None):
        assert item_name == "Hot Honey Burger"
        assert modifier_text == "1 pattie"
        assert allowed_options == ["Single", "Double", "Triple", "Quadruple"]
        return ClosestModifierResolution(status="match", canonical_modifier="Single")

    monkeypatch.setattr(
        item_detection_service,
        "resolve_closest_modifier_match",
        fake_resolve_closest_modifier_match,
    )

    result = asyncio.run(
        item_detection_service.validate_order_item_modifiers(
            [{"name": "Hot Honey Burger", "quantity": 1, "modifier": "1 pattie"}],
            latest_message="hot honey burger with 1 pattie",
        )
    )

    assert result.items == [{"name": "Hot Honey Burger", "quantity": 1, "modifier": "Single"}]
    assert result.invalid_modifiers == []


def test_validate_order_item_modifiers_strips_invalid_modifier_before_follow_ups(monkeypatch):
    monkeypatch.setattr(
        item_detection_service,
        "get_item_definition",
        lambda name: {
            "modifier_groups": [
                {"modifiers": [{"name": "Spicy"}, {"name": "Mild"}]},
            ],
        },
    )

    result = asyncio.run(
        item_detection_service.validate_order_item_modifiers(
            [{"name": "Chicken Sando", "quantity": 1, "modifier": "Ranch"}]
        )
    )

    assert result.items == [{"name": "Chicken Sando", "quantity": 1, "modifier": None}]
    assert len(result.invalid_modifiers) == 1
    assert result.invalid_modifiers[0].invalid_modifier == "Ranch"
    assert result.follow_up_requirements == []


def test_validate_order_items_adds_burger_patty_follow_up(monkeypatch):
    monkeypatch.setattr(item_detection_service, "get_item_definition", lambda name: {"modifier_groups": []})

    async def fake_get_item_category(name: str):
        return "Smash Burgers"

    monkeypatch.setattr(item_detection_service, "get_item_category", fake_get_item_category)

    result = asyncio.run(
        item_detection_service.validate_order_items(
            [{"name": "All American Burger", "quantity": 1, "modifier": None}]
        )
    )

    assert result.items == [{"name": "All American Burger", "quantity": 1, "modifier": None}]
    assert result.invalid_modifiers == []
    assert len(result.follow_up_requirements) == 1
    assert result.follow_up_requirements[0].kind == "burger_patties"


def test_validate_order_items_adds_wings_flavor_follow_up(monkeypatch):
    monkeypatch.setattr(item_detection_service, "get_item_definition", lambda name: {"modifier_groups": []})

    async def fake_get_item_category(name: str):
        return "Boneless Wings"

    monkeypatch.setattr(item_detection_service, "get_item_category", fake_get_item_category)

    result = asyncio.run(
        item_detection_service.validate_order_items(
            [{"name": "Boneless Wings", "quantity": 12, "modifier": None}]
        )
    )

    assert result.items == [{"name": "Boneless Wings", "quantity": 12, "modifier": None}]
    assert result.invalid_modifiers == []
    assert len(result.follow_up_requirements) == 1
    assert result.follow_up_requirements[0].kind == "wings_flavor"
