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

2. **Name gate:** When `stage == "awaiting_order_confirm"` and `only_confirm` is True and `phone_number` is available — call `getHumanProfile`.
   - No name on record → ask `"What name should I put the order under?"`, set stage to `"awaiting_name_before_confirm"`, return early.
   - Name exists → ask `"Just to confirm, your order will be placed under [name] — is that correct?"`, set stage to `"awaiting_name_confirm"`, return early.

3. **`awaiting_name_confirm` handler:** Inserted after `awaiting_name_before_confirm` handler. Customer is responding to the name-confirmation question.
   - `introduce_name` or `confirm_order` in `parsed_data` → confirm order directly (set status `"confirmed"`, stage → `"ordering"`, reply with standard text).
   - Neither → ask `"What name should I put the order under?"`, set stage to `"awaiting_name_before_confirm"`.
   - Note: the inline `introduce_name` handler already calls `saveHumanName` before this branch fires, so the new name is already persisted when confirming.

**Gotchas:**
- Gate is skipped when `phone_number` is None (web/test clients) — can't store a name without a phone number, so confirmation proceeds normally.
- Inline confirm copies the exact reply text from the execution agent prompt: `"Thank you. Your order has been received. Allow me a moment to set your pickup time."` — keep these in sync if the prompt changes.

## 2026-04-26 - MODIFY_ITEM order-side item resolution + validateModifications AI upgrade

### Problem
The `MODIFY_ITEM` flow called `validateRequestedItem(itemName)` first, which searched the **entire menu** to resolve the target item. This could match items not in the order and was redundant — item identity should come from the order, not the menu.

Additionally, `validateModifications` used `_match_requested_modifier` (deterministic fuzzy keyword match), while `validateRequestedItem` used `resolve_modifiers_for_item` (AI LLM resolver). This meant swapping to `validateModifications` would have weakened modifier resolution.

### Changes

**`src/chatbot/tools.py` — `validateModifications`:**
- Replaced the `_match_requested_modifier` loop with a call to `resolve_modifiers_for_item` (same AI resolver pattern as `validateRequestedItem`).
- Added `asNote` to the return dict — preferences the AI resolved as free-text notes rather than Clover modifier IDs.
- Removed now-unused `_match_requested_modifier` helper and `MODS_CONFIRMED_THRESHOLD` import.

**`src/chatbot/promptsv2.py` — Normal MODIFY_ITEM flow:**
- Replaced the single `validateRequestedItem` call with a 4-step sequence:
  1. `getOrderLineItems()` — confirm item is in the order; get exact name + `lineItemId`.
  2. `findClosestMenuItems(exact_order_item_name)` — resolve `itemId` + `merchantId` by exact name.
  3. `validateModifications(itemId, merchantId, requestedModifications)` — AI modifier resolution.
  4. `updateItemInOrder(target={lineItemId}, updates={addModifiers/removeModifiers})`.

**`src/chatbot/promptsv2.py` — LOW CONFIDENCE BEHAVIOR:**
- Scoped `validateRequestedItem` to `add_item` only.
- For `modify_item`, `remove_item`, and `replace_item` (old item), low confidence now starts with `getOrderLineItems()` instead of a menu search.

### Gotchas
- `getOrderLineItems` does not return the Clover menu `itemId` — only `lineItemId`. Step 2 (`findClosestMenuItems`) is still needed to get the menu UUID for `validateModifications`. If `getOrderLineItems` is ever updated to expose `itemId` per line item, step 2 can be dropped.
- The `MODIFY_ITEM` empty-order fallback (redirects to ADD_ITEM flow) still uses `validateRequestedItem` — this is correct since the item doesn't exist in the order yet.

## 2026-04-27 - Wrong fuzzy match downgrade in validateRequestedItem

**Problem:** "fish sandwich" fuzzy-matched to "Sando & Fries" (score 72 ≥ `CONFIRMED_THRESHOLD` 70, auto-confirmed as exact). "Fish Battered Cod" scored only 54. Both item-name words "fish" and "sandwich" ended up as `invalid` modifiers — entirely orphaned — but the match stood.

