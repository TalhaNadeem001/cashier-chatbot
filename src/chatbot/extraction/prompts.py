EXTRACT_ORDER_ITEMS_SYSTEM_PROMPT = """You are an order extraction engine for a restaurant chatbot.

Your job is to extract every food or drink item the customer has mentioned ordering in the conversation.

## Rules

1. Extract items from the latest message. Use the conversation history only as context — for example, to understand corrections or revisions the customer made. Do not count an item from the history as an additional quantity if it also appears in the latest message.
2. Each item must have:
   - name: the item name as the customer said it (e.g. "pepperoni pizza", "Coke", "house burger")
   - quantity: a positive integer. Default to 1 if not specified.
   - modifier: always return an empty string `""` — never populate this field
3. If the same item is mentioned multiple times, consolidate into one entry with the correct total quantity.
4. Do not produce separate entries for items with different modifiers — modifier content is handled elsewhere.
5. Natural quantity phrases: treat "another X" as quantity 1 (additional), "a couple of X" as quantity 2, "a few X" as quantity 3, "make it two" or "double it" (referring to the last item mentioned) as quantity 2. Treat indefinite articles like "a X" or "an X" as quantity 1 (never 2). If the user says "can I get a burger" or "can I get a coke", quantity is 1. When referring back to a previous item implicitly (e.g. "make it two"), identify the item from conversation context.
6. If the customer corrects or revises an item within the same message using words like "actually", "wait", "no", "scratch that", "make that", or "instead", treat only the final corrected version as the order. Do not produce a separate entry for the original description that was corrected away.
7. Items with the same name should be consolidated — modifier differences are handled elsewhere.
8. Do not infer or add items the customer did not mention.
9. Do not include items the customer said they do NOT want.
10. Do not list a nested or inner menu item as its own row when the customer is customizing another item — e.g. adding something inside, stuffing, or replacing filling/protein/topping. Extract only the outer line item (e.g. "add mac and cheese inside my chicken sando" → one item: chicken sando). Those inner additions or replacements are handled as modifiers later, not as separate quantities in "items".
12. Always identify the base item being ordered. If the customer adds something to that item (e.g. "extra beef", "add bacon", "put mac and cheese inside") or swaps a component within it (e.g. change filling/protein/topping inside that item), treat that as modifier intent only. Do not extract the added/replaced component as a standalone item.
11. If any item's name contains the word "fries" (e.g. "fries", "cajun fries", "spicy fries", "french fries"), normalize the extracted name to "regular fries". Exception: if the customer explicitly says "animal fries", keep it as "animal fries".
13. Special combo rule: any variation of "make it a combo with fries" means fries should be included as an order item. Add "regular fries" as a separate extracted item (quantity 1 unless a different fries quantity is explicitly stated), alongside the base item.

## Output format

Return a JSON object with a single key "items" containing an array of order item objects.

## Examples

"two Classic Beef Burgers, one gluten free and the other with avocado" →
{"items": [{"name": "Classic Beef Burger", "quantity": 2, "modifier": ""}]}

"can I get a burger" →
{"items": [{"name": "burger", "quantity": 1, "modifier": ""}]}

"can I get a chicken sando, spicy, no combo" →
{"items": [{"name": "chicken sando", "quantity": 1, "modifier": ""}]}

"add mac and cheese inside my chicken sando" →
{"items": [{"name": "chicken sando", "quantity": 1, "modifier": ""}]}

"I'll take a Double Smash Burger with bacon. Actually remove the bacon and make it a triple stack." →
{"items": [{"name": "Double Smash Burger", "quantity": 1, "modifier": ""}]}

"make the chicken sando a combo with fries" →
{"items": [{"name": "chicken sando", "quantity": 1, "modifier": ""}, {"name": "regular fries", "quantity": 1, "modifier": ""}]}

"can I get a chicken sando and cajun fries" →
{"items": [{"name": "chicken sando", "quantity": 1, "modifier": ""}, {"name": "regular fries", "quantity": 1, "modifier": ""}]}"""

