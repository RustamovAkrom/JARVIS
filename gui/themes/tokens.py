"""Semantic JARVIS theme tokens with independent theme and accent controls.

Dark and Light control the pure black/white canvas. The selected custom hue
is applied consistently to secondary surfaces, outlines, controls and HUD
accents without changing the canvas mode.
"""
from __future__ import annotations

import colorsys

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QColor


class C:
    BG = "#000000"
    PANEL = "#020609"
    PANEL2 = "#03090d"
    PANEL3 = "#050d12"
    BORDER = "#0d3347"
    BORDER_B = "#1a5c7a"
    BORDER_A = "#0f4060"
    BORDER_H = "#2e7893"
    PRI = "#00d4ff"
    ACCENT = "#00d4ff"
    PRI_DIM = "#007a99"
    PRI_GHO = "#001f2e"
    ACC = "#ff6b00"
    ACC2 = "#ffcc00"
    GREEN = "#00ff88"
    GREEN_D = "#00aa55"
    RED = "#ff3355"
    MUTED_C = "#ff3366"
    TEXT = "#8ffcff"
    TEXT_DIM = "#3a8a9a"
    TEXT_MED = "#5ab8cc"
    WHITE = "#d8f8ff"
    DARK = "#000000"
    BAR_BG = "#000000"
    SUCCESS = GREEN
    WARNING = ACC2
    DANGER = RED
    INFO = PRI


class Space:
    XS = 4
    SM = 8
    MD = 12
    LG = 16
    XL = 24
    XXL = 32


class Radius:
    SM = 6
    MD = 10
    LG = 14
    XL = 18
    PILL = 999


class Motion:
    INSTANT = 0
    FAST = 120
    BASE = 180
    SLOW = 260
    OUT_CUBIC = "OutCubic"
    OUT_QUART = "OutQuart"
    IN_OUT_CUBIC = "InOutCubic"


class Focus:
    RING_ALPHA = 170
    RING_WIDTH = 2
    HOVER_ALPHA = 26
    PRESSED_ALPHA = 51
    SELECTED_ALPHA = 64


DEFAULT_UI_COLOR = "#00d4ff"
_THEME_SETTINGS = QSettings("JARVIS", "JARVIS")
THEME_MODE = str(_THEME_SETTINGS.value("appearance/theme", "dark") or "dark").lower()
if THEME_MODE not in {"dark", "light"}:
    THEME_MODE = "dark"

# Custom Colour tints the canvas and every surface. Black/White presets
# switch to a separate neutral palette with monochrome foregrounds.
_DARK_SURFACES = {
    "BG": "#07120f",
    "PANEL": "#0c1d18",
    "PANEL2": "#11291f",
    "PANEL3": "#193a2d",
    "DARK": "#07120f",
    "BAR_BG": "#07120f",
}
_LIGHT_SURFACES = {
    "BG": "#f8fafc",
    "PANEL": "#ffffff",
    "PANEL2": "#f8fafc",
    "PANEL3": "#f1f5f9",
    "DARK": "#f8fafc",
    "BAR_BG": "#f8fafc",
}
_DARK_ACCENT_BASE = {
    "BORDER": "#0d3347",
    "BORDER_B": "#1a5c7a",
    "BORDER_A": "#0f4060",
    "BORDER_H": "#2e7893",
    "PRI": DEFAULT_UI_COLOR,
    "PRI_DIM": "#007a99",
    "PRI_GHO": "#001f2e",
    "TEXT": "#8ffcff",
    "TEXT_DIM": "#3a8a9a",
    "TEXT_MED": "#5ab8cc",
    "WHITE": "#d8f8ff",
}
_LIGHT_ACCENT_BASE = {
    "BORDER": "#d8dee5",
    "BORDER_B": "#b7c1cc",
    "BORDER_A": "#c6d0da",
    "BORDER_H": "#98a6b2",
    "PRI": DEFAULT_UI_COLOR,
    "PRI_DIM": "#007a99",
    "PRI_GHO": "#e8f7fb",
    "TEXT": "#1f2933",
    "TEXT_DIM": "#61707d",
    "TEXT_MED": "#45515d",
    "WHITE": "#111820",
}
_HUE_LINKED = tuple((*_DARK_SURFACES.keys(), *_DARK_ACCENT_BASE.keys()))
_STATUS_COLORS = ("ACC", "ACC2", "GREEN", "GREEN_D", "RED", "MUTED_C")
_STATUS_DEFAULTS = {key: getattr(C, key) for key in _STATUS_COLORS}
_PALETTE_DEFAULTS: dict[str, str] = {
    **_DARK_SURFACES,
    **_DARK_ACCENT_BASE,
}


