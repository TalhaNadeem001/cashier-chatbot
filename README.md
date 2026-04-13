# Cashier Chatbot

An AI-powered restaurant cashier chatbot built with FastAPI, OpenAI, and Redis. It handles natural-language food ordering conversations through a browser-based chat UI — taking orders, answering menu and restaurant questions, applying modifiers, resolving ambiguous items via fuzzy matching, and pinging staff on demand.

---

## Table of Contents

- [Architecture](#architecture)
- [Conversation Flow](#conversation-flow)
- [Setup](#setup)
- [Adding Menu Data & Restaurant Info](#adding-menu-data--restaurant-info)
- [Running the Server](#running-the-server)
- [Project Structure](#project-structure)
- [Development Commands](#development-commands)

---

## Architecture

```
Browser (index.html)
    │  POST /api/bot/message
    ▼
ChatReplyService
    ├── StateResolver          — classifies the user's intent (GPT-4o-mini, JSON mode)
    │   └── StateVerifier      — double-checks low-confidence or invalid-transition results
    │
    └── StateHandlerFactory    — routes to the correct handler
            │
            ├── Greeting / Farewell / Misc / VagueMessage / HumanEscalation
            ├── RestaurantQuestionHandler  — answers from restaurant_context Redis key
            ├── MenuQuestionHandler        — answers from menu_context Redis key
            ├── PickupPingHandler          — signals order-ready status to frontend
            └── FoodOrderHandlerFactory
                    ├── FoodOrderStateResolver  — classifies the order sub-intent
                    └── Handlers
                            ├── new_order        — extract → fuzzy match → add to state
                            ├── add_to_order     — extract new items, merge into state
                            ├── modify_order     — change quantity / modifier on existing item
                            ├── remove_from_order
                            ├── swap_item        — atomic remove + add
                            └── cancel_order

Redis (per user_id)
    menu_context:{user_id}               — full menu text for menu Q&A
    menu_item_names:{user_id}            — comma-separated names for fuzzy matching
    restaurant_context:{user_id}         — restaurant info for restaurant Q&A
    restaurant_name_location:{user_id}   — name + address for greeting
```

**Key design decisions:**

- **Stateless backend** — the frontend sends the full conversation history and current order state with every request; the server holds no session.
- **Two-stage intent classification** — a fast primary classifier is checked by an independent verifier only when confidence is low or the state transition is invalid.
- **Fuzzy item matching** — user item names are matched against canonical menu names using RapidFuzz (WRatio scorer), with three outcomes: confirmed (≥ 70), ambiguous (multiple close matches), or not found (< 50).
- **All AI calls use `gpt-4o-mini`** in JSON mode with `temperature=0` for deterministic extraction, and `temperature=0.4–0.7` for natural-language replies.

---

## Conversation Flow

### Conversation states

| State | Trigger | Response |
|---|---|---|
| `greeting` | First message / hello | Welcome with restaurant name and location |
| `farewell` | Goodbye / thanks | Warm sign-off |
| `menu_question` | Questions about dishes, prices, allergens | Answered from menu context |
| `restaurant_question` | Hours, location, parking, seating | Answered from restaurant context |
| `food_order` | Placing or changing an order | Routed to food order sub-state |
| `pickup_ping` | "How long?" / "Is it ready?" | `pickup_ping: true` flag sent to frontend |
| `human_escalation` | "Can I speak to someone?" | `ping_for_human: true` flag — triggers staff popup |
| `vague_message` | Unclear intent | Clarifying question |
| `misc` | Off-topic chat | Brief reply + redirect |

### Food order sub-states

| Sub-state | Example |
|---|---|
| `new_order` | "I'll have a burger and a Coke" |
| `add_to_order` | "Also get me some fries" |
| `modify_order` | "Make the burger a double" |
| `remove_from_order` | "Actually drop the Coke" |
| `swap_item` | "Swap the Coke for a milkshake" |
| `cancel_order` | "Cancel everything" |

### Order confirmation flow

After each successful order update the bot asks "Is that all?". The next message is classified as `confirm`, `modify`, or `unclear` by a dedicated finalization classifier — bypassing the main intent pipeline entirely.

### Item matching

When a user names an item:

1. Exact case-insensitive match → confirmed immediately.
2. Fuzzy match score ≥ 70 with no close competitors → confirmed, name normalised to canonical menu name.
3. Multiple matches within 6 points of each other → bot asks "Did you mean X, Y, or Z?" and sets `has_pending_clarification: true`.
4. Best score < 50 → "I couldn't find that on our menu."

---

## Setup

### Prerequisites

- Python 3.13+
- [uv](https://github.com/astral-sh/uv)
- Redis (local or remote)
- An OpenAI API key

### Install

```bash
git clone <repo-url>
cd cashier-chatbot
uv sync
```

### Environment variables

Create a `.env` file in the project root:

```env
OPENAI_API_KEY=sk-...
REDIS_URL=redis://127.0.0.1:6379
ENVIRONMENT=development
```

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | OpenAI API key (used for all AI calls) |
| `REDIS_URL` | Yes | Redis connection URL |
| `ENVIRONMENT` | No | `development` (default), `staging`, or `production` |

---

## Adding Menu Data & Restaurant Info

All data is stored in Redis, scoped per `user_id`. The default `user_id` used by the web UI is `"1"`.

### Menu items

Menu items are defined in `src/constants.py` as `MENU_ITEM_MAP`. Each entry follows this structure:

```python
"Item Name": {
    "description": "Short description of the item.",
    "price": 9.50,                              # float
    "modifiers": [                              # size / preparation options
        "Make it double (+£2.00)",
        "Make it gluten-free (+£1.00)",
    ],
    "add_ons": [                                # extras
        "Extra cheese (£1.00)",
        "Bacon (£1.50)",
    ],
},
```

To add, remove, or edit menu items, update `MENU_ITEM_MAP` in `src/constants.py`, then re-run the seed script.

### Restaurant context

The seed script (`scripts/seed_menu.py`) writes the restaurant name and address:

```python
RESTAURANT_NAME_LOCATION_STRING = "The Burger Joint, 123 Main St, Anytown, USA"
```

To answer questions about hours, parking, seating etc., set the `restaurant_context` key directly in Redis:

```bash
redis-cli SET "restaurant_context:1" "Opening hours: Mon–Sun 10am–10pm. Seating: 60 covers. Free parking at rear. Phone: 01234 567890."
```

Or extend `scripts/seed_menu.py` to write this key alongside the others.

### Seed the data

After any changes to `src/constants.py` or the seed script, run:

```bash
python scripts/seed_menu.py
```

This writes three Redis keys for `user_id = "1"`:

| Key | Content |
|---|---|
| `menu_context:1` | Full menu text with prices, descriptions, modifiers, and add-ons |
| `menu_item_names:1` | Comma-separated item names used for fuzzy matching |
| `restaurant_name_location:1` | Restaurant name and address shown in the greeting |

---

## Running the Server

```bash
uvicorn src.app.main:app --reload
```

Open `http://localhost:8000` in your browser. The chat UI is served from `templates/index.html`.

### API

`POST /api/bot/message`

**Request body:**

```json
{
  "user_id": "1",
  "latest_message": "I'll have a Classic Beef Burger please",
  "message_history": [],
  "order_state": null,
  "previous_state": null,
  "previous_food_order_state": null,
  "awaiting_order_confirmation": false,
  "has_pending_clarification": false
}
```

**Response:**

```json
{
  "chatbot_message": "Got it! I've added 1x Classic Beef Burger to your order. Is that all?",
  "order_state": { "items": [{ "name": "Classic Beef Burger", "quantity": 1, "modifier": null }] },
  "pickup_ping": false,
  "ping_for_human": false,
  "previous_state": "food_order",
  "previous_food_order_state": "new_order",
  "awaiting_order_confirmation": true,
  "has_pending_clarification": false
}
```

The frontend is responsible for maintaining state between turns and sending it back with each request.

**Response flags:**

| Flag | Effect |
|---|---|
| `pickup_ping: true` | Shows the "Order Placed" modal with order summary |
| `ping_for_human: true` | Shows the "Cashier Called" staff popup |
| `has_pending_clarification: true` | Order state not updated yet; bot is waiting for user input |
| `awaiting_order_confirmation: true` | Next message goes directly to the finalization classifier |

---

## Project Structure

```
cashier-chatbot/
├── scripts/
│   ├── seed_menu.py          # Seed menu + restaurant data into Redis
│   ├── create_app.py         # Scaffold a new src/<module> directory
│   └── init_ai.py            # Scaffold an src/ai/ module structure
│
├── src/
│   ├── main.py               # FastAPI app, lifespan, router registration
│   ├── config.py             # Pydantic settings (reads .env)
│   ├── database.py           # Async SQLAlchemy engine + session factory
│   ├── cache.py              # Redis async helpers (cache_get, cache_set, cache_delete)
│   ├── constants.py          # MENU_ITEM_MAP, MENU_CONTEXT_STRING
│   │
│   ├── chatbot/
│   │   ├── router.py                  # POST /api/bot/message
│   │   ├── service.py                 # ChatReplyService — top-level orchestrator
│   │   ├── chatbot_ai.py              # ChatbotAI — all OpenAI calls
│   │   ├── handlers.py                # StateHandlerFactory — one handler per ConversationState
│   │   ├── food_order_handlers.py     # FoodOrderHandlerFactory — order sub-state handlers + fuzzy matching
│   │   ├── state_resolver.py          # StateResolver + FoodOrderStateResolver
│   │   ├── prompts.py                 # All system prompts
│   │   ├── schema.py                  # BotMessageRequest, BotMessageResponse, OrderItem, …
│   │   ├── internal_schemas.py        # AI response schemas (IntentAnalysis, etc.)
│   │   ├── constants.py               # ConversationState, FoodOrderState enums
│   │   ├── exceptions.py              # AIServiceError, UnhandledStateError, …
│   │   └── exception_handlers.py      # FastAPI exception handler registration
│   │
│   └── menu/                          # Scaffolded module — ingestion endpoint stub
│
├── templates/
│   └── index.html            # Browser chat UI (vanilla JS, no build step)
│
├── tests/
├── pyproject.toml            # Dependencies + project metadata (uv)
├── alembic.ini
└── .env                      # Local environment variables (do not commit)
```

---

## Development Commands

```bash
# Run dev server
uvicorn src.app.main:app --reload

# Seed menu and restaurant data into Redis
python scripts/seed_menu.py

# Lint
ruff check .

# Lint and auto-fix
ruff check --fix .

# Format
ruff format .

# Run tests
pytest

# Scaffold a new app module under src/
python scripts/create_app.py <module_name>

# Database migrations (if wiring up SQLAlchemy models)
alembic revision --autogenerate -m "description"
alembic upgrade head
alembic downgrade -1
alembic current
```
