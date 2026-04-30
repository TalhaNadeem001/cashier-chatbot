from __future__ import annotations

from typing import Literal


# Intents that go through ExecutionAgent.run_single because they need
# tool-driven cart manipulation, menu lookups, or order math.
EXECUTOR_LANE_INTENTS: frozenset[str] = frozenset({
    "add_item",
    "modify_item",
    "replace_item",
    "remove_item",
    "change_item_number",
    "cancel_order",
    "order_question",
    "menu_question",
    "restaurant_question",
})

# Intents Composer handles directly via its own tools. The executor is not
# invoked for these — the orchestrator builds an ActionOutcome straight from
# the parsed intent.
COMPOSER_LANE_INTENTS: frozenset[str] = frozenset({
    "confirm_order",
    "pickuptime_question",
    "identity_question",
    "greeting",
    "introduce_name",
    "escalation",
    "outside_agent_scope",
})


def route_intent(intent: str) -> Literal["executor", "composer"]:
    """Decide which agent handles an intent.

    Unknown intents default to executor lane on the principle that mutating
    intents are the high-stakes ones and the executor's tool guards (Phase 1)
    will refuse anything inappropriate. A truly unknown intent producing a
    no-op tool reply is recoverable; a truly unknown intent skipping order
    validation is not.
    """
    if intent in EXECUTOR_LANE_INTENTS:
        return "executor"
    if intent in COMPOSER_LANE_INTENTS:
        return "composer"
    return "executor"