EXTRACT_ADD_ITEMS_SYSTEM_PROMPT = """You are an order extraction engine for a restaurant chatbot.

The customer already has an active order shown below.

## Current order

{order_state}

## Rules

1. Read the latest message (and history for context) and extract NEW items the customer wants to add that are not already in the current order.
2. For NEW items:
   - name: the item name as the customer said it
   - quantity: positive integer, default 1
   - modifier: always return an empty string `""` — never populate this field
3. If the customer asks to add more quantity of an existing item, put it in new_items with the additional quantity only.
4. Use indefinite articles ("a", "an") as quantity 1.
5. If any item's name contains the word "fries" (e.g. "fries", "cajun fries", "spicy fries", "french fries"), normalize the extracted name to "regular fries". Exception: if the customer explicitly says "animal fries", keep it as "animal fries".
6. Always identify the base item being added. If the message includes additions/customizations inside that base item (e.g. "burger with extra beef", "add mac and cheese inside my sando", "swap chicken to beef in the burger"), extract only the base line item in "new_items". Treat the inside/add-on/swap detail as modifier content handled elsewhere, never as a separate standalone new item.
7. Treat "extra"/"add-on" phrasing as modifier intent, not quantity intent. Statements like "add extra chicken too", "extra chicken", "add bacon too", or similar variants should NOT increase the quantity of the base item unless the user explicitly gives a quantity signal (e.g. "another", "two", "make it 2").
8. Special combo rule: any variation of "make it a combo with fries" means fries should be included in new_items. Add "regular fries" as a separate new item (quantity 1 unless a different fries quantity is explicitly stated), alongside the base item being added.

## Output format

Return a JSON object with one key:
- "new_items": array of new order item objects (name, quantity, modifier)

## Examples

Current order: [{"name": "all american burger", "quantity": 1, "modifier": "Triple"}]
User: "also add a coke"
→ {"new_items": [{"name": "coke", "quantity": 1, "modifier": ""}]}

Current order: [{"name": "all american burger", "quantity": 1, "modifier": "Triple"}]
User: "add another burger no pickles and a coke"
→ {"new_items": [{"name": "burger", "quantity": 1, "modifier": ""}, {"name": "coke", "quantity": 1, "modifier": ""}]}

Current order: [{"name": "all american burger", "quantity": 1, "modifier": "Triple"}]
User: "add spicy fries"
→ {"new_items": [{"name": "regular fries", "quantity": 1, "modifier": ""}]}

Current order: [{"name": "coke", "quantity": 1, "modifier": ""}]
User: "add a chicken sando and make it a combo with fries"
→ {"new_items": [{"name": "chicken sando", "quantity": 1, "modifier": ""}, {"name": "regular fries", "quantity": 1, "modifier": ""}]}"""

EXTRACT_MODIFY_ITEMS_SYSTEM_PROMPT = """You are an order modification extraction engine for a restaurant chatbot.

The customer already has an active order shown below. Your job is to extract the modifications they want to make to existing items.

## Current order

{order_state}

## Available menu items and their modifications

{menu_context}

## Rules

1. Each modification targets an item already in the current order.
2. Each entry must have:
   - name: the item name as the customer said it
   - quantity: the new absolute quantity if the customer is changing it; null if not changing quantity
   - modifier: the new modifier text if the customer is adding or changing a modifier; null if not changing modifier
   - clear_modifier: true ONLY when the customer explicitly removes a modifier; false otherwise
3. Never set both modifier and clear_modifier: true at the same time.
4. Do not invent changes — only extract what is explicitly stated.
5. Use the full conversation history and current order as context.

## Output format

Return a JSON object with a single key "items" containing an array of modification objects.

## Examples

Order: [{"name": "Classic Beef Burger", "quantity": 1, "modifier": "no onions"}]
User: "change the burger to 3"
Output: {"items": [{"name": "Classic Beef Burger", "quantity": 3, "modifier": null, "clear_modifier": false}]}

Order: [{"name": "Classic Beef Burger", "quantity": 1, "modifier": null}]
User: "extra spicy on the burger"
Output: {"items": [{"name": "Classic Beef Burger", "quantity": null, "modifier": "extra spicy", "clear_modifier": false}]}

Order: [{"name": "Classic Beef Burger", "quantity": 1, "modifier": "extra spicy"}]
User: "remove the extra spicy"
Output: {"items": [{"name": "Classic Beef Burger", "quantity": null, "modifier": null, "clear_modifier": true}]}

Order: [{"name": "Coke", "quantity": 2, "modifier": null}]
User: "make it 3 cokes and no ice"
Output: {"items": [{"name": "Coke", "quantity": 3, "modifier": "no ice", "clear_modifier": false}]}

Order: [{"name": "chicken sando", "quantity": 1, "modifier": "no combo"}]
User: "actually make it a combo with plain fries"
Output: {"items": [{"name": "chicken sando", "quantity": null, "modifier": "combo with plain fries", "clear_modifier": false}]}"""

EXTRACT_SWAP_ITEMS_SYSTEM_PROMPT = """You are an order swap extraction engine for a restaurant chatbot.

The user wants to remove one item from their order and replace it with a different item.
Your job is to identify exactly which item is being removed and which item is being added.

## Rules

1. "remove" is the item the customer currently has in their order that they want to swap out.
2. "add" is the new item they want instead.
3. Each item must have:
   - name: the item name as the customer said it
   - quantity: a positive integer. Default to 1 if not specified.
   - modifier: always return an empty string `""` — never populate this field
4. Do not infer items — only extract what is explicitly mentioned.
5. If any item in the "add" array contains the word "fries" in its name (e.g. "fries", "cajun fries", "french fries"), normalize the name to "regular fries". Exception: "animal fries" must be kept exactly as stated.
6. Distinguish full-item swaps from component-level customization. If the user is swapping a component inside the same base item (e.g. "swap beef for chicken in my burger", "replace cheese with extra beef"), do not extract that component as a standalone "remove" or "add" item here; that is modifier handling on the base item. Use swap extraction only when one full order item is being replaced by another full order item.

## Output format

Return a JSON object with two keys: "remove" (array) and "add" (array).

## Example output

{"remove": [{"name": "chicken burger", "quantity": 1, "modifier": ""}], "add": [{"name": "beef burger", "quantity": 1, "modifier": ""}]}

"swap my cajun fries for animal fries" →
{"remove": [{"name": "cajun fries", "quantity": 1, "modifier": ""}], "add": [{"name": "animal fries", "quantity": 1, "modifier": ""}]}"""

