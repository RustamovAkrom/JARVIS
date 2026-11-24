"""Regression tests for theme contrast, dark surfaces and custom accents."""

from PyQt6.QtCore import QSettings

from gui.themes.tokens import C, DEFAULT_UI_COLOR, apply_ui_accent, apply_ui_theme


def test_dark_theme_uses_near_black_surfaces_and_original_accent_language():
    assert apply_ui_accent(DEFAULT_UI_COLOR)
    assert apply_ui_theme("dark")
    assert C.BG == "#000000"
    assert C.PANEL != "#000000" and int(C.PANEL[1:], 16) < 0x080808
    assert C.PANEL2 != "#000000" and int(C.PANEL2[1:], 16) < 0x0a0a0a
    assert C.PRI == DEFAULT_UI_COLOR
    assert C.TEXT.startswith("#")
    assert C.BORDER != "#ffffff"
    assert C.BORDER_B != "#ffffff"


def test_light_theme_uses_white_surfaces_and_dark_text():
    assert apply_ui_theme("light")
    assert C.BG == "#ffffff"
    assert C.PANEL == "#ffffff"
    assert C.TEXT == "#1f2933"
    assert C.WHITE == "#111820"
    assert C.BORDER == "#d8dee5"


def test_custom_accent_recolours_accents_without_changing_surfaces():
    assert apply_ui_theme("dark")
    assert apply_ui_accent("#7c5cff")
    assert C.ACCENT == "#7c5cff"
    assert C.PRI == "#7c5cff"
    assert C.BG == "#000000"
    assert C.PANEL != "#000000"
    assert C.BORDER != "#ffffff"
    assert C.BORDER != "#0d3347"


def test_custom_accent_is_preserved_when_switching_theme():
    assert apply_ui_accent("#ff3355")
    assert apply_ui_theme("light")
    assert C.PRI == "#ff3355"
    assert C.BG == "#ffffff"
    assert C.TEXT == "#1f2933"
    assert apply_ui_theme("dark")
    assert C.PRI == "#ff3355"
    assert C.BG == "#000000"


def test_black_and_white_presets_select_theme_modes():
    assert apply_ui_accent(DEFAULT_UI_COLOR)
    assert apply_ui_accent("#ffffff")
    assert C.BG == "#ffffff"
    assert C.TEXT == "#1f2933"
    assert apply_ui_accent("#000000")
    assert C.BG == "#000000"
    assert C.TEXT == "#8ffcff"


def test_theme_mode_is_persisted_independently():
    assert apply_ui_accent(DEFAULT_UI_COLOR)
    settings = QSettings("JARVIS", "JARVIS")
    try:
        assert apply_ui_theme("light")
        settings.sync()
        assert settings.value("appearance/theme") == "light"
        assert apply_ui_theme("dark")
        settings.sync()
        assert settings.value("appearance/theme") == "dark"
    finally:
        settings.remove("appearance/theme")
        settings.sync()
        apply_ui_theme("dark")
        apply_ui_accent(DEFAULT_UI_COLOR)
