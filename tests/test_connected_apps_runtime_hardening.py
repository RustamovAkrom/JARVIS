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

        def delete_credential(self, cid, key):
            self.credentials.pop((cid, key), None)

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
    svc.connect_stdio(descriptor, connection_id="c1")
    assert store.get_credential("c1", "stdio_env:API_TOKEN") == "secret-value"
    descriptor.env={}
    svc.connect_stdio(descriptor, connection_id="c1")
    assert store.get_credential("c1", "stdio_env:API_TOKEN") == ""
    assert store.get_connection("c1")["metadata"]["env_keys"] == []

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

def test_mcp_headers_reject_invalid_names_and_transport_owned_headers():
    from core.connected_apps.runtime import _validate_headers
    assert _validate_headers({"Authorization":"Bearer token","X-Trace":"abc"})=={"Authorization":"Bearer token","X-Trace":"abc"}
    with pytest.raises(ValueError):
        _validate_headers({"Bad Header":"value"})
    with pytest.raises(ValueError):
        _validate_headers({"Host":"example.com"})
    with pytest.raises(ValueError):
        _validate_headers({"X-Trace":"bad\r\nInjected: true"})

def test_connected_apps_health_check_updates_connection_metadata(monkeypatch):
    import importlib
    module = importlib.import_module("core.connected_apps.service")
    from core.connected_apps.service import ConnectedAppsService

    class Store:
        def __init__(self):
            self.rows = {"c1": {"id": "c1", "status": "connected", "enabled": True,
                "metadata": {}, "last_error": "", "updated_at": 0}}
        def get_connection(self, cid):
            return dict(self.rows[cid]) if cid in self.rows else None
        def upsert_connection(self, row):
            self.rows[row["id"]] = dict(row)
        def list_connections(self):
            return list(self.rows.values())

    store = Store()
    svc = ConnectedAppsService(store=store)
    monkeypatch.setattr(module.runtime, "health", lambda cid: True)
    result = svc.health_check("c1")
    assert result["healthy"] is True
    row = store.get_connection("c1")
    assert row["status"] == "connected"
    assert row["metadata"]["health"] == "healthy"
    assert row["metadata"]["last_health_check"] > 0

def test_connected_apps_health_failure_marks_error_and_disconnects(monkeypatch):
    import importlib
    module = importlib.import_module("core.connected_apps.service")
    from core.connected_apps.service import ConnectedAppsService

    class Store:
        def __init__(self):
            self.rows = {"c1": {"id": "c1", "status": "connected", "enabled": True,
                "metadata": {}, "last_error": "", "updated_at": 0}}
        def get_connection(self, cid):
            return dict(self.rows[cid]) if cid in self.rows else None
        def upsert_connection(self, row):
            self.rows[row["id"]] = dict(row)
        def list_connections(self):
            return list(self.rows.values())

    store = Store()
    svc = ConnectedAppsService(store=store)
    monkeypatch.setattr(module.runtime, "health", lambda cid: (_ for _ in ()).throw(RuntimeError("server unavailable")))
    disconnected = []
    monkeypatch.setattr(module.runtime, "disconnect", lambda cid: disconnected.append(cid))
    result = svc.health_check("c1")
    assert result["healthy"] is False
    assert store.get_connection("c1")["status"] == "error"
    assert store.get_connection("c1")["metadata"]["health"] == "unhealthy"
    assert disconnected == ["c1"]

def test_connected_apps_tool_calls_record_activity_and_errors(monkeypatch):
    import importlib
    module = importlib.import_module("core.connected_apps.service")
    from core.connected_apps.service import ConnectedAppsService

    class Store:
        def __init__(self):
            self.rows = {"c1": {"id": "c1", "status": "connected", "enabled": True,
                "metadata": {}, "last_error": "", "updated_at": 0}}
        def get_connection(self, cid):
            return dict(self.rows[cid]) if cid in self.rows else None
        def upsert_connection(self, row):
            self.rows[row["id"]] = dict(row)
        def list_connections(self):
            return list(self.rows.values())

    store = Store()
    svc = ConnectedAppsService(store=store)
    monkeypatch.setattr(module.runtime, "tools", lambda cid: [
        {
            "name": "search",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        }
    ])
    monkeypatch.setattr(module.runtime, "call", lambda cid, tool, args: [{"type": "text", "text": "ok"}])
    result = svc.call_tool("c1", "search", {"query": "test"})
    metadata = store.get_connection("c1")["metadata"]
    assert result[0]["text"] == "ok"
    assert metadata["last_tool_name"] == "search"
    assert metadata["tool_call_count"] == 1
    assert metadata["last_tool_activity"] > 0
    assert metadata["last_tool_duration_ms"] >= 0

    monkeypatch.setattr(module.runtime, "call", lambda cid, tool, args: (_ for _ in ()).throw(RuntimeError("failed")))
    with pytest.raises(RuntimeError):
        svc.call_tool("c1", "search", {"query": "test"})
    metadata = store.get_connection("c1")["metadata"]
    assert metadata["tool_call_count"] == 2
    assert metadata["last_tool_error"].startswith("RuntimeError:")

