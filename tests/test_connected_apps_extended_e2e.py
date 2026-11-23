from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import threading
import time
from types import SimpleNamespace

import pytest


class MemoryStore:
    def __init__(self, rows=None, credentials=None):
        self.rows = copy.deepcopy(rows or {})
        self.credentials = dict(credentials or {})
        self.lock = threading.RLock()

    def get_connection(self, cid):
        with self.lock:
            row = self.rows.get(cid)
            return copy.deepcopy(row) if row else None

    def upsert_connection(self, row):
        with self.lock:
            self.rows[row["id"]] = copy.deepcopy(row)

    def list_connections(self):
        with self.lock:
            return copy.deepcopy(list(self.rows.values()))

    def set_credential(self, cid, key, value):
        with self.lock:
            self.credentials[(cid, key)] = value

    def set_credentials(self, cid, values, *, delete=()):
        with self.lock:
            for key in delete:
                self.credentials.pop((cid, key), None)
            for key, value in values.items():
                self.credentials[(cid, key)] = value

    def get_credential(self, cid, key):
        with self.lock:
            return self.credentials.get((cid, key), "")

    def delete_credential(self, cid, key):
        with self.lock:
            return self.credentials.pop((cid, key), None) is not None

    def delete_credentials(self, cid):
        with self.lock:
            for key in [item for item in self.credentials if item[0] == cid]:
                self.credentials.pop(key, None)

    def remove_connection(self, cid):
        with self.lock:
            return self.rows.pop(cid, None) is not None


class SessionPasswordNeededError(Exception):
    pass


class SentCodeType:
    pass


class FakeStringSession:
    def __init__(self, value=""):
        self.value = value

    def save(self):
        return "saved-session"


class FakeTelegramClient:
    instances = []

    def __init__(self, session, api_id, api_hash, **kwargs):
        self.__class__.instances.append(self)
        self.session = session if isinstance(session, FakeStringSession) else FakeStringSession()
        self.authorized = self.session.value == "restored-session"
        self.connected = False
        self.disconnected = False
        self.loop_ids = set()
        self.api_id = api_id
        self.api_hash = api_hash

    def _record_loop(self):
        self.loop_ids.add(asyncio.get_running_loop())

    async def connect(self):
        self._record_loop()
        self.connected = True

    async def disconnect(self):
        self._record_loop()
        self.disconnected = True

    async def send_code_request(self, phone):
        self._record_loop()
        return SimpleNamespace(phone_code_hash="test-code-hash", type=SentCodeType())

    async def sign_in(self, phone=None, code=None, phone_code_hash=None, password=None):
        self._record_loop()
        if password is None:
            raise SessionPasswordNeededError("2FA required")
        if password != "correct-password":
            raise ValueError("invalid 2FA password")
        self.authorized = True

    async def is_user_authorized(self):
        self._record_loop()
        return self.authorized

    async def get_me(self):
        self._record_loop()
        return SimpleNamespace(id=123, first_name="Test", username="test_account")


def _stop_telegram_loop(adapter):
    loop = adapter._loop
    thread = adapter._loop_thread
    if loop is not None and loop.is_running():
        loop.call_soon_threadsafe(loop.stop)
    if thread is not None and thread.is_alive():
        thread.join(timeout=3)
    if loop is not None and not loop.is_closed():
        loop.close()


def test_telegram_login_handles_2fa_and_keeps_one_event_loop(monkeypatch):
    import core.connected_apps.adapters.telegram_personal as telegram

    monkeypatch.setattr(telegram, "TelegramClient", FakeTelegramClient)
    monkeypatch.setattr(telegram, "StringSession", FakeStringSession)
    FakeTelegramClient.instances.clear()
    adapter = telegram.TelegramPersonalAdapter(MemoryStore())

    try:
        started = adapter.begin_login(12345, "api-hash", "+10000000000")
        assert started["code_sent"] is True
        assert started["code_type"] == "SentCodeType"

        pending = adapter.finish_login("+10000000000", "12345")
        assert pending == {"two_factor_required": True}
        assert "+10000000000" in adapter._pending

        result = adapter.finish_login(
            "+10000000000", "12345", password="correct-password"
        )
        assert result["authenticated"] is True
        assert result["session_string"] == "saved-session"
        assert result["api_id"] == 12345
        assert result["user"]["username"] == "test_account"
        assert "+10000000000" not in adapter._pending

        # Telethon clients must remain bound to one event loop for their
        # complete login/connect/disconnect lifecycle.
        assert adapter._pending == {}
        client = FakeTelegramClient.instances[-1]
        assert len(client.loop_ids) == 1
    finally:
        _stop_telegram_loop(adapter)


def test_telegram_login_cancel_closes_pending_client(monkeypatch):
    import core.connected_apps.adapters.telegram_personal as telegram

    monkeypatch.setattr(telegram, "TelegramClient", FakeTelegramClient)
    monkeypatch.setattr(telegram, "StringSession", FakeStringSession)
    adapter = telegram.TelegramPersonalAdapter(MemoryStore())

    try:
        adapter.begin_login(12345, "api-hash", "+10000000001")
        pending = adapter._pending["+10000000001"]["client"]
        assert adapter.cancel_login("+10000000001") is True
        assert pending.disconnected is True
        assert adapter.cancel_login("+10000000001") is False
    finally:
        _stop_telegram_loop(adapter)


