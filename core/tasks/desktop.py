"""core/tasks/desktop.py — the task engine as the desktop app runs it.

Where tasks and reports live on this computer, and how a finished task reaches
the person: a line in the activity log, the result in the content panel, and a
sentence spoken through the same channel plugins use (player.request_say), so
the assistant says it in the user's own language rather than reading English
out of this file.

main.py calls attach(ui) once the interface exists, which is also what resumes
anything a restart interrupted. The background_tasks tool calls ensure(), so a
test harness or a plugin without main.py still gets an engine.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent.parent
_lock = threading.Lock()
_engine = None
_player = None


def tasks_dir() -> Path:
    return _BASE / "memory" / "tasks"


def reports_dir() -> Path:
    return _BASE / "reports"


def agents_dir() -> Path:
    return _BASE / "agents"


def _max_parallel() -> int:
    try:
        from memory.config_manager import load_api_keys
        return max(1, min(4, int(load_api_keys().get("tasks_max_parallel") or 2)))
    except Exception:
        return 2


def _migrate_legacy_background_tasks(engine) -> None:
    """Import the retired research-only JSON store once, without discarding it."""
    from core.runtime_paths import PATHS
    from core.tasks.store import new_id

    legacy_path = PATHS.databases_dir / "background_tasks.json"
    if not legacy_path.is_file():
        return
    try:
        payload = json.loads(legacy_path.read_text(encoding="utf-8"))
        rows = payload.get("tasks", []) if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            raise ValueError("legacy task store has no tasks list")
        profiles = engine.agents()
        profile = profiles.get("researcher") or profiles.get("general")
        imported = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            source_id = str(row.get("id") or "").strip()
            query = " ".join(str(row.get("query") or "").split())
            if not source_id or not query:
                continue
            already_imported = any(
                task.get("legacy_source_id") == source_id
                for task in engine.store.all()
            )
            if already_imported:
                continue
            old_status = str(row.get("status") or "queued").lower()
            status = old_status if old_status in {"done", "failed", "cancelled"} else "queued"
            tid = new_id()
            created = float(row.get("created") or time.time())
            finished = float(row.get("updated") or 0) if status in {
                "done", "failed", "cancelled"
            } else 0.0
            task = {
                "id": tid,
                "agent": profile.name if profile else "general",
                "title": query[:70],
                "task": query,
                "workspace": str(engine.store.workdir(tid)),
                "origin": "legacy-background-research",
                "legacy_source_id": source_id,
                "deliver": "panel",
                "status": status,
                "created": created,
                "started": created if old_status == "running" else 0.0,
                "finished": finished,
                "not_before": 0.0,
                "budget": {
                    "minutes": profile.minutes if profile else 10,
                    "rounds": profile.rounds if profile else 40,
                },
                "used": {"rounds": 0, "seconds": 0.0},
                "attempts": 1 if old_status == "running" else 0,
                "quota_until": 0.0,
                "wrap_up": False,
                "notes_in": [],
                "events": [],
                "files": [],
                "result": str(row.get("result") or "")[:40_000],
                "report": "",
                "report_path": "",
                "report_status": "not_available" if status == "done" else "",
                "report_error": "",
                "error": str(row.get("error") or "")[:1000],
            }
            engine.store.create(task)
            engine._event(
                tid,
                "state",
                "imported from legacy background research; "
                + ("queued to resume" if status == "queued" else status),
            )
            imported += 1
        # Retain the old file as a backup after every record has been imported.
        backup = legacy_path.with_name("background_tasks.json.migrated")
        if not backup.exists():
            legacy_path.replace(backup)
        else:
            # A prior backup exists; keep the source file if its contents differ.
            if backup.read_bytes() == legacy_path.read_bytes():
                legacy_path.unlink()
        if imported:
            print(f"[Tasks] Imported {imported} legacy background research task(s).")
    except Exception as exc:
        print(f"[Tasks] Legacy background task migration was not completed: {exc}")


def ensure(player=None):
    global _engine, _player
    with _lock:
        if player is not None:
            _player = player
        if _engine is None:
            from core.tasks.engine import TaskEngine
            _engine = TaskEngine(
                tasks_dir(),
                reports_dir(),
                [agents_dir()],
                notify=_notify,
                max_parallel=_max_parallel(),
                autostart=False,
            )
            _migrate_legacy_background_tasks(_engine)
            _engine.start_scheduler()
        return _engine


def attach(player):
    return ensure(player)


def engine():
    return _engine


def shutdown(wait: float = 5.0) -> bool:
    """Stop the singleton TaskEngine and report whether workers have exited."""
    with _lock:
        current = _engine
    if current is None:
        return True
    current.shutdown(wait=wait)
    # Keep the singleton reference: a bounded shutdown must not permit another
    # engine to start beside workers that are still blocked in provider/tool I/O.
    workers_alive = any(thread.is_alive() for thread in current._workers.values())
    scheduler = current._thread
    return not workers_alive and not (scheduler and scheduler.is_alive())


def _call(method: str, *args) -> None:
    fn = getattr(_player, method, None)
    if callable(fn):
        try:
            fn(*args)
        except Exception as e:
            print(f"[Tasks] {method} failed: {e}")


def _notify(event: str, task: dict | None, text: str) -> None:
    if not task:
        return
    title = task.get("title") or ""
    deliver = task.get("deliver") or "voice"
    if event == "started":
        _call("write_log", f"SYS: Background task started — {title}")
    elif event == "done":
        _call("write_log", f"SYS: Background task finished — {title}")
        if deliver == "silent":
            return
        report = task.get("report") or ""
        report_error = task.get("report_error") or ""
        body = f"{title}\n\n{(task.get('result') or '').strip()}"
        if report:
            body += f"\n\n— Full report: {report}"
        elif task.get("report_status") == "failed":
            body += f"\n\n— Report file could not be saved: {report_error or 'unknown error'}"
        _call("show_content", "BACKGROUND TASK", body[:4000])
        if deliver == "voice":
            from core import outcome
            delivery_note = (
                "The full result is on the screen and saved as a report."
                if report
                else "The result is on the screen, but the report file could not be saved."
            )
            _call("request_say",
                  "[BACKGROUND TASK FINISHED] A task the user gave you earlier is done: "
                  f"'{(task.get('task') or '')[:200]}'. Tell the user in one or two sentences "
                  f"what came of it and that {delivery_note} Speak in the user's language. "
                  "Do not call any tool for this.\n"
                  + outcome.untrusted("task-result", (task.get("result") or "")[:3000]))
    elif event == "failed":
        _call("write_log", f"ERR: Background task failed — {title}: {(task.get('error') or '')[:160]}")
        if deliver != "silent":
            _call("request_say",
                  f"[BACKGROUND TASK FAILED] The background task '{title[:120]}' could not finish: "
                  f"{(task.get('error') or 'unknown reason')[:200]}. Tell the user in one short "
                  f"sentence, in their language. Do not call any tool for this.")
    elif event == "waiting":
        _call("write_log", f"SYS: Background task waiting for a model (quota) — {title}")
    elif event == "cancelled":
        _call("write_log", f"SYS: Background task cancelled — {title}")
