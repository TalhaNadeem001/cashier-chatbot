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
_SESSION_ORDER_DATA_REDIS_TTL_SECONDS = 3 * 60 * 60  # 3 hours, matches order id TTL
_SESSION_CLARIFICATION_AND_INTENT_TTL_SECONDS = 3 * 60 * 60  # 3 hours
_SUMMARIZE_HISTORY_MAX_OUTPUT_TOKENS = 180

# Combo items that are sold as bundles. These are tracked here for reference
# but excluded from the system menu — customers order individual components.
_COMBO_ITEMS: dict[str, str] = {
    "TC23CJG848T0C": "2 Tenders & Fries",
    "9GRXSAZGRY88G": "2 Sandos & Fries",
    "WD778SKQJWKJR": "Sando + Tender & Fries",
    "W0QYPBG50G2RP": "Sando & Fries",
}

# Clover item IDs that should never appear in the system menu.
# Add IDs here for placeholder or misconfigured items that exist in Clover
# but should not be orderable by customers.
_MENU_ITEM_ID_BLOCKLIST: frozenset[str] = frozenset({
    "KYNK3BZB1798J",  # "Wings" — Clover placeholder, real wing items are the sized variants
    *_COMBO_ITEMS,    # combo bundles excluded — customers order individual components
})

# Clover item IDs whose display name in Clover is wrong or has an accidental
# quantity prefix (e.g. "1 Tender"). Maps item_id → corrected name used
# everywhere in the system (by_name key, order submission, display).
_MENU_ITEM_NAME_OVERRIDES: dict[str, str] = {
    "E9GZ5CT761C24": "Tender",
    "CAYKD5B3BHD70": "Chicken Sando",
    "Y23V3Y50YC2A4": "6 Pc Boneless Wings"
}
