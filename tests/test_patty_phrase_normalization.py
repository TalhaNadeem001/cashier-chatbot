"""Tests for patty-phrase canonicalization (ZAP-88).

The reported bug: the customer said "double patty" and the chatbot echoed
back "Dsouble Patty" (a mutated typo) and wrote the wrong text into the
order. Root cause: the modifier text "double patty" doesn't exactly match
the canonical option "Double", so validation hops to the LLM
re-canonicalization step, where it can hallucinate typos.

Fix: a deterministic lookup that maps the common natural-language patty
phrasings to the canonical Single/Double/Triple/Quadruple label before the
LLM is involved.
"""
import asyncio
import os

import pytest


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _ensure_menu_loaded():
    os.environ.setdefault(
        "CLOVER_MENU_JSON_PATH",
        "tests/fixtures/clover_menu_pricing.json",
    )
    from src.menu.loader import init_menu

    _run(init_menu())


@pytest.fixture
def hot_honey_allowed_names():
    from src.menu.loader import get_item_definition

    item = get_item_definition("Hot Honey Burger")
    assert item is not None, "Hot Honey Burger must exist in the test fixture"

    names: list[str] = []
    for group in item.get("modifier_groups", []):
        for mod in group.get("modifiers", []):
            name = mod.get("name")
            if name:
                names.append(str(name))
    # Sanity check: the patty labels must be present on the fixture.
    for patty in ("Single", "Double", "Triple", "Quadruple"):
        assert patty in names, f"Patty option {patty!r} missing from fixture"
    return names


@pytest.mark.parametrize(
    "user_text,expected",
    [
        ("double patty", "Double"),
        ("Double Patty", "Double"),
        ("double patties", "Double"),
        ("2 patty", "Double"),
        ("2 patties", "Double"),
        ("two patty", "Double"),
        ("two patties", "Double"),
        ("single patty", "Single"),
        ("one patty", "Single"),
        ("1 patty", "Single"),
        ("triple patty", "Triple"),
        ("3 patties", "Triple"),
        ("three patty", "Triple"),
        ("quadruple patty", "Quadruple"),
        ("quad patty", "Quadruple"),
        ("4 patties", "Quadruple"),
    ],
)
def test_patty_phrase_maps_to_canonical(hot_honey_allowed_names, user_text, expected):
    from src.chatbot.cart.item_detection_service import _normalize_patty_phrase

    result = _normalize_patty_phrase(user_text, hot_honey_allowed_names)
    assert result == expected, (
        f"Expected {user_text!r} -> {expected!r}, got {result!r}"
    )


def test_non_patty_phrases_not_normalized(hot_honey_allowed_names):
    from src.chatbot.cart.item_detection_service import _normalize_patty_phrase

    assert _normalize_patty_phrase("lettuce bun", hot_honey_allowed_names) is None
    assert _normalize_patty_phrase("cajun fries", hot_honey_allowed_names) is None
    assert _normalize_patty_phrase("jalapenos", hot_honey_allowed_names) is None


def test_patty_phrase_skipped_when_option_absent():
    """Non-burger items don't offer Single/Double/etc., so skip the mapping."""
    from src.chatbot.cart.item_detection_service import _normalize_patty_phrase

    allowed = ["Naked", "Buffalo", "Hot Honey"]  # wing flavors
    assert _normalize_patty_phrase("double patty", allowed) is None


def test_end_to_end_double_patty_lands_as_double():
    """Full pipeline: modifier text "double patty" produces "Double" not a typo."""
    from src.chatbot.cart.item_detection_service import detect_mods_allowed

    order_item = {
        "name": "Hot Honey Burger",
        "quantity": 1,
        "modifier": "double patty, lettuce bun",
    }
    issues = _run(detect_mods_allowed(order_item, "Hot Honey Burger"))
    assert issues == [], (
        f"No invalid modifiers expected; got {[i.invalid_modifier for i in issues]}"
    )
    # Canonical spellings, no 'Dsouble' typo.
    assert order_item["modifier"] == "Double, Lettuce Bun", (
        f"Unexpected canonical modifier string: {order_item['modifier']!r}"
    )
