"""
core/lazyimport.py — third-party imports that are paid only if they are used.

THE PROBLEM THIS EXISTS FOR
    `action_loader` executes every file in `actions/` on the launch path, and
    `plugin_loader` does the same for `plugins/`. A module-scope
    `import pyautogui` is therefore paid at EVERY start, whether or not the
    user ever asks for a keystroke.

    Measured in the real boot order: `pyautogui` 290 ms, `playwright` 198 ms —
    together 78% of all module import time on the boot path, for two libraries
    a session that only ever answers questions never touches.

    Mark LV already solved this once, for `plugins/youtube_video.py`, and left
    a note saying whose bill the rest was: "pyautogui's 271 ms is real, but it
    is the bundled actions' bill, not this plugin's." This module is how
    `actions/` pays it.

WHY A PROXY OBJECT RATHER THAN AN ACCESSOR FUNCTION
    youtube_video defers with small accessors — `_requests()` returns the
    module — which is the clearest thing to read when there are two call sites.
    `computer_settings.py` has EIGHTY-SIX. Rewriting each one is eighty-six
    chances to miss one, and the one that is missed is not a slow launch: it is
    a `NameError` on a keystroke, at the moment somebody is talking.

    So the name stays a module global and what it holds changes: a `LazyModule`
    standing in for the real one, importing it on the first attribute touched.
    Every existing call site reads exactly as it did.

WHY NOT PEP 562's MODULE `__getattr__`, WHICH LOOKS LIKE THE OBVIOUS ANSWER
    Because it does not fire for the call sites that matter. A module-level
    `__getattr__` is consulted for attribute access on the module OBJECT —
    `computer_control.pyautogui` from outside. A bare `pyautogui.press(...)`
    INSIDE the module is a global-name lookup: locals, then the module's
    globals dict, then builtins. It never reaches `__getattr__`, and the name
    is not in globals, so it raises NameError.

    That was written here first, and it passed a check that read
    `cc.pyautogui` from outside — the one access path that works. It took
    calling `_clipboard_get()` for real to see it. A proxy does not have the
    problem: the name IS in globals, so the ordinary lookup finds it.

WHAT DOES NOT CHANGE
    The `_PYAUTOGUI` / `_PYPERCLIP` flags each module already had still mean
    "is it installed?", and still answer before anything is imported —
    `find_spec` reads metadata without executing the package. Every guard
    written against those flags behaves as it always did.
"""
from __future__ import annotations

import importlib
import importlib.util
from typing import Any, Callable, Optional


def installed(name: str) -> bool:
    """Is this package importable, without importing it?

    `find_spec` locates the module and stops. It is the whole of what a
    `_PYAUTOGUI = True` flag ever needed to know, at roughly a millisecond
    instead of the hundreds an import costs.

    ValueError is caught alongside ImportError because `find_spec` raises it
    for a module that is present but half-initialised — a state a crashed
    install can leave behind, and one that should read as "no" rather than
    take the launch down.
    """
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


class LazyModule:
    """Stands in for a module until something actually asks it for an attribute.

    Bind it where the import used to be, under the same name:

        _PYAUTOGUI = installed("pyautogui")

        def _configure(m):
            m.FAILSAFE = True
            m.PAUSE    = 0.05

        pyautogui = LazyModule("pyautogui", _configure)

    Every `pyautogui.press(...)` in the file then reads as it always did. The
    import happens on the first one, and on no launch that never reaches one.

    `configure` is an optional hook run on the real module — where settings
    that used to sit beside the import belong now, since pyautogui's FAILSAFE
    and PAUSE cannot be set before the module exists.

    THE HOOK RUNS ON EVERY ACCESS, NOT ONLY THE FIRST, AND THAT IS THE POINT
        `pyautogui` is one object in `sys.modules`, so `PAUSE` is shared by
        every file that touches it — and the files disagree: computer_control
        wants 0.05, send_message wants 0.06 because typing into somebody
        else's chat window wants more room than a hotkey does, and
        _whatsapp_core wants 0.0 because it does its own settling.

        Eagerly imported, the winner was whichever file was imported LAST, and
        discovery order made that send_message every time: 0.06 for everybody,
        stable and wrong for two of the three. Deferred, "last import" would
        become "first use" — a different answer on different days, depending on
        what the user happened to ask for first. Trading a wrong constant for
        an unpredictable one is not a fix.

        So the hook re-runs on each access: whoever is about to use the module
        sets it up the way that file wants, immediately before the call. All
        three now get the value they asked for, which none of them reliably had
        before. Measured at well under a microsecond per access, against UI
        automation that sleeps in tenths of a second.
    """

    __slots__ = ("_name", "_configure", "_module")

    def __init__(self, name: str, configure: Optional[Callable[[Any], None]] = None):
        # object.__setattr__ because this class's own __setattr__ forwards to
        # the wrapped module, which does not exist yet.
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_configure", configure)
        object.__setattr__(self, "_module", None)

    def _resolve(self) -> Any:
        module = object.__getattribute__(self, "_module")
        if module is None:
            module = importlib.import_module(object.__getattribute__(self, "_name"))
            object.__setattr__(self, "_module", module)
        configure = object.__getattribute__(self, "_configure")
        if configure is not None:
            configure(module)
        return module

    def __getattr__(self, attr: str) -> Any:
        # Reached only for names not found normally. `_name`, `_configure`,
        # `_module` and `_resolve` all resolve before this, so no recursion.
        return getattr(self._resolve(), attr)

    def __setattr__(self, attr: str, value: Any) -> None:
        setattr(self._resolve(), attr, value)

    def __dir__(self):
        return dir(self._resolve())

    def __repr__(self) -> str:
        name = object.__getattribute__(self, "_name")
        loaded = object.__getattribute__(self, "_module") is not None
        return f"<LazyModule {name!r} {'loaded' if loaded else 'not yet imported'}>"
