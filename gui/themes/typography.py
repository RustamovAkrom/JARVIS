"""Platform-aware type system for native Qt interfaces."""

import platform

from PyQt6.QtGui import QFont

_OS = platform.system()
_SYSTEM_UI = (
    "Segoe UI"
    if _OS == "Windows"
    else ("Helvetica Neue" if _OS == "Darwin" else "Noto Sans")
)
_SYSTEM_MONO = (
    "Consolas"
    if _OS == "Windows"
    else ("Menlo" if _OS == "Darwin" else "DejaVu Sans Mono")
)
FONT_UI = "Inter"
FONT_MONO = "JetBrains Mono"
R_L, R_S = 16, 8


class TypeScale:
    """Stable pixel sizes for UI text; diagnostic output may use mono fonts."""

    MICRO = 10  # dense metadata, timestamps
    CAPTION = 11
    SECONDARY = 12
    BODY = 14
    SECTION = 13
    TITLE = 20
    METRIC = 22
    DISPLAY = 28  # hero headings, onboarding titles, empty-state headlines


class Weights:
    """Named QFont weights so call sites read as design intent."""

    REGULAR = QFont.Weight.Normal
    DEMIBOLD = QFont.Weight.DemiBold
    BOLD = QFont.Weight.Bold


def ui_font(pixel_size: int = TypeScale.BODY, weight=QFont.Weight.Normal) -> QFont:
    font = QFont(FONT_UI)
    font.setFamilies(["Inter", "Geist", _SYSTEM_UI])
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    return font


def mono_font(pixel_size: int = TypeScale.BODY, weight=QFont.Weight.Normal) -> QFont:
    font = QFont(FONT_MONO)
    font.setFamilies(["JetBrains Mono", "Cascadia Code", "Cascadia Mono", _SYSTEM_MONO])
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    return font
