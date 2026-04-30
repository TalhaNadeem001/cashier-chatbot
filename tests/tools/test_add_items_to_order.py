import asyncio
from unittest.mock import AsyncMock, call, patch

from src.chatbot import tools as tools_mod
from src.chatbot.tools import addItemsToOrder

_FAKE_CREDS = {
    "token": "fake-token",
    "merchant_id": "test-merchant-id",
    "base_url": "https://apisandbox.dev.clover.com",
}

_FAKE_MENU = {
    "by_id": {
        "item-chicken": {
            "id": "item-chicken",
            "name": "Chicken Sando",
            "price": 899,
            "modifierGroups": {
                "elements": [
                    {
                        "id": "grp-1",
                        "modifiers": {
                            "elements": [
                                {"id": "mod-spicy", "name": "Spicy"},
                            ]
                        },
                    }
                ]
            },
        },
        "item-fries": {
            "id": "item-fries",
            "name": "Regular Fries",
            "price": 350,
            "modifierGroups": {"elements": []},
        },
        "item-wings": {
            "id": "item-wings",
            "name": "6 Pc Bone In Wings",
            "price": 699,
            "modifierGroups": {
                "elements": [
                    {
                        "id": "grp-style",
                        "name": "Wing Style",
                        "minRequired": 1,
                        "maxAllowed": 1,
                        "modifiers": {
                            "elements": [
                                {"id": "mod-flats", "name": "All Flats", "price": 200},
                                {"id": "mod-mixed", "name": "Mixed", "price": 0},
                                {"id": "mod-drums", "name": "All Drums", "price": 200},
                            ]
                        },
                    },
                    {
                        "id": "grp-sauce",
                        "name": "Wings Sauce",
                        "minRequired": 2,
                        "maxAllowed": 3,
                        "modifiers": {
                            "elements": [
                                {"id": "mod-hot-honey", "name": "Hot Honey", "price": 0},
                                {"id": "mod-buffalo", "name": "Buffalo", "price": 0},
                                {"id": "mod-bbq", "name": "BBQ", "price": 0},
                            ]
                        },
                    },
                ]
            },
        },
    },
    "by_modifier_id": {
        "mod-spicy": "item-chicken",
        "mod-flats": "item-wings",
        "mod-mixed": "item-wings",
        "mod-drums": "item-wings",
        "mod-hot-honey": "item-wings",
        "mod-buffalo": "item-wings",
        "mod-bbq": "item-wings",
    },
    "by_name": {},
    "by_category": {},
}


def _run(coro):
    return asyncio.run(coro)


def test_add_single_item_success():
    line_item_response = {"id": "li-abc", "price": 899}
    order_response = {"id": "order-1", "total": 899}

    async def _test():
        with (
            patch("src.chatbot.tools.cache_get", new_callable=AsyncMock, return_value="order-1"),
            patch("src.chatbot.tools.cache_set", new_callable=AsyncMock),
            patch("src.chatbot.tools._menu_items_cached_or_fresh", new_callable=AsyncMock, return_value=_FAKE_MENU),
            patch("src.chatbot.tools.add_clover_line_item", new_callable=AsyncMock, return_value=line_item_response) as mock_add_line,
            patch("src.chatbot.tools.add_clover_modification", new_callable=AsyncMock) as mock_add_mod,
            patch("src.chatbot.tools.fetch_clover_order", new_callable=AsyncMock, return_value=order_response),
        ):
            result = await addItemsToOrder(
                "session-1",
                [{"itemId": "item-chicken", "quantity": 1}],
                creds=_FAKE_CREDS,
            )
            mock_add_line.assert_awaited_once()
            mock_add_mod.assert_not_awaited()
            return result

    result = _run(_test())
    assert result["success"] is True
    assert len(result["addedItems"]) == 1
    assert result["addedItems"][0]["lineItemId"] == "li-abc"
    assert result["addedItems"][0]["itemId"] == "item-chicken"
    assert result["addedItems"][0]["quantity"] == 1
    assert result["addedItems"][0]["modifiersApplied"] == []
    assert result["updatedOrderTotal"] == 899
    assert result["failedItems"] == []


