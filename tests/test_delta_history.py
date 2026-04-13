from src.chatbot.extraction.ai_client import build_delta_history_messages, get_latest_assistant_context
from src.chatbot.schema import Message


def test_build_delta_history_messages_uses_short_recent_window():
    history = [
        Message(role="assistant", content="welcome"),
        Message(role="user", content="one"),
        Message(role="assistant", content="two"),
        Message(role="user", content="three"),
        Message(role="assistant", content="four"),
        Message(role="user", content="five"),
        Message(role="assistant", content="six"),
        Message(role="user", content="seven"),
    ]

    assert build_delta_history_messages(history) == [
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
        {"role": "assistant", "content": "four"},
        {"role": "user", "content": "five"},
        {"role": "assistant", "content": "six"},
        {"role": "user", "content": "seven"},
    ]


def test_get_latest_assistant_context_returns_most_recent_assistant_message():
    history = [
        Message(role="assistant", content="What spice level would you like?"),
        Message(role="user", content="hold on"),
        Message(role="assistant", content="Did you mean Chicken Sando or Chicken Sub?"),
        Message(role="user", content="hmm"),
    ]

    assert get_latest_assistant_context(history) == "Did you mean Chicken Sando or Chicken Sub?"
