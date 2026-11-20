"""QtGui from whichever binding core/qt chose. See core/qt/__init__.py.
"""
from core.qt import BINDING as _BINDING

# Spelled out rather than imported by name at runtime, so a compiler that
# follows imports (Nuitka) sees which Qt modules the app needs.
if _BINDING == "PySide6":
    from PySide6 import QtGui as _real
else:
    from PyQt6 import QtGui as _real

__all__ = [n for n in dir(_real) if not n.startswith("_")]


def __getattr__(name):
    return getattr(_real, name)
