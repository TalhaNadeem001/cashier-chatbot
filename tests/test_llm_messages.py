from src.chatbot.llm_messages import chat_history_from_messages, split_system_instruction
from src.chatbot.schema import Message


def test_chat_history_from_messages_uses_recent_window_and_sanitizes_content():
    history = [
        Message(role="assistant", content="welcome"),
        Message(role="user", content="one"),
        Message(role="assistant", content="two\x00"),
        Message(role="system", content="summary"),
    ]

    assert chat_history_from_messages(history, tail=3) == [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
        {"role": "system", "content": "summary"},
    ]


def test_split_system_instruction_collects_system_messages_in_order():
    messages = [
        {"role": "system", "content": "Primary prompt"},
        {"role": "assistant", "content": "Hello"},
        {"role": "system", "content": "[Conversation summary so far]: Customer ordered fries."},
        {"role": "user", "content": "Anything spicy?"},
    ]

    system_instruction, contents = split_system_instruction(messages)

    assert system_instruction == (
        "Primary prompt\n\n[Conversation summary so far]: Customer ordered fries."
    )
    assert contents == [
        {"role": "assistant", "content": "Hello"},
        {"role": "user", "content": "Anything spicy?"},
    ]
