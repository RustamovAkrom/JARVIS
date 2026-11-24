"""Regression tests for theme contrast, dark surfaces and custom accents."""

import colorsys

from PyQt6.QtCore import QSettings

from gui.themes.tokens import C, DEFAULT_UI_COLOR, apply_ui_accent, apply_ui_theme, current_theme_mode


def test_dark_theme_uses_near_black_surfaces_and_original_accent_language():
    assert apply_ui_accent(DEFAULT_UI_COLOR)
    assert apply_ui_theme("dark")
    assert C.BG != "#000000" and C.BG.startswith("#")
    assert C.PANEL != C.BG and int(C.PANEL[1:], 16) < 0x303030
    assert C.PANEL2 != "#000000" and int(C.PANEL2[1:], 16) < 0x181818
    assert C.PRI == DEFAULT_UI_COLOR
    assert C.TEXT.startswith("#")
    assert C.BORDER != "#ffffff"
    assert C.BORDER_B != "#ffffff"


def test_light_theme_uses_white_surfaces_and_dark_text():
    assert apply_ui_accent("#000000")
    assert apply_ui_theme("light")
    assert C.BG == "#ffffff"
    assert C.PANEL == "#ffffff"
    assert C.TEXT == "#111111"
    assert C.WHITE == "#000000"
    assert C.BORDER == "#000000"


def test_legacy_neutral_default_migrates_to_original_jarvis_accent():
    assert apply_ui_theme("dark")
    assert apply_ui_accent("#f2f2f2")
    assert C.PRI == DEFAULT_UI_COLOR
    assert C.BORDER != "#ffffff"
    assert C.TEXT != "#ffffff"


def test_custom_accent_tints_canvas_surfaces_and_controls():
    assert apply_ui_theme("dark")
    assert apply_ui_accent("#7c5cff")
    assert C.ACCENT == "#7c5cff"
    assert C.PRI == "#7c5cff"
    assert C.BG != "#000000"
    assert C.PANEL != "#081018"
    assert C.PANEL2 != "#0c1a28"
    assert C.BORDER != "#ffffff"
    assert C.BORDER != "#0d3347"
    assert C.GREEN != "#00ff88"
    assert C.RED != "#ff3355"


def test_custom_colour_uses_one_hue_across_text_borders_and_status_tokens():
    assert apply_ui_theme("dark")
    assert apply_ui_accent("#ff3355")

    def hue(value):
        rgb = tuple(int(value[i:i + 2], 16) / 255 for i in (1, 3, 5))
        return colorsys.rgb_to_hsv(*rgb)[0]

    selected_hue = hue(C.PRI)
    for token in (C.TEXT, C.TEXT_DIM, C.BORDER, C.BORDER_B, C.GREEN, C.RED, C.ACC):
        distance = abs(hue(token) - selected_hue)
        assert min(distance, 1.0 - distance) < 0.02


def test_changing_custom_hue_updates_panel_backgrounds():
    assert apply_ui_theme("dark")
    assert apply_ui_accent("#ff3355")
    red_panel = C.PANEL
    red_canvas = C.BG
    assert apply_ui_accent("#7c5cff")
    purple_panel = C.PANEL
    purple_canvas = C.BG
    assert red_panel != purple_panel
    assert red_canvas != purple_canvas
    assert C.BG != "#000000"
    assert C.PRI == "#7c5cff"


def test_custom_accent_is_preserved_when_switching_theme():
    assert apply_ui_accent("#ff3355")
    assert apply_ui_theme("light")
    assert C.PRI == "#ff3355"
    assert C.BG != "#ffffff"
    assert C.TEXT != "#1f2933"
    assert apply_ui_theme("dark")
    assert C.PRI == "#ff3355"
    assert C.BG != "#000000"


def test_theme_mode_api_preserves_custom_colour_palette():
    assert apply_ui_accent("#ff3355")
    assert apply_ui_theme("light")
    assert C.BG != "#ffffff"
    assert C.PRI == "#ff3355"
    assert C.TEXT != "#1f2933"
    assert apply_ui_theme("dark")
    assert C.BG != "#000000"
    assert C.PRI == "#ff3355"


def test_black_white_presets_are_pure_monochrome():
    assert apply_ui_accent("#ffffff")
    assert apply_ui_theme("dark")
    assert C.BG == "#000000"
    assert C.PANEL == "#000000"
    assert C.TEXT == "#e8e8e8"
    assert C.PRI == "#ffffff"
    assert C.BORDER == "#ffffff"

    assert apply_ui_accent("#000000")
    assert apply_ui_theme("light")
    assert C.BG == "#ffffff"
    assert C.PANEL == "#ffffff"
    assert C.TEXT == "#111111"
    assert C.PRI == "#000000"
    assert C.BORDER == "#d0d0d0"


def test_custom_white_or_black_hex_does_not_switch_theme_mode():
    assert apply_ui_theme("dark")
    assert apply_ui_accent("#ffffff")
    assert C.BG == "#000000"
    assert current_theme_mode() == "dark"
    assert apply_ui_accent("#000000")
    assert C.BG == "#000000"
    assert current_theme_mode() == "dark"


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
