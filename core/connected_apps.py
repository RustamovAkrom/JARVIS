"""Connected Apps core service and task agent.

This module provides the stable backend contract used by plugins/connected_apps.py.
Integrations register adapters; the core never imports app-specific SDKs and never
stores OAuth tokens or passwords. Adapters own credential storage and API calls.

Adapter contract:
    name: str
    display_name: str
    execute(task: str, cancel_event: threading.Event) -> str

Optional:
    requires_confirmation(task: str) -> bool

The module is intentionally dependency-free and safe to import when no adapters
are installed. Connected Apps remains optional.
"""
from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass
from typing import Callable, Protocol

from core import outcome


class ConnectedAppAdapter(Protocol):
    name: str
    display_name: str

    def execute(self, task: str, cancel_event: threading.Event) -> str: ...


@dataclass
class AppTask:
    app: str
    task: str
    cancel_event: threading.Event
    thread: threading.Thread | None = None
    started_at: float = 0.0


class ConnectedAppsService:
    """Thread-safe adapter registry."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._adapters: dict[str, ConnectedAppAdapter] = {}

    @staticmethod
    def normalize(name: str) -> str:
        return " ".join(str(name or "").strip().casefold().split())

    def register(self, adapter: ConnectedAppAdapter, *, replace: bool = False) -> None:
        key = self.normalize(getattr(adapter, "name", ""))
        if not key:
            raise ValueError("Connected app adapter must define a non-empty name")
        if not callable(getattr(adapter, "execute", None)):
            raise TypeError(f"Connected app adapter '{key}' must implement execute()")
        with self._lock:
            if key in self._adapters and not replace:
                raise ValueError(f"Connected app adapter '{key}' is already registered")
            self._adapters[key] = adapter

    def unregister(self, name: str) -> bool:
        key = self.normalize(name)
        with self._lock:
            return self._adapters.pop(key, None) is not None

    def get(self, name: str) -> ConnectedAppAdapter | None:
        key = self.normalize(name)
        with self._lock:
            adapter = self._adapters.get(key)
            if adapter is not None:
                return adapter
            # Friendly lookup by display name, without accepting ambiguous matches.
            matches = [
                item for item in self._adapters.values()
                if self.normalize(getattr(item, "display_name", "")) == key
            ]
            return matches[0] if len(matches) == 1 else None

    def list_apps(self) -> list[dict]:
        with self._lock:
            adapters = tuple(self._adapters.values())
        return [
            {
                "name": str(adapter.name),
                "display_name": str(getattr(adapter, "display_name", adapter.name)),
            }
            for adapter in sorted(adapters, key=lambda item: str(item.display_name).casefold())
        ]

    def summary(self) -> str:
        apps = self.list_apps()
        if not apps:
            return ""
        return ", ".join(item["display_name"] for item in apps)

    def clear(self) -> None:
        with self._lock:
            self._adapters.clear()


class ConnectedAppsAgent:
    """Runs app tasks in daemon workers and supports cooperative cancellation."""

    def __init__(self, app_service: ConnectedAppsService) -> None:
        self._service = app_service
        self._lock = threading.RLock()
        self._tasks: dict[str, AppTask] = {}

    def start(
        self,
        app: str,
        task: str,
        *,
        say: Callable[[str], None] | None = None,
    ) -> str:
        app_name = str(app or "").strip()
        task_text = str(task or "").strip()
        if not app_name:
            return outcome.ask("connected_apps", "which app?")
        if not task_text:
            return outcome.ask("connected_apps", "what should I do in that app?")
        adapter = self._service.get(app_name)
        if adapter is None:
            available = self._service.summary() or "no integrations are installed"
            return outcome.failed(
                "connected_apps",
                f"'{app_name}' is not connected or supported. Available integrations: {available}.",
                app=app_name,
            )

        key = self._service.normalize(app_name)
        with self._lock:
            current = self._tasks.get(key)
            if current is not None and current.thread is not None and current.thread.is_alive():
                return outcome.failed("connected_apps", f"A task is already running for {app_name}.",
                                      app=app_name)
            state = AppTask(app=app_name, task=task_text, cancel_event=threading.Event())
            worker = threading.Thread(
                target=self._worker,
                args=(key, state, adapter, say),
                daemon=True,
                name=f"connected-app-{key[:24]}",
            )
            state.thread = worker
            state.started_at = time.monotonic()
            self._tasks[key] = state
            try:
                worker.start()
            except Exception as exc:
                self._tasks.pop(key, None)
                return outcome.failed(
                    "connected_apps.start",
                    f"Could not start app task: {type(exc).__name__}: {exc}",
                    app=app_name,
                )

        return outcome.done("connected_apps.start", app=app_name, task="started",
                           instruction="Tell the user the task has started in the background.")

    def _worker(self, key: str, state: AppTask, adapter: ConnectedAppAdapter,
                say: Callable[[str], None] | None) -> None:
        try:
            if state.cancel_event.is_set():
                return
            result = adapter.execute(state.task, state.cancel_event)
            if state.cancel_event.is_set():
                return
            message = str(result or "The app task completed without a result.")
            if say:
                try:
                    say(message)
                except Exception:
                    pass
        except Exception as exc:
            print(f"[ConnectedApps] task failed ({state.app}): {type(exc).__name__}: {exc}")
            traceback.print_exc()
            if say and not state.cancel_event.is_set():
                try:
                    say(f"Connected Apps task failed in {state.app}: {exc}")
                except Exception:
                    pass
        finally:
            with self._lock:
                if self._tasks.get(key) is state:
                    self._tasks.pop(key, None)

    def stop(self, app: str = "") -> str:
        key = self._service.normalize(app)
        with self._lock:
            if app:
                task = self._tasks.get(key)
                if task is None:
                    # Try display-name resolution.
                    adapter = self._service.get(app)
                    if adapter is not None:
                        task = self._tasks.get(self._service.normalize(adapter.name))
            else:
                task = None
            if task is None and not app:
                tasks = list(self._tasks.values())
            elif task is None:
                return outcome.failed("connected_apps.stop", f"No running task for '{app}'.")
            else:
                tasks = [task]
            for item in tasks:
                item.cancel_event.set()
        names = ", ".join(item.app for item in tasks)
        return outcome.done("connected_apps.stop", stopped=names,
                           instruction="Tell the user cancellation was requested; it may take a moment.")

    def active_tasks(self) -> list[dict]:
        with self._lock:
            tasks = tuple(self._tasks.values())
        return [
            {"app": item.app, "task": item.task, "started_at": item.started_at,
             "cancel_requested": item.cancel_event.is_set()}
            for item in tasks
        ]


service = ConnectedAppsService()
agent = ConnectedAppsAgent(service)
