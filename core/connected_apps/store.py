"""Persistent metadata and protected credentials for Connected Apps."""
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
    def __init__(self, path: Path | None = None):
        self.path = Path(path or (PATHS.config_dir / "connected_apps.json"))
        self._lock = threading.RLock()

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Connected Apps store must be an object")
            # Forward-compatible fail-closed behavior: don't overwrite a newer
            # schema written by a newer JARVIS version.
            version = data.get("schema_version", 0)
            if not isinstance(version, int) or version > _SCHEMA_VERSION:
                raise RuntimeError("Connected Apps data was created by a newer version")
            connections = data.get("connections", [])
            credentials = data.get("credentials", {})
            if not isinstance(connections, list) or not isinstance(credentials, dict):
                raise ValueError("Invalid Connected Apps store structure")
            return {"schema_version": _SCHEMA_VERSION, "connections": connections,
                    "credentials": credentials}
        except FileNotFoundError:
            return {"schema_version": _SCHEMA_VERSION, "connections": [], "credentials": {}}
        except RuntimeError:
            raise
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # A corrupt store is not silently treated as empty and overwritten.
            raise RuntimeError(f"Connected Apps store is unreadable: {self.path}")

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".connected-apps-", suffix=".tmp", dir=self.path.parent)
        try:
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def list_connections(self):
        with self._lock:
            return [dict(row) for row in self._read()["connections"]]

    def get_connection(self, cid):
        with self._lock:
            return next((dict(x) for x in self._read()["connections"] if x.get("id") == cid), None)

    def upsert_connection(self, connection):
        row = dict(connection)
        row.setdefault("created_at", time.time())
        row["updated_at"] = time.time()
        with self._lock:
            data = self._read()
            rows = data["connections"]
            for i, old in enumerate(rows):
                if old.get("id") == row.get("id"):
                    row["created_at"] = old.get("created_at", row["created_at"])
                    rows[i] = row
                    break
            else:
                rows.append(row)
            self._write(data)

    def remove_connection(self, cid):
        with self._lock:
            data = self._read()
            before = len(data["connections"])
            data["connections"] = [x for x in data["connections"] if x.get("id") != cid]
            data["credentials"].pop(cid, None)
            if before == len(data["connections"]):
                return False
            self._write(data)
            return True

    def set_credential(self, cid, name, value):
        protected = secretstore.protect(str(value), f"connected_apps:{cid}:{name}")
        with self._lock:
            data = self._read()
            data["credentials"].setdefault(cid, {})[name] = protected
            self._write(data)

    def get_credential(self, cid, name):
        with self._lock:
            stored = self._read()["credentials"].get(cid, {}).get(name, "")
        if not stored:
            return ""
        value = secretstore.reveal(stored, f"connected_apps:{cid}:{name}")
        if value is None:
            raise RuntimeError("Stored credential cannot be decrypted on this system")
        return value

    def delete_credentials(self, cid):
        with self._lock:
            data = self._read()
            if cid in data["credentials"]:
                data["credentials"].pop(cid, None)
                self._write(data)

    def credential_names(self, cid):
        with self._lock:
            return list(self._read()["credentials"].get(cid, {}))
