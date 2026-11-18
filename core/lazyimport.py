"""Thread-safe lazy third-party module proxy used by JARVIS actions/plugins."""
from __future__ import annotations

import importlib
import importlib.util
import threading
from typing import Any, Callable, Optional


def installed(name: str) -> bool:
    """Check import availability without importing/executing the package."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


class LazyModule:
    """Import a module on first attribute access; optionally configure per use.

    The configure callback intentionally runs on every proxied access because
    some shared third-party modules (notably pyautogui) have caller-specific
    mutable settings.
    """

    __slots__ = ("_name", "_configure", "_module", "_lock")

    def __init__(self, name: str, configure: Optional[Callable[[Any], None]] = None):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("LazyModule requires a non-empty import name")
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_configure", configure)
        object.__setattr__(self, "_module", None)
        object.__setattr__(self, "_lock", threading.RLock())

    def _resolve(self) -> Any:
        lock = object.__getattribute__(self, "_lock")
        with lock:
            module = object.__getattribute__(self, "_module")
            if module is None:
                name = object.__getattribute__(self, "_name")
                module = importlib.import_module(name)
                object.__setattr__(self, "_module", module)
            configure = object.__getattribute__(self, "_configure")
            if configure is not None:
                configure(module)
            return module

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._resolve(), attr)

    def __setattr__(self, attr: str, value: Any) -> None:
        if attr in self.__slots__:
            object.__setattr__(self, attr, value)
            return
        setattr(self._resolve(), attr, value)

    def __dir__(self):
        return sorted(set(object.__dir__(self)) | set(dir(self._resolve())))

    def __repr__(self) -> str:
        name = object.__getattribute__(self, "_name")
        loaded = object.__getattribute__(self, "_module") is not None
        state = "loaded" if loaded else "not yet imported"
        return f"<LazyModule {name!r} {state}>"
