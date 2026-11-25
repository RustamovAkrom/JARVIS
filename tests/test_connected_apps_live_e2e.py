from __future__ import annotations

import json
import os
import pytest


class LiveCredentialStore:
    def __init__(self, credentials=None, rows=None):
        self.credentials = dict(credentials or {})
        self.rows = dict(rows or {})

    def get_credential(self, connection_id, name):
        return self.credentials.get((connection_id, name), "")

    def set_credential(self, connection_id, name, value):
        self.credentials[(connection_id, name)] = value

    def get_connection(self, connection_id):
        return self.rows.get(connection_id)


def _required_env(name):
    value = os.getenv(name, "").strip()
    if not value:
        pytest.skip(f"Set {name} to enable this live integration check")
    return value


def test_live_github_profile_and_repository_api():
    token = _required_env("JARVIS_E2E_GITHUB_TOKEN")
    from core.connected_apps.adapters.github.adapter import GitHubAdapter

    adapter = GitHubAdapter(LiveCredentialStore({("github-live", "access_token"): token}))
    assert adapter.health_check("github-live") is True

    profile = adapter.execute("github-live", "github_get_profile", {})
    assert isinstance(profile.get("login"), str) and profile["login"]

    repositories = adapter.execute(
        "github-live", "github_list_repositories", {"limit": 1}
    )
    assert isinstance(repositories, list)


def test_live_google_calendar_primary_calendar_api():
    token = _required_env("JARVIS_E2E_GOOGLE_ACCESS_TOKEN")
    from core.connected_apps.adapters.google_calendar.adapter import GoogleCalendarAdapter

    store = LiveCredentialStore(
        {
            ("google-live", "access_token"): token,
            ("google-live", "expires_at"): "0",
        }
    )
    adapter = GoogleCalendarAdapter(store)
    assert adapter.health_check("google-live") is True

    result = adapter.execute(
        "google-live", "google_calendar_list_events",
        {"calendar_id": "primary", "limit": 1},
    )
    assert isinstance(result, dict)
    assert isinstance(result.get("items", []), list)


def test_live_telegram_personal_session_health_and_chat_metadata():
    api_id = _required_env("JARVIS_E2E_TELEGRAM_API_ID")
    api_hash = _required_env("JARVIS_E2E_TELEGRAM_API_HASH")
    session = _required_env("JARVIS_E2E_TELEGRAM_SESSION")

    from core.connected_apps.adapters.telegram_personal import (
        TelegramPersonalAdapter,
        DEFAULT_PERMISSIONS,
    )

    permissions = dict(DEFAULT_PERMISSIONS)
    permissions["list_chats"] = True
    # No message-content permissions are enabled by this test.
    permissions["read_recent_messages"] = False
    store = LiveCredentialStore(
        {
            ("telegram-live", "mtproto_session"): session,
            (
                "telegram-live",
                "telegram_api_config",
            ): json.dumps({"api_id": int(api_id), "api_hash": api_hash}),
        },
        {
            "telegram-live": {
                "id": "telegram-live",
                "app_id": "telegram_personal",
                "status": "connected",
                "enabled": True,
                "metadata": {
                    "telegram_permissions": permissions,
                    "telegram_allowed_chats": [],
                    "native": True,
                },
            }
        },
    )
    adapter = TelegramPersonalAdapter(store)
    try:
        assert adapter.health_check("telegram-live") is True
        chats = adapter.execute(
            "telegram-live", "telegram_list_chats", {"limit": 20}
        )
        assert isinstance(chats, list)

        with pytest.raises(PermissionError, match="No Telegram chats are allowlisted"):
            adapter.execute(
                "telegram-live", "telegram_search_messages",
                {"query": "JARVIS_E2E_NEGATIVE_ALLOWLIST_PROBE", "limit": 1},
            )
    finally:
        adapter.disconnect("telegram-live")
        _stop_telegram_loop(adapter)


def _stop_telegram_loop(adapter):
    loop = getattr(adapter, "_loop", None)
    thread = getattr(adapter, "_loop_thread", None)
    if loop is not None and loop.is_running():
        loop.call_soon_threadsafe(loop.stop)
    if thread is not None and thread.is_alive():
        thread.join(timeout=3)
    if loop is not None and not loop.is_closed():
        loop.close()


def test_live_remote_mcp_streamable_http_lifecycle():
    endpoint = _required_env("JARVIS_E2E_MCP_HTTP_URL")
    headers_text = os.getenv("JARVIS_E2E_MCP_HEADERS_JSON", "{}")
    try:
        headers = json.loads(headers_text)
    except json.JSONDecodeError:
        pytest.fail("JARVIS_E2E_MCP_HEADERS_JSON must be a JSON object")
    if not isinstance(headers, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in headers.items()
    ):
        pytest.fail("JARVIS_E2E_MCP_HEADERS_JSON must contain string keys and values")

    from core.connected_apps.runtime import MCPRuntime

    runtime = MCPRuntime()
    try:
        tools = runtime.connect_http("remote-http-live", endpoint, headers=headers)
        assert isinstance(tools, list)
        assert runtime.health("remote-http-live") is True
        assert runtime.disconnect("remote-http-live") is True
        assert runtime.tools("remote-http-live") == []
    finally:
        runtime.shutdown()


def test_live_remote_mcp_sse_lifecycle():
    endpoint = _required_env("JARVIS_E2E_MCP_SSE_URL")
    headers_text = os.getenv("JARVIS_E2E_MCP_HEADERS_JSON", "{}")
    try:
        headers = json.loads(headers_text)
    except json.JSONDecodeError:
        pytest.fail("JARVIS_E2E_MCP_HEADERS_JSON must be a JSON object")
    if not isinstance(headers, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in headers.items()
    ):
        pytest.fail("JARVIS_E2E_MCP_HEADERS_JSON must contain string keys and values")

    from core.connected_apps.runtime import MCPRuntime

    runtime = MCPRuntime()
    try:
        tools = runtime.connect_sse("remote-sse-live", endpoint, headers=headers)
        assert isinstance(tools, list)
        assert runtime.health("remote-sse-live") is True
        assert runtime.disconnect("remote-sse-live") is True
        assert runtime.tools("remote-sse-live") == []
    finally:
        runtime.shutdown()
