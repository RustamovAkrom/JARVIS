"""Native Connected Apps adapter platform.

Native adapters expose first-party API integrations through a stable tool contract.
They are intentionally independent from MCP transport/runtime.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class NativeTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool = True
    destructive: bool = False
    requires_confirmation: bool = False


@dataclass(frozen=True, slots=True)
class NativeAppDescriptor:
    id: str
    name: str
    description: str
    category: str
    icon: str = ""
    auth_provider: str = ""
    scopes: tuple[str, ...] = ()


class NativeAppAdapter:
    descriptor: NativeAppDescriptor
    tools: tuple[NativeTool, ...]

    def __init__(self, store):
        self.store = store

    def execute(self, connection_id: str, tool_name: str, arguments: dict[str, Any]):
        raise NotImplementedError

    def health_check(self, connection_id: str) -> bool:
        raise NotImplementedError


class NativeAppRegistry:
    def __init__(self):
        self._lock = threading.RLock()
        self._apps: dict[str, NativeAppAdapter] = {}

    def register(self, adapter: NativeAppAdapter, *, replace=False):
        app_id = adapter.descriptor.id.strip().casefold()
        if not app_id:
            raise ValueError("Native app id cannot be empty")
        with self._lock:
            if app_id in self._apps and not replace:
                raise ValueError(f"Native app already registered: {app_id}")
            self._apps[app_id] = adapter

    def get(self, app_id):
        with self._lock:
            return self._apps.get(str(app_id or "").casefold())

    def list(self):
        with self._lock:
            return [adapter.descriptor for adapter in self._apps.values()]

    def adapters(self):
        with self._lock:
            return list(self._apps.values())


native_registry = NativeAppRegistry()
