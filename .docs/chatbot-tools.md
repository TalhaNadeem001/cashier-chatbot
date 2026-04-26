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

## 2026-04-22 - Menu Numeric Variant Merging

### Overview
Items like "Wings 6", "Wings 12", "Wings 24" all normalize to the same `by_name` key `"wings"`. Previously only the first variant was ever retrieved. Now they are merged into one item.

### How It Works (`src/chatbot/utils.py`)
- **`_merge_numeric_name_variants(norm_name, items)`** — new helper above `_normalize_item_name`. If every item in the group has a numeric token in its original name, collapses them into a single item with a synthetic `"Quantity"` required modifier group (one option per variant). Returns the list unchanged if any item lacks a number.
- **`_normalize_menu`** — stores `item["_original_name"]` before overwriting `item["name"]`, then post-processes `by_name` to call `_merge_numeric_name_variants` for any key with >1 item, then strips `_original_name` from all `by_id` entries.
- `by_id` is **unchanged** — each original variant (e.g. "Wings 6") still lives there by its real ID so `addItemsToOrder` can look it up.

### Agent Flow
1. `findClosestMenuItems("wings")` returns the merged item with `merged: True` and a `"Quantity"` modifier group.
2. Agent prompts user to choose a quantity.
3. User picks "12" → agent passes that modifier option's `id` (the original "Wings 12" item ID) as `itemId` to `addItemsToOrder`.

### Gotchas
- Non-numeric multi-variant items (e.g. two items that both normalize to the same name without numbers) are NOT merged — list stays as-is.
- Quantity modifier `id` fields are the original item IDs, not synthetic IDs, so `addItemsToOrder` needs no changes.

## 2026-04-22 - Skip "Wings" Placeholder Item

### Overview
Clover has a placeholder item with raw name exactly `"Wings"`, price 0, and no category. After normalization it collides with real bone-in wing items (e.g. "6 PC Wings" → `"wings"`). An explicit exclusion prevents it from ever entering the menu index.

### Fix (`src/chatbot/utils.py` — `_normalize_item_name`)
Added a check before the existing normalization logic:
```python
if name.strip().lower() == "wings":
    return None
```
This returns `None` (skip) only when the raw name is **exactly** "wings" (any casing). It does not affect:
- "Boneless Wings" → normalizes to `"boneless wings"` ✓
- "6 PC Wings" / "10 PC Wings" → raw name is not exactly "wings" ✓

## 2026-04-22 - Provider-Agnostic LLM Routing

### Rule
All LLM calls must go through `src/chatbot/llm_client.py`. Never import from `gemini_client` or `openai_client` directly in feature code.

### How It Works
- `src/config.py` sets `AI_MODE` (default `"chatgpt"`)
- `llm_client.py` routes `generate_text` / `generate_model` to OpenAI or Gemini based on `AI_MODE`
- Switching providers requires only changing `AI_MODE` in config — no code changes needed

### Files Updated
Swapped direct `gemini_client` imports to `llm_client` in:
- `src/chatbot/visibility/ai_client.py`
- `src/chatbot/infrastructure/summarizer.py`
- `src/chatbot/tools.py`
- `src/chatbot/clarification/ai_resolver.py`
- `src/chatbot/cart/ai_client.py`

## 2026-04-23 - Redis Cache for Clover Order Data

### Overview
Introduced a read-through Redis cache for full Clover order responses to eliminate redundant Clover API calls within a single user turn.

### Key Changes
- `src/chatbot/constants.py` — added `_SESSION_ORDER_DATA_REDIS_TTL_SECONDS = 3 * 60 * 60`
- `src/chatbot/utils.py` — added `_session_order_data_redis_key(session_id)` → `order:data:{session_id}`
- `src/chatbot/tools.py` — added two private helpers:
  - `_get_order_data(session_id, creds, *, force_refresh=False)` — read-through cache; calls `get_order_id_for_session` + `fetch_clover_order` on miss, stores result in Redis
  - `_invalidate_order_data_cache(session_id)` — deletes the cache key
- `src/chatbot/router.py` — `clear_session` now also deletes `order:data:{session_id}`

### Which tools use it
- **Read-only** (`getOrderLineItems`, `calcOrderPrice`): call `_get_order_data(session_id, creds)` — uses cache on hit
- **Mutation tools** (`addItemsToOrder`, `replaceItemInOrder`, `removeItemFromOrder`, `changeItemQuantity`, `updateItemInOrder`): pre-reads use cache; post-mutation fetch uses `force_refresh=True` to re-populate cache with fresh data
- **`cancelOrder`**: calls `_invalidate_order_data_cache` alongside existing order-state and order-id deletes

### Gotchas
- `calcOrderPrice` previously used `expand=["lineItems", "lineItems.modifications", "discounts"]`; now uses the standard cached response. Pricing breakdown still works since `_pricing_breakdown_from_order` uses the Clover `total` and line item prices which are always present in the default response.
- `_get_order_data` forward-references `get_order_id_for_session` (defined later in the same file at ~line 3400). This is fine in Python since both are module-level functions resolved at call time.
- `confirmOrder`'s `fetch_clover_order` calls were intentionally left unchanged — confirmation is a mutation that needs authoritative data and doesn't benefit from caching.

## 2026-04-26 - saveHumanName tool

### Overview
Persists the customer's name to Firestore under `Users/{firebase_uid}/Customers/{phone_number}`.

### How It Works
- Triggered whenever the agent detects the customer mentioned their name (GREETING or any intent).
- Looks up existing doc; skips the write if the name is already identical (`already_saved=True`).
- Uses `merge=True` so other fields on the Customers doc are not overwritten.
- Fails silently (returns `success=False`) when `phone_number` is None or Firebase is uninitialised — agent continues normally in both cases.

