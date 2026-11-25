"""QtCore from whichever binding core/qt chose. See core/qt/__init__.py.

`Signal`, `Slot` and `Property` are PySide6's names and are what the app uses;
under PyQt6 they are its pyqtSignal/pyqtSlot/pyqtProperty.
"""
from core.qt import BINDING as _BINDING

# Spelled out rather than imported by name at runtime, so a compiler that
# follows imports (Nuitka) sees which Qt modules the app needs.
if _BINDING == "PySide6":
    from PySide6 import QtCore as _real
    Signal, Slot, Property = _real.Signal, _real.Slot, _real.Property
else:
    from PyQt6 import QtCore as _real
    Signal, Slot, Property = _real.pyqtSignal, _real.pyqtSlot, _real.pyqtProperty

__all__ = [n for n in dir(_real) if not n.startswith("_")] + ["Signal", "Slot", "Property"]


def __getattr__(name):
    return getattr(_real, name)