def test_telegram_session_is_restored_from_persistent_credentials(monkeypatch):
    import core.connected_apps.adapters.telegram_personal as telegram

    store = MemoryStore(
        rows={
            "telegram-1": {
                "id": "telegram-1",
                "app_id": "telegram_personal",
                "status": "connected",
                "enabled": True,
                "metadata": {
                    "native": True,
                    "telegram_permissions": dict(telegram.DEFAULT_PERMISSIONS),
                    "telegram_allowed_chats": ["1001"],
                },
            }
        },
        credentials={
            ("telegram-1", "mtproto_session"): "restored-session",
            (
                "telegram-1",
                "telegram_api_config",
            ): json.dumps({"api_id": 12345, "api_hash": "api-hash"}),
        },
    )
    monkeypatch.setattr(telegram, "TelegramClient", FakeTelegramClient)
    monkeypatch.setattr(telegram, "StringSession", FakeStringSession)
    adapter = telegram.TelegramPersonalAdapter(store)

    try:
        assert adapter.health_check("telegram-1") is True
        assert "telegram-1" in adapter._clients
        assert adapter._clients["telegram-1"].session.value == "restored-session"
        assert adapter.health_check("telegram-1") is True
        # The adapter reuses the same client instead of creating a second one.
        assert len(adapter._clients) == 1
    finally:
        adapter.disconnect("telegram-1")
        _stop_telegram_loop(adapter)


def test_telegram_allowlist_and_read_permissions_are_enforced_before_access():
    import core.connected_apps.adapters.telegram_personal as telegram

    class ReadClient:
        async def iter_messages(self, chat_id, **kwargs):
            for message_id in (10, 11):
                yield SimpleNamespace(
                    id=message_id,
                    date=None,
                    message=f"fixture-message-{message_id}",
                )

    permissions = dict(telegram.DEFAULT_PERMISSIONS)
    permissions["read_recent_messages"] = True
    store = MemoryStore(
        rows={
            "telegram-2": {
                "id": "telegram-2",
                "app_id": "telegram_personal",
                "status": "connected",
                "enabled": True,
                "metadata": {
                    "telegram_permissions": permissions,
                    "telegram_allowed_chats": ["1001"],
                },
            }
        }
    )
    adapter = telegram.TelegramPersonalAdapter(store)
    adapter._client = lambda cid: ReadClient()
    adapter._run = lambda coroutine: asyncio.run(coroutine)

    messages = adapter.execute(
        "telegram-2", "telegram_get_recent_messages",
        {"chat_id": "1001", "limit": 2},
    )
    assert [item["message_id"] for item in messages] == [10, 11]

    with pytest.raises(PermissionError, match="not explicitly allowed"):
        adapter.execute(
            "telegram-2", "telegram_get_recent_messages",
            {"chat_id": "9999", "limit": 1},
        )

    permissions["read_recent_messages"] = False
    store.upsert_connection(store.get_connection("telegram-2"))
    store.rows["telegram-2"]["metadata"]["telegram_permissions"] = permissions
    with pytest.raises(PermissionError, match="permission is disabled"):
        adapter.execute(
            "telegram-2", "telegram_get_recent_messages",
            {"chat_id": "1001", "limit": 1},
        )

    assert adapter._permissions("telegram-2")["ai_access"] is False


def test_telegram_write_and_destructive_tools_require_confirmation():
    from core.connected_apps.adapters.telegram_personal import TelegramPersonalAdapter

    tools = {tool.name: tool for tool in TelegramPersonalAdapter.tools}
    for name in (
        "telegram_send_message",
        "telegram_reply_message",
        "telegram_forward_message",
        "telegram_edit_message",
        "telegram_delete_message",
    ):
        assert tools[name].requires_confirmation is True
        assert tools[name].read_only is False
    assert tools["telegram_delete_message"].destructive is True


def test_telegram_connection_forces_ai_access_off_and_persists_session():
    from core.connected_apps.service import ConnectedAppsService

    store = MemoryStore()
    service = ConnectedAppsService(store=store)
    service._native_adapters["telegram_personal"].health_check = lambda cid: True

    row = service.connect_telegram_personal(
        api_id=12345,
        api_hash="api-hash",
        session_string="session-material",
        permissions={"ai_access": True, "list_chats": True},
        allowed_chats=["1001"],
        connection_id="telegram-3",
    )

    assert row["status"] == "connected"
    assert row["metadata"]["telegram_permissions"]["ai_access"] is False
    assert store.get_credential("telegram-3", "mtproto_session") == "session-material"
    assert store.get_connection("telegram-3")["metadata"]["telegram_allowed_chats"] == ["1001"]


