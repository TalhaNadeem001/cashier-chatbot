from __future__ import annotations

from typing import Any

from src.chatbot.schema import ActionOutcome, ExecutionAgentSingleResult


def outcome_from_executor_result(
    *, intent: str, result: ExecutionAgentSingleResult, entry_id: str = ""
) -> ActionOutcome:
    """Convert an ExecutionAgentSingleResult into the ActionOutcome shape
    Composer consumes. Preserves the executor's free-text reply as a hint
    (raw_executor_reply) - Composer paraphrases, never quotes verbatim.
    """
    return ActionOutcome(
        intent=intent,
        success=result.success,
        facts={
            "entry_id": entry_id,
            "actions_executed": list(result.actions_executed),
            "order_updated": result.order_updated,
        },
        needs_clarification=bool(result.clarification_questions),
        clarification_questions=list(result.clarification_questions),
        raw_executor_reply=result.reply,
        escalated=getattr(result, "escalated", False),
    )


def outcome_from_parsed_intent(parsed_item: dict[str, Any]) -> ActionOutcome:
    """Build an ActionOutcome for composer-lane intents that don't go through
    the executor (greeting, identity_question, pickuptime_question, etc.).

    parsed_item is the dict produced by ParsingAgent (alias-keyed: "Intent",
    "Confidence_level", "Request_items", "Request_details").
    """
    return ActionOutcome(
        intent=str(parsed_item.get("Intent", "")),
        success=True,
        facts={"parsed_item": parsed_item},
    )
