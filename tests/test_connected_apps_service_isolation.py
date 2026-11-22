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
