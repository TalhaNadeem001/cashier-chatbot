# Chatbot Tools

## Overview
Agent tool functions called directly by the AI agent. All in `src/chatbot/tools.py`. All return plain `dict` (no Pydantic).

## Key Files
- `src/chatbot/tools.py` — all tool implementations
- `src/chatbot/orchestrator.py` — JSON schema + tool descriptors passed to the LLM
- `src/chatbot/utils.py` — shared helpers (`_extract_line_item_modification_records`, `_normalize_order_line_items`, etc.)

## removeItemFromOrder

### How It Works
Clover stores each ordered unit as a **separate line item** with the same name. So "3x Chicken Sando" = three distinct line items each named "Chicken Sando".

Target resolution priority:
1. `target["orderPosition"]` → deletes that specific 1-indexed line item; `removedCount=1`, `lineItemId=<id>`
2. `target["itemName"]` only → fuzzy-matches name, then **deletes ALL** line items sharing that best-matched name; `removedCount=N`, `lineItemId=None`
3. `target["itemName"]` + `target["details"]` → fuzzy-matches name first, then scores `details` against each matching item's modifier names via `_extract_line_item_modification_records`; if a modifier scores >= `NOT_FOUND_THRESHOLD` (50), only that specific item is deleted (`removedCount=1`, `lineItemId=<id>`); otherwise falls back to remove-all

### Return Fields Added (2026-04-22)
- `removedCount` (int) — total line items deleted
- `lineItemId` (str | None) — the specific Clover line item id when one specific item was deleted; None for bulk removes

### Orchestrator Schema
`_REMOVE_ITEM_FROM_ORDER_PARAMETERS_JSON_SCHEMA` in `orchestrator.py` (~line 224) includes `target.details` as an optional string with description telling the LLM when to omit vs. include it.

## 2026-04-22 - REMOVE_ITEM quantity disambiguation

**Problem:** When a customer said "remove 2 chicken sandos" with 3 in the order, the execution agent was calling `removeItemFromOrder` (removing all 3) instead of `changeItemQuantity` to reduce the count.

**Fix:** Updated two places:
1. `src/chatbot/promptsv2.py` — `DEFAULT_EXECUTION_AGENT_SYSTEM_PROMPT`, `For REMOVE_ITEM:` section now has a PRE-CHECK block instructing the agent to:
   - If specific quantity mentioned AND `requestedQty < currentQty` → call `changeItemQuantity(target, newQuantity=currentQty - requestedQty)`
   - If specific quantity mentioned AND `requestedQty >= currentQty` → call `removeItemFromOrder(target)`
   - If no specific quantity → call `removeItemFromOrder(target)` directly
2. `src/chatbot/orchestrator.py` — Updated descriptions for both `removeItemFromOrder` and `changeItemQuantity` `GeminiFunctionTool`s to reinforce this routing.

## Gotchas / Decisions
- `details` falls back to remove-all when modifier scoring is below `NOT_FOUND_THRESHOLD`. This is intentional — if the qualifier is too vague, safer to remove all matching items and let the agent tell the customer.
- Individual delete failures in the bulk-remove loop are logged but don't abort the whole operation. Only if `removedCount == 0` at the end is `success=False` returned.
- `LOW_MENU_MATCH_THRESHOLD` (65) gates the initial item name match; `NOT_FOUND_THRESHOLD` (50) gates the modifier/details match.
