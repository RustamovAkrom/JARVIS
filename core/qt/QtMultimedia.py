"""QtMultimedia from whichever binding core/qt chose. See core/qt/__init__.py.
"""
from core.qt import BINDING as _BINDING
from core.platform_support import choose_qt_audio_backend

# Before Qt Multimedia picks a sound backend (it crashes on a PipeWire
# library with no PipeWire server; see platform_support).
choose_qt_audio_backend()

# Spelled out rather than imported by name at runtime, so a compiler that
# follows imports (Nuitka) sees which Qt modules the app needs.
if _BINDING == "PySide6":
    from PySide6 import QtMultimedia as _real
else:
    from PyQt6 import QtMultimedia as _real

__all__ = [n for n in dir(_real) if not n.startswith("_")]


def __getattr__(name):
    return getattr(_real, name)
