"""Persistent Connected Apps metadata and protected credentials."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from core.runtime_paths import PATHS
from core import secretstore

_SCHEMA_VERSION = 1


class ConnectedAppsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or (PATHS.config_dir / "connected_apps.json"))
        self._lock = threading.RLock()

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("schema_version") == _SCHEMA_VERSION:
                return data
        except (OSError, ValueError, TypeError):
            pass
        return {"schema_version": _SCHEMA_VERSION, "connections": [], "credentials": {}}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".connected-apps-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
        finally:
            try:
                os.unlink(temp_name)
            except OSError:
                pass

    def list_connections(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._read()["connections"])

    def get_connection(self, connection_id: str) -> dict[str, Any] | None:
        with self._lock:
            return next((row for row in self._read()["connections"]
                         if row.get("id") == connection_id), None)

    def upsert_connection(self, connection: dict[str, Any]) -> None:
        row = dict(connection)
        row.setdefault("created_at", time.time())
        row["updated_at"] = time.time()
        with self._lock:
            data = self._read()
            rows = data["connections"]
            for i, existing in enumerate(rows):
                if existing.get("id") == row.get("id"):
                    row["created_at"] = existing.get("created_at", row["created_at"])
                    rows[i] = row
                    break
            else:
                rows.append(row)
            self._write(data)

    def remove_connection(self, connection_id: str) -> bool:
        with self._lock:
            data = self._read()
            before = len(data["connections"])
            data["connections"] = [row for row in data["connections"]
                                   if row.get("id") != connection_id]
            data["credentials"].pop(connection_id, None)
            if len(data["connections"]) == before:
                return False
            self._write(data)
            return True

    def set_credential(self, connection_id: str, name: str, value: str) -> None:
        protected = secretstore.protect(str(value), f"connected_apps:{connection_id}:{name}")
        with self._lock:
            data = self._read()
            data["credentials"].setdefault(connection_id, {})[name] = protected
            self._write(data)

    def get_credential(self, connection_id: str, name: str) -> str:
        with self._lock:
            stored = self._read()["credentials"].get(connection_id, {}).get(name, "")
        return secretstore.reveal(stored, f"connected_apps:{connection_id}:{name}") if stored else ""

    def delete_credentials(self, connection_id: str) -> None:
        with self._lock:
            data = self._read()
            if connection_id in data["credentials"]:
                data["credentials"].pop(connection_id, None)
                self._write(data)

    def credential_names(self, connection_id: str) -> list[str]:
        with self._lock:
            return list(self._read()["credentials"].get(connection_id, {}))
