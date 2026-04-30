from __future__ import annotations

from typing import Any

from src.cache import cache_get, cache_set
from src.chatbot.tools import (
    confirmOrder,
    getHumanProfile,
    humanInterventionNeeded,
)
from src.chatbot.utils import (
    _session_name_provided_redis_key,
    _session_status_redis_key,
)

# Long enough to outlast a slow turn (Gemini 503 retries ~20s; 429 retries ~30s).
_HUMAN_INTERVENTION_DEDUP_TTL_SECONDS = 120


async def confirmOrder_guarded(
    *,
    session_id: str,
    creds: dict | None,
    phone_number: str | None,
    firebase_uid: str,
) -> dict[str, Any]:
    """confirmOrder with already-confirmed and name-gate enforcement.

    Returns the same shape as confirmOrder on success. On gate refusal returns
    a dict with success=False, an error code, and an agentInstruction string
    describing what the caller should do (used by the Composer in later phases).

    Gates (checked in order):
      1. already_confirmed — Redis status key == "confirmed"; underlying confirmOrder
         is not called.
      2. name_gate_unsatisfied — profile has no name AND session name-provided flag
         is not "1"; underlying confirmOrder is not called.

    Cache errors are caught and fail-open: better to risk a duplicate-confirm refusal
    by the underlying tool than to wedge confirmation when Redis blips.
    getHumanProfile errors propagate unchanged.
    """
    # Gate 1: already confirmed.
    try:
        status = await cache_get(_session_status_redis_key(session_id))
    except Exception:
        status = None
    if status == "confirmed":
        return {
            "success": False,
            "error": "already_confirmed",
            "agentInstruction": (
                "The order is already confirmed; do not attempt to confirm it again."
            ),
        }

    # Gate 2: name gate.
    profile = await getHumanProfile(
        phone_number=phone_number, firebase_uid=firebase_uid
    )
    try:
        name_provided_this_session = (
            await cache_get(_session_name_provided_redis_key(session_id)) == "1"
        )
    except Exception:
        name_provided_this_session = False
    if not profile.get("name") and not name_provided_this_session:
        return {
            "success": False,
            "error": "name_gate_unsatisfied",
            "agentInstruction": (
                "Ask the customer for a name before confirming the order."
            ),
        }

    return await confirmOrder(session_id=session_id, creds=creds)


async def humanInterventionNeeded_idempotent(
    *,
    session_id: str,
    escalation_type: str,
    merchant_id: str,
) -> dict[str, Any]:
    """humanInterventionNeeded with per-(session, escalation_type) dedup.

    Within _HUMAN_INTERVENTION_DEDUP_TTL_SECONDS, calling this twice with the
    same (session_id, escalation_type) results in exactly one underlying call.
    Different escalation_types for the same session are tracked separately.

    Cache errors fail-open: dropping an escalation is worse than firing it twice.
    """
    turn_key = f"hi_called:{session_id}:{escalation_type}"
    try:
        already = await cache_get(turn_key) == "1"
    except Exception:
        already = False
    if already:
        return {"success": True, "already_escalated": True}

    result = await humanInterventionNeeded(
        session_id=session_id,
        escalation_type=escalation_type,
        merchant_id=merchant_id,
    )
    try:
        await cache_set(turn_key, "1", ttl=_HUMAN_INTERVENTION_DEDUP_TTL_SECONDS)
    except Exception:
        pass
    return result
