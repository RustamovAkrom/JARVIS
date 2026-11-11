"""Persistent, read-only background research for JARVIS."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Callable


_TASKS_PATH = Path.home() / ".jarvis" / "background_tasks.json"
_ACTIVE = {"queued", "running", "cancelling"}
_MAX_ACTIVE = 2
_MAX_STORED = 50
_MAX_RESULT_CHARS = 12_000


def _research(query: str) -> str:
    from actions.web_search import web_search
    from core import outcome

    raw = web_search({"query": query, "mode": "research"})
    if raw.startswith(f"status={outcome.FAILED} "):
        raise RuntimeError(outcome.unwrap(raw))
    result = outcome.unwrap(raw).strip()
    if not result:
        raise RuntimeError("The research search returned no report.")
    return result


class BackgroundTaskManager:
    """Runs bounded, read-only research requests and persists their outcomes."""

    def __init__(
        self,
        path: Path | str = _TASKS_PATH,
        search: Callable[[str], str] = _research,
        max_active: int = _MAX_ACTIVE,
    ):
        self._path = Path(path)
        self._search = search
        self._max_active = max(1, int(max_active))
        self._lock = threading.RLock()
        self._tasks = self._load()
        self._threads: dict[str, threading.Thread] = {}
        self._player = None
        self._recovered = False

        changed = False
        for task in self._tasks:
            if task.get("status") == "running":
                task["status"] = "queued"
                task["updated"] = time.time()
                changed = True
        if changed:
            self._save_locked()

    def _load(self) -> list[dict]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not read background task history: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
            raise RuntimeError("Background task history has an invalid format.")
        return [row for row in data["tasks"] if isinstance(row, dict)]

    def _save_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n",
                dir=self._path.parent, prefix=self._path.name + ".",
                suffix=".tmp", delete=False,
            ) as stream:
                temp_path = Path(stream.name)
                json.dump({"version": 1, "tasks": self._tasks}, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            temp_path.replace(self._path)
        except OSError as exc:
            if temp_path and temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
            raise RuntimeError(f"Could not save background task history: {exc}") from exc

    def bind_player(self, player) -> None:
        self._player = player
        with self._lock:
            if self._recovered:
                return
            self._recovered = True
            queued = [task["id"] for task in self._tasks if task.get("status") == "queued"]
        for task_id in queued:
            self._launch(task_id)

    def start(self, query: str) -> dict:
        query = " ".join(str(query or "").split())
        if not query:
            raise ValueError("A research question is required.")
        if len(query) > 500:
            raise ValueError("A research question must be 500 characters or fewer.")

        task = {
            "id": uuid.uuid4().hex[:10],
            "query": query,
            "status": "queued",
            "created": time.time(),
            "updated": time.time(),
            "result": "",
            "error": "",
        }
        with self._lock:
            active = sum(row.get("status") in _ACTIVE for row in self._tasks)
            if active >= self._max_active:
                raise ValueError("The background research limit is reached; wait for a task to finish.")
            self._tasks.append(task)
            while len(self._tasks) > _MAX_STORED:
                old_index = next(
                    (i for i, row in enumerate(self._tasks) if row.get("status") not in _ACTIVE),
                    None,
                )
                if old_index is None:
                    break
                self._tasks.pop(old_index)
            try:
                self._save_locked()
            except RuntimeError:
                self._tasks.remove(task)
                raise
        self._launch(task["id"])
        return dict(task)

    def _launch(self, task_id: str) -> None:
        with self._lock:
            task = self._find_locked(task_id)
            if not task or task.get("status") != "queued":
                return
            thread = self._threads.get(task_id)
            if thread and thread.is_alive():
                return
            thread = threading.Thread(
                target=self._run, args=(task_id,), daemon=True,
                name=f"jarvis-research-{task_id}",
            )
            self._threads[task_id] = thread
        thread.start()

    def _run(self, task_id: str) -> None:
        with self._lock:
            task = self._find_locked(task_id)
            if not task or task.get("status") != "queued":
                return
            task["status"] = "running"
            task["updated"] = time.time()
            query = task["query"]
            try:
                self._save_locked()
            except RuntimeError as exc:
                task["status"] = "failed"
                task["error"] = str(exc)[:1000]
                notify_error = task["error"]
            else:
                notify_error = ""
        if notify_error:
            self._notify_failure(task_id, notify_error)
            return

        try:
            result = str(self._search(query) or "").strip()
            if not result:
                raise RuntimeError("The research search returned no report.")
        except Exception as exc:
            final_status = self._finish(task_id, status="failed", error=str(exc))
            if final_status == "failed":
                self._notify_failure(task_id, str(exc))
            return

        notify_error = ""
        with self._lock:
            task = self._find_locked(task_id)
            if not task:
                return
            if task.get("status") == "cancelling":
                task["status"] = "cancelled"
                task["updated"] = time.time()
                try:
                    self._save_locked()
                except RuntimeError as exc:
                    notify_error = str(exc)
                if notify_error:
                    task["error"] = notify_error[:1000]
                completed = None
            else:
                task["status"] = "done"
                task["result"] = result[:_MAX_RESULT_CHARS]
                task["updated"] = time.time()
                try:
                    self._save_locked()
                except RuntimeError as exc:
                    task["status"] = "failed"
                    task["result"] = ""
                    task["error"] = str(exc)[:1000]
                    notify_error = task["error"]
                completed = dict(task)
        if notify_error:
            self._notify_failure(task_id, notify_error)
            return
        if completed is None:
            return
        self._notify_success(completed)

    def _finish(self, task_id: str, *, status: str, error: str = "") -> str:
        with self._lock:
            task = self._find_locked(task_id)
            if not task:
                return "missing"
            if task.get("status") == "cancelling":
                status = "cancelled"
            task["status"] = status
            task["error"] = error[:1000]
            task["updated"] = time.time()
            try:
                self._save_locked()
            except RuntimeError as exc:
                task["status"] = "failed"
                task["error"] = f"{error[:700]} (could not save task status: {exc})"[:1000]
                print(f"[BackgroundTasks] {task['error']}")
            return task["status"]

    def _notify_success(self, task: dict) -> None:
        player = self._player
        if not player:
            return
        try:
            player.show_content(
                f"RESEARCH — {task['query'][:36]}",
                task["result"],
                key=f"background-research-{task['id']}",
            )
            player.write_log(f"SYS: Background research {task['id']} completed.")
            say = getattr(player, "request_say", None)
            if callable(say):
                say("A background research task is complete. The report is available in the content panel.")
        except Exception as exc:
            print(f"[BackgroundTasks] Could not notify the UI for {task['id']}: {exc}")

    def _notify_failure(self, task_id: str, error: str) -> None:
        player = self._player
        if not player:
            return
        try:
            player.write_log(f"ERR: Background research {task_id} failed: {error[:240]}")
            say = getattr(player, "request_say", None)
            if callable(say):
                say("A background research task failed. The reason is in the JARVIS activity log.")
        except Exception as exc:
            print(f"[BackgroundTasks] Could not report failure for {task_id}: {exc}")

    def list(self, limit: int = 10) -> list[dict]:
        with self._lock:
            return [dict(row) for row in reversed(self._tasks[-max(1, limit):])]

    def get(self, task_id: str) -> dict | None:
        with self._lock:
            task = self._find_locked(task_id)
            return dict(task) if task else None

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self._find_locked(task_id)
            if not task or task.get("status") not in {"queued", "running"}:
                return False
            old_status = task["status"]
            task["status"] = "cancelled" if task["status"] == "queued" else "cancelling"
            task["updated"] = time.time()
            try:
                self._save_locked()
            except RuntimeError:
                task["status"] = old_status
                raise
            return True

    def _find_locked(self, task_id: str) -> dict | None:
        return next((row for row in self._tasks if row.get("id") == task_id), None)


_manager: BackgroundTaskManager | None = None
_manager_lock = threading.Lock()


def get_background_task_manager(player=None) -> BackgroundTaskManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = BackgroundTaskManager()
        manager = _manager
    if player is not None:
        manager.bind_player(player)
    return manager
