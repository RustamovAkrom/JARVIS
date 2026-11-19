"""Runtime singleton for the routine action and scheduler."""
from __future__ import annotations
from threading import RLock

_engine = None
_lock = RLock()

def set_engine(engine) -> None:
    global _engine
    with _lock: _engine = engine

def get_engine():
    with _lock: return _engine
