from __future__ import annotations

import io
import json
import urllib.error

import pytest

from core.connected_apps.adapters.google_calendar.adapter import GoogleCalendarAdapter


class Store:
    def __init__(self, token="test-token"):
        self.token = token
    def get_credential(self, connection_id, name):
        return self.token if name == "access_token" else ""


def test_calendar_adapter_catalog_and_confirmation():
    names = {tool.name for tool in GoogleCalendarAdapter.tools}
    assert {"google_calendar_list_events", "google_calendar_get_event", "google_calendar_create_event"} <= names
    create = next(tool for tool in GoogleCalendarAdapter.tools if tool.name == "google_calendar_create_event")
    assert create.read_only is False
    assert create.requires_confirmation is True


def test_calendar_list_events_builds_query(monkeypatch):
    adapter = GoogleCalendarAdapter(Store())
    captured = {}
    def fake_request(cid, method, path, query=None, payload=None):
        captured.update(method=method, path=path, query=query, payload=payload)
        return {"items": []}
    monkeypatch.setattr(adapter, "_request", fake_request)
    adapter.execute("c1", "google_calendar_list_events", {"time_min":"2026-01-01T00:00:00Z","limit":5})
    assert captured["method"] == "GET"
    assert captured["path"] == "/calendars/primary/events"
    assert captured["query"]["timeMin"] == "2026-01-01T00:00:00Z"
    assert captured["query"]["maxResults"] == 5


def test_calendar_create_event_requires_confirmation_and_builds_payload(monkeypatch):
    adapter = GoogleCalendarAdapter(Store())
    captured = {}
    def fake_request(cid, method, path, query=None, payload=None):
        captured.update(method=method, path=path, payload=payload)
        return {"id":"event-1"}
    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.execute("c1", "google_calendar_create_event", {
        "summary":"Meeting", "start":"2026-01-01T10:00:00+05:00",
        "end":"2026-01-01T11:00:00+05:00", "timezone":"Asia/Tashkent"
    })
    assert result["id"] == "event-1"
    assert captured["method"] == "POST"
    assert captured["payload"]["start"]["timeZone"] == "Asia/Tashkent"


def test_calendar_api_error_is_sanitized(monkeypatch):
    adapter = GoogleCalendarAdapter(Store())
    error = urllib.error.HTTPError("https://www.googleapis.com/calendar/v3", 401, "Unauthorized", None, io.BytesIO(b'{"error":{"message":"bad token"}}'))
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: (_ for _ in ()).throw(error))
    with pytest.raises(PermissionError, match="authorization failed"):
        adapter.execute("c1", "google_calendar_list_events", {})
