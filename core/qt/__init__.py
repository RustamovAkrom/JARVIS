"""
core/qt — the one place that knows which Qt binding is running.

    from core.qt.QtCore import QTimer, Qt, Signal, Slot
    from core.qt.QtGui import QColor
    from core.qt import isdeleted, image_buffer, BINDING

WHY PYSIDE6
    It is the binding the packaged build uses. Nuitka's PyQt6 plugin warns
    that Qt threading is not supported with it, and this app runs Qt signals
    from worker threads everywhere (the Live session, the task engine, the
    screen agent). PySide6 is also LGPL, which a closed binary may ship.

WHY PyQt6 IS STILL ACCEPTED
    An open-source install that updates its code but not its packages has
    PyQt6 and no PySide6. It keeps working: the fallback is automatic. Both
    bindings expose the same Qt 6 classes; the differences the app touches are
    the three names re-exported here (Signal, Slot, Property) and the two
    helpers below. `QT_API=pyside6` or `QT_API=pyqt6` forces one — the same
    variable matplotlib and qtpy read, so one setting governs all of them.

WHY `import PyQt6` IS REDIRECTED WHEN PYSIDE6 RUNS
    Plugins written for earlier versions say `from PyQt6.QtCore import
    pyqtSignal`. Loading real PyQt6 next to PySide6 puts two copies of Qt in one
    process, which crashes on the first widget shared between them. So while
    PySide6 is the binding, `PyQt6.*` resolves to PySide6 behind a thin module
    that also answers `pyqtSignal`/`pyqtSlot`/`pyqtProperty` and `PyQt6.sip`.
"""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import os
import sys
import types

# Jarvis is built on PyQt6 (ui.py, gui/ import it directly). Plugins that ask
# for core.qt must get the SAME binding: two Qt copies in one process crash
# on the first widget they share. So PyQt6 first here; PySide6 only when
# PyQt6 is missing (or QT_API says so).
_ORDER = ("PyQt6", "PySide6")


def _choose() -> str:
    wanted = os.environ.get("QT_API", "").strip().lower()
    order = list(_ORDER)
    for name in _ORDER:
        if name.lower() == wanted:
            order.remove(name)
            order.insert(0, name)
    for name in order:
        if importlib.util.find_spec(name) is not None:
            return name
    raise ImportError("No Qt binding found. Install PyQt6:  pip install PyQt6")


BINDING = _choose()


def module(name: str) -> types.ModuleType:
    """The binding's own module, e.g. module("QtGui")."""
    return importlib.import_module(f"{BINDING}.{name}")


def isdeleted(obj) -> bool:
    """True when the C++ object behind `obj` is gone (PyQt6's sip.isdeleted)."""
    if BINDING == "PySide6":
        import shiboken6
        return not shiboken6.isValid(obj)
    from PyQt6 import sip
    return sip.isdeleted(obj)


def image_buffer(img):
    """A QImage's pixels as a read-only buffer of `img.sizeInBytes()` bytes.

    PyQt6 hands back a sip pointer with no length until `setsize` is called;
    PySide6 hands back a memoryview that already has one.
    """
    ptr = img.constBits()
    if hasattr(ptr, "setsize"):
        ptr.setsize(img.sizeInBytes())
    return ptr


def set_uniform(program, name: str, *values) -> None:
    """QOpenGLShaderProgram.setUniformValue that means the same in both bindings.

    Python has one number type where GLSL has two, so a bare `1.0` or `0` has
    to be routed by hand. PyQt6 picks the float or int overload from the value's
    type. PySide6 does not accept `(str, float)` at all, and takes `(str, 0)` —
    silently — as the QColor overload, which sets a sampler to a colour. Going
    through the uniform's location and PySide6's explicit `setUniformValue1f` /
    `1i` makes a float a float and an int an int in either binding.
    """
    loc = program.uniformLocation(name)
    if len(values) == 1 and type(values[0]) in (int, float):
        value = values[0]
        single = getattr(program, "setUniformValue1i" if type(value) is int
                         else "setUniformValue1f", None)
        if single is not None:
            single(loc, value)
            return
    program.setUniformValue(loc, *values)


# ── PyQt6 names for plugins written against it ────────────────────────────────

_PYQT_NAMES = {"pyqtSignal": "Signal", "pyqtSlot": "Slot", "pyqtProperty": "Property"}


class _Alias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Serves `PyQt6` and `PyQt6.<X>` from PySide6."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "PyQt6" or fullname.startswith("PyQt6."):
            return importlib.util.spec_from_loader(fullname, self,
                                                   is_package=fullname == "PyQt6")
        return None

    def create_module(self, spec):
        mod = types.ModuleType(spec.name)
        name = spec.name
        if name == "PyQt6":
            mod.__path__ = []
            return mod
        sub = name.split(".", 1)[1]
        if sub == "sip":
            mod.isdeleted = isdeleted
            return mod
        real = importlib.import_module(f"PySide6.{sub}")
        extra = {}
        if sub == "QtCore":
            extra = {old: getattr(real, new) for old, new in _PYQT_NAMES.items()}

        def __getattr__(attr, _real=real, _extra=extra):
            if attr in _extra:
                return _extra[attr]
            return getattr(_real, attr)

        mod.__getattr__ = __getattr__
        mod.__all__ = [n for n in dir(real) if not n.startswith("_")] + list(extra)
        return mod

    def exec_module(self, module):
        pass


if BINDING == "PySide6" and not any(isinstance(f, _Alias) for f in sys.meta_path):
    sys.meta_path.insert(0, _Alias())
