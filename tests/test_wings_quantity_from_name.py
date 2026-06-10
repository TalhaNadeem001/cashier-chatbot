"""Tests for ZAP-90: wings quantity should come from the item name.

The reported bug: ordering "2 wings" (or an explicit 2-piece order) was accepted
even though the menu only sells wings in fixed bundles (6/12/18/24/30). The
root cause was that wings validation treated ``OrderItem.quantity`` (the
number of *bundles*) as the piece count. With production items named
"6Pc Boneless" / "12pc Boneless" etc., buying ``quantity=2`` of "6Pc Boneless"
incorrectly triggered "please pick a valid wings quantity" because 2 is not
in [6, 12, 18, 24, 30].

Fix: parse the piece count out of the item name when present and treat
``OrderItem.quantity`` as the bundle count. Fall back to the old behaviour
only when the name carries no explicit piece count.
"""
import asyncio

from src.chatbot.cart import item_detection_service


def _run(coro):
    return asyncio.run(coro)


def test_parse_wings_pieces_from_name_variants():
    parse = item_detection_service._parse_wings_pieces_from_name
    assert parse("6Pc Boneless") == 6
    assert parse("6pc Boneless") == 6
    assert parse("12pc Boneless") == 12
    assert parse("18 Piece Boneless") == 18
    assert parse("24-pc Boneless") == 24
    assert parse("30 pcs Boneless") == 30
    assert parse("12PC Bone-In Breaded") == 12


def test_parse_wings_pieces_rejects_unknown_counts():
    parse = item_detection_service._parse_wings_pieces_from_name
    # Only menu-valid piece counts are returned; everything else is None.
    assert parse("2pc Boneless") is None
    assert parse("9pc Boneless") is None
    assert parse("Boneless Wings") is None
    assert parse("") is None
    assert parse("Spicy Garlic Parm") is None


def test_parse_does_not_match_unrelated_numbers():
    parse = item_detection_service._parse_wings_pieces_from_name
    # Digits not followed by pc/piece should not match.
    assert parse("6 Boneless") is None
    assert parse("6-Boneless") is None
    assert parse("Boneless 6 special") is None


def test_double_bundle_of_six_piece_no_longer_triggers_quantity_follow_up(monkeypatch):
    """Regression for ZAP-90: buying 2×6pc wings is a valid order (12 wings)."""
    monkeypatch.setattr(
        item_detection_service,
        "get_item_definition",
        lambda name: {"modifier_groups": []},
    )

    async def fake_get_item_category(name: str):
        return "Boneless Wings"

    monkeypatch.setattr(item_detection_service, "get_item_category", fake_get_item_category)

    result = _run(
        item_detection_service.validate_order_items(
            [{"name": "6Pc Boneless", "quantity": 2, "modifier": "Buffalo"}]
        )
    )

    # 2 bundles of 6 pieces is fine — no quantity follow-up should be raised.
    assert [r.kind for r in result.follow_up_requirements] == []


def test_wings_item_with_size_in_name_asks_for_flavor_only(monkeypatch):
    monkeypatch.setattr(
        item_detection_service,
        "get_item_definition",
        lambda name: {"modifier_groups": []},
    )

    async def fake_get_item_category(name: str):
        return "Boneless Wings"

    monkeypatch.setattr(item_detection_service, "get_item_category", fake_get_item_category)

    result = _run(
        item_detection_service.validate_order_items(
            [{"name": "12pc Boneless", "quantity": 1, "modifier": None}]
        )
    )

    kinds = [r.kind for r in result.follow_up_requirements]
    # We know the size (12 → 2 flavors max), so ask for flavor, not quantity.
    assert kinds == ["wings_flavor"]
    follow_up = result.follow_up_requirements[0]
    assert follow_up.details.get("quantity") == 12
    assert follow_up.details.get("max_flavors") == 2


def test_wings_flavor_limit_uses_pieces_from_name(monkeypatch):
    monkeypatch.setattr(
        item_detection_service,
        "get_item_definition",
        lambda name: {"modifier_groups": []},
    )

    async def fake_get_item_category(name: str):
        return "Boneless Wings"

    monkeypatch.setattr(item_detection_service, "get_item_category", fake_get_item_category)

    # 6 pieces only allows 1 flavor; asking for 3 flavors should be rejected.
    result = _run(
        item_detection_service.validate_order_items(
            [{"name": "6Pc Boneless", "quantity": 1, "modifier": "Buffalo, BBQ, Hot Honey"}]
        )
    )

    assert [r.kind for r in result.follow_up_requirements] == ["wings_flavor_limit"]
    details = result.follow_up_requirements[0].details
    assert details.get("quantity") == 6
    assert details.get("max_flavors") == 1
    assert details.get("selected_count") == 3


def test_generic_wings_item_still_requires_quantity(monkeypatch):
    """Fallback: when the name has no piece count, keep old quantity-as-pieces behavior."""
    monkeypatch.setattr(
        item_detection_service,
        "get_item_definition",
        lambda name: {"modifier_groups": []},
    )

    async def fake_get_item_category(name: str):
        return "Boneless Wings"

    monkeypatch.setattr(item_detection_service, "get_item_category", fake_get_item_category)

    # Generic "Boneless Wings" with quantity=2 → not a valid piece count → ask.
    result = _run(
        item_detection_service.validate_order_items(
            [{"name": "Boneless Wings", "quantity": 2, "modifier": None}]
        )
    )

    assert [r.kind for r in result.follow_up_requirements] == ["wings_quantity"]


def test_generic_wings_item_with_valid_piece_quantity_proceeds_to_flavor(monkeypatch):
    """Legacy: 'Boneless Wings' with quantity=12 (piece-count semantics) still works."""
    monkeypatch.setattr(
        item_detection_service,
        "get_item_definition",
        lambda name: {"modifier_groups": []},
    )

    async def fake_get_item_category(name: str):
        return "Boneless Wings"

    monkeypatch.setattr(item_detection_service, "get_item_category", fake_get_item_category)

    result = _run(
        item_detection_service.validate_order_items(
            [{"name": "Boneless Wings", "quantity": 12, "modifier": None}]
        )
    )

    assert [r.kind for r in result.follow_up_requirements] == ["wings_flavor"]
    assert result.follow_up_requirements[0].details.get("quantity") == 12