def test_mcp_catalog_contains_database_and_developer_integrations():
    from core.connected_apps.catalog import list_templates, get_template

    ids = {item.id for item in list_templates()}
    assert {"filesystem", "playwright", "fetch", "memory", "sequential-thinking",
            "github", "postgres", "sqlite", "brave-search"} <= ids
    github = get_template("github")
    assert github is not None
    assert "GITHUB_PERSONAL_ACCESS_TOKEN" in github.env_keys
    assert get_template("brave-search").env_keys == ("BRAVE_API_KEY",)

def test_health_check_does_not_change_disconnected_connection(monkeypatch):
    import importlib
    module = importlib.import_module("core.connected_apps.service")
    from core.connected_apps.service import ConnectedAppsService

    class Store:
        def __init__(self):
            self.rows = {"c1": {"id": "c1", "status": "disconnected", "enabled": True,
                "metadata": {}, "last_error": "", "updated_at": 0}}
        def get_connection(self, cid):
            return dict(self.rows[cid])
        def upsert_connection(self, row):
            self.rows[row["id"]] = dict(row)
        def list_connections(self):
            return list(self.rows.values())

    store = Store()
    svc = ConnectedAppsService(store=store)
    monkeypatch.setattr(module.runtime, "health", lambda cid: pytest.fail("Disconnected session pinged"))
    result = svc.health_check("c1")
    assert result["healthy"] is False
    assert result["status"] == "disconnected"
    assert store.get_connection("c1")["status"] == "disconnected"

def test_connected_apps_enable_disable_lifecycle(monkeypatch):
    import importlib
    module = importlib.import_module("core.connected_apps.service")
    from core.connected_apps.service import ConnectedAppsService

    class Store:
        def __init__(self):
            self.rows = {"c1": {"id": "c1", "app_id": "demo", "display_name": "Demo",
                "status": "connected", "enabled": True, "metadata": {"transport": "stdio"},
                "last_error": "", "updated_at": 0}}
        def get_connection(self, cid):
            return dict(self.rows[cid]) if cid in self.rows else None
        def upsert_connection(self, row):
            self.rows[row["id"]] = dict(row)
        def list_connections(self):
            return list(self.rows.values())

    store = Store()
    svc = ConnectedAppsService(store=store)
    disconnected = []
    monkeypatch.setattr(module.runtime, "disconnect", lambda cid: disconnected.append(cid))
    result = svc.set_enabled("c1", False)
    assert result["enabled"] is False
    assert result["status"] == "disconnected"
    assert disconnected == ["c1"]
    result = svc.set_enabled("c1", True, reconnect=False)
    assert result["enabled"] is True
    assert result["status"] == "disconnected"

def test_disabled_connection_is_not_reconnected(monkeypatch):
    import importlib
    module = importlib.import_module("core.connected_apps.service")
    from core.connected_apps.service import ConnectedAppsService

    class Store:
        def __init__(self):
            self.rows = {"c1": {"id": "c1", "app_id": "demo", "display_name": "Demo",
                "status": "disconnected", "enabled": False, "metadata": {"transport": "stdio"},
                "last_error": "", "updated_at": 0}}
        def get_connection(self, cid):
            return dict(self.rows[cid]) if cid in self.rows else None
        def upsert_connection(self, row):
            self.rows[row["id"]] = dict(row)
        def list_connections(self):
            return list(self.rows.values())
        def get_credential(self, cid, key):
            return ""

    store = Store()
    svc = ConnectedAppsService(store=store)
    monkeypatch.setattr(module.runtime, "connect_stdio", lambda *args: pytest.fail("Disabled app reconnected"))
    result = svc.reconnect("c1")
    assert result["enabled"] is False
    assert result["status"] == "disconnected"
    assert result["last_error"] == "Connection is disabled"

def test_runtime_health_falls_back_to_tool_discovery(monkeypatch):
    import importlib
    module = importlib.import_module("core.connected_apps.runtime")
    runtime = module.MCPRuntime()

    class Session:
        async def list_tools(self):
            return object()

    runtime._sessions["c1"] = (None, Session())
    monkeypatch.setattr(runtime, "_submit", lambda coro, timeout=60: coro.__await__().__next__() if False else True)
    # Verify the fallback is selected when ping is absent.
    import asyncio
    async def run():
        return await runtime._ping("c1")
    assert asyncio.run(run()) is True

