from __future__ import annotations

import pytest

from core.connected_apps.agent import _validate_arguments
from core.connected_apps.runtime import _validate_http_endpoint
from core.connected_apps.store import ConnectedAppsStore


def test_http_endpoint_rejects_non_http_scheme():
    with pytest.raises(ValueError):
        _validate_http_endpoint("file:///etc/passwd")


@pytest.mark.parametrize("url", [
    "https://user:password@example.com/mcp",
    "https://example.com/mcp#fragment",
    "not-a-url",
])
def test_http_endpoint_rejects_unsafe_url_forms(url):
    with pytest.raises(ValueError):
        _validate_http_endpoint(url)


def test_http_endpoint_accepts_valid_endpoint():
    assert _validate_http_endpoint("https://example.com/mcp") == "https://example.com/mcp"


def test_schema_validation_checks_nested_types_and_enum():
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "enum": [1, 2, 3]},
            "filters": {
                "type": "array",
                "items": {"type": "object", "properties": {
                    "enabled": {"type": "boolean"}
                }}
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    _validate_arguments(schema, {"query": "hello", "limit": 2, "filters": [{"enabled": True}]})
    with pytest.raises(ValueError):
        _validate_arguments(schema, {"query": 123})
    with pytest.raises(ValueError):
        _validate_arguments(schema, {"query": "hello", "limit": 5})
    with pytest.raises(ValueError):
        _validate_arguments(schema, {"query": "hello", "unexpected": True})


def test_store_does_not_silently_reset_corrupt_data(tmp_path):
    path = tmp_path / "connected_apps.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unreadable"):
        ConnectedAppsStore(path).list_connections()

def test_stdio_environment_is_persisted_as_credentials_and_restored(monkeypatch, tmp_path):
    from core.connected_apps.models import AppDescriptor, TransportType
    from core.connected_apps.service import ConnectedAppsService
    import importlib
    service_module = importlib.import_module("core.connected_apps.service")

    class Store:
        def __init__(self):
            self.rows = {}
            self.credentials = {}

        def get_connection(self, cid):
            row = self.rows.get(cid)
            return dict(row) if row else None

        def upsert_connection(self, row):
            self.rows[row["id"]] = dict(row)

        def list_connections(self):
            return list(self.rows.values())

        def set_credential(self, cid, key, value):
            self.credentials[(cid, key)] = value

        def get_credential(self, cid, key):
            return self.credentials.get((cid, key), "")

        def remove_connection(self, cid):
            return self.rows.pop(cid, None) is not None

    store = Store()
    svc = ConnectedAppsService(store=store)
    calls = []

    def connect_stdio(cid, command, args, env):
        calls.append(dict(env))
        return [{"name": "test", "input_schema": {"type": "object"}}]

    monkeypatch.setattr(service_module.runtime, "connect_stdio", connect_stdio)
    descriptor = AppDescriptor(
        id="test-server",
        name="Test Server",
        transport=TransportType.STDIO,
        command="python",
        args=["server.py"],
        env={"API_TOKEN": "secret-value"},
    )
    result = svc.connect_stdio(descriptor, connection_id="c1")
    assert result["status"] == "connected"
    assert store.get_credential("c1", "stdio_env:API_TOKEN") == "secret-value"

    # Trust metadata should survive reconnects, while credentials are restored.
    row = store.get_connection("c1")
    row["metadata"]["auto_approve_read_only"] = True
    store.upsert_connection(row)
    svc.reconnect("c1")
    assert calls[-1] == {"API_TOKEN": "secret-value"}
    assert store.get_connection("c1")["metadata"]["auto_approve_read_only"] is True

def test_sse_connection_persists_transport_and_reconnects(monkeypatch):
    import importlib
    from core.connected_apps.models import AppDescriptor, TransportType
    from core.connected_apps.service import ConnectedAppsService
    module=importlib.import_module("core.connected_apps.service")

    class Store:
        def __init__(self):
            self.rows={}
            self.credentials={}
        def get_connection(self,cid):
            row=self.rows.get(cid)
            return dict(row) if row else None
        def upsert_connection(self,row):
            self.rows[row["id"]]=dict(row)
        def list_connections(self):
            return list(self.rows.values())
        def set_credential(self,cid,key,value):
            self.credentials[(cid,key)]=value
        def get_credential(self,cid,key):
            return self.credentials.get((cid,key),"")
        def remove_connection(self,cid):
            return self.rows.pop(cid,None) is not None

    store=Store()
    svc=ConnectedAppsService(store=store)
    calls=[]
    monkeypatch.setattr(module.runtime,"connect_sse",lambda cid,url,headers=None: calls.append((cid,url,headers)) or [{"name":"search"}])
    monkeypatch.setattr(module.runtime,"disconnect",lambda cid: None)
    descriptor=AppDescriptor(id="sse-demo",name="SSE Demo",transport=TransportType.SSE,endpoint="https://example.com/mcp")
    first=svc.connect_sse(descriptor,connection_id="sse-1",headers={"Authorization":"Bearer secret"})
    assert first["status"]=="connected"
    assert store.get_connection("sse-1")["metadata"]["transport"]=="sse"
    assert store.get_credential("sse-1","authorization_header")=="Bearer secret"
    second=svc.reconnect("sse-1",attempts=1)
    assert second["status"]=="connected"
    assert calls[-1][2]=={"Authorization":"Bearer secret"}
