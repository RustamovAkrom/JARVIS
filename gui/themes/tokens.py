"""Semantic color tokens and accent palette derivation.

Kept independent of widgets so presentation code can share the same palette
without creating imports back into the legacy compatibility module.

Layered surface model
---------------------
``BG``      window canvas (deepest)
``PANEL``   raised card surface
``PANEL2``  inset / well surface (inputs, lists)
``PANEL3``  elevated surface (popovers, tooltips, headers)

Every accent-linked token participates in the hue-shift performed by
``apply_ui_accent``; status colours stay semantic and fixed.
"""

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QColor


class C:
    BG = "#00060a"
    PANEL = "#010d14"
    PANEL2 = "#010f18"
    PANEL3 = "#03141d"  # elevated surface — popovers, headers, tooltips
    BORDER = "#0d3347"
    BORDER_B = "#1a5c7a"
    BORDER_A = "#0f4060"
    BORDER_H = "#2e7893"  # hover/active border emphasis
    PRI = "#f2f2f2"
    ACCENT = "#f2f2f2"
    PRI_DIM = "#b8b8b8"
    PRI_GHO = "#242424"
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
    DARK = "#000d14"
    BAR_BG = "#011520"

    # --- semantic aliases (stable names, current values) ------------------
    SUCCESS = GREEN
    WARNING = ACC2
    DANGER = RED
    INFO = PRI


class Space:
    """Shared spacing scale (device-independent Qt pixels)."""

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
    XL = 18  # hero cards, overlays
    PILL = 999  # fully rounded (chips, toggles)


class Motion:
    """Animation durations (ms) and easing curves for UI transitions.

    Values are deliberately short: desktop UIs feel "commercial" when
    feedback is instant and transitions only smooth the change.
    """

    INSTANT = 0
    FAST = 120
    BASE = 180
    SLOW = 260
    # QEasingCurve presets used with QVariantAnimation/QPropertyAnimation
    OUT_CUBIC = "OutCubic"
    OUT_QUART = "OutQuart"
    IN_OUT_CUBIC = "InOutCubic"


class Focus:
    """Focus / state ring conventions (keyboard accessibility)."""

    RING_ALPHA = 170
    RING_WIDTH = 2
    # QSS hover overlays, expressed as alpha over the base colour
    HOVER_ALPHA = 26
    PRESSED_ALPHA = 51
    SELECTED_ALPHA = 64


# Keys tied to the accent colour – status colours (ACC, GREEN, RED…) stay fixed
_HUE_LINKED = (
    "BG",
    "PANEL",
    "PANEL2",
    "PANEL3",
    "BORDER",
    "BORDER_B",
    "BORDER_A",
    "BORDER_H",
    "PRI",
    "PRI_DIM",
    "PRI_GHO",
    "TEXT",
    "TEXT_DIM",
    "TEXT_MED",
    "WHITE",
    "DARK",
    "BAR_BG",
)
_STATUS_COLORS = ("ACC", "ACC2", "GREEN", "GREEN_D", "RED", "MUTED_C")
_STATUS_DEFAULTS = {key: getattr(C, key) for key in _STATUS_COLORS}

_PALETTE_DEFAULTS: dict[str, str] = {k: getattr(C, k) for k in _HUE_LINKED}
DEFAULT_UI_COLOR = _PALETTE_DEFAULTS["PRI"]


