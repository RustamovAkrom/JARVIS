"""Regression tests for the independent monochrome interface themes."""
from PyQt6.QtCore import QSettings

from gui.themes.tokens import C, apply_ui_accent, apply_ui_theme


def test_dark_theme_uses_monochrome_surfaces_and_foreground():
    assert apply_ui_theme("dark")
    assert C.BG == "#000000"
    assert C.TEXT == "#f2f2f2"
    assert C.WHITE == "#ffffff"
    assert C.BORDER == "#303030"
    assert C.PRI == "#f2f2f2"


def test_light_theme_uses_white_surfaces_and_dark_foreground():
    assert apply_ui_theme("light")
    assert C.BG == "#ffffff"
    assert C.TEXT == "#171717"
    assert C.WHITE == "#111111"
    assert C.BORDER == "#d0d0d0"
    assert C.PRI == "#171717"


def test_custom_accent_does_not_recolor_monochrome_surfaces():
    assert apply_ui_theme("dark")
    assert apply_ui_accent("#7c5cff")
    assert C.ACCENT == "#7c5cff"
    assert C.BG == "#000000"
    assert C.TEXT == "#f2f2f2"
    assert C.BORDER == "#303030"
    assert C.PRI == "#7c5cff"


def test_legacy_black_and_white_presets_select_theme_modes():
    assert apply_ui_accent("#ffffff")
    assert C.BG == "#ffffff"
    assert C.TEXT == "#171717"
    assert apply_ui_accent("#000000")
    assert C.BG == "#000000"
    assert C.TEXT == "#f2f2f2"


def test_theme_mode_is_persisted_independently():
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