def test_connected_apps_rename_validates_and_persists_name():
    from core.connected_apps.service import ConnectedAppsService

    class Store:
        def __init__(self):
            self.rows = {"c1": {"id": "c1", "display_name": "Original", "updated_at": 0}}
        def get_connection(self, cid):
            return dict(self.rows[cid]) if cid in self.rows else None
        def upsert_connection(self, row):
            self.rows[row["id"]] = dict(row)

    store = Store()
    svc = ConnectedAppsService(store=store)
    result = svc.rename("c1", "  My  GitHub  ")
    assert result["display_name"] == "My GitHub"
    assert store.get_connection("c1")["display_name"] == "My GitHub"
    with pytest.raises(ValueError):
        svc.rename("c1", "  ")
    with pytest.raises(ValueError):
        svc.rename("c1", "x" * 121)

def test_connected_apps_error_redaction_masks_common_credentials():
    from core.connected_apps.service import _safe_error

    message = _safe_error(RuntimeError(
        "Bearer abc123 access_token=secret refresh_token=refresh "
        "https://user:password@example.com/mcp"
    ))
    assert "abc123" not in message
    assert "secret" not in message
    assert "refresh" not in message
    assert "password" not in message
    assert "[redacted]" in message


@pytest.mark.parametrize("transport", ["stdio", "http", "sse"])
def test_failed_mcp_connect_closes_stale_runtime_session(monkeypatch, transport):
    import importlib
    from core.connected_apps.models import AppDescriptor, TransportType
    from core.connected_apps.service import ConnectedAppsService

    module = importlib.import_module("core.connected_apps.service")

    class Store:
        def __init__(self):
            self.rows = {}
        def get_connection(self, cid):
            row = self.rows.get(cid)
            return dict(row) if row else None
        def upsert_connection(self, row):
            self.rows[row["id"]] = dict(row)
        def list_connections(self):
            return list(self.rows.values())

    store = Store()
    svc = ConnectedAppsService(store=store)
    disconnected = []
    monkeypatch.setattr(module.runtime, "disconnect", lambda cid: disconnected.append(cid))
    def fail(*args, **kwargs):
        raise RuntimeError("connection failed")
    if transport == "stdio":
        monkeypatch.setattr(module.runtime, "connect_stdio", fail)
        descriptor = AppDescriptor(id="demo", name="Demo", transport=TransportType.STDIO, command="python")
        result = svc.connect_stdio(descriptor, connection_id="c1")
    elif transport == "http":
        monkeypatch.setattr(module.runtime, "connect_http", fail)
        descriptor = AppDescriptor(id="demo", name="Demo", transport=TransportType.HTTP, endpoint="https://example.com/mcp")
        result = svc.connect_http(descriptor, connection_id="c1")
    else:
        monkeypatch.setattr(module.runtime, "connect_sse", fail)
        descriptor = AppDescriptor(id="demo", name="Demo", transport=TransportType.SSE, endpoint="https://example.com/mcp")
        result = svc.connect_sse(descriptor, connection_id="c1")
    assert result["status"] == "error"
    assert disconnected == ["c1"]


def test_runtime_disconnect_reports_stopped_loop_without_unawaited_coroutine():
    from core.connected_apps.runtime import MCPRuntime, RuntimeUnavailable

    runtime = MCPRuntime()
    with pytest.raises(RuntimeUnavailable, match="not running"):
        runtime.disconnect("c1")


def test_runtime_uses_stable_per_connection_lock():
    from core.connected_apps.runtime import MCPRuntime

    runtime = MCPRuntime()
    first = runtime._connection_lock("connection-1")
    second = runtime._connection_lock("connection-1")
    other = runtime._connection_lock("connection-2")
    assert first is second
    assert first is not other


def test_runtime_loop_starts_without_lock_deadlock():
    from core.connected_apps.runtime import MCPRuntime

    runtime = MCPRuntime()
    runtime._ensure_loop()
    assert runtime._loop is not None
    assert runtime._loop.is_running()
    runtime.shutdown()


def test_service_uses_stable_per_connection_operation_locks():
    from core.connected_apps.service import ConnectedAppsService

    service = ConnectedAppsService()
    first = service._connection_lock("connection-1")
    second = service._connection_lock("connection-1")
    other = service._connection_lock("connection-2")
    assert first is second
    assert first is not other


