
CONFIRMED_THRESHOLD = 70     # single top match at or above this → confirmed
MODS_CONFIRMED_THRESHOLD = 80 # single top match at or above this → confirmed
NOT_FOUND_THRESHOLD = 50     # match_free_modifier: top match below this → not on option list
LOW_MENU_MATCH_THRESHOLD = 65  # match_item: below this → not on menu (no ambiguity / gap pass)
LOW_MENU_MATCH_MESSAGE = (
    "This item doesn't exist on our menu. Please refer to our menu for available options!"
)
AMBIGUITY_GAP = 6            # top N matches within this score range of each other → ambiguous