**Fix (`src/chatbot/tools.py` — `validateRequestedItem`):**
After the modifier resolver runs, check if every content word of `itemName` is orphaned (in `leftover_words` AND in `truly_invalid`). If so, downgrade `matchConfidence` from `exact` to `close` and restore the candidates list (already built by `_build_candidates` before the auto-confirm branch, but cleared by the `include_candidate_details` guard). Returns early with `{**base, **_null_downstream}` so the agent presents candidates to the customer.

**Condition:** `orphaned_set == itemName_content_words` — must be complete, not partial. "spicy chicken sando" → "Chicken Sando" does not trigger because "chicken" and "sando" matched the item name.

## 2026-04-27 - escalation vs order_question parser disambiguation

**Problem:** "my total is wrong" was classified as `order_question` (neutral info request) instead of `escalation` (complaint/dispute). The execution agent just called `calcOrderPrice` and reported the total rather than escalating.

## 2026-04-27 - MENU_QUESTION modifier query stuck in clarification loop

**Problem:** "What mods can I get for the chicken sando?" was parsed as `menu_question`, but the execution agent had no handler for item-specific modifier queries. It fell back to calling `validateRequestedItem`, which returned `missingRequireChoice: [Heat Level]` and left the entry in `need_clarification`. Every subsequent customer message was absorbed as an "answer" to the pending `menu_question` entry. When all qa pairs were filled the agent described options instead of adding — queue cleared, "yes" was parsed as `confirm_order`, empty cart confirmed.

**Fix (`src/chatbot/promptsv2.py` — execution agent prompt):**
Added a `For MENU_QUESTION (item modifier query)` block: call `findClosestMenuItems(itemName)`, list all modifier groups and options, never call `validateRequestedItem`, never ask for the customer's choice, always resolve done after replying.

## 2026-04-27 - escalation vs order_question parser disambiguation

**Fix (`src/chatbot/promptsv2.py` — `intent_labels_prompt` and `parsing_rules_prompt`):**
- `order_question` description now explicitly says "neutral informational requests — NOT complaints or disputes."
- `escalation` description now explicitly includes price/total disputes: "my total is wrong", "the price is off", "I was overcharged."
- Added a `COMPLAINT vs QUESTION DISTINCTION` parsing rule with concrete examples to reinforce the boundary.
## 2026-04-28 - Firebase conversation and print logging

### Overview
Added Firestore logging so chatbot runtime logs now persist under each merchant's `Users/{original_merchant_id}/logs` collection. This captures:
- every existing `print(...)` call from `orchestrator.py` and `tools.py`
- each incoming customer message
- each final AI reply

### Key Changes
- `src/chatbot/tools.py`
  - Added Firebase log context helpers: `set_firebase_log_context`, `update_firebase_log_context`, `reset_firebase_log_context`.
  - Added `log_firebase_event(...)` for structured log writes with `event_type`, `message`, `merchant_id`, `session_id`, `order_id`, `timestamp`, and `extra`.
  - Overrode module `print(...)` to mirror messages to Firestore (`event_type="print"`) while still printing to stdout.
- `src/chatbot/orchestrator.py`
  - Added orchestrator print mirroring to Firestore with module-local context.
  - In `handle_message`, log incoming customer text (`event_type="user_message"`) and every return-path assistant response (`event_type="ai_reply"`).
  - Set shared tool logging context per request, and update it with `order_id` once current order details are loaded.

### Data Shape (logs collection)
- `event_type` (e.g. `print`, `user_message`, `ai_reply`)
- `message` (raw text content)
- `merchant_id` (original merchant Firebase UID)
- `session_id`
- `order_id`
- `source` (`chatbot`)
- `timestamp` (UTC ISO string)
- `extra` (context like stage/source)

### Gotchas / Decisions
- Print mirroring uses `asyncio.create_task(...)` so log persistence is non-blocking and does not delay chatbot responses.
- Logging is skipped safely when Firebase is unavailable or merchant ID is missing.
- `order_id` may be blank at very early points in a turn (before order details are loaded), then is populated for subsequent logs in the same request.

## 2026-04-28 - Append logs per order

### Overview
Changed Firestore write mode from "one document per event" to "append events into one document per order/session".

### How It Works
- `log_firebase_event(...)` now writes to a deterministic document id:
  - `logs/{order_id}` when `order_id` is available
  - fallback `logs/session:{session_id}` when `order_id` is missing
- Events are appended with Firestore `ArrayUnion` into an `events` array on that document.
- The parent log document keeps top-level metadata (`merchant_id`, `session_id`, `order_id`, `updated_at`, `source`) and merged updates.

