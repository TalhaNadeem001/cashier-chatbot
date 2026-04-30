import asyncio
from unittest.mock import AsyncMock, patch

from src.chatbot.guarded_tools import (
    confirmOrder_guarded,
    humanInterventionNeeded_idempotent,
)


# ─── confirmOrder_guarded ────────────────────────────────────────────────────


def test_confirm_order_guarded_already_confirmed():
    """Returns already_confirmed when cache status == 'confirmed'; confirmOrder not called."""
    mock_confirm = AsyncMock()
    mock_profile = AsyncMock()
    with (
        patch("src.chatbot.guarded_tools.cache_get", new=AsyncMock(return_value="confirmed")),
        patch("src.chatbot.guarded_tools.confirmOrder", new=mock_confirm),
        patch("src.chatbot.guarded_tools.getHumanProfile", new=mock_profile),
    ):
        result = asyncio.run(
            confirmOrder_guarded(
                session_id="sess-1",
                creds=None,
                phone_number="+1555",
                firebase_uid="uid-1",
            )
        )

    assert result["success"] is False
    assert result["error"] == "already_confirmed"
    mock_confirm.assert_not_called()
    mock_profile.assert_not_called()


def test_confirm_order_guarded_name_gate_unsatisfied():
    """Returns name_gate_unsatisfied when profile has no name and session flag is unset."""
    # First call → status key (None), second call → name_provided key (None)
    cache_values = [None, None]

    async def fake_cache_get(key):
        return cache_values.pop(0)

    mock_confirm = AsyncMock()
    with (
        patch("src.chatbot.guarded_tools.cache_get", new=fake_cache_get),
        patch("src.chatbot.guarded_tools.getHumanProfile", new=AsyncMock(return_value={})),
        patch("src.chatbot.guarded_tools.confirmOrder", new=mock_confirm),
    ):
        result = asyncio.run(
            confirmOrder_guarded(
                session_id="sess-1",
                creds=None,
                phone_number="+1555",
                firebase_uid="uid-1",
            )
        )

    assert result["success"] is False
    assert result["error"] == "name_gate_unsatisfied"
    mock_confirm.assert_not_called()


def test_confirm_order_guarded_calls_confirm_when_profile_has_name():
    """Calls underlying confirmOrder and returns its result when profile has a name."""
    cache_values = [None]  # status key → None; name_provided key never reached

    async def fake_cache_get(key):
        return cache_values.pop(0) if cache_values else None

    confirm_result = {"success": True, "orderId": "order-1"}
    mock_confirm = AsyncMock(return_value=confirm_result)
    with (
        patch("src.chatbot.guarded_tools.cache_get", new=fake_cache_get),
        patch("src.chatbot.guarded_tools.getHumanProfile", new=AsyncMock(return_value={"name": "Sarah"})),
        patch("src.chatbot.guarded_tools.confirmOrder", new=mock_confirm),
    ):
        result = asyncio.run(
            confirmOrder_guarded(
                session_id="sess-1",
                creds={"token": "t"},
                phone_number="+1555",
                firebase_uid="uid-1",
            )
        )

    assert result == confirm_result
    mock_confirm.assert_called_once_with(session_id="sess-1", creds={"token": "t"})


def test_confirm_order_guarded_calls_confirm_when_session_flag_set():
    """Calls underlying confirmOrder when session flag is '1' even with no profile name."""
    # First call → status key (None), second call → name_provided key ("1")
    cache_values = [None, "1"]

    async def fake_cache_get(key):
        return cache_values.pop(0)

    confirm_result = {"success": True, "orderId": "order-2"}
    mock_confirm = AsyncMock(return_value=confirm_result)
    with (
        patch("src.chatbot.guarded_tools.cache_get", new=fake_cache_get),
        patch("src.chatbot.guarded_tools.getHumanProfile", new=AsyncMock(return_value={})),
        patch("src.chatbot.guarded_tools.confirmOrder", new=mock_confirm),
    ):
        result = asyncio.run(
            confirmOrder_guarded(
                session_id="sess-1",
                creds=None,
                phone_number="+1555",
                firebase_uid="uid-1",
            )
        )

    assert result == confirm_result
    mock_confirm.assert_called_once()