def apply_ui_accent(accent_hex: str) -> bool:
    """
    Re-derives the whole teal-family palette from the chosen accent colour
    (hue shift — brightness/saturation ratios are preserved, design stays intact).
    Painted elements (HUD, waveform, metrics) pick up the new colour on the next
    frame; stylesheet-based panels pick it up when they are rebuilt.
    """
    import colorsys

    accent_hex = (accent_hex or "").strip().lower()
    if not (accent_hex.startswith("#") and len(accent_hex) == 7):
        return False
    try:
        int(accent_hex[1:], 16)
    except ValueError:
        return False

    if accent_hex not in {"#000000", "#ffffff"}:
        C.ACCENT = accent_hex

    if accent_hex == "#00d4ff":
        # Migrate the former built-in cyan default to the new neutral default.
        C.ACCENT = DEFAULT_UI_COLOR
        C.PRI = "#f2f2f2" if THEME_MODE == "dark" else "#171717"
        return apply_ui_theme(THEME_MODE)

    if accent_hex == "#000000":
        # Legacy BLACK preset selects the monochrome dark theme, not an accent.
        C.ACCENT = DEFAULT_UI_COLOR
        return apply_ui_theme("dark")

    if accent_hex == "#ffffff":
        # Legacy WHITE preset selects the monochrome light theme, not an accent.
        C.ACCENT = DEFAULT_UI_COLOR
        return apply_ui_theme("light")
        # White is a neutral monochrome preset with light surfaces and dark text.
        for key, value in _PALETTE_DEFAULTS.items():
            setattr(C, key, value)
        for key, value in _STATUS_DEFAULTS.items():
            setattr(C, key, value)
        for key, value in {
            "ACC": "#b54708",
            "ACC2": "#815b00",
            "GREEN": "#137333",
            "GREEN_D": "#116329",
            "RED": "#b42318",
            "MUTED_C": "#b42318",
        }.items():
            setattr(C, key, value)
        for key, value in {
            "BG": "#f3f5f7",
            "PANEL": "#ffffff",
            "PANEL2": "#f7f9fb",
            "PANEL3": "#eef2f6",
            "BORDER": "#d8dee5",
            "BORDER_B": "#b7c1cc",
            "BORDER_A": "#c6d0da",
            "BORDER_H": "#98a6b2",
            "PRI": "#36434f",
            "PRI_DIM": "#62717f",
            "PRI_GHO": "#e8edf2",
            "TEXT": "#1f2933",
            "TEXT_DIM": "#61707d",
            "TEXT_MED": "#45515d",
            "WHITE": "#111820",
            "DARK": "#ffffff",
            "BAR_BG": "#f7f9fb",
        }.items():
            setattr(C, key, value)
        _init_aliases()
        return True

    def _hsv(h: str) -> tuple[float, float, float]:
        r = int(h[1:3], 16) / 255
        g = int(h[3:5], 16) / 255
        b = int(h[5:7], 16) / 255
        return colorsys.rgb_to_hsv(r, g, b)

    # Status colours return to their normal semantic colours for every
    # chromatic palette; only the explicit white preset is monochrome.
    for key, value in _STATUS_DEFAULTS.items():
        setattr(C, key, value)

    base_h = _hsv(_PALETTE_DEFAULTS["PRI"])[0]
    acc_h, acc_s, _av = _hsv(accent_hex)
    dh = acc_h - base_h
    grey = acc_s < 0.08  # near-grey accent → the whole theme is desaturated

    for key, hex0 in _PALETTE_DEFAULTS.items():
        h, s, v = _hsv(hex0)
        if grey:
            s *= 0.15
        r, g, b = colorsys.hsv_to_rgb((h + dh) % 1.0, s, v)
        setattr(
            C,
            key,
            "#{:02x}{:02x}{:02x}".format(
                int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5)
            ),
        )
    # The accent itself is exact; derived palette shades remain secondary.
    C.PRI = accent_hex
    C.ACCENT = accent_hex
    _init_aliases()
    apply_ui_theme(THEME_MODE)
    return True


def current_palette() -> dict[str, str]:
    """A snapshot of the accent-linked colours currently on class C."""
    return {k: getattr(C, k) for k in (*_HUE_LINKED, *_STATUS_COLORS)}


def _init_aliases() -> None:
    """Point semantic aliases at their current semantic sources.

    Called at import and after every accent switch so ``C.SUCCESS`` etc.
    always track live class values (they are plain attributes, so a plain
    rebind is enough; hue-shift never touches status colours).
    """
    C.SUCCESS = C.GREEN
    C.WARNING = C.ACC2
    C.DANGER = C.RED
    C.INFO = C.PRI


_init_aliases()