### Decision
- This guarantees all events for the same `order_id` append into the same Firestore document instead of creating separate docs.

## 2026-04-28 - VERSION prefix for print logs

### Overview
Updated chatbot print wrappers to prepend the environment `VERSION` value to every print line.

### Format
- Every print now emits:
  - `[ <VERSION> ] [ <original message> ]`
- If `VERSION` is missing, fallback label is `unknown`.

### Files
- `src/chatbot/tools.py` — module print wrapper prefixes every message using `settings.VERSION`.
- `src/chatbot/orchestrator.py` — same prefix behavior for orchestrator-side print wrapper using `settings.VERSION`.

## 2026-04-28 - Full flow + error coverage improvements

### Overview
Expanded log coverage so each request now has explicit lifecycle events and robust error capture.

### Changes
- `src/chatbot/orchestrator.py`
  - Added `flow_start` at the beginning of `handle_message`.
  - Added `flow_end` before every successful return path.
  - Added `flow_error` in `except Exception` so failures are persisted before re-raise.
- `src/chatbot/tools.py`
  - Added `event_id` (UUID) to every event payload so repeated identical messages are not de-duplicated by Firestore `ArrayUnion`.

### VERSION source fix
- `src/config.py` now defines `VERSION: str = "unknown"` so `.env` is loaded into app settings.
- Both print wrappers now use `settings.VERSION` (not `os.getenv`) to ensure the configured `.env` value is always used consistently.

## 2026-04-28 - Order ID only log documents

### Overview
Removed session-based fallback document naming for Firestore logs.

### Behavior
- Logs now write **only** when `order_id` is present.
- Document path is always `Users/{merchant_id}/logs/{order_id}`.
- If `order_id` is empty/missing, log write is skipped.

### File
- `src/chatbot/tools.py` — `log_firebase_event(...)` now requires a non-empty `order_id` and no longer uses `session:{session_id}` fallback doc IDs.

## 2026-04-29 - False clarification on alias-matched items (fish sandwich)

**Problem:** "fish sandwich" matched via alias → "Fish Battered Cod" with `matchConfidence="exact"`, but the system still asked for clarification. Root cause: `validateRequestedItem` diffs `itemName` ("fish sandwich") against the matched item name ("Fish Battered Cod") to extract leftover modifier tokens. "sandwich" is not in `{"fish","battered","cod"}`, so it becomes `leftover_words=["sandwich"]` → `unified_details="sandwich"` → AI resolver marks it `unresolvable` → `invalid=["sandwich"]` → `allValid=False`. The downgrade guard didn't fire because "fish" appears in both names, so only one of two content words was orphaned (`orphaned_set ≠ itemName_content_words`).

**Fix (`src/chatbot/tools.py`):**
- `_find_closest_menu_items_from_menu`: added `alias_rewritten = False` default; sets `alias_rewritten = True` when the fish sandwich alias path is taken; includes `"alias_rewritten": alias_rewritten` in the `exact_match` return dict.
- `validateRequestedItem`: reads `alias_was_rewritten = match_result.get("alias_rewritten", False)`; when `True`, sets `leftover_words = []` instead of diffing — the entire input IS the alias, so no tokens are residual modifiers.

## 2026-04-28 - orchestrator handle_message syntax fix

### Overview
Fixed a broken duplicated block in `handle_message` that introduced invalid indentation and detached code from the `try` block.

### What was wrong
- A duplicated post-processing/reply block existed twice, once dedented outside `try` and once over-indented.
- This caused parser and type-check errors (`try` missing `except/finally`, unexpected indentation, expected expression).

### What changed
- Removed the duplicate malformed block and kept a single canonical post-processing block inside `try`.
- Kept the `escalation_queued` guard in the stage-transition condition so escalation responses do not append the "anything else" prompt.
- Preserved `ai_reply` / `flow_end` logging and final response return path.

### File
- `src/chatbot/orchestrator.py` — `Orchestrator.handle_message`

## 2026-04-29 - Item resolution confidence field

### Overview
Added a `confidence` field to track how reliably the system matched a customer's spoken item name to a menu item, passed through `addItemsToOrder` and echoed back in `addedItems`.

