from enum import Enum

class ConversationState(str, Enum):
    GREETING = "greeting"
    FAREWELL = "farewell"
    VAGUE_MESSAGE = "vague_message"
    RESTAURANT_QUESTION = "restaurant_question"
    MENU_QUESTION = "menu_question"
    FOOD_ORDER = "food_order"
    PICKUP_PING = "pickup_ping"
    PICKUP_TIME_SUGGESTION = "pickup_time_suggestion"
    MISC = "misc"
    HUMAN_ESCALATION = "human_escalation"
    ORDER_COMPLETE = "order_complete"
    ORDER_REVIEW = "order_review"


SUMMARIZATION_THRESHOLD = 10          # compress when history exceeds this
SUMMARIZATION_TAIL_MESSAGES = 4       # keep this many recent messages verbatim
CONVERSATION_SUMMARY_TTL = 60 * 60 * 4  # 4 hours in seconds

_PARSE_VALIDATION_ERROR_PREFIX = "Failed to parse Gemini structured response:"

_MENU_AVAILABILITY_STALE_SECONDS = 43200
_MENU_CACHE_TTL_SECONDS = 43200
_CLOVER_CREDS_REDIS_TTL_SECONDS = 3 * 60 * 60
_HARDCODED_SALES_TAX_PERCENT = 9
_COOKING_PREFERENCE_HINTS = (
    "rare",
    "medium",
    "well",
    "done",
    "crispy",
    "grilled",
    "fried",
    "seared",
)
_COOKING_MODIFIER_HINTS = (
    "patty",
    "cook",
    "temp",
    "temperature",
    "protein",
    "beef",
    "steak",
    "burger",
)

_MENU_CACHE_VERSION = (
    4  # bump when normalized shape changes (e.g. new index keys added)
)

# How long we keep the Clover order id in Redis for a chat session (seconds).
_SESSION_CLOVER_ORDER_REDIS_TTL_SECONDS = 3 * 60 * 60
_SESSION_CLARIFICATION_AND_INTENT_TTL_SECONDS = 3 * 60 * 60  # 3 hours
_SUMMARIZE_HISTORY_MAX_OUTPUT_TOKENS = 180

_BUFFER_TIMER_TTL_MS: int = 5000
_BUFFER_LOCK_TTL_SECONDS: int = 30
_BUFFER_RESULT_TTL_SECONDS: int = 60
_BUFFER_POLL_INTERVAL_SECONDS: float = 0.1
_BUFFER_POLL_TIMEOUT_SECONDS: float = 45.0
_BUFFER_MAX_WAIT_SECONDS: float = 10.0

# Default pickup window (minutes) reported to the customer when an order is
# confirmed and no specific pickup time has been requested.
_DEFAULT_PICKUP_MINUTES: int = 30

# Clover item IDs that should never appear in the system menu.
# Add IDs here for placeholder or misconfigured items that exist in Clover
# but should not be orderable by customers.
_MENU_ITEM_ID_BLOCKLIST: frozenset[str] = frozenset({
    "KYNK3BZB1798J",  # "Wings" — Clover placeholder, real wing items are the sized variants
})