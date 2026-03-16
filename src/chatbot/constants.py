from enum import Enum


class ConversationState(str, Enum):
    GREETING = "greeting"
    FAREWELL = "farewell"
    VAGUE_MESSAGE = "vague_message"
    RESTAURANT_QUESTION = "restaurant_question"
    MENU_QUESTION = "menu_question"
    FOOD_ORDER = "food_order"
    PICKUP_PING = "pickup_ping"
    MISC = "misc"
    FINALIZING_ORDER = "finalizing_order"
    HUMAN_ESCALATION = "human_escalation"


class FoodOrderState(str, Enum):
    NEW_ORDER = "new_order"
    ADD_TO_ORDER = "add_to_order"
    MODIFY_ORDER = "modify_order"
    REMOVE_FROM_ORDER = "remove_from_order"
    SWAP_ITEM = "swap_item"
    CANCEL_ORDER = "cancel_order"
