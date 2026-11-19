"""
Plugin — Google Calendar control.

"What's on my calendar today?", "what's my next meeting?", "add a dentist
appointment tomorrow at 3", "am I free Friday afternoon?". JARVIS reads your
agenda and creates events by voice.

FOR EVERYONE, not one calendar: no account details live in this file. Each user
connects THEIR OWN Google account in the ⚙ plugin-settings tab (OAuth Client
ID/Secret + AUTHORIZE — the browser consent screen is where they choose which
account/calendar). Cross-OS and every-language: JARVIS re-speaks the English
return lines in the user's language, and the assistant converts the user's
spoken date/time into an ISO timestamp before calling this — so there is NO
hard-coded, language-specific date parsing here.

Requires:  pip install google-api-python-client google-auth-oauthlib
(the plugin still loads and explains this if they're missing).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from plugins._google_core import (
    build_settings, get_service,
    explain,
)

_NS = "calendar"
_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

PLUGIN = {
    # For the plugin manager (core/plugin_loader): never sent to a model.
    "level": "advanced",
    "label": "Google Calendar",
    "summary": "Reads your Google Calendar and adds events.",
    "setup": [
        "This one needs a free Google Cloud key of your own (about 10 minutes, once).",
        "Go to console.cloud.google.com and create a project (any name).",
        "APIs & Services → Library: search for the Google Calendar API and press Enable.",
        "APIs & Services → OAuth consent screen (Google Auth Platform): choose External and fill in the app name and your e-mail.",
        "Under Audience, add your own Google address as a Test user — or press Publish app, so Google doesn't ask you to sign in again every 7 days.",
        "APIs & Services → Credentials → Create credentials → OAuth client ID → Desktop app. Copy the Client ID and the Client Secret into these settings and save.",
        "Press Authorize, or just ask me: a browser window asks you to allow access — tick every box and allow it once. If Google says 'Access blocked', the Test user step is missing.",
        "Say: \"What's on my calendar tomorrow?\" or \"Add a meeting on Friday at 3.\"",
    ],
    "name": "calendar_control",
    "description": (
        "Reads the user's GOOGLE CALENDAR and creates events by voice. Use for: "
        "'what's on my calendar today / tomorrow', 'what's my next meeting', "
        "'do I have anything on Friday', 'search my calendar for dentist', 'add "
        "an event / meeting / appointment …', 'schedule …', 'remind me to … at a "
        "specific date and time'. IMPORTANT: convert the user's spoken date/time "
        "into ISO-8601 yourself and pass it in 'start' (e.g. '2026-08-09T15:00'); "
        "for a whole-day item pass just the date ('2026-08-09') and set "
        "'all_day' true. Use 'date' (ISO date) to list a specific day."
    ),
    # Used in place of `description` only when a small model is on a tool
    # budget - Gemini still gets the full text. See providers/schema.py.
    "brief": (
        "Reads and creates calendar events."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": ("'agenda' list a day's events, 'next' the upcoming "
                                "events, 'add' create an event, 'search' find events. "
                                "Default 'agenda'."),
                "enum": ["agenda", "next", "add", "search"],
            },
            "title": {"type": "STRING", "description": "For 'add': the event title/summary."},
            "start": {"type": "STRING",
                      "description": "For 'add': ISO-8601 start, e.g. '2026-08-09T15:00' (or a date for all-day)."},
            "duration_minutes": {"type": "INTEGER", "description": "For 'add': length in minutes (default 60)."},
            "all_day": {"type": "STRING", "description": "For 'add': 'true' for a whole-day event."},
            "date": {"type": "STRING", "description": "For 'agenda': ISO date to list (default today)."},
            "query": {"type": "STRING", "description": "For 'search': free-text to match event titles."},
            "count": {"type": "INTEGER", "description": "For 'next'/'search': how many events (default 5)."},
        },
        "required": [],
    },
}

# Rendered by the ⚙ plugin-settings "token" tab — client id/secret + AUTHORIZE.
PLUGIN_SETTINGS = build_settings(_NS, "📅  GOOGLE CALENDAR", _SCOPES)


# ── time helpers (pure — unit-testable, cross-OS, no tz database needed) ──────

def _local_now() -> datetime:
    return datetime.now().astimezone()


def _parse_dt(s: str) -> datetime:
    """ISO-8601 → tz-aware datetime (assume local tz if none given)."""
    dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_local_now().tzinfo)
    return dt


def _is_all_day(start: str, all_day_flag) -> bool:
    if str(all_day_flag).strip().lower() in ("true", "1", "yes"):
        return True
    return "T" not in (start or "")


def _event_body(title: str, start: str, duration_minutes: int, all_day: bool) -> dict:
    if all_day:
        d = _parse_dt(start).date()
        return {"summary": title,
                "start": {"date": d.isoformat()},
                "end": {"date": (d + timedelta(days=1)).isoformat()}}
    sdt = _parse_dt(start)
    edt = sdt + timedelta(minutes=max(1, duration_minutes))
    return {"summary": title,
            "start": {"dateTime": sdt.isoformat()},
            "end": {"dateTime": edt.isoformat()}}


def _day_bounds(date_iso: str | None):
    d = _parse_dt(date_iso).date() if date_iso else _local_now().date()
    tz = _local_now().tzinfo
    start = datetime(d.year, d.month, d.day, tzinfo=tz)
    return start.isoformat(), (start + timedelta(days=1)).isoformat()


def _fmt_event(ev: dict) -> str:
    start = ev.get("start", {})
    summary = ev.get("summary", "(no title)")
    if start.get("dateTime"):
        try:
            dt = _parse_dt(start["dateTime"])
            return f"{dt.strftime('%a %d %b %H:%M')} — {summary}"
        except Exception:
            return f"{start['dateTime']} — {summary}"
    if start.get("date"):
        return f"{start['date']} (all day) — {summary}"
    return summary


# ── HUD + error helpers ───────────────────────────────────────────────────────

def _panel(player, title: str, text: str) -> None:
    if player:
        try:
            player.show_content(title, text)
        except Exception:
            pass



# ── entry point ───────────────────────────────────────────────────────────────

# What these return was written by somebody else (a file, a page, a mail,
# a video): it reaches the model marked as DATA (core/outcome.untrusted,
# core/prompt.txt [UNTRUSTED CONTENT]), never as instructions.
def run(parameters: dict, player=None, session_memory=None) -> str:
    result = _run_unwrapped(parameters, player, session_memory)
    if str((parameters or {}).get("action") or "agenda").strip().lower() in ("add", "create"):
        return result
    from core import outcome
    return outcome.untrusted("calendar", result)


def _run_unwrapped(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "agenda").strip().lower()

    try:
        service = get_service(_NS, "calendar", "v3", _SCOPES)
    except Exception as e:
        return explain(e, _NS, _SCOPES, "calendar")

    try:
        # ---- ADD ----
        if action in ("add", "create"):
            title = (parameters.get("title") or "").strip()
            start = (parameters.get("start") or "").strip()
            if not title:
                return "What should I call the event?"
            if not start:
                return "When should it be? I need a date and time."
            try:
                dur = max(1, min(1440, int(parameters.get("duration_minutes") or 60)))
            except (TypeError, ValueError):
                dur = 60
            all_day = _is_all_day(start, parameters.get("all_day"))
            try:
                body = _event_body(title, start, dur, all_day)
            except Exception:
                return "I couldn't understand that date and time — could you say it again?"
            ev = service.events().insert(calendarId="primary", body=body).execute()
            when = _fmt_event(ev)
            _panel(player, "📅 CALENDAR — EVENT ADDED", when)
            return f"Added to your calendar: {when}."

        # ---- AGENDA (a single day) ----
        if action in ("agenda", "day", "list"):
            tmin, tmax = _day_bounds((parameters.get("date") or "").strip() or None)
            resp = service.events().list(
                calendarId="primary", timeMin=tmin, timeMax=tmax,
                singleEvents=True, orderBy="startTime").execute()
            items = resp.get("items", [])
            label = "today" if not parameters.get("date") else parameters.get("date")
            if not items:
                return f"Nothing scheduled for {label}."
            lines = [_fmt_event(e) for e in items]
            _panel(player, f"📅 CALENDAR — {str(label).upper()}", "\n".join(lines))
            return (f"You have {len(items)} event{'s' if len(items) != 1 else ''} "
                    f"{('for ' + label) if parameters.get('date') else 'today'}: "
                    + "; ".join(lines) + ".")

        # ---- NEXT (upcoming) ----
        if action in ("next", "upcoming"):
            try:
                count = max(1, min(15, int(parameters.get("count") or 5)))
            except (TypeError, ValueError):
                count = 5
            resp = service.events().list(
                calendarId="primary", timeMin=_local_now().isoformat(),
                singleEvents=True, orderBy="startTime", maxResults=count).execute()
            items = resp.get("items", [])
            if not items:
                return "You have no upcoming events."
            lines = [_fmt_event(e) for e in items]
            _panel(player, "📅 CALENDAR — UPCOMING", "\n".join(lines))
            if len(items) == 1:
                return f"Your next event is {lines[0]}."
            return f"Your next {len(items)} events: " + "; ".join(lines) + "."

        # ---- SEARCH ----
        if action == "search":
            q = (parameters.get("query") or "").strip()
            if not q:
                return "What should I search your calendar for?"
            try:
                count = max(1, min(15, int(parameters.get("count") or 5)))
            except (TypeError, ValueError):
                count = 5
            resp = service.events().list(
                calendarId="primary", q=q, singleEvents=True,
                orderBy="startTime", timeMin=_local_now().isoformat(),
                maxResults=count).execute()
            items = resp.get("items", [])
            if not items:
                return f"I couldn't find any upcoming events matching '{q}'."
            lines = [_fmt_event(e) for e in items]
            _panel(player, f"📅 CALENDAR — SEARCH: {q}", "\n".join(lines))
            return (f"Found {len(items)} event{'s' if len(items) != 1 else ''} "
                    f"for '{q}': " + "; ".join(lines) + ".")

        return "I can show today's agenda, your next events, add an event, or search."

    except Exception as e:
        return explain(e, _NS, _SCOPES, "calendar")
