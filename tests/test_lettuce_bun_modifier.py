"""Tests for Hot Honey Burger lettuce-bun modifier resolution (ZAP-85).

The reported bug: the chatbot replied "I don't recognize `lettuce bun` for the
Hot Honey Burger" when the customer asked for a lettuce bun. The underlying
cause is that colloquial variants like "lettuce wrap" / "lettuce leaf" /
"make it a lettuce bun" score between NOT_FOUND_THRESHOLD and
MODS_CONFIRMED_THRESHOLD against the canonical "Lettuce Bun" modifier, so they
would silently fall through to the AI resolver and could come back rejected.
These tests lock in the dominant-match fallback so those variants resolve
locally without an AI round-trip.
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
def allowed_names():
    from src.menu.loader import get_item_definition

    item = get_item_definition("Hot Honey Burger")
    assert item is not None, "Hot Honey Burger must exist in the test fixture"

    names: list[str] = []
    for group in item.get("modifier_groups", []):
        for mod in group.get("modifiers", []):
            name = mod.get("name")
            if name:
                names.append(str(name))
    # Lettuce Bun is the canonical modifier we care about.
    assert "Lettuce Bun" in names
    return names


@pytest.mark.parametrize(
    "user_text",
    [
        "lettuce bun",
        "Lettuce Bun",
        "LETTUCE BUN",
        "lettuce wrap",
        "lettuce leaf",
        "make it a lettuce bun",
    ],
)
def test_lettuce_bun_variants_resolve_to_canonical(allowed_names, user_text):
    from src.chatbot.cart.item_detection_service import _resolve_allowed_modifier

    resolved = _run(
        _resolve_allowed_modifier(
            item_name="Hot Honey Burger",
            mod_text=user_text,
            allowed_names=allowed_names,
        )
    )
    assert resolved == "Lettuce Bun", (
        f"'{user_text}' should map to 'Lettuce Bun', got {resolved!r}"
    )


def test_hot_honey_burger_end_to_end_lettuce_bun_is_valid():
    """Full validate-mod-selections path must not flag lettuce bun as invalid."""
    from src.chatbot.cart.item_detection_service import detect_mods_allowed

    order_item = {
        "name": "Hot Honey Burger",
        "quantity": 1,
        "modifier": "lettuce bun",
    }
    issues = _run(detect_mods_allowed(order_item, "Hot Honey Burger"))
    assert issues == [], (
        f"Lettuce bun should be accepted; got {len(issues)} invalid issues: "
        f"{[i.invalid_modifier for i in issues]}"
    )
    # And the canonical name should have been written back.
    assert order_item["modifier"] == "Lettuce Bun"


def test_truly_ambiguous_modifier_still_falls_through_to_ai(monkeypatch, allowed_names):
    """The dominant-match shortcut must not mask genuine ambiguity."""
    from src.chatbot.cart import item_detection_service

    # Force a scenario where two candidates tie — neither dominates the other.
    def _fake_extract(*args, **kwargs):  # noqa: ANN002, ANN003
        return [
            ("Lettuce Bun", 72.0, 0),
            ("Beef Bacon", 70.0, 1),
        ]

    class _FakeProcess:
        extract = staticmethod(_fake_extract)

    monkeypatch.setattr(item_detection_service, "process", _FakeProcess)

    async def _fake_ai(**_kwargs):
        class _R:
            status = "match"
            canonical_modifier = "Lettuce Bun"

        return _R()

    monkeypatch.setattr(
        item_detection_service, "resolve_closest_modifier_match", _fake_ai
    )

    resolved = _run(
        item_detection_service._resolve_allowed_modifier(
            item_name="Hot Honey Burger",
            mod_text="something ambiguous",
            allowed_names=allowed_names,
        )
    )
    # Should have taken the AI path and returned its canonical, not silently
    # picked the local winner.
    assert resolved == "Lettuce Bun"