def test_local_stdio_mcp_server_real_process_lifecycle(tmp_path):
    pytest.importorskip("mcp.server.fastmcp")
    from core.connected_apps.runtime import MCPRuntime

    server_code = """
from mcp.server.fastmcp import FastMCP

server = FastMCP("jarvis-connected-apps-e2e")

@server.tool()
def add_numbers(left: int, right: int) -> int:
    return left + right

server.run()
"""
    server_path = tmp_path / "connected_apps_mcp_server.py"
    server_path.write_text(server_code, encoding="utf-8")

    runtime = MCPRuntime()
    connection_id = "stdio-e2e"
    try:
        discovered = runtime.connect_stdio(
            connection_id,
            sys.executable,
            ["-u", str(server_path)],
            env=None,
        )
        assert any(tool["name"] == "add_numbers" for tool in discovered)
        assert runtime.health(connection_id) is True

        result = runtime.call(
            connection_id, "add_numbers", {"left": 2, "right": 5}
        )
        assert any("7" in item.get("text", "") for item in result)

        assert runtime.disconnect(connection_id) is True
        assert runtime.tools(connection_id) == []

        # Reconnect starts a new subprocess and re-discovers its tools.
        discovered_again = runtime.connect_stdio(
            connection_id,
            sys.executable,
            ["-u", str(server_path)],
            env=None,
        )
        assert any(tool["name"] == "add_numbers" for tool in discovered_again)
        assert runtime.health(connection_id) is True
    finally:
        runtime.shutdown()


def test_service_serializes_concurrent_native_health_checks():
    from core.connected_apps.service import ConnectedAppsService

    store = MemoryStore(
        rows={
            "github-concurrent": {
                "id": "github-concurrent",
                "app_id": "github",
                "display_name": "GitHub",
                "status": "connected",
                "enabled": True,
                "metadata": {"native": True, "native_adapter": "github"},
                "last_error": "",
                "updated_at": 0,
            }
        }
    )
    service = ConnectedAppsService(store=store)
    adapter = service._native_adapters["github"]
    guard = threading.Lock()
    active = 0
    max_active = 0

    def health_check(_cid):
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with guard:
            active -= 1
        return True

    adapter.health_check = health_check
    barrier = threading.Barrier(3)
    errors = []

    def run_check():
        try:
            barrier.wait(timeout=3)
            service.health_check("github-concurrent")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run_check) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=3)
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert max_active == 1


def test_connected_apps_store_fails_closed_for_unreadable_credential(tmp_path, monkeypatch):
    from core import secretstore
    from core.connected_apps.store import ConnectedAppsStore

    monkeypatch.setattr(secretstore, "protect", lambda value, name: "aead1:tampered")
    monkeypatch.setattr(secretstore, "reveal", lambda stored, name: None)

    store = ConnectedAppsStore(tmp_path / "connected_apps.json")
    store.set_credential("connection-1", "access_token", "secret-token")

    with pytest.raises(RuntimeError, match="cannot be decrypted"):
        store.get_credential("connection-1", "access_token")


def test_connected_apps_restart_restores_persisted_native_connection(tmp_path, monkeypatch):
    from core import secretstore
    from core.connected_apps.service import ConnectedAppsService
    from core.connected_apps.store import ConnectedAppsStore

    monkeypatch.setattr(secretstore, "protect", lambda value, name: f"test-protected:{value}")
    monkeypatch.setattr(
        secretstore,
        "reveal",
        lambda stored, name: stored.removeprefix("test-protected:"),
    )
    path = tmp_path / "connected_apps.json"

    first_store = ConnectedAppsStore(path)
    first_service = ConnectedAppsService(store=first_store)
    first_service._native_adapters["github"].health_check = lambda cid: True
    connected = first_service.connect_native(
        "github", access_token="persisted-test-token",
        connection_id="github-restart",
    )
    assert connected["status"] == "connected"

    # Simulate a fresh JARVIS process with a new service and adapter registry.
    second_store = ConnectedAppsStore(path)
    second_service = ConnectedAppsService(store=second_store)
    second_service._native_adapters["github"].health_check = lambda cid: (
        second_store.get_credential(cid, "access_token") == "persisted-test-token"
    )
    restored = second_service.reconnect("github-restart", attempts=1)

    assert restored["status"] == "connected"
    assert second_store.get_credential("github-restart", "access_token") == "persisted-test-token"


def test_native_connection_recovers_after_transient_network_failure():
    from urllib.error import URLError
    from core.connected_apps.service import ConnectedAppsService

    store = MemoryStore()
    service = ConnectedAppsService(store=store)
    adapter = service._native_adapters["github"]
    states = {"calls": 0}

    def health_check(_cid):
        states["calls"] += 1
        if states["calls"] == 1:
            raise URLError("temporary network outage")
        return True

    adapter.health_check = health_check
    failed = service.connect_native(
        "github", access_token="test-token", connection_id="github-network-recovery"
    )
    assert failed["status"] == "error"
    assert "network" in failed["last_error"].casefold()

    recovered = service.reconnect("github-network-recovery", attempts=1)
    assert recovered["status"] == "connected"
    assert recovered["last_error"] == ""
    assert states["calls"] == 2