def test_add_item_with_modifier():
    line_item_response = {"id": "li-abc", "price": 899}
    order_response = {"id": "order-1", "total": 949}

    async def _test():
        with (
            patch("src.chatbot.tools.cache_get", new_callable=AsyncMock, return_value="order-1"),
            patch("src.chatbot.tools.cache_set", new_callable=AsyncMock),
            patch("src.chatbot.tools._menu_items_cached_or_fresh", new_callable=AsyncMock, return_value=_FAKE_MENU),
            patch("src.chatbot.tools.add_clover_line_item", new_callable=AsyncMock, return_value=line_item_response),
            patch("src.chatbot.tools.add_clover_modification", new_callable=AsyncMock) as mock_add_mod,
            patch("src.chatbot.tools.fetch_clover_order", new_callable=AsyncMock, return_value=order_response),
        ):
            result = await addItemsToOrder(
                "session-1",
                [{"itemId": "item-chicken", "quantity": 1, "modifiers": ["mod-spicy"]}],
                creds=_FAKE_CREDS,
            )
            mock_add_mod.assert_awaited_once()
            return result

    result = _run(_test())
    assert result["success"] is True
    assert result["addedItems"][0]["modifiersApplied"] == ["mod-spicy"]
    assert result["failedItems"] == []
    assert result["updatedOrderTotal"] == 949


def test_add_item_quantity_creates_separate_line_items():
    """quantity=3 should call add_clover_line_item 3 times, producing 3 entries each with quantity=1."""
    line_item_responses = [
        {"id": "li-1", "price": 899},
        {"id": "li-2", "price": 899},
        {"id": "li-3", "price": 899},
    ]
    order_response = {"id": "order-1", "total": 2697}

    async def _test():
        with (
            patch("src.chatbot.tools.cache_get", new_callable=AsyncMock, return_value="order-1"),
            patch("src.chatbot.tools.cache_set", new_callable=AsyncMock),
            patch("src.chatbot.tools._menu_items_cached_or_fresh", new_callable=AsyncMock, return_value=_FAKE_MENU),
            patch("src.chatbot.tools.add_clover_line_item", new_callable=AsyncMock, side_effect=line_item_responses) as mock_add_line,
            patch("src.chatbot.tools.add_clover_modification", new_callable=AsyncMock),
            patch("src.chatbot.tools.fetch_clover_order", new_callable=AsyncMock, return_value=order_response),
        ):
            result = await addItemsToOrder(
                "session-1",
                [{"itemId": "item-chicken", "quantity": 3}],
                creds=_FAKE_CREDS,
            )
            assert mock_add_line.await_count == 3
            return result

    result = _run(_test())
    assert result["success"] is True
    assert len(result["addedItems"]) == 3
    for entry in result["addedItems"]:
        assert entry["quantity"] == 1
        assert entry["itemId"] == "item-chicken"
    assert result["addedItems"][0]["lineItemId"] == "li-1"
    assert result["addedItems"][1]["lineItemId"] == "li-2"
    assert result["addedItems"][2]["lineItemId"] == "li-3"
    assert result["failedItems"] == []


def test_add_item_quantity_with_modifiers_per_unit():
    """quantity=2 with 1 modifier should apply the modifier to each unit independently (2 mod calls total)."""
    line_item_responses = [
        {"id": "li-1", "price": 899},
        {"id": "li-2", "price": 899},
    ]
    order_response = {"id": "order-1", "total": 1798}

    async def _test():
        with (
            patch("src.chatbot.tools.cache_get", new_callable=AsyncMock, return_value="order-1"),
            patch("src.chatbot.tools.cache_set", new_callable=AsyncMock),
            patch("src.chatbot.tools._menu_items_cached_or_fresh", new_callable=AsyncMock, return_value=_FAKE_MENU),
            patch("src.chatbot.tools.add_clover_line_item", new_callable=AsyncMock, side_effect=line_item_responses) as mock_add_line,
            patch("src.chatbot.tools.add_clover_modification", new_callable=AsyncMock) as mock_add_mod,
            patch("src.chatbot.tools.fetch_clover_order", new_callable=AsyncMock, return_value=order_response),
        ):
            result = await addItemsToOrder(
                "session-1",
                [{"itemId": "item-chicken", "quantity": 2, "modifiers": ["mod-spicy"]}],
                creds=_FAKE_CREDS,
            )
            assert mock_add_line.await_count == 2
            assert mock_add_mod.await_count == 2
            return result

    result = _run(_test())
    assert result["success"] is True
    assert len(result["addedItems"]) == 2
    for entry in result["addedItems"]:
        assert entry["quantity"] == 1
        assert entry["modifiersApplied"] == ["mod-spicy"]
    assert result["failedItems"] == []


