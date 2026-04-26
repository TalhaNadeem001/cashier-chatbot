# Post-Confirmation Conversational Flow

## Overview
After an order is confirmed (`session_status == "confirmed"`), customers can ask informational questions (order details, menu, restaurant, pickup time) and the bot answers directly. Any request (modification, cancellation, escalation) is immediately routed to a human without going through the ExecutionAgent.

## Key Files
- `src/chatbot/orchestrator.py` — routing guard in the execution loop; `_INFORMATIONAL_INTENTS` set; escalation type enum
- `src/chatbot/promptsv2.py` — `CONFIRMED ORDER RULE` in `DEFAULT_EXECUTION_AGENT_SYSTEM_PROMPT`
- `src/chatbot/tools.py` — `humanInterventionNeeded` docstring (new `post_confirm_request` type)

## How It Works

### Orchestrator routing (orchestrator.py ~line 514)
Inside the execution loop, after `is_order_confirmed` is determined, each pending queue entry is checked:
- If `is_order_confirmed` is `True` **and** the intent is NOT in `_INFORMATIONAL_INTENTS` → call `humanInterventionNeeded(escalation_type="post_confirm_request")` directly, append "Let me check on that for you.", mark entry done, `continue` (skip ExecutionAgent entirely).
- Otherwise → pass to ExecutionAgent as normal.

### Informational intent set (`_INFORMATIONAL_INTENTS`)
```
order_question, menu_question, restaurant_question, pickuptime_question, identity_question, greeting
```
`greeting` is included so a stray post-confirmation greeting doesn't trigger an unnecessary escalation.

### ExecutionAgent prompt (`CONFIRMED ORDER RULE`)
When `is_order_confirmed` is True and an informational intent reaches the ExecutionAgent:
- Answer using the appropriate read-only tool (same tool rules as normal flow).
- Must not call any mutation tool.

For any request that somehow reaches the agent: call `humanInterventionNeeded(escalation_type="post_confirm_request")` and reply "Let me check on that for you." (safety net — the orchestrator should catch these first).

### New escalation type
`"post_confirm_request"` distinguishes a proactively routed post-confirmation request from `"made_changes_to_order"` (which fires from the mutation tool guard). Both result in human escalation but the external handler can distinguish them.

## Gotchas / Decisions
- The orchestrator guard fires **before** the ExecutionAgent is called, so mutation tools never see post-confirmation requests. The existing mutation tool guards remain as a second line of defence.
- `greeting` was added to `_INFORMATIONAL_INTENTS` beyond what the plan listed, to avoid escalating a stray post-confirmation greeting to a human.
- `only_informational_queued` (used in stage transition logic) is also affected by the expanded set — `identity_question` and `greeting` now correctly suppress the "Is there anything else?" prompt in the normal pre-confirmation flow as well.

## 2026-04-26 - Initial implementation
Added post-confirmation routing branch, updated `CONFIRMED ORDER RULE` in ExecutionAgent prompt, added `post_confirm_request` escalation type.
