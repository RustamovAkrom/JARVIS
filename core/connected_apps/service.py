"""Connected Apps orchestration API."""
from __future__ import annotations

import secrets
import time
from typing import Any

from core.connected_apps.models import AppConnection, AppDescriptor, ConnectionStatus
from core.connected_apps.registry import AppRegistry, registry
from core.connected_apps.store import ConnectedAppsStore
from core.connected_apps.runtime import runtime

class ConnectedAppsService:
    def __init__(self, app_registry: AppRegistry | None = None, store: ConnectedAppsStore | None = None):
        self.registry = app_registry or registry
        self.store = store or ConnectedAppsStore()

    def catalog(self, query="", category=""):
        return self.registry.search(query, category=category)

    def connections(self):
        return self.store.list_connections()

    def connect_stdio(self, descriptor: AppDescriptor, *, display_name=None):
        connection_id = secrets.token_urlsafe(16)
        row = AppConnection(id=connection_id, app_id=descriptor.id,
            display_name=display_name or descriptor.name, status=ConnectionStatus.CONNECTING,
            auth_type=descriptor.auth_type, scopes=list(descriptor.scopes),
            created_at=time.time(), updated_at=time.time())
        self.store.upsert_connection(row.__dict__ if hasattr(row, "__dict__") else {
            "id": row.id, "app_id": row.app_id, "display_name": row.display_name,
            "status": row.status.value, "enabled": row.enabled, "auth_type": row.auth_type.value,
            "scopes": row.scopes, "created_at": row.created_at, "updated_at": row.updated_at,
            "last_error": row.last_error, "metadata": row.metadata})
        try:
            tools = runtime.connect_stdio(connection_id, descriptor.command, descriptor.args, descriptor.env)
            row.status = ConnectionStatus.CONNECTED
            row.metadata["tools"] = tools
        except Exception as exc:
            row.status = ConnectionStatus.ERROR
            row.last_error = f"{type(exc).__name__}: {exc}"
        data = {"id": row.id, "app_id": row.app_id, "display_name": row.display_name,
                "status": row.status.value, "enabled": row.enabled, "auth_type": row.auth_type.value,
                "scopes": row.scopes, "created_at": row.created_at, "updated_at": time.time(),
                "last_error": row.last_error, "metadata": row.metadata}
        self.store.upsert_connection(data)
        return data

    def disconnect(self, connection_id):
        runtime.disconnect(connection_id)
        row = self.store.get_connection(connection_id)
        if not row: return False
        row["status"] = ConnectionStatus.DISCONNECTED.value
        row["updated_at"] = time.time()
        self.store.upsert_connection(row)
        return True

    def remove(self, connection_id):
        runtime.disconnect(connection_id)
        return self.store.remove_connection(connection_id)

    def tools(self, connection_id):
        return runtime.tools(connection_id)

    def call_tool(self, connection_id, tool, arguments=None):
        return runtime.call(connection_id, tool, arguments or {})

service = ConnectedAppsService()
