"""
JARVIS plugin — Birthday & Event Countdown (event_countdown.py).

Tracks birthdays, anniversaries, bill due dates, holidays, and milestones with
exact day countdowns. Reminds the user of upcoming events, announces birthdays
celebrating today, and keeps events persistently saved in memory/events.json.

Actions:
  - add:       Add a new birthday, anniversary, or event
  - list:      Show all upcoming events ordered by days remaining
  - countdown: Get the countdown for a specific event or person
  - delete:    Remove an event by name
  - today:     Check if any event or birthday is happening today
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

try:
    from core.paths import data_dir as _data_dir
    _MEMORY_DIR = _data_dir() / "memory"
except Exception:
    _MEMORY_DIR = Path(__file__).resolve().parent.parent / "memory"

_EVENTS_FILE = _MEMORY_DIR / "events.json"

PLUGIN = {
    "name": "event_countdown",
    "description": (
        "Tracks birthdays, anniversaries, holidays, bill due dates, and special events with "
        "days remaining countdowns. Use when the user says: 'when is Malu's birthday', 'how many "
        "days until New Year', 'add Mom's birthday on October 14th', 'remind me about electricity "
        "bill on 25th November', 'what events or birthdays are coming up', 'delete the event X', "
        "or 'is there any birthday today'. Automatically advances yearly birthdays and anniversaries."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["add", "list", "countdown", "delete", "today"],
                "description": (
                    "What to do: 'add' create an event, 'list' view all upcoming events, "
                    "'countdown' check days left for a specific event, 'delete' remove an event, "
                    "'today' check events happening today. Default is 'list'."
                ),
            },
            "title": {
                "type": "STRING",
                "description": "Name or description of the event (e.g. 'Malu's Birthday', 'Electricity Bill', 'Anniversary').",
            },
            "date": {
                "type": "STRING",
                "description": "Date of the event. Supports formats like 'YYYY-MM-DD', 'MM-DD', 'June 15', '15 October', '15/12'.",
            },
            "category": {
                "type": "STRING",
                "enum": ["birthday", "anniversary", "bill", "holiday", "event"],
                "description": "Category of the event. Default is 'event' or auto-detected if title contains 'birthday'/'anniversary'.",
            },
            "recurring": {
                "type": "BOOLEAN",
                "description": "Whether the event repeats every year (true for birthdays/anniversaries). Default is true for birthdays/anniversaries.",
            },
        },
        "required": [],
    },
}


# ── Storage Helpers ─────────────────────────────────────────────────────────

def _load_events() -> list[dict[str, Any]]:
    try:
        if _EVENTS_FILE.exists():
            data = json.loads(_EVENTS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def _save_events(events: list[dict[str, Any]]) -> None:
    try:
        _EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _EVENTS_FILE.write_text(json.dumps(events, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


# ── Date Parsing ────────────────────────────────────────────────────────────

_MONTH_NAMES = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12
}


def _parse_event_date(date_str: str) -> tuple[Optional[int], int, int]:
    """Returns (year_or_none, month, day)."""
    s = date_str.strip().lower()
    # Strip ordinals like 1st, 2nd, 3rd, 4th
    s = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", s)

    # 1. ISO format: YYYY-MM-DD
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", s)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))

    # 2. DD/MM/YYYY or DD-MM-YYYY
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$", s)
    if m:
        return int(m.group(3)), int(m.group(2)), int(m.group(1))

    # 3. MM-DD or DD-MM without year
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})$", s)
    if m:
        # Default assume month/day or day/month
        val1, val2 = int(m.group(1)), int(m.group(2))
        if val1 > 12:
            return None, val2, val1
        return None, val1, val2

    # 4. Text formats: 'June 15', '15 June', 'October 24 2026'
    parts = re.split(r"[\s,]+", s)
    month = None
    day = None
    year = None

    for part in parts:
        if part in _MONTH_NAMES:
            month = _MONTH_NAMES[part]
        elif part.isdigit():
            n = int(part)
            if n > 1900 and year is None:
                year = n
            elif n <= 31 and day is None:
                day = n
            elif n <= 12 and month is None:
                month = n

    if month and day:
        return year, month, day

    return None, 0, 0


def _get_next_occurrence(ev: dict[str, Any], today: date) -> tuple[date, int]:
    """Calculate the next target date and days remaining."""
    month = ev["month"]
    day = ev["day"]
    year = ev.get("year")
    recurring = ev.get("recurring", True)

    if not recurring and year:
        target = date(year, month, day)
        return target, (target - today).days

    # Recurring: test this year, otherwise next year
    try:
        target = date(today.year, month, day)
    except ValueError:
        # Leap day Feb 29 on non-leap year -> Feb 28
        target = date(today.year, month, 28)

    if target < today:
        try:
            target = date(today.year + 1, month, day)
        except ValueError:
            target = date(today.year + 1, month, 28)

    days_left = (target - today).days
    return target, days_left


# ── Main Runner ─────────────────────────────────────────────────────────────

def run(parameters: dict[str, Any], player=None) -> str:
    action = str(parameters.get("action") or "list").strip().lower()
    title = str(parameters.get("title") or "").strip()
    date_input = str(parameters.get("date") or "").strip()
    today = date.today()

    events = _load_events()

    # ── 1. ADD EVENT ────────────────────────────────────────────────────────
    if action == "add":
        if not title:
            return "Please specify the event or person's name (e.g. 'Malu's Birthday')."
        if not date_input:
            return f"What date is '{title}' on? (e.g. 'June 15' or '2026-11-25')."

        year, month, day = _parse_event_date(date_input)
        if not month or not day:
            return f"I couldn't understand the date '{date_input}'. Please use a format like 'June 15' or '2026-12-25'."

        # Auto-detect category
        cat = parameters.get("category")
        if not cat:
            low_title = title.lower()
            if "birthday" in low_title or "bday" in low_title:
                cat = "birthday"
            elif "anniversary" in low_title:
                cat = "anniversary"
            elif "bill" in low_title or "rent" in low_title or "due" in low_title:
                cat = "bill"
            else:
                cat = "event"

        recurring = parameters.get("recurring")
        if recurring is None:
            recurring = (cat in ["birthday", "anniversary"])

        # Check if already exists; if so, update
        existing = next((e for e in events if e.get("title", "").lower() == title.lower()), None)
        if existing:
            existing.update({
                "title": title, "year": year, "month": month, "day": day,
                "category": cat, "recurring": recurring, "updated_at": today.isoformat()
            })
            _save_events(events)
            next_date, days_left = _get_next_occurrence(existing, today)
            days_str = "TODAY! 🎉" if days_left == 0 else ("TOMORROW!" if days_left == 1 else f"in {days_left} days")
            return f"Updated '{title}' to {next_date.strftime('%B %d, %Y')} ({days_str})."

        new_ev = {
            "id": f"ev_{int(datetime.now().timestamp())}",
            "title": title,
            "year": year,
            "month": month,
            "day": day,
            "category": cat,
            "recurring": recurring,
            "created_at": today.isoformat()
        }
        events.append(new_ev)
        _save_events(events)

        next_date, days_left = _get_next_occurrence(new_ev, today)
        days_str = "TODAY! 🎉" if days_left == 0 else ("TOMORROW! ⏳" if days_left == 1 else f"in {days_left} days ({next_date.strftime('%A, %B %d')})")
        return f"Added '{title}' ({cat}) on {next_date.strftime('%B %d')}. It is {days_str}."

    # ── 2. TODAY ────────────────────────────────────────────────────────────
    if action == "today":
        today_events = []
        for ev in events:
            _, days_left = _get_next_occurrence(ev, today)
            if days_left == 0:
                today_events.append(ev)

        if not today_events:
            return "No birthdays or events scheduled for today, sir."

        lines = [f"🎉 TODAY'S EVENTS ({today.strftime('%B %d')}):"]
        for ev in today_events:
            emoji = "🎂" if ev["category"] == "birthday" else ("💍" if ev["category"] == "anniversary" else "📅")
            lines.append(f"  {emoji} {ev['title']} ({ev['category'].upper()})")
        return "\n".join(lines)

    # ── 3. COUNTDOWN FOR SPECIFIC EVENT ─────────────────────────────────────
    if action == "countdown":
        if not title:
            # If no title given, list upcoming
            action = "list"
        else:
            matches = [e for e in events if title.lower() in e.get("title", "").lower()]
            if not matches:
                return f"I couldn't find any event or birthday matching '{title}'. Say 'add {title}' to schedule it."

            ev = matches[0]
            next_date, days_left = _get_next_occurrence(ev, today)
            emoji = "🎂" if ev["category"] == "birthday" else ("💍" if ev["category"] == "anniversary" else "📅")

            if days_left == 0:
                return f"🎉 {ev['title']} is TODAY ({next_date.strftime('%B %d')})! Don't forget to wish them!"
            elif days_left == 1:
                return f"⏳ {ev['title']} is TOMORROW, {next_date.strftime('%A, %B %d')}!"
            else:
                weeks = days_left // 7
                rem_days = days_left % 7
                time_desc = f"{days_left} days" if weeks == 0 else f"{weeks} week{'s' if weeks > 1 else ''} and {rem_days} days ({days_left} days total)"
                return f"{emoji} {ev['title']} is in {time_desc}, on {next_date.strftime('%A, %B %d, %Y')}."

    # ── 4. DELETE EVENT ─────────────────────────────────────────────────────
    if action == "delete":
        if not title:
            return "Which event or birthday would you like to remove?"
        initial_len = len(events)
        events = [e for e in events if title.lower() not in e.get("title", "").lower()]
        if len(events) == initial_len:
            return f"No event found matching '{title}' to delete."
        _save_events(events)
        return f"Removed event '{title}' from your countdown schedule."

    # ── 5. LIST UPCOMING EVENTS ─────────────────────────────────────────────
    if not events:
        return "You have no upcoming events or birthdays saved yet. Say 'Jarvis, add Malu's birthday on June 15' to get started."

    # Sort events by days remaining
    annotated = []
    for ev in events:
        next_date, days_left = _get_next_occurrence(ev, today)
        annotated.append((days_left, next_date, ev))

    annotated.sort(key=lambda x: x[0])

    lines = [f"📅 UPCOMING EVENTS & BIRTHDAYS ({len(events)} total):"]
    for days_left, next_date, ev in annotated:
        emoji = "🎂" if ev["category"] == "birthday" else ("💍" if ev["category"] == "anniversary" else ("💳" if ev["category"] == "bill" else "📌"))
        if days_left == 0:
            when = "TODAY! 🎉"
        elif days_left == 1:
            when = "Tomorrow ⏳"
        elif days_left < 7:
            when = f"in {days_left} days ({next_date.strftime('%a')})"
        elif days_left < 30:
            when = f"in {days_left} days ({next_date.strftime('%b %d')})"
        else:
            when = f"in {days_left} days ({next_date.strftime('%b %d, %Y')})"

        lines.append(f"  {emoji} {ev['title']:<25} ➔ {when}")

    return "\n".join(lines)
