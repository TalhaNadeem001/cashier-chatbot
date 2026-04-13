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

class FoodOrderState(str, Enum):
    NEW_ORDER = "new_order"
    ADD_TO_ORDER = "add_to_order"
    REMOVE_FROM_ORDER = "remove_from_order"
    SWAP_ITEM = "swap_item"
    CANCEL_ORDER = "cancel_order"
    ADDING_MODIFIERS = "adding_modifiers"
    ORDER_MODIFIER_REQUEST = "order_modifier_request"


class ModifierOrderState(str, Enum):
    NEW_MODIFIER     = "new_modifier"
    ADD_MODIFIER     = "add_modifier"
    REMOVE_MODIFIER  = "remove_modifier"
    SWAP_MODIFIER    = "swap_modifier"
    CANCEL_MODIFIER  = "cancel_modifier"
    REVIEW_MODIFIER  = "review_modifier"
    NO_MODIFIER      = "no_modifier"


SUMMARIZATION_THRESHOLD = 10          # compress when history exceeds this
SUMMARIZATION_TAIL_MESSAGES = 4       # keep this many recent messages verbatim
CONVERSATION_SUMMARY_TTL = 60 * 60 * 4  # 4 hours in seconds
