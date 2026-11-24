"""Regression tests for the independent monochrome interface themes."""
from PyQt6.QtCore import QSettings

from gui.themes.tokens import C, DEFAULT_UI_COLOR, apply_ui_accent, apply_ui_theme


def test_dark_theme_uses_monochrome_surfaces_and_foreground():
    assert apply_ui_accent(DEFAULT_UI_COLOR)
    assert apply_ui_theme("dark")
    assert {C.BG, C.PANEL, C.PANEL2, C.PANEL3, C.DARK, C.BAR_BG} == {"#000000"}
    assert {C.TEXT, C.TEXT_DIM, C.TEXT_MED, C.WHITE, C.PRI} == {"#ffffff"}
    assert {C.BORDER, C.BORDER_B, C.BORDER_A, C.BORDER_H} == {"#ffffff"}


def test_light_theme_uses_white_surfaces_and_dark_foreground():
    assert apply_ui_accent(DEFAULT_UI_COLOR)
    assert apply_ui_theme("light")
    assert {C.BG, C.PANEL, C.PANEL2, C.PANEL3, C.DARK, C.BAR_BG} == {"#ffffff"}
    assert {C.TEXT, C.TEXT_DIM, C.TEXT_MED, C.WHITE, C.PRI} == {"#000000"}
    assert {C.BORDER, C.BORDER_B, C.BORDER_A, C.BORDER_H} == {"#000000"}


def test_custom_accent_does_not_recolor_monochrome_surfaces():
    assert apply_ui_accent(DEFAULT_UI_COLOR)
    assert apply_ui_theme("dark")
    assert apply_ui_accent("#7c5cff")
    assert C.ACCENT == "#7c5cff"
    assert C.BG == "#000000"
    assert C.TEXT == "#ffffff"
    assert C.BORDER == "#ffffff"
    assert C.PANEL == C.PANEL2 == C.PANEL3 == "#000000"
    assert C.PRI == "#7c5cff"


def test_legacy_black_and_white_presets_select_theme_modes():
    assert apply_ui_accent("#ffffff")
    assert C.BG == "#ffffff"
    assert C.TEXT == "#000000"
    assert C.PANEL == C.PANEL2 == C.PANEL3 == "#ffffff"
    assert apply_ui_accent("#000000")
    assert C.BG == "#000000"
    assert C.TEXT == "#ffffff"


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
