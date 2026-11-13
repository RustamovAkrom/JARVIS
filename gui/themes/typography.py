"""Platform-aware base font families for the native Qt interface."""

import platform

_OS = platform.system()
FONT_UI = (
    "Segoe UI"
    if _OS == "Windows"
    else ("Helvetica Neue" if _OS == "Darwin" else "Noto Sans")
)
FONT_MONO = (
    "Consolas"
    if _OS == "Windows"
    else ("Menlo" if _OS == "Darwin" else "DejaVu Sans Mono")
)
R_L, R_S = 16, 8