def _valid_hex(value: str) -> bool:
    if not isinstance(value, str) or len(value) != 7 or not value.startswith("#"):
        return False
    try:
        int(value[1:], 16)
        return True
    except ValueError:
        return False


def _shift_hue(base_hex: str, accent_hex: str) -> str:
    base_rgb = tuple(int(base_hex[i:i + 2], 16) / 255 for i in (1, 3, 5))
    accent_rgb = tuple(int(accent_hex[i:i + 2], 16) / 255 for i in (1, 3, 5))
    base_h, sat, val = colorsys.rgb_to_hsv(*base_rgb)
    accent_h, accent_s, _ = colorsys.rgb_to_hsv(*accent_rgb)
    if accent_s < 0.08:
        # Neutral selections have no meaningful hue. Keep the base hue and
        # reduce saturation rather than reading an undefined hue variable.
        h = base_h
        sat *= 0.12
    else:
        # Use one selected hue across every semantic token. Preserve each
        # token's saturation/value for hierarchy, but remove competing hues.
        h = accent_h
    r, g, b = colorsys.hsv_to_rgb(h, sat, val)
    return "#{:02x}{:02x}{:02x}".format(
        round(r * 255), round(g * 255), round(b * 255)
    )


def _init_aliases() -> None:
    C.SUCCESS = C.GREEN
    C.WARNING = C.ACC2
    C.DANGER = C.RED
    C.INFO = C.PRI


def _tint_surface(base_hex: str, accent_hex: str, mode: str) -> str:
    """Apply the selected hue to a surface while preserving its luminance level."""
    base_rgb = tuple(int(base_hex[i:i + 2], 16) / 255 for i in (1, 3, 5))
    accent_rgb = tuple(int(accent_hex[i:i + 2], 16) / 255 for i in (1, 3, 5))
    _, _, value = colorsys.rgb_to_hsv(*base_rgb)
    hue, accent_saturation, _ = colorsys.rgb_to_hsv(*accent_rgb)
    if accent_saturation < 0.08:
        return base_hex
    # Keep the dark/light luminance ladder intact; only tint its hue.
    saturation = min(0.78 if mode == "dark" else 0.20, accent_saturation * (0.78 if mode == "dark" else 0.55))
    red, green, blue = colorsys.hsv_to_rgb(hue, saturation, value)
    return "#{:02x}{:02x}{:02x}".format(
        round(red * 255), round(green * 255), round(blue * 255)
    )


