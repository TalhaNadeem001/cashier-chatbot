from importlib import import_module


def test_legacy_and_canonical_app_entrypoints_import() -> None:
    canonical = import_module("src.app.main")
    legacy = import_module("src.main")

    assert canonical.app is legacy.app
    assert canonical.create_app is legacy.create_app


def test_legacy_and_canonical_schema_modules_import() -> None:
    legacy_chatbot = import_module("src.chatbot.schema")
    canonical_chatbot = import_module("src.chatbot.api.schema")
    legacy_menu = import_module("src.menu.schema")
    canonical_menu = import_module("src.menu.api.schema")

    assert legacy_chatbot.BotInteractionRequest is canonical_chatbot.BotInteractionRequest
    assert legacy_menu.MenuIngestResponse is canonical_menu.MenuIngestResponse