def test_ambiguous_id_fails():
    ambiguous_menu = {
        **_FAKE_MENU,
        "by_id": {
            **_FAKE_MENU["by_id"],
            "mod-spicy": {"id": "mod-spicy", "name": "Spicy Item", "price": 100},
        },
    }

    async def _test():
        with (
            patch("src.chatbot.tools.cache_get", new_callable=AsyncMock, return_value="order-1"),
            patch("src.chatbot.tools.cache_set", new_callable=AsyncMock),
            patch("src.chatbot.tools._menu_items_cached_or_fresh", new_callable=AsyncMock, return_value=ambiguous_menu),
            patch("src.chatbot.tools.add_clover_line_item", new_callable=AsyncMock) as mock_add_line,
            patch("src.chatbot.tools.add_clover_modification", new_callable=AsyncMock),
            patch("src.chatbot.tools.fetch_clover_order", new_callable=AsyncMock, return_value={"total": 0}),
        ):
            result = await addItemsToOrder("session-1", [{"itemId": "mod-spicy"}], creds=_FAKE_CREDS)
            mock_add_line.assert_not_awaited()
            return result

    result = _run(_test())
    assert result["success"] is False
    assert len(result["failedItems"]) == 1
    assert "ambiguous" in result["failedItems"][0]["reason"].lower()
    assert result["addedItems"] == []


def test_unknown_item_fails():
    async def _test():
        with (
            patch("src.chatbot.tools.cache_get", new_callable=AsyncMock, return_value="order-1"),
            patch("src.chatbot.tools.cache_set", new_callable=AsyncMock),
            patch("src.chatbot.tools._menu_items_cached_or_fresh", new_callable=AsyncMock, return_value=_FAKE_MENU),
            patch("src.chatbot.tools.add_clover_line_item", new_callable=AsyncMock) as mock_add_line,
            patch("src.chatbot.tools.add_clover_modification", new_callable=AsyncMock),
            patch("src.chatbot.tools.fetch_clover_order", new_callable=AsyncMock, return_value={"total": 0}),
        ):
            result = await addItemsToOrder("session-1", [{"itemId": "UNKNOWN-ID"}], creds=_FAKE_CREDS)
            mock_add_line.assert_not_awaited()
            return result

    result = _run(_test())
    assert result["success"] is False
    assert len(result["failedItems"]) == 1
    assert "not found" in result["failedItems"][0]["reason"].lower()
    assert result["addedItems"] == []


