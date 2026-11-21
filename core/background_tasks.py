"""Compatibility facade over the unified durable TaskEngine.

Historical imports and the old start/list/get/cancel surface remain available,
but all work is stored, scheduled, resumed and displayed by the shared Tasks
engine used by the Task Panel.
"""
from __future__ import annotations

from core.tasks import desktop


class BackgroundTaskManager:
    """Legacy method names backed by the desktop TaskEngine singleton."""

    def __init__(self, player=None):
        self.engine = desktop.ensure(player)

    def bind_player(self, player):
        desktop.attach(player)

    def start(self, query: str) -> dict:
        query = " ".join(str(query or "").split())
        if not query:
            raise ValueError("A research question is required.")
        if len(query) > 500:
            raise ValueError("A research question must be 500 characters or fewer.")
        return self.engine.start(
            query,
            agent="researcher",
            title=query,
            origin="assistant",
            deliver="voice",
        )

    def list(self, limit: int = 10) -> list[dict]:
        return [self._legacy(row) for row in self.engine.list(limit)]

    def get(self, task_id: str) -> dict | None:
        row = self.engine.get(task_id)
        return self._legacy(row) if row else None

    def cancel(self, task_id: str) -> bool:
        return self.engine.cancel(task_id)

    @staticmethod
    def _legacy(row: dict) -> dict:
        item = dict(row)
        item["query"] = row.get("task") or row.get("title") or ""
        item["result"] = row.get("result") or ""
        item["error"] = row.get("error") or ""
        return item


def shutdown_background_task_manager(timeout: float = 5.0) -> bool:
    return desktop.shutdown(wait=timeout)


def get_background_task_manager(player=None) -> BackgroundTaskManager:
    return BackgroundTaskManager(player)
