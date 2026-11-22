"""Google Calendar native Connected App adapter."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from core.connected_apps.native import NativeAppAdapter, NativeAppDescriptor, NativeTool


def _schema(properties, required=()):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


class GoogleCalendarAdapter(NativeAppAdapter):
    descriptor = NativeAppDescriptor(
        id="google_calendar",
        name="Google Calendar",
        description="Read calendar events and create events with user confirmation.",
        category="productivity",
        icon="google-calendar",
        auth_provider="google",
        scopes=("https://www.googleapis.com/auth/calendar.events",),
    )

    tools = (
        NativeTool(
            "google_calendar_list_events",
            "List events from a Google Calendar in a time range.",
            _schema({
                "calendar_id": {"type": "string", "maxLength": 512},
                "time_min": {"type": "string", "maxLength": 64},
                "time_max": {"type": "string", "maxLength": 64},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            }),
        ),
        NativeTool(
            "google_calendar_create_event",
            "Create a calendar event. Requires user confirmation.",
            _schema({
                "calendar_id": {"type": "string", "maxLength": 512},
                "summary": {"type": "string", "minLength": 1, "maxLength": 1024},
                "description": {"type": "string", "maxLength": 8192},
                "start": {"type": "string", "minLength": 1, "maxLength": 128},
                "end": {"type": "string", "minLength": 1, "maxLength": 128},
                "timezone": {"type": "string", "maxLength": 128},
                "location": {"type": "string", "maxLength": 1024},
            }, ("summary", "start", "end")),
            read_only=False,
            requires_confirmation=True,
        ),
        NativeTool(
            "google_calendar_get_event",
            "Get a Google Calendar event by ID.",
            _schema({
                "calendar_id": {"type": "string", "maxLength": 512},
                "event_id": {"type": "string", "minLength": 1, "maxLength": 512},
            }, ("event_id",)),
        ),
    )

    def _token(self, connection_id):
        from core.connected_apps.auth_service import OAuthService

        token = OAuthService(self.store).get_access_token(connection_id)
        if not token:
            raise PermissionError("Google Calendar is not authorized")
        return token

    def _request(self, connection_id, method, path, query=None, payload=None):
        if not path.startswith("/") or ".." in path.split("/"):
            raise ValueError("Invalid Google Calendar API path")
        url = "https://www.googleapis.com/calendar/v3" + path
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token(connection_id)}",
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if data is not None else {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read(2_000_000)
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read(8192)).get("error", {}).get("message", "")
            except Exception:
                detail = ""
            if exc.code in (401, 403):
                raise PermissionError(f"Google Calendar authorization failed ({exc.code}): {detail[:300]}") from None
            if exc.code == 404:
                raise LookupError("Calendar or event was not found") from None
            raise RuntimeError(f"Google Calendar API returned HTTP {exc.code}: {detail[:300]}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"Google Calendar API connection failed: {type(exc).__name__}") from None

    @staticmethod
    def _calendar_id(value):
        value = str(value or "primary").strip() or "primary"
        if len(value) > 512:
            raise ValueError("Calendar ID is too long")
        return urllib.parse.quote(value, safe="")

    def execute(self, connection_id, tool_name, arguments):
        args = dict(arguments or {})
        calendar_id = self._calendar_id(args.get("calendar_id", "primary"))
        if tool_name == "google_calendar_list_events":
            query = {
                "maxResults": max(1, min(int(args.get("limit", 20)), 100)),
                "singleEvents": "true",
                "orderBy": "startTime",
            }
            if args.get("time_min"):
                query["timeMin"] = args["time_min"]
            if args.get("time_max"):
                query["timeMax"] = args["time_max"]
            return self._request(connection_id, "GET", f"/calendars/{calendar_id}/events", query)
        if tool_name == "google_calendar_get_event":
            event_id = urllib.parse.quote(str(args["event_id"]), safe="")
            return self._request(connection_id, "GET", f"/calendars/{calendar_id}/events/{event_id}")
        if tool_name == "google_calendar_create_event":
            start = {"dateTime": args["start"]}
            end = {"dateTime": args["end"]}
            if args.get("timezone"):
                start["timeZone"] = args["timezone"]
                end["timeZone"] = args["timezone"]
            payload = {
                "summary": args["summary"],
                "start": start,
                "end": end,
            }
            if args.get("description"):
                payload["description"] = args["description"]
            if args.get("location"):
                payload["location"] = args["location"]
            return self._request(connection_id, "POST", f"/calendars/{calendar_id}/events", payload=payload)
        raise ValueError(f"Unknown Google Calendar tool: {tool_name}")

    def health_check(self, connection_id):
        self._request(connection_id, "GET", "/users/me/calendarList", {"maxResults": 1})
        return True
