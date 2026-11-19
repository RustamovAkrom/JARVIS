"""Pure time-rule helpers for the future unified routine scheduler.

Rules are serializable data. This module intentionally has no runtime,
threading, GUI, or persistence dependencies so scheduling can be tested in
isolation.
"""
from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta

ALL_DAYS = [1, 2, 3, 4, 5, 6, 7]


def parse_hhmm(value: object) -> tuple[int, int]:
    text = str(value or "").strip().replace(".", ":")
    hour, _, minute = text.partition(":")
    try:
        hour, minute = int(hour), int(minute or 0)
    except ValueError:
        raise ValueError(f"{value!r} is not a time of day (HH:MM)") from None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"{value!r} is not a time of day (HH:MM)")
    return hour, minute


def hhmm(value: object) -> str:
    hour, minute = parse_hhmm(value)
    return f"{hour:02d}:{minute:02d}"


def parse_days(value: object = None) -> list[int]:
    if value in (None, "", []):
        return list(ALL_DAYS)
    items = value if isinstance(value, (list, tuple)) else re.split(r"[^\d]+", str(value))
    days = sorted({int(item) for item in items if str(item).strip().isdigit() and 1 <= int(item) <= 7})
    if not days:
        raise ValueError("days must contain ISO weekday numbers 1-7 (Monday=1)")
    return days


def next_fire(rule: dict, after: datetime) -> datetime | None:
    """Return the first scheduled moment strictly after ``after``."""
    if not isinstance(rule, dict):
        raise ValueError("rule must be an object")
    kind = rule.get("type")
    if kind == "once":
        at = datetime.fromtimestamp(float(rule["at_ts"]), after.tzinfo)
        return at if at > after else None

    days = set(parse_days(rule.get("days")))
    if kind == "weekly":
        hour, minute = parse_hhmm(rule["time"])
        for offset in range(8):
            candidate = (after + timedelta(days=offset)).replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
            if candidate > after and candidate.isoweekday() in days:
                return candidate
        return None

    if kind == "monthly":
        hour, minute = parse_hhmm(rule["time"])
        wanted_day = int(rule["day"])
        if not 1 <= wanted_day <= 31:
            raise ValueError("monthly day must be between 1 and 31")
        year, month = after.year, after.month
        for _ in range(14):
            day = min(wanted_day, calendar.monthrange(year, month)[1])
            candidate = datetime(year, month, day, hour, minute, tzinfo=after.tzinfo)
            if candidate > after:
                return candidate
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        return None

    if kind == "interval":
        minutes = int(rule["minutes"])
        if minutes < 5:
            raise ValueError("interval must be at least 5 minutes")
        start_h, start_m = parse_hhmm(rule.get("start") or "00:00")
        end_h, end_m = parse_hhmm(rule.get("end") or "23:59")
        start_tuple, end_tuple = (start_h, start_m), (end_h, end_m)
        if start_tuple > end_tuple:
            raise ValueError("interval start must not be later than end")
        for offset in range(8):
            base = (after + timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
            if base.isoweekday() not in days:
                continue
            start = base.replace(hour=start_h, minute=start_m)
            end = base.replace(hour=end_h, minute=end_m)
            candidate = start
            if after >= start:
                elapsed = int((after - start).total_seconds() // (minutes * 60)) + 1
                candidate = start + timedelta(minutes=minutes * elapsed)
            if start <= candidate <= end and candidate > after:
                return candidate
        return None

    raise ValueError(f"unsupported rule type: {kind!r}")


def describe_rule(rule: dict, tz=None) -> str:
    kind = rule.get("type")
    days = parse_days(rule.get("days"))
    day_suffix = "" if days == ALL_DAYS else " on weekdays " + ",".join(map(str, days))
    if kind == "once":
        return "once at " + datetime.fromtimestamp(float(rule["at_ts"]), tz).strftime("%Y-%m-%d %H:%M")
    if kind == "weekly":
        return f"at {hhmm(rule['time'])}" + (day_suffix or " every day")
    if kind == "monthly":
        return f"on day {int(rule['day'])} of every month at {hhmm(rule['time'])}"
    if kind == "interval":
        return f"every {int(rule['minutes'])} min, {hhmm(rule.get('start') or '00:00')}-{hhmm(rule.get('end') or '23:59')}{day_suffix}"
    return str(kind)


def rule_from_args(args: dict, tz) -> dict:
    """Validate model/tool arguments and convert them into a canonical rule."""
    args = args if isinstance(args, dict) else {}
    days = parse_days(args.get("days"))
    time_of_day = args.get("time") or args.get("daily")
    if args.get("every_minutes") is not None:
        minutes = int(args["every_minutes"])
        if minutes < 5:
            raise ValueError("every_minutes must be at least 5")
        start, end = "00:00", "23:59"
        if args.get("between"):
            first, sep, last = str(args["between"]).replace(" ", "").partition("-")
            if not sep:
                raise ValueError("between must be HH:MM-HH:MM")
            start, end = hhmm(first), hhmm(last)
            if parse_hhmm(start) > parse_hhmm(end):
                raise ValueError("between start must not be later than end")
        return {"type": "interval", "minutes": minutes, "start": start, "end": end, "days": days}
    if args.get("monthly_day") is not None:
        day = int(args["monthly_day"])
        if not 1 <= day <= 31:
            raise ValueError("monthly_day must be between 1 and 31")
        if not time_of_day:
            raise ValueError("a monthly routine needs a time (HH:MM)")
        return {"type": "monthly", "day": day, "time": hhmm(time_of_day)}
    if time_of_day:
        return {"type": "weekly", "time": hhmm(time_of_day), "days": days}
    if args.get("at"):
        value = str(args["at"]).strip().replace("T", " ")
        try:
            when = datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        except ValueError:
            raise ValueError("at must be YYYY-MM-DD HH:MM") from None
        return {"type": "once", "at_ts": when.timestamp()}
    raise ValueError("specify at, time (+ days), monthly_day + time, or every_minutes")
