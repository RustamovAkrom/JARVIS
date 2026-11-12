"""Small cross-process-safe helpers for mutable JSON in a user profile."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Lock a sibling lock file across threads and processes."""
    lock_path = Path(path).with_name(Path(path).name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    key = str(lock_path.resolve()).casefold()
    with _guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        with lock_path.open("a+b") as stream:
            if os.name == "nt":
                import msvcrt

                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def atomic_write_text(path: Path, content: str) -> None:
    """Replace one file atomically after flushing a complete sibling temp."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temp_path = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def update_json(path: Path, update: Callable[[dict], None]) -> dict:
    """Read, update, and atomically save a JSON object under a process lock.

    Invalid existing data raises instead of being silently replaced with an
    empty configuration.
    """
    path = Path(path)
    with file_lock(path):
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"Refusing to overwrite unreadable {path.name}") from exc
            if not isinstance(data, dict):
                raise RuntimeError(f"Refusing to overwrite invalid {path.name}")
        else:
            data = {}
        update(data)
        atomic_write_text(path, json.dumps(data, indent=4, ensure_ascii=False))
        return data
