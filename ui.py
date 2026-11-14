"""Compatibility facade for the native JARVIS desktop UI.

Existing imports such as ``from ui import JarvisUI`` continue to resolve while
the implementation is organized into the ``gui`` package.
"""

from gui.app import main_window as _implementation
from gui.app.main_window import *  # noqa: F401,F403
from gui.app.main_window import __all__


def __getattr__(name: str):
    """Forward legacy private imports while components are being extracted."""
    try:
        return getattr(_implementation, name)
    except AttributeError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None


def __dir__():
    return sorted(set(globals()) | set(dir(_implementation)))
