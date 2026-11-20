"""core/tasks/store.py — tasks on disk, one JSON file each, written atomically.

A task is a small `<id>.json` holding what the task is and where it has got to
(the part the interface reads every couple of seconds), and one or more
`<id>[.<key>].turns.json` files holding model conversations — its checkpoints,
read only by the worker. Keeping them apart means listing tasks never parses a
conversation full of web pages. A research task keeps one conversation per
sub-question, hence the key.

Written through a temporary file and a rename, so a crash or a power cut
mid-write leaves the previous version rather than half of a new one.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import tempfile
import threading
import time
from pathlib import Path

_MAX_EVENTS = 200
_KEEP_FINISHED = 100


def new_id() -> str:
    return f"t{time.strftime('%m%d')}{secrets.token_hex(3)}"


def write_json(path: Path, data) -> None:
    """Atomically persist JSON with a unique temporary file per writer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=1)
            stream.flush()
            os.fsync(stream.fileno())
        # Retry transient Windows file-lock failures without losing old data.
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass

def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


class TaskStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._tasks: dict[str, dict] = {}
        for path in self.root.glob("*.json"):
            if path.name.endswith(".turns.json"):
                continue
            data = read_json(path, None)
            if isinstance(data, dict) and data.get("id"):
                self._tasks[data["id"]] = data

    # ── tasks ────────────────────────────────────────────────────────────────
    def _path(self, tid: str) -> Path:
        return self.root / f"{tid}.json"

    def workdir(self, tid: str) -> Path:
        return self.root / tid

    def create(self, task: dict) -> dict:
        with self._lock:
            self._tasks[task["id"]] = task
            write_json(self._path(task["id"]), task)
            return json.loads(json.dumps(task))

    def get(self, tid: str) -> dict | None:
        with self._lock:
            t = self._tasks.get(str(tid or ""))
            return json.loads(json.dumps(t)) if t else None

    def mutate(self, tid: str, fn) -> dict | None:
        """Change a task under the lock and write it. `fn(task)` edits in place."""
        with self._lock:
            t = self._tasks.get(tid)
            if t is None:
                return None
            fn(t)
            if len(t.get("events") or []) > _MAX_EVENTS:
                t["events"] = t["events"][-_MAX_EVENTS:]
            write_json(self._path(tid), t)
            return json.loads(json.dumps(t))

    def update(self, tid: str, **fields) -> dict | None:
        return self.mutate(tid, lambda t: t.update(fields))

    def all(self) -> list[dict]:
        with self._lock:
            return sorted((json.loads(json.dumps(t)) for t in self._tasks.values()),
                          key=lambda t: t.get("created", 0))

    # ── checkpoints ──────────────────────────────────────────────────────────
    def _turns_path(self, tid: str, key: str = "") -> Path:
        return self.root / (f"{tid}.{key}.turns.json" if key else f"{tid}.turns.json")

    def load_turns(self, tid: str, key: str = "") -> list:
        data = read_json(self._turns_path(tid, key), [])
        return data if isinstance(data, list) else []

    def save_turns(self, tid: str, turns: list, key: str = "") -> None:
        write_json(self._turns_path(tid, key), turns)

    def delete(self, tid: str) -> bool:
        """Delete task metadata, checkpoints and its private workspace."""
        with self._lock:
            task = self._tasks.pop(str(tid), None)
            if task is None:
                return False
            for path in [self._path(tid), self._turns_path(tid)] + list(self.root.glob(f"{tid}.*.turns.json")):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            shutil.rmtree(self.workdir(tid), ignore_errors=True)
            return True

    def delete_finished(self) -> int:
        """Delete all terminal tasks and their task-owned data."""
        with self._lock:
            ids = [tid for tid, task in self._tasks.items()
                   if task.get("status") not in ("queued", "scheduled", "running", "waiting_quota")]
        return sum(1 for tid in ids if self.delete(tid))

    # ── housekeeping ─────────────────────────────────────────────────────────
    def _prune(self) -> None:
        """Keep the newest finished tasks; their reports live on regardless."""
        finished = sorted((t for t in self._tasks.values()
                           if t.get("status") in ("done", "failed", "cancelled")),
                          key=lambda t: t.get("created", 0))
        for t in finished[:-_KEEP_FINISHED]:
            tid = t["id"]
            self._tasks.pop(tid, None)
            for p in [self._path(tid)] + list(self.root.glob(f"{tid}.*turns.json")):
                try:
                    p.unlink()
                except OSError:
                    pass
            shutil.rmtree(self.workdir(tid), ignore_errors=True)