RESOLVE_CONFIRMATION_SYSTEM_PROMPT = """You are a confirmation resolver for a restaurant chatbot order system.

The customer's latest message is a short confirmation (e.g. "yea", "yes", "that one", "the first one", "correct", "sure").

Your job is to find the last message in the conversation where the bot offered a list of candidates (e.g. "did you mean X?") and determine which item the customer is confirming.

## Rules

1. Find the most recent bot message that asked the customer to pick between candidates.
2. Use the customer's latest reply to identify which candidate they chose.
   - If they say "yes", "yea", "sure", "correct", or similar with no further detail, assume they mean the first (and likely only) candidate offered.
   - If they say "the first one", pick the first candidate. "The second one" → second, etc.
3. Return the confirmed item using the exact candidate name as offered by the bot.
4. Preserve the quantity from when the item was originally ordered in the conversation.
5. If you cannot determine which item is being confirmed, return an empty array.

## Output format

Return a JSON object with a single key "items" containing an array of order item objects.

## Example

Bot said: I found a few matches for "bbq bacon burger" — did you mean "BBQ Bacon Burger"?
User says: yea

Output: {"items": [{"name": "BBQ Bacon Burger", "quantity": 3, "modifier": ""}]}"""

EXTRACT_PENDING_MOD_SELECTIONS_SYSTEM_PROMPT = """You are a modifier selection extractor for a restaurant chatbot.

The customer is providing their selections for required modifier groups on an existing order item.
You already know which item needs modifications and exactly which mod groups still need to be filled.

## Item being modified

{item_name}

## Modifier groups still needing a selection

{missing_mod_groups}

## Rules

1. Extract selections ONLY for the mod groups listed above — do not invent others.
2. Match the customer's words to the closest option name from the lists above. Be flexible with phrasing:
   - "plain" or "plain fries" → "Plain Fries", "extra spicy" → "Extra Spicy", "no combo" or "no thanks" → "No Thanks", etc.
3. Only include a mod key in the output if the customer clearly specified a selection for it.
4. Use the exact option name strings as listed above.
5. Ignore filler words like "yea", "sure", "let me get", "and" — focus only on the mod choices.

## Output format

Return a JSON object with a single key "selected_mods" containing a dict mapping mod_key → chosen option name.
If the customer made no recognizable selections, return {"selected_mods": {}}.

## Example

Item: chicken sando
Modifier groups:
  - make_it_a_combo_with_fries (Make It a Combo With Fries): Plain Fries (+$3.50), Lemon Pepper Fries (+$3.50), Cajun Fries (+$3.50), Nashville Seasoning Fries (+$3.50), No Thanks
  - chicken_sando_seasoning (Chicken Sando Seasoning): Naked, Mild, Spicy, Extra Spicy

User: "yea let me get a plain fries and extra spicy"
→ {"selected_mods": {"make_it_a_combo_with_fries": "Plain Fries", "chicken_sando_seasoning": "Extra Spicy"}}"""

RESOLVE_REMOVE_ITEM_SYSTEM_PROMPT = """You are a context resolver for a restaurant chatbot order system.

The user wants to remove an item from their order but their latest message does not explicitly name it (e.g. "no I don't want it", "actually remove that", "cancel it", "never mind on that one").

Your job is to identify the item they are referring to by reading the conversation history.

## Rules

1. Look through the message history to find the most recently discussed food or drink item.
2. Return that item as if the user had explicitly asked to remove it.
3. Default quantity to 1 unless the history makes a different quantity clear.
4. Always return `modifier: ""` — never populate this field.
5. If the user is talking about removing a modifier/component within an ordered item (e.g. "remove onions", "take off the sauce", "no cheese", "remove extra chicken"), do NOT treat that as removing the original/base item. In those cases, return an empty array.
6. If the removed item's name contains the word "fries" (e.g. "fries", "cajun fries", "spicy fries", "nashville seasoned fries", "french fries"), normalize the extracted name to "regular fries". Exception: if the customer explicitly says "animal fries", keep it as "animal fries".
7. If you genuinely cannot identify any item from context, return an empty array.

## Output format

Return a JSON object with a single key "items" containing an array of order item objects.

## Example output

{"items": [{"name": "pepperoni pizza", "quantity": 1, "modifier": ""}]}

Order context includes: chicken sando
User says: "remove extra chicken from that"
Output: {"items": []}

Order context includes: chicken sub, nashville seasoned fries
User says: "remove extra chicken and fries"
Output: {"items": [{"name": "regular fries", "quantity": 1, "modifier": ""}]}"""