from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from core.connected_apps.adapters.telegram_personal import (
    DEFAULT_PERMISSIONS,
    TelegramPersonalAdapter,
)


class Store:
    def __init__(self, *, permissions=None, allowed_chats=None):
        self.row = {
            "id": "telegram-1",
            "metadata": {
                "telegram_permissions": {
                    **DEFAULT_PERMISSIONS,
                    **(permissions or {}),
                },
                "telegram_allowed_chats": list(allowed_chats or []),
            },
        }

    def get_connection(self, connection_id):
        return self.row if connection_id == self.row["id"] else None

    def get_credential(self, connection_id, name):
        return ""


class FakeMessage:
    id = 42


class FakeTelegramClient:
    def __init__(self):
        self.sent = []
        self.disconnected = False

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        return FakeMessage()

    async def disconnect(self):
        self.disconnected = True


def test_telegram_chat_allowlist_is_enforced_for_message_access():
    adapter = TelegramPersonalAdapter(Store(allowed_chats=["1001"]))

    adapter._require("telegram-1", "send_messages", "1001")
    with pytest.raises(PermissionError, match="not explicitly allowed"):
        adapter._require("telegram-1", "send_messages", "2002")


def test_telegram_disabled_permission_fails_closed():
    adapter = TelegramPersonalAdapter(
        Store(permissions={"send_messages": False}, allowed_chats=["1001"])
    )

    with pytest.raises(PermissionError, match="permission is disabled"):
        adapter._require("telegram-1", "send_messages", "1001")


def test_telegram_send_message_uses_authorized_chat_and_client(monkeypatch):
    store = Store(allowed_chats=["1001"])
    adapter = TelegramPersonalAdapter(store)
    client = FakeTelegramClient()
    monkeypatch.setattr(adapter, "_client", lambda cid: client)
    monkeypatch.setattr(adapter, "_run", lambda coro: asyncio.run(coro))

    result = adapter.execute(
        "telegram-1",
        "telegram_send_message",
        {"chat_id": "1001", "text": "Test message"},
    )

    assert result == {"chat_id": "1001", "message_id": 42, "sent": True}
    assert client.sent == [(1001, "Test message", {})]


def test_telegram_send_message_rejects_non_allowlisted_chat_before_client_use(monkeypatch):
    adapter = TelegramPersonalAdapter(Store(allowed_chats=["1001"]))
    client_requests = []
    monkeypatch.setattr(
        adapter, "_client",
        lambda cid: client_requests.append(cid) or FakeTelegramClient(),
    )

    with pytest.raises(PermissionError, match="not explicitly allowed"):
        adapter.execute(
            "telegram-1",
            "telegram_send_message",
            {"chat_id": "2002", "text": "Must not send"},
        )

    assert client_requests == []


def test_telegram_disconnect_closes_cached_client(monkeypatch):
    adapter = TelegramPersonalAdapter(Store())
    client = FakeTelegramClient()
    adapter._clients["telegram-1"] = client
    monkeypatch.setattr(adapter, "_run", lambda coro: asyncio.run(coro))

    assert adapter.disconnect("telegram-1") is True
    assert client.disconnected is True
    assert "telegram-1" not in adapter._clients