def test_missing_required_modifiers_rejects_before_adding_line_item():
    order_response = {"id": "order-1", "total": 0}

    async def _test():
        with (
            patch("src.chatbot.tools.cache_get", new_callable=AsyncMock, return_value="order-1"),
            patch("src.chatbot.tools.cache_set", new_callable=AsyncMock),
            patch("src.chatbot.tools._menu_items_cached_or_fresh", new_callable=AsyncMock, return_value=_FAKE_MENU),
            patch("src.chatbot.tools.add_clover_line_item", new_callable=AsyncMock) as mock_add_line,
            patch("src.chatbot.tools.add_clover_modification", new_callable=AsyncMock) as mock_add_mod,
            patch("src.chatbot.tools.fetch_clover_order", new_callable=AsyncMock, return_value=order_response),
        ):
            result = await addItemsToOrder(
                "session-1",
                [
                    {
                        "itemId": "item-wings",
                        "quantity": 1,
                        "modifiers": ["mod-hot-honey", "mod-buffalo"],
                    }
                ],
                creds=_FAKE_CREDS,
            )
            mock_add_line.assert_not_awaited()
            mock_add_mod.assert_not_awaited()
            return result

    result = _run(_test())
    assert result["success"] is False
    assert result["addedItems"] == []
    assert len(result["failedItems"]) == 1
    failure = result["failedItems"][0]
    assert failure["itemId"] == "item-wings"
    assert failure["missingRequiredModifiers"] == [
        {
            "id": "grp-style",
            "name": "Wing Style",
            "minRequired": 1,
            "maxAllowed": 1,
            "remainingRequired": 1,
            "modifiers": [
                {"id": "mod-flats", "name": "All Flats", "price": 200},
                {"id": "mod-mixed", "name": "Mixed", "price": 0},
                {"id": "mod-drums", "name": "All Drums", "price": 200},
            ],
        }
    ]
    assert result["missingRequiredModifiers"] == [
        {
            "itemId": "item-wings",
            "name": "6 Pc Bone In Wings",
            "groups": failure["missingRequiredModifiers"],
        }
    ]


def test_partially_satisfied_required_group_reports_remaining_count():
    order_response = {"id": "order-1", "total": 0}

    async def _test():
        with (
            patch("src.chatbot.tools.cache_get", new_callable=AsyncMock, return_value="order-1"),
            patch("src.chatbot.tools.cache_set", new_callable=AsyncMock),
            patch("src.chatbot.tools._menu_items_cached_or_fresh", new_callable=AsyncMock, return_value=_FAKE_MENU),
            patch("src.chatbot.tools.add_clover_line_item", new_callable=AsyncMock) as mock_add_line,
            patch("src.chatbot.tools.add_clover_modification", new_callable=AsyncMock),
            patch("src.chatbot.tools.fetch_clover_order", new_callable=AsyncMock, return_value=order_response),
        ):
            result = await addItemsToOrder(
                "session-1",
                [
                    {
                        "itemId": "item-wings",
                        "quantity": 1,
                        "modifiers": ["mod-mixed", "mod-hot-honey"],
                    }
                ],
                creds=_FAKE_CREDS,
            )
            mock_add_line.assert_not_awaited()
            return result

    result = _run(_test())
    assert result["success"] is False
    missing = result["failedItems"][0]["missingRequiredModifiers"]
    assert missing == [
        {
            "id": "grp-sauce",
            "name": "Wings Sauce",
            "minRequired": 2,
            "maxAllowed": 3,
            "remainingRequired": 1,
            "modifiers": [
                {"id": "mod-hot-honey", "name": "Hot Honey", "price": 0},
                {"id": "mod-buffalo", "name": "Buffalo", "price": 0},
                {"id": "mod-bbq", "name": "BBQ", "price": 0},
            ],
        }
    ]


def test_modifier_from_another_item_rejects_before_adding_line_item():
    order_response = {"id": "order-1", "total": 0}

    async def _test():
        with (
            patch("src.chatbot.tools.cache_get", new_callable=AsyncMock, return_value="order-1"),
            patch("src.chatbot.tools.cache_set", new_callable=AsyncMock),
            patch("src.chatbot.tools._menu_items_cached_or_fresh", new_callable=AsyncMock, return_value=_FAKE_MENU),
            patch("src.chatbot.tools.add_clover_line_item", new_callable=AsyncMock) as mock_add_line,
            patch("src.chatbot.tools.add_clover_modification", new_callable=AsyncMock) as mock_add_mod,
            patch("src.chatbot.tools.fetch_clover_order", new_callable=AsyncMock, return_value=order_response),
        ):
            result = await addItemsToOrder(
                "session-1",
                [{"itemId": "item-fries", "quantity": 1, "modifiers": ["mod-spicy"]}],
                creds=_FAKE_CREDS,
            )
            mock_add_line.assert_not_awaited()
            mock_add_mod.assert_not_awaited()
            return result

    result = _run(_test())
    assert result["success"] is False
    assert result["addedItems"] == []
    assert result["failedItems"][0]["invalidModifiers"] == ["mod-spicy"]
    assert "invalid modifiers" in result["failedItems"][0]["reason"]


