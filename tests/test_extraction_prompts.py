from src.chatbot.extraction.prompts import (
    APPLY_ORDER_DELTA_SYSTEM_PROMPT,
    EXTRACT_MODIFY_ITEMS_SYSTEM_PROMPT,
    EXTRACT_ORDER_ITEMS_SYSTEM_PROMPT,
)


def test_modifier_bearing_prompts_require_comma_separated_modifiers():
    expected_rule = 'separate them with a comma and a space, for example: "modifier 1, modifier 2"'

    assert expected_rule in APPLY_ORDER_DELTA_SYSTEM_PROMPT
    assert expected_rule in EXTRACT_MODIFY_ITEMS_SYSTEM_PROMPT


def test_main_order_extraction_prompt_still_keeps_modifier_empty():
    assert 'modifier: always return an empty string `""` — never populate this field' in EXTRACT_ORDER_ITEMS_SYSTEM_PROMPT
