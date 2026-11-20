"""QtWidgets from whichever binding core/qt selected.

Keep imports routed through core.qt so plugins never load a second Qt binding
alongside the one used by the main GUI.
"""
from core.qt import BINDING as _BINDING

# Explicit imports make the selected binding visible to static packagers.
if _BINDING == "PySide6":
    from PySide6 import QtWidgets as _real
else:
    from PyQt6 import QtWidgets as _real

__all__ = [name for name in dir(_real) if not name.startswith("_")]


def __getattr__(name):
    return getattr(_real, name)
