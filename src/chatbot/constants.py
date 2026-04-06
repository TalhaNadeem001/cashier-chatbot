from enum import Enum

class ConversationState(str, Enum):
    GREETING = "greeting"
    FAREWELL = "farewell"
    VAGUE_MESSAGE = "vague_message"
    RESTAURANT_QUESTION = "restaurant_question"
    MENU_QUESTION = "menu_question"
    FOOD_ORDER = "food_order"
    ADDING_MODIFIERS = "adding_modifiers"
    PICKUP_PING = "pickup_ping"
    MISC = "misc"
    HUMAN_ESCALATION = "human_escalation"
    ORDER_COMPLETE = "order_complete"

class FoodOrderState(str, Enum):
    NEW_ORDER = "new_order"
    ADD_TO_ORDER = "add_to_order"
    MODIFY_ORDER = "modify_order"
    REMOVE_FROM_ORDER = "remove_from_order"
    SWAP_ITEM = "swap_item"
    CANCEL_ORDER = "cancel_order"
    ADDING_MODIFIERS = "adding_modifiers"
    REVIEW_ORDER = "review_order"

class ModifierState(str, Enum):
    NEW_MODIFIER = "new_modifier"
    MODIFY_MODIFIER = "modify_modifier"
    REMOVE_MODIFIER = "remove_modifier"
    COMPLETE_MODIFIER = "complete_modifier"
    NO_MODIFIER = "no_modifier"

class ModifierJourneyState(str, Enum):
    PROMPTING  = "prompting"   # Bot asked; awaiting user response
    COLLECTING = "collecting"  # User is providing selections; extract and apply
    COMPLETE   = "complete"    # All required mods filled; exit modifier flow