def rgba(hex_color: str, alpha: int) -> str:
    """Theme colour with alpha as a CSS ``rgba()`` string (0-255)."""
    c = QColor(hex_color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"


def mix(base: str, overlay: str, ratio: float = 0.5) -> str:
    """Blend two hex colours; ``ratio`` is the weight of ``overlay``."""
    b, o = QColor(base), QColor(overlay)
    r = max(0.0, min(1.0, ratio))
    return "#{:02x}{:02x}{:02x}".format(
        round(b.red() + (o.red() - b.red()) * r),
        round(b.green() + (o.green() - b.green()) * r),
        round(b.blue() + (o.blue() - b.blue()) * r),
    )


def qcol(hex_color: str, alpha: int = 255) -> QColor:
    """Create a QColor from a semantic token and optional opacity."""
    color = QColor(hex_color)
    color.setAlpha(alpha)
    return color


# ---------------------------------------------------------------------------
# Monochrome interface modes. Accent selection is intentionally independent:
# theme mode owns surfaces, text and borders; C.PRI remains the chosen accent.
_theme_settings = QSettings("JARVIS", "JARVIS")
THEME_MODE = str(_theme_settings.value("appearance/theme", "dark") or "dark").lower()
if THEME_MODE not in {"dark", "light"}:
    THEME_MODE = "dark"

_DARK_THEME = {
    # One continuous black canvas across HUD, sidebars, chat, footer,
    # drawers, cards, inputs, tabs and overlays. No charcoal panel layers.
    "BG": "#000000",
    "PANEL": "#000000",
    "PANEL2": "#000000",
    "PANEL3": "#000000",
    "BORDER": "#ffffff",
    "BORDER_B": "#ffffff",
    "BORDER_A": "#ffffff",
    "BORDER_H": "#ffffff",
    "PRI": "#ffffff",
    "PRI_DIM": "#ffffff",
    "PRI_GHO": "#000000",
    "TEXT": "#ffffff",
    "TEXT_DIM": "#ffffff",
    "TEXT_MED": "#ffffff",
    "WHITE": "#ffffff",
    "DARK": "#000000",
    "BAR_BG": "#000000",
}
_LIGHT_THEME = {
    # Matching white canvas with black typography and outlines.
    "BG": "#ffffff",
    "PANEL": "#ffffff",
    "PANEL2": "#ffffff",
    "PANEL3": "#ffffff",
    "BORDER": "#000000",
    "BORDER_B": "#000000",
    "BORDER_A": "#000000",
    "BORDER_H": "#000000",
    "PRI": "#000000",
    "PRI_DIM": "#000000",
    "PRI_GHO": "#ffffff",
    "TEXT": "#000000",
    "TEXT_DIM": "#000000",
    "TEXT_MED": "#000000",
    "WHITE": "#000000",
    "DARK": "#ffffff",
    "BAR_BG": "#ffffff",
}


def apply_ui_theme(mode: str) -> bool:
    """Apply the independent monochrome light/dark surface and typography theme.

    The selected UI accent is retained in C.PRI. Theme changes never overwrite
    the saved accent, status colours, or application behavior.
    """
    global THEME_MODE
    selected = str(mode or "").strip().lower()
    if selected in {"black", "dark"}:
        selected = "dark"
    elif selected in {"white", "light"}:
        selected = "light"
    else:
        return False

    palette = _DARK_THEME if selected == "dark" else _LIGHT_THEME
    for key, value in palette.items():
        setattr(C, key, value)

    # Default accent is monochrome; an explicitly selected custom accent remains.
    if getattr(C, "ACCENT", DEFAULT_UI_COLOR) == DEFAULT_UI_COLOR:
        C.PRI = "#ffffff" if selected == "dark" else "#000000"
    else:
        C.PRI = C.ACCENT
    C.SUCCESS = C.GREEN
    C.WARNING = C.ACC2
    C.DANGER = C.RED
    C.INFO = C.PRI
    THEME_MODE = selected
    _theme_settings.setValue("appearance/theme", selected)
    _theme_settings.sync()
    return True


# Restore the saved monochrome mode; dark is the first-run default.
apply_ui_theme(THEME_MODE)
