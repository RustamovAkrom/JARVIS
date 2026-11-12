"""Auto-discovered tool for starting and managing background research."""

from __future__ import annotations

from core import outcome
from core.background_tasks import get_background_task_manager


def background_tasks(parameters: dict, player=None) -> str:
    args = dict(parameters or {})
    action = str(args.get("action") or "list").strip().lower()
    try:
        manager = get_background_task_manager(player)
        if action == "start":
            task = manager.start(args.get("query", ""))
            return outcome.started(
                "background_tasks",
                task_id=task["id"],
                status=task["status"],
                note="the research report will appear in the content panel",
            )

        if action == "list":
            rows = manager.list()
            if not rows:
                return "No background research tasks."
            return "\n".join(
                f"{row['id']} [{row['status']}] {row['query'][:100]}" for row in rows
            )

        task_id = str(args.get("task_id") or "").strip()
        if not task_id:
            return outcome.failed("background_tasks", "provide a task_id")
        task = manager.get(task_id)
        if task is None:
            return outcome.failed(
                "background_tasks", "no task with that id", task_id=task_id
            )

        if action == "status":
            return outcome.done(
                "background_tasks",
                task_id=task_id,
                status=task["status"],
                query=task["query"],
                error=task.get("error") or None,
            )
        if action == "result":
            if task["status"] != "done":
                if task["status"] == "failed":
                    return outcome.failed(
                        "background_tasks",
                        task.get("error") or "research failed",
                        task_id=task_id,
                    )
                if task["status"] == "cancelled":
                    return outcome.failed(
                        "background_tasks",
                        "the task was cancelled",
                        task_id=task_id,
                    )
                return outcome.unverified(
                    "background_tasks",
                    "the research is not finished yet",
                    task_id=task_id,
                    status=task["status"],
                )
            return (
                outcome.done("background_tasks", task_id=task_id, status="done")
                + "\n"
                + outcome.untrusted("background-research", task["result"])
            )
        if action == "cancel":
            if not manager.cancel(task_id):
                return outcome.failed(
                    "background_tasks",
                    "the task is not running",
                    task_id=task_id,
                )
            return outcome.done(
                "background_tasks", task_id=task_id, cancellation_requested=True
            )
        return outcome.failed("background_tasks", f"unknown action '{action}'")
    except (OSError, RuntimeError, ValueError) as exc:
        return outcome.failed("background_tasks", str(exc))


TOOL = {
    "name": "background_tasks",
    "description": (
        "Run longer, read-only web research in the background while the conversation continues. "
        "Use start with the user's research question; the completed report appears in the content "
        "panel. Use list, status, result, or cancel to manage tasks. For a quick answer, use "
        "web_search instead. This tool cannot control the computer or modify files."
    ),
    "tier": "core",
    "brief": "Start, list, check, retrieve, or cancel a background web research task.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["start", "list", "status", "result", "cancel"],
            },
            "query": {
                "type": "STRING",
                "description": "A self-contained research question for start.",
            },
            "task_id": {"type": "STRING"},
        },
        "required": ["action"],
    },
    "handler": background_tasks,
}