@pytest.mark.parametrize("transport", ["stdio", "http", "sse"])
def test_failed_connect_cleanup_error_does_not_replace_original_error(monkeypatch, transport):
    import importlib
    from core.connected_apps.models import AppDescriptor, TransportType
    from core.connected_apps.service import ConnectedAppsService

    module = importlib.import_module("core.connected_apps.service")

    class Store:
        def __init__(self):
            self.rows = {}
        def get_connection(self, cid):
            row = self.rows.get(cid)
            return dict(row) if row else None
        def upsert_connection(self, row):
            self.rows[row["id"]] = dict(row)
        def list_connections(self):
            return list(self.rows.values())

    svc = ConnectedAppsService(store=Store())
    def fail_connect(*args, **kwargs):
        raise RuntimeError("original connect failure")
    def fail_cleanup(cid):
        raise RuntimeError("cleanup failure")
    monkeypatch.setattr(module.runtime, "disconnect", fail_cleanup)

    if transport == "stdio":
        monkeypatch.setattr(module.runtime, "connect_stdio", fail_connect)
        descriptor = AppDescriptor(id="demo", name="Demo", transport=TransportType.STDIO, command="python")
        result = svc.connect_stdio(descriptor, connection_id="c1")
    elif transport == "http":
        monkeypatch.setattr(module.runtime, "connect_http", fail_connect)
        descriptor = AppDescriptor(id="demo", name="Demo", transport=TransportType.HTTP, endpoint="https://example.com/mcp")
        result = svc.connect_http(descriptor, connection_id="c1")
    else:
        monkeypatch.setattr(module.runtime, "connect_sse", fail_connect)
        descriptor = AppDescriptor(id="demo", name="Demo", transport=TransportType.SSE, endpoint="https://example.com/mcp")
        result = svc.connect_sse(descriptor, connection_id="c1")

    assert result["status"] == "error"
    assert "original connect failure" in result["last_error"]
    assert "cleanup failure" not in result["last_error"]


@pytest.mark.parametrize("transport", ["http", "sse"])
def test_failed_http_transport_connect_cleanup_error_is_best_effort(monkeypatch, transport):
    import importlib
    from core.connected_apps.models import AppDescriptor, TransportType
    from core.connected_apps.service import ConnectedAppsService

    module = importlib.import_module("core.connected_apps.service")

    class Store:
        def __init__(self):
            self.rows = {}
        def get_connection(self, cid):
            row = self.rows.get(cid)
            return dict(row) if row else None
        def upsert_connection(self, row):
            self.rows[row["id"]] = dict(row)
        def list_connections(self):
            return list(self.rows.values())
        def set_credential(self, cid, key, value):
            pass
        def delete_credential(self, cid, key):
            pass

    svc = ConnectedAppsService(store=Store())
    monkeypatch.setattr(module.runtime, "disconnect", lambda cid: (_ for _ in ()).throw(RuntimeError("cleanup failure")))
    def fail(*args, **kwargs):
        raise RuntimeError("original transport failure")

    if transport == "http":
        monkeypatch.setattr(module.runtime, "connect_http", fail)
        descriptor = AppDescriptor(id="demo", name="Demo", transport=TransportType.HTTP, endpoint="https://example.com/mcp")
        result = svc.connect_http(descriptor, connection_id="c1")
    else:
        monkeypatch.setattr(module.runtime, "connect_sse", fail)
        descriptor = AppDescriptor(id="demo", name="Demo", transport=TransportType.SSE, endpoint="https://example.com/mcp")
        result = svc.connect_sse(descriptor, connection_id="c1")

    assert result["status"] == "error"
    assert "original transport failure" in result["last_error"]
    assert "cleanup failure" not in result["last_error"]


def test_native_disconnect_disable_and_remove_do_not_require_mcp_runtime(monkeypatch):
    import importlib
    from core.connected_apps.service import ConnectedAppsService

    module = importlib.import_module("core.connected_apps.service")

    class Store:
        def __init__(self):
            self.rows = {"native-1": {"id": "native-1", "app_id": "github", "status": "connected",
                "enabled": True, "metadata": {"native": True}, "last_error": "", "updated_at": 0}}
        def get_connection(self, cid):
            row = self.rows.get(cid)
            return dict(row) if row else None
        def upsert_connection(self, row):
            self.rows[row["id"]] = dict(row)
        def list_connections(self):
            return list(self.rows.values())
        def remove_connection(self, cid):
            return self.rows.pop(cid, None) is not None

    store = Store()
    service = ConnectedAppsService(store=store)
    monkeypatch.setattr(module.runtime, "disconnect", lambda cid: pytest.fail("Native app must not use MCP runtime"))
    assert service.disconnect("native-1") is True
    assert store.get_connection("native-1")["status"] == "disconnected"
    service.set_enabled("native-1", False)
    assert store.get_connection("native-1")["enabled"] is False
    assert service.remove("native-1") is True
