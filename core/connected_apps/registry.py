"""Application descriptor registry and catalog search."""
from __future__ import annotations

import threading
from dataclasses import asdict
from typing import Iterable

from core.connected_apps.models import AppDescriptor


class AppRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._apps: dict[str, AppDescriptor] = {}

    @staticmethod
    def normalize(value: str) -> str:
        return " ".join(str(value or "").strip().casefold().replace("_", " ").split())

    def register(self, descriptor: AppDescriptor, *, replace: bool = False) -> None:
        key = self.normalize(descriptor.id)
        if not key:
            raise ValueError("App descriptor id cannot be empty")
        with self._lock:
            if key in self._apps and not replace:
                raise ValueError(f"App descriptor already registered: {descriptor.id}")
            self._apps[key] = descriptor

    def register_many(self, descriptors: Iterable[AppDescriptor]) -> None:
        for descriptor in descriptors:
            self.register(descriptor)

    def get(self, app_id: str) -> AppDescriptor | None:
        key = self.normalize(app_id)
        with self._lock:
            exact = self._apps.get(key)
            if exact:
                return exact
            matches = [app for app in self._apps.values()
                       if self.normalize(app.name) == key]
            return matches[0] if len(matches) == 1 else None

    def remove(self, app_id: str) -> bool:
        with self._lock:
            return self._apps.pop(self.normalize(app_id), None) is not None

    def list(self) -> list[AppDescriptor]:
        with self._lock:
            return sorted(self._apps.values(), key=lambda app: app.name.casefold())

    def search(self, query: str = "", *, category: str = "") -> list[AppDescriptor]:
        q, cat = self.normalize(query), self.normalize(category)
        rows = self.list()
        if cat:
            rows = [app for app in rows if self.normalize(app.category) == cat]
        if not q:
            return rows
        return [app for app in rows if q in self.normalize(
            f"{app.id} {app.name} {app.description} {app.category}")]

    def as_dicts(self) -> list[dict]:
        return [asdict(app) for app in self.list()]


registry = AppRegistry()