def test_confirm_order_guarded_fail_open_on_cache_error():
    """Fail-open: when cache_get raises, proceeds to call confirmOrder without raising."""
    async def fake_cache_get_raises(key):
        raise RuntimeError("redis down")

    confirm_result = {"success": True, "orderId": "order-3"}
    mock_confirm = AsyncMock(return_value=confirm_result)
    with (
        patch("src.chatbot.guarded_tools.cache_get", new=fake_cache_get_raises),
        patch("src.chatbot.guarded_tools.getHumanProfile", new=AsyncMock(return_value={"name": "Ali"})),
        patch("src.chatbot.guarded_tools.confirmOrder", new=mock_confirm),
    ):
        result = asyncio.run(
            confirmOrder_guarded(
                session_id="sess-1",
                creds=None,
                phone_number="+1555",
                firebase_uid="uid-1",
            )
        )

    assert result == confirm_result
    mock_confirm.assert_called_once()


# ─── humanInterventionNeeded_idempotent ──────────────────────────────────────


def test_human_intervention_idempotent_deduplicates_same_type():
    """Second call with same (session, escalation_type) within TTL returns already_escalated."""
    call_count = 0

    async def fake_cache_get(key):
        nonlocal call_count
        call_count += 1
        return "1" if call_count > 1 else None

    hi_result = {"success": True, "escalated": True}
    mock_hi = AsyncMock(return_value=hi_result)
    with (
        patch("src.chatbot.guarded_tools.cache_get", new=fake_cache_get),
        patch("src.chatbot.guarded_tools.cache_set", new=AsyncMock()),
        patch("src.chatbot.guarded_tools.humanInterventionNeeded", new=mock_hi),
    ):
        first = asyncio.run(
            humanInterventionNeeded_idempotent(
                session_id="sess-1",
                escalation_type="post_confirm_request",
                merchant_id="merch-1",
            )
        )
        second = asyncio.run(
            humanInterventionNeeded_idempotent(
                session_id="sess-1",
                escalation_type="post_confirm_request",
                merchant_id="merch-1",
            )
        )

    assert first == hi_result
    assert second == {"success": True, "already_escalated": True}
    mock_hi.assert_called_once()


def test_human_intervention_idempotent_no_cross_type_dedup():
    """Different escalation_types for the same session both invoke the underlying tool."""
    mock_hi = AsyncMock(return_value={"success": True})
    with (
        patch("src.chatbot.guarded_tools.cache_get", new=AsyncMock(return_value=None)),
        patch("src.chatbot.guarded_tools.cache_set", new=AsyncMock()),
        patch("src.chatbot.guarded_tools.humanInterventionNeeded", new=mock_hi),
    ):
        asyncio.run(
            humanInterventionNeeded_idempotent(
                session_id="sess-1",
                escalation_type="post_confirm_request",
                merchant_id="merch-1",
            )
        )
        asyncio.run(
            humanInterventionNeeded_idempotent(
                session_id="sess-1",
                escalation_type="off_topic_question",
                merchant_id="merch-1",
            )
        )

    assert mock_hi.call_count == 2


def test_human_intervention_idempotent_fail_open_on_cache_get_error():
    """When cache_get raises, still invokes the underlying tool (fail-open)."""
    async def fake_cache_get_raises(key):
        raise RuntimeError("redis down")

    mock_hi = AsyncMock(return_value={"success": True})
    with (
        patch("src.chatbot.guarded_tools.cache_get", new=fake_cache_get_raises),
        patch("src.chatbot.guarded_tools.cache_set", new=AsyncMock()),
        patch("src.chatbot.guarded_tools.humanInterventionNeeded", new=mock_hi),
    ):
        result = asyncio.run(
            humanInterventionNeeded_idempotent(
                session_id="sess-1",
                escalation_type="post_confirm_request",
                merchant_id="merch-1",
            )
        )

    assert result == {"success": True}
    mock_hi.assert_called_once()


def test_human_intervention_idempotent_returns_result_when_cache_set_raises():
    """When cache_set raises after a successful call, result is still returned."""
    async def fake_cache_set_raises(key, value, ttl=None):
        raise RuntimeError("redis down")

    hi_result = {"success": True, "escalated": True}
    mock_hi = AsyncMock(return_value=hi_result)
    with (
        patch("src.chatbot.guarded_tools.cache_get", new=AsyncMock(return_value=None)),
        patch("src.chatbot.guarded_tools.cache_set", new=fake_cache_set_raises),
        patch("src.chatbot.guarded_tools.humanInterventionNeeded", new=mock_hi),
    ):
        result = asyncio.run(
            humanInterventionNeeded_idempotent(
                session_id="sess-1",
                escalation_type="post_confirm_request",
                merchant_id="merch-1",
            )
        )

    assert result == hi_result
    mock_hi.assert_called_once()
