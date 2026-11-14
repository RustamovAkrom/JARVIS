"""Semantic color tokens and accent palette derivation.

Kept independent of widgets so presentation code can share the same palette
without creating imports back into the legacy compatibility module.
"""

from PyQt6.QtGui import QColor


class C:
    BG = "#00060a"
    PANEL = "#010d14"
    PANEL2 = "#010f18"
    BORDER = "#0d3347"
    BORDER_B = "#1a5c7a"
    BORDER_A = "#0f4060"
    PRI = "#00d4ff"
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
    DARK = "#000d14"
    BAR_BG = "#011520"


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


# Keys tied to the accent colour — status colours (ACC, GREEN, RED…) stay fixed
_HUE_LINKED = (
    "BG",
    "PANEL",
    "PANEL2",
    "BORDER",
    "BORDER_B",
    "BORDER_A",
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
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineWidgets import QWebEngineView

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

    if accent_hex == "#000000":
        # Black is a dark-theme preset: keep the readable cyan accent while
        # moving the structural palette to near-black surfaces.
        for key, value in _PALETTE_DEFAULTS.items():
            setattr(C, key, value)
        for key, value in {
            "BG": "#000103",
            "PANEL": "#030609",
            "PANEL2": "#050a10",
            "BORDER": "#101a22",
            "BORDER_B": "#1b2d3a",
            "BORDER_A": "#14232d",
            "DARK": "#000205",
            "BAR_BG": "#02070b",
        }.items():
            setattr(C, key, value)
        return True

    if accent_hex == "#ffffff":
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
            "BORDER": "#d8dee5",
            "BORDER_B": "#b7c1cc",
            "BORDER_A": "#c6d0da",
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
    return True


def current_palette() -> dict[str, str]:
    """A snapshot of the accent-linked colours currently on class C."""
    return {k: getattr(C, k) for k in (*_HUE_LINKED, *_STATUS_COLORS)}



def qcol(hex_color: str, alpha: int = 255) -> QColor:
    """Create a QColor from a semantic token and optional opacity."""
    color = QColor(hex_color)
    color.setAlpha(alpha)
    return color