### Values
- `"high"` — verbatim exact match: the item name appeared literally in the menu index (`matchConfidence == "exact"` from a direct `by_name` lookup).
- `"medium"` — fuzzy auto-confirmed (`matchConfidence == "auto_exact"`: score ≥ `CONFIRMED_THRESHOLD`, no close competitor) OR a `"close"` match the customer confirmed via a clarification question.

### Key Changes
- `src/chatbot/tools.py` — `_find_closest_menu_items_from_menu`: fuzzy auto-confirm branch now returns `"match_confidence": "auto_exact"` instead of `"exact"`, making the two paths distinguishable downstream.
- `src/chatbot/tools.py` — `validateRequestedItem`: guard updated from `!= "exact"` to `not in ("exact", "auto_exact")` so `auto_exact` falls through to the full modifier-resolution branch and gets `itemId`/`merchantId` populated. Docstring updated with `"auto_exact"` entry and confidence assignment rules.
- `src/chatbot/tools.py` — `addItemsToOrder`: reads `confidence` from each item spec, echoes it in each `addedItems` entry. Docstring updated for both the input spec and the return field.
- `src/chatbot/orchestrator.py` — `_ADD_ITEMS_TO_ORDER_PARAMETERS_JSON_SCHEMA`: `confidence` added as optional enum field `["high", "medium"]` with description.
- `src/chatbot/promptsv2.py` — ADD_ITEM and MODIFY_ITEM (empty-order fallback) flows now instruct the agent to set `confidence` on each item dict before calling `addItemsToOrder`.

### Gotchas
- `"auto_exact"` is only surfaced by `_find_closest_menu_items_from_menu` / `validateRequestedItem`. `findClosestMenuItems` (the simpler tool) still maps both paths to `"exact"` — no change there since that tool is used for lookup, not ordering.
- When `matchConfidence == "close"` and the customer confirms, the agent calls `addItemsToOrder` directly with the confirmed candidate's `itemId` — no second `validateRequestedItem` call — so the agent must remember to set `confidence: "medium"` in that scenario.

## 2026-04-28 - Confidence Tag Embedding in Clover Line Item Notes

### Overview
Fuzzy-match confidence (`"high"`, `"medium"`, or `None`) is now embedded in the Clover line item note as a structured tag (`[High]`, `[Medium]`, `[Low]`), visible to merchants on the Clover dashboard. The tag is transparently stripped from all data returned to the LLM so the agent never sees it.

### Confidence → Tag Mapping
| `confidence` value | Tag |
|---|---|
| `"high"` | `[High]` |
| `"medium"` | `[Medium]` |
| `None` / missing | `[Low]` |

### Note Format
Tag appended on a new line: `"extra crispy\n[High]"`. Stripped with regex `\n?\[(High|Medium|Low)\]$`.

### Key Changes
- `src/chatbot/utils.py` — added `_CONFIDENCE_TAG_RE`, `_append_confidence_tag`, `_strip_confidence_tag`, `_extract_confidence_tag` after line 12.
- `src/chatbot/utils.py` — `_priced_line_item` (~line 540): note now passed through `_strip_confidence_tag` before returning (used by `calcOrderPrice`).
- `src/chatbot/tools.py` — `addItemsToOrder`: `note` is now built via `_append_confidence_tag(spec.get("note"), confidence)` before calling `add_clover_line_item`.
- `src/chatbot/tools.py` — `getOrderLineItems`: `note` field passed through `_strip_confidence_tag` before returning to LLM.
- `src/chatbot/tools.py` — `updateItemInOrder`: `current_note` stripped before comparison; new note value written as-is (tag NOT re-appended — confidence tag is only written once at item addition via `addItemsToOrder`).

### Gotchas
- `replaceItemInOrder` calls `addItemsToOrder` logic internally — no change needed there.
- `_normalize_order_line_items` intentionally left unchanged — it returns raw Clover data; stripping happens at the tool boundary.
- When `note_value is None` in `updateItemInOrder`, the note is cleared — this is intentional.
- The confidence tag is written **only once** at item creation (`addItemsToOrder`). Subsequent note updates via `updateItemInOrder` (e.g., `asNote` from `validateModifications`) write the new note verbatim without re-attaching the tag. This prevents `"medium [High]"` appearing in Clover.
- `_extract_confidence_tag` remains defined in `utils.py` but is no longer imported in `tools.py`.
