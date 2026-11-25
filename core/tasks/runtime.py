"""Application lifecycle owner for the optional Task Engine.

The live assistant owns one runtime instance. UI code receives snapshots and
commands through an adapter; this module intentionally has no Qt dependency.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from core.tasks.engine import TaskEngine


class TaskRuntime:
    """Construct, expose and shut down the TaskEngine exactly once."""

    def __init__(
        self,
        *,
        data_root: Path,
        resource_root: Path,
        user_agents_dir: Path | None = None,
        notify: Callable[[dict, str], None] | None = None,
        log: Callable[[str], None] = print,
        max_parallel: int = 2,
    ) -> None:
        tasks_root = Path(data_root) / "tasks"
        root = tasks_root / "database"
        workspaces_dir = tasks_root / "workspaces"
        reports_dir = tasks_root / "reports"
        for directory in (root, workspaces_dir, reports_dir):
            directory.mkdir(parents=True, exist_ok=True)
        shipped_agents = Path(resource_root) / "core" / "tasks" / "agents"
        agents_dirs = [shipped_agents]
        if user_agents_dir is not None:
            agents_dirs.append(Path(user_agents_dir))

        self.engine = TaskEngine(
            root=root,
            reports_dir=reports_dir,
            agents_dirs=agents_dirs,
            notify=notify,
            max_parallel=max_parallel,
            log=log,
            workspaces_dir=workspaces_dir,
            autostart=True,
        )
        self._closed = False

    def snapshot(self, limit: int = 100) -> list[dict]:
        """Return detached task snapshots suitable for UI-thread rendering."""
        return self.engine.list(limit=limit)

    def shutdown(self, wait: float = 2.0) -> None:
        if self._closed:
            return
        self._closed = True
        self.engine.shutdown(wait=wait)
