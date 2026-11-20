"""Core action for the persistent Task Engine shared with the desktop Tasks panel."""
from __future__ import annotations

from core import outcome


def task_engine(parameters: dict, player=None) -> str:
    args = dict(parameters or {})
    action = str(args.get("action") or "start").strip().lower()
    if player is None or not callable(getattr(player, "task_engine_command", None)):
        return outcome.failed("task_engine", "Task Engine UI bridge is unavailable")
    try:
        result = player.task_engine_command(action, args)
    except Exception as exc:
        return outcome.failed("task_engine", str(exc))
    if not result.get("ok"):
        return outcome.failed("task_engine", result.get("error") or "operation failed")

    if action == "start":
        task = result.get("task") or {}
        return outcome.started(
            "task_engine",
            task_id=task.get("id", ""),
            title=task.get("title") or args.get("title") or args.get("task", ""),
            status=task.get("status", "queued"),
            note="Task is running in the shared Tasks panel. Continue with other work.",
        )
    if action == "list":
        tasks = result.get("tasks") or []
        if not tasks:
            return "Task Engine: no tasks."
        return "\\n".join(
            f"{t.get('id')} [{t.get('status')}] {t.get('title') or t.get('task', '')[:100]}"
            for t in tasks
        )
    task = result.get("task") or {}
    if action == "status":
        return outcome.done("task_engine", task_id=task.get("id"), status=task.get("status"),
                           title=task.get("title"), progress=task.get("progress"),
                           report=task.get("report") or task.get("result") or "")
    if action == "result":
        return outcome.done("task_engine", task_id=task.get("id"),
                           status=task.get("status"),
                           result=result.get("result") or task.get("report") or task.get("result") or "")
    return outcome.done("task_engine", action=action, task_id=args.get("task_id"),
                        success=bool(result.get("ok")))


TOOL = {
    "name": "task_engine",
    "description": (
        "Create and manage persistent background tasks in JARVIS's shared Task Engine. "
        "Use start for work that should continue independently while chatting; list/status/result "
        "to monitor it; steer to change instructions; finish to ask for a report; cancel to stop; "
        "retry to restart a failed task. Tasks appear in the right-side TASKS panel."
    ),
    "tier": "core",
    "brief": "Start and manage persistent background tasks.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "enum": ["start", "list", "status", "result", "steer", "finish", "cancel", "retry"]},
            "task": {"type": "STRING", "description": "Self-contained task instructions for start."},
            "title": {"type": "STRING"},
            "task_id": {"type": "STRING"},
            "agent": {"type": "STRING"},
            "minutes": {"type": "INTEGER"},
            "rounds": {"type": "INTEGER"},
            "deliver": {"type": "STRING", "enum": ["panel", "voice", "silent"]},
            "note": {"type": "STRING", "description": "New instruction for steer."},
        },
        "required": ["action"],
    },
    "handler": task_engine,
}