### Data Flow
`ChatbotV2MessageRequest.phone_number` → `buffer.py merged_request` → `ExecutionAgentContext.phone_number` → `PreparedExecutionContext.phone_number` → `ExecutionToolRuntime context.phone_number` → `_save_human_name_tool` → `saveHumanName`

### Key Files
- `src/chatbot/tools.py` — `saveHumanName` implementation (~line 3853)
- `src/chatbot/orchestrator.py` — `_SAVE_HUMAN_NAME_PARAMETERS_JSON_SCHEMA`, `_save_human_name_tool`, registered in `tools_list` (no `_guard` wrapper — safe post-confirmation)
- `src/chatbot/schema.py` — `phone_number` added to `ExecutionAgentContext` and `PreparedExecutionContext`
- `src/chatbot/buffer.py` — `phone_number` forwarded in `merged_request`
- `src/chatbot/promptsv2.py` — GREETING section updated to instruct agent to call `saveHumanName` when name is detected

## 2026-04-26 - Add `getHumanProfile` (read-side counterpart to `saveHumanName`)

`getHumanProfile(phone_number, firebase_uid) -> dict` added to `src/chatbot/tools.py` (~line 3913).

- Reads `Users/{firebase_uid}/Customers/{phone_number}` from Firestore (same path as `saveHumanName`).
- Returns `{ success, name, error }`.
- **Not** registered as an agent tool — called directly by Python orchestrator code.
- Returns `success=False` immediately when `phone_number` is `None` or Firebase is uninitialised.

## 2026-04-26 - Fix note erasure when adding modifiers via `updateItemInOrder`

**Bug:** When a customer added an item with a free-text note (e.g. "add lettuce no smash sauce") and then modified it with a modifier ("add beef bacon to it"), the LLM was including `"note": null` in the `updates` dict, silently clearing the note.

**Root cause:** The `note` field in `_UPDATE_ITEM_IN_ORDER_PARAMETERS_JSON_SCHEMA` had no description, so the LLM filled it in as `null` when it wasn't needed. The prompt also had no instruction to omit `note` when only changing modifiers.

**Fix (two changes):**
1. `src/chatbot/orchestrator.py` ~line 298 — Added `description` to the `note` field in `_UPDATE_ITEM_IN_ORDER_PARAMETERS_JSON_SCHEMA` instructing the LLM to OMIT `note` entirely when only modifying modifiers.
2. `src/chatbot/promptsv2.py` ~line 749 — Added IMPORTANT note-preservation instruction in the `Normal MODIFY_ITEM flow` block.

**Design:** `updateItemInOrder` uses a sentinel (`"note" in updates`) — the fix is correct; the implementation didn't need to change.

## 2026-04-26 - introduce_name intent for saveHumanName

**Problem:** Name detection was a hidden side-effect in the execution agent prompt — the agent was told to call `saveHumanName` for "GREETING or any intent where the customer mentions their name." No parser signal meant name mentions embedded in `add_item` or other intents could be missed.

**Fix (four changes):**
1. `src/chatbot/schema.py` — Added `INTRODUCE_NAME = "introduce_name"` to `ParsedRequestIntent` enum.
2. `src/chatbot/promptsv2.py` `intent_labels_prompt` — Added `introduce_name` label with instructions: store the name in `Request_items.name`, `quantity=0`, `details=""`. Notes that it can co-occur with any other intent as a separate object.
3. `src/chatbot/promptsv2.py` `few_shot_examples_prompt` — Added Examples 12–14 covering: greeting+name, name+add_item, name-only.
4. `src/chatbot/orchestrator.py` `_INFORMATIONAL_INTENTS` — Added `"introduce_name"` so name-only messages don't trigger the "Is there anything else?" prompt.
5. `src/chatbot/promptsv2.py` execution agent prompt — Added explicit `For INTRODUCE_NAME:` block routing to `saveHumanName(name=Request_items.name)`. Kept the fallback for name mentions without the explicit intent.

**Behaviour:** When mixed with action intents (e.g., `add_item`), `introduce_name` is NOT stripped from the queue (unlike `greeting`), so `saveHumanName` always fires.

## 2026-04-26 - Name gate before order confirmation

**Feature:** Before confirming the order, the orchestrator checks if a customer name is on record. If not, it asks for the name, saves it, then confirms.

**New tool — `getHumanProfile` (`tools.py`):**
Reads `Users/{firebase_uid}/Customers/{phone_number}` from Firestore. Returns `{success, name, phone_number, error}`. Orchestrator-only — not exposed to the execution agent.

**New stage — `awaiting_name_before_confirm`:**
Inserted between `awaiting_order_confirm` (customer said "yes, confirm") and actual confirmation.

**Orchestrator flow changes (`orchestrator.py`):**

Two new branches added after the `introduce_name` inline handler, before queue building:

1. **`awaiting_name_before_confirm` handler:** If `introduce_name` is in `parsed_data` (name was given and already saved by the inline handler), confirm the order directly — set session status `"confirmed"`, stage → `"ordering"`, reply with the standard confirmation text. If no name, re-ask and stay in the same stage.

2. **Name gate:** When `stage == "awaiting_order_confirm"` and `only_confirm` is True and `phone_number` is available — call `getHumanProfile`. If no name on record, ask `"What name should I put the order under?"`, set stage to `"awaiting_name_before_confirm"`, return early. If name exists, fall through to normal queue processing.

**Gotchas:**
- Gate is skipped when `phone_number` is None (web/test clients) — can't store a name without a phone number, so confirmation proceeds normally.
- Inline confirm copies the exact reply text from the execution agent prompt: `"Thank you. Your order has been received. Allow me a moment to set your pickup time."` — keep these in sync if the prompt changes.
