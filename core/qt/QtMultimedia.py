"""QtMultimedia from whichever binding core/qt chose. See core/qt/__init__.py."""
from core.qt import BINDING as _BINDING

# Keep concrete imports visible to Nuitka/PyInstaller dependency analysis.
if _BINDING == "PySide6":
    from PySide6 import QtMultimedia as _real
else:
    from PyQt6 import QtMultimedia as _real

__all__ = [name for name in dir(_real) if not name.startswith("_")]


def __getattr__(name: str):
    return getattr(_real, name)