def test_required_modifiers_present_allows_add():
    line_item_response = {"id": "li-wings", "price": 699}
    order_response = {"id": "order-1", "total": 699}

    async def _test():
        with (
            patch("src.chatbot.tools.cache_get", new_callable=AsyncMock, return_value="order-1"),
            patch("src.chatbot.tools.cache_set", new_callable=AsyncMock),
            patch("src.chatbot.tools._menu_items_cached_or_fresh", new_callable=AsyncMock, return_value=_FAKE_MENU),
            patch("src.chatbot.tools.add_clover_line_item", new_callable=AsyncMock, return_value=line_item_response) as mock_add_line,
            patch("src.chatbot.tools.add_clover_modification", new_callable=AsyncMock) as mock_add_mod,
            patch("src.chatbot.tools.fetch_clover_order", new_callable=AsyncMock, return_value=order_response),
        ):
            result = await addItemsToOrder(
                "session-1",
                [
                    {
                        "itemId": "item-wings",
                        "quantity": 1,
                        "modifiers": ["mod-mixed", "mod-hot-honey", "mod-buffalo"],
                    }
                ],
                creds=_FAKE_CREDS,
            )
            mock_add_line.assert_awaited_once()
            assert mock_add_mod.await_count == 3
            return result

    result = _run(_test())
    assert result["success"] is True
    assert result["failedItems"] == []
    assert result["missingRequiredModifiers"] == []
    assert result["addedItems"][0]["modifiersApplied"] == [
        "mod-mixed",
        "mod-hot-honey",
        "mod-buffalo",
    ]


def test_partial_success():
    line_item_response = {"id": "li-abc", "price": 899}
    order_response = {"id": "order-1", "total": 899}

    async def _test():
        with (
            patch("src.chatbot.tools.cache_get", new_callable=AsyncMock, return_value="order-1"),
            patch("src.chatbot.tools.cache_set", new_callable=AsyncMock),
            patch("src.chatbot.tools._menu_items_cached_or_fresh", new_callable=AsyncMock, return_value=_FAKE_MENU),
            patch("src.chatbot.tools.add_clover_line_item", new_callable=AsyncMock, return_value=line_item_response),
            patch("src.chatbot.tools.add_clover_modification", new_callable=AsyncMock),
            patch("src.chatbot.tools.fetch_clover_order", new_callable=AsyncMock, return_value=order_response),
        ):
            result = await addItemsToOrder(
                "session-1",
                [{"itemId": "item-chicken"}, {"itemId": "UNKNOWN-ID"}],
                creds=_FAKE_CREDS,
            )
            return result

    result = _run(_test())
    assert result["success"] is False
    assert len(result["addedItems"]) == 1
    assert result["addedItems"][0]["itemId"] == "item-chicken"
    assert len(result["failedItems"]) == 1
    assert result["failedItems"][0]["itemId"] == "UNKNOWN-ID"


def test_no_items_returns_success():
    async def _test():
        with (
            patch("src.chatbot.tools.cache_get", new_callable=AsyncMock, return_value="order-1"),
            patch("src.chatbot.tools.cache_set", new_callable=AsyncMock),
            patch("src.chatbot.tools._menu_items_cached_or_fresh", new_callable=AsyncMock),
            patch("src.chatbot.tools.add_clover_line_item", new_callable=AsyncMock) as mock_add_line,
            patch("src.chatbot.tools.add_clover_modification", new_callable=AsyncMock),
            patch("src.chatbot.tools.fetch_clover_order", new_callable=AsyncMock),
        ):
            result = await addItemsToOrder("session-1", None, creds=_FAKE_CREDS)
            mock_add_line.assert_not_awaited()
            return result

    result = _run(_test())
    assert result["success"] is True
    assert result["addedItems"] == []
    assert result["failedItems"] == []
    assert result["updatedOrderTotal"] == 0
