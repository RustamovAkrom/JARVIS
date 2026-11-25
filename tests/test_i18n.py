from core import i18n


def test_supported_languages_are_copy():
    languages = i18n.supported()
    languages.clear()
    assert i18n.supported()


def test_current_falls_back_to_english(monkeypatch):
    monkeypatch.setattr("memory.config_manager.get_ui_language", lambda: "invalid")
    assert i18n.current() == "en"


def test_translation_preserves_missing_placeholders(monkeypatch):
    monkeypatch.setattr(i18n, "current", lambda: "en")
    assert i18n.t("Missing {name}") == "Missing {name}"


def test_explicit_language_falls_back_to_english():
    assert i18n.in_language("Unknown phrase", "unsupported") == "Unknown phrase"


def test_non_string_is_returned_unchanged():
    assert i18n.t(None) is None
