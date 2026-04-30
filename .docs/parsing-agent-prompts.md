# Parsing Agent Prompts

## Overview
The parsing agent reads raw customer SMS and extracts structured intents. Its full prompt is built from `DEFAULT_PARSING_AGENT_PROMPTS` in `src/chatbot/promptsv2.py`.

## Key Files
- `src/chatbot/promptsv2.py` — all prompt segments (`identity_prompt`, `intent_labels_prompt`, `parsing_rules_prompt`, `few_shot_examples_prompt`, etc.)
- `src/chatbot/schema.py` — `ParsingAgentPrompts` dataclass

## How It Works
The prompt is composed from several named segments and injected into the parsing agent at call time. The parsing agent returns JSON only — no customer-facing text.

## Intent Labels of Note
- `escalation` — complaints, disputes, human intervention requests, AND any allergy/dietary/food safety question. The bot cannot safely answer allergy questions, so they are always escalated to a human via `humanInterventionNeeded`.
- `menu_question` — general menu info (available items, modifiers, what comes on a dish). Does NOT include allergy questions.
- `identity_question` — "are you a bot?", "who are you?" — never `outside_agent_scope`.

## Gotchas / Decisions

### Allergy questions → escalation (added 2026-04-30)
Allergy/food safety questions (e.g. "are the buns gluten-free?", "can wings be made without dairy?") look like `menu_question` on the surface but must be `escalation`. The AI cannot reliably verify allergen information, so they need human follow-up via `humanInterventionNeeded`. This is enforced via:
1. The `escalation` intent label description explicitly lists allergy examples.
2. A `ALLERGY / FOOD SAFETY vs MENU QUESTION DISTINCTION` rule in `parsing_rules_prompt`.
3. Example 19 in `few_shot_examples_prompt` demonstrates a mixed greeting + allergy message parsed as `greeting` + `escalation` + `escalation`.

The execution agent's existing escalation flow already handles this correctly (calls `humanInterventionNeeded`, replies "Let me check on that for you.") — no changes needed there.