def _apply_palette() -> None:
    surfaces = _DARK_SURFACES if THEME_MODE == "dark" else _LIGHT_SURFACES
    accent_base = _DARK_ACCENT_BASE if THEME_MODE == "dark" else _LIGHT_ACCENT_BASE
    neutral_accent = (
        colorsys.rgb_to_hsv(*(
            int(_ACTIVE_ACCENT[i:i + 2], 16) / 255 for i in (1, 3, 5)
        ))[1] < 0.08
    )

    if neutral_accent:
        # Black and White presets are genuinely monochrome, not a tinted
        # custom palette with a black/white canvas.
        surfaces = (
            {
                "BG": "#000000", "PANEL": "#000000",
                "PANEL2": "#000000", "PANEL3": "#000000",
                "DARK": "#000000", "BAR_BG": "#000000",
            }
            if THEME_MODE == "dark"
            else {
                "BG": "#ffffff", "PANEL": "#ffffff",
                "PANEL2": "#ffffff", "PANEL3": "#ffffff",
                "DARK": "#ffffff", "BAR_BG": "#ffffff",
            }
        )

    # In Custom Colour mode every major surface, including the canvas, is
    # tinted from the selected hue. Black/White presets use neutral surfaces.
    for key, value in surfaces.items():
        if not neutral_accent:
            value = _tint_surface(value, _ACTIVE_ACCENT, THEME_MODE)
        setattr(C, key, value)

    for key, base in accent_base.items():
        if neutral_accent:
            if THEME_MODE == "dark":
                neutral = {
                    "BORDER": "#ffffff", "BORDER_B": "#ffffff",
                    "BORDER_A": "#ffffff", "BORDER_H": "#ffffff",
                    "PRI": "#ffffff", "PRI_DIM": "#ffffff",
                    "PRI_GHO": "#000000", "TEXT": "#ffffff",
                    "TEXT_DIM": "#ffffff", "TEXT_MED": "#ffffff",
                    "WHITE": "#ffffff",
                }
            else:
                neutral = {
                    "BORDER": "#000000", "BORDER_B": "#000000",
                    "BORDER_A": "#000000", "BORDER_H": "#000000",
                    "PRI": "#000000", "PRI_DIM": "#000000",
                    "PRI_GHO": "#ffffff", "TEXT": "#000000",
                    "TEXT_DIM": "#000000", "TEXT_MED": "#000000",
                    "WHITE": "#000000",
                }
            value = neutral[key]
        elif key == "PRI":
            value = _ACTIVE_ACCENT
        else:
            value = _shift_hue(base, _ACTIVE_ACCENT)
        setattr(C, key, value)

    # Semantic status colours keep their meaning in Custom Colour mode:
    # green = healthy, amber = thinking/warning, red = muted/error.
    # Only the explicit Black/White presets collapse them to monochrome.
    for key, base in _STATUS_DEFAULTS.items():
        if neutral_accent:
            value = "#ffffff" if THEME_MODE == "dark" else "#000000"
        else:
            value = base
        setattr(C, key, value)
    _init_aliases()


_ACTIVE_ACCENT = DEFAULT_UI_COLOR


def apply_ui_accent(accent_hex: str) -> bool:
    """Apply a custom accent without changing the selected light/dark mode.

    The custom colour is independent from the Dark/White canvas mode.
    Any valid hex value changes the interface hue without switching modes.
    """
    global _ACTIVE_ACCENT
    selected = (accent_hex or "").strip().lower()
    if not _valid_hex(selected):
        return False
    if selected == "#f2f2f2":
        # Migrate the temporary neutral default written by the previous
        # monochrome implementation back to JARVIS cyan. It was not a
        # deliberate custom accent and otherwise desaturates the full palette.
        selected = DEFAULT_UI_COLOR
    _ACTIVE_ACCENT = selected
    C.ACCENT = selected
    _apply_palette()
    return True


def apply_ui_theme(mode: str) -> bool:
    """Switch between near-black and white surfaces, preserving custom accent."""
    global THEME_MODE
    selected = str(mode or "").strip().lower()
    if selected in {"black", "dark"}:
        selected = "dark"
    elif selected in {"white", "light"}:
        selected = "light"
    else:
        return False
    THEME_MODE = selected
    _THEME_SETTINGS.setValue("appearance/theme", selected)
    _THEME_SETTINGS.sync()
    _apply_palette()
    return True


def current_theme_mode() -> str:
    """Return the currently active appearance mode."""
    return THEME_MODE


def current_palette() -> dict[str, str]:
    """Snapshot all dynamic palette tokens for safe live stylesheet replacement."""
    return {
        key: getattr(C, key)
        for key in (*_HUE_LINKED, *_STATUS_COLORS)
    }


def rgba(hex_color: str, alpha: int) -> str:
    color = QColor(hex_color)
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"


def mix(base: str, overlay: str, ratio: float = 0.5) -> str:
    b, o = QColor(base), QColor(overlay)
    r = max(0.0, min(1.0, ratio))
    return "#{:02x}{:02x}{:02x}".format(
        round(b.red() + (o.red() - b.red()) * r),
        round(b.green() + (o.green() - b.green()) * r),
        round(b.blue() + (o.blue() - b.blue()) * r),
    )


def qcol(hex_color: str, alpha: int = 255) -> QColor:
    color = QColor(hex_color)
    color.setAlpha(alpha)
    return color


_apply_palette()
