from __future__ import annotations


def test_native_health_check_uses_service_local_adapter(monkeypatch):
    from core.connected_apps.service import ConnectedAppsService
    from core.connected_apps.native import native_registry

    class Store:
        def __init__(self):
            self.rows = {
                "native-1": {
                    "id": "native-1",
                    "app_id": "github",
                    "status": "connected",
                    "enabled": True,
                    "metadata": {"native": True},
                    "last_error": "",
                    "updated_at": 0,
                }
            }
        def get_connection(self, cid):
            row = self.rows.get(cid)
            return dict(row) if row else None
        def upsert_connection(self, row):
            self.rows[row["id"]] = dict(row)
        def list_connections(self):
            return list(self.rows.values())

    class Adapter:
        descriptor = type("Descriptor", (), {"id": "github"})()
        def health_check(self, cid):
            return True

    service = ConnectedAppsService(store=Store())
    service._native_adapters["github"] = Adapter()
    monkeypatch.setattr(native_registry, "get", lambda app_id: (_ for _ in ()).throw(
        AssertionError("global registry must not be used for service-local health checks")
    ))
    result = service.health_check("native-1")
    assert result["healthy"] is True
    assert result["status"] == "connected"


def test_service_call_tool_enforces_connection_and_permissions(monkeypatch):
    from core.connected_apps.service import ConnectedAppsService
    from core.connected_apps.models import ConnectionStatus
    import importlib
    service_module = importlib.import_module("core.connected_apps.service")

    class Store:
        def __init__(self, row):
            self.row = dict(row)
        def get_connection(self, cid):
            return dict(self.row) if cid == self.row["id"] else None
        def upsert_connection(self, row):
            self.row = dict(row)
        def list_connections(self):
            return [dict(self.row)]

    base = {
        "id": "c1", "app_id": "demo", "display_name": "Demo",
        "status": "connected", "enabled": True,
        "metadata": {"blocked_tools": []}, "last_error": "", "updated_at": 0,
    }
    store = Store(base)
    svc = ConnectedAppsService(store=store)
    monkeypatch.setattr(service_module.runtime, "tools", lambda cid: [
        {"name": "read", "input_schema": {"type": "object", "properties": {
            "limit": {"type": "integer", "minimum": 1}
        }, "required": ["limit"], "additionalProperties": False}}
    ])
    calls = []
    monkeypatch.setattr(service_module.runtime, "call", lambda cid, tool, args: calls.append((cid, tool, args)) or "ok")

    assert svc.call_tool("c1", "read", {"limit": 1}) == "ok"
    assert len(calls) == 1

    store.row["metadata"]["blocked_tools"] = ["read"]
    with pytest.raises(PermissionError, match="blocked"):
        svc.call_tool("c1", "read", {"limit": 1})
    assert len(calls) == 1

    store.row["metadata"]["blocked_tools"] = []
    store.row["enabled"] = False
    with pytest.raises(RuntimeError, match="disabled"):
        svc.call_tool("c1", "read", {"limit": 1})
    store.row["enabled"] = True
    store.row["status"] = ConnectionStatus.DISCONNECTED.value
    with pytest.raises(RuntimeError, match="not connected"):
        svc.call_tool("c1", "read", {"limit": 1})
    store.row["status"] = ConnectionStatus.CONNECTED.value

    with pytest.raises(ValueError, match="required"):
        svc.call_tool("c1", "read", {})
    with pytest.raises(ValueError, match="unknown"):
        svc.call_tool("c1", "read", {"limit": 1, "unexpected": True})
    assert len(calls) == 1
