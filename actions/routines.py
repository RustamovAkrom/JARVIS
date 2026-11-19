"""Voice-manageable JARVIS routines (schedule, list, run, update, remove)."""
from __future__ import annotations

from datetime import datetime
from core.routines import rules
from core.routines.runtime import get_engine

TOOL = {
    "name": "routines",
    "description": "Manage scheduled routines and reminders. Create one-time, daily, weekly, monthly or interval routines; list, update, run now, or remove existing routines.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "operation": {"type": "STRING", "enum": ["create", "list", "run", "update", "remove", "quiet_hours"]},
            "id": {"type": "STRING", "description": "Routine ID for run/update/remove."},
            "name": {"type": "STRING"},
            "kind": {"type": "STRING", "enum": ["say", "prompt", "task", "builtin"]},
            "text": {"type": "STRING", "description": "Message or instruction to execute."},
            "at": {"type": "STRING", "description": "One-time local date/time: YYYY-MM-DD HH:MM."},
            "time": {"type": "STRING", "description": "Daily/weekly/monthly time HH:MM."},
            "days": {"type": "ARRAY", "items": {"type": "INTEGER"}, "description": "ISO weekdays: Monday=1, Sunday=7."},
            "monthly_day": {"type": "INTEGER", "description": "Day of month, 1-31."},
            "every_minutes": {"type": "INTEGER", "description": "Repeat interval, minimum 5 minutes."},
            "between": {"type": "STRING", "description": "Interval window HH:MM-HH:MM."},
            "enabled": {"type": "BOOLEAN"},
            "quiet": {"type": "BOOLEAN"},
            "deliver": {"type": "STRING", "enum": ["voice", "panel", "silent"]},
            "agent": {"type": "STRING"},
            "minutes": {"type": "INTEGER"},
            "builtin": {"type": "STRING", "enum": ["briefing", "system_check"]},
            "quiet_hours": {"type": "STRING", "description": "Set quiet hours HH:MM-HH:MM, or empty to clear."}
        },
        "required": ["operation"]
    },
    "handler": None,
}

def _handler(parameters: dict, **_ctx) -> str:
    engine = get_engine()
    if engine is None:
        return "Routine engine is not available yet."
    args = parameters or {}
    op = str(args.get("operation", "")).lower()
    try:
        if op == "list":
            rows = engine.list()
            if not rows:
                return "There are no routines scheduled."
            return "\n".join(
                f"{r['id']} | {r['name']} | {r['kind']} | {r['trigger']} | "
                f"{'enabled' if r['enabled'] else 'disabled'} | runs={r['runs']}"
                for r in rows
            )
        if op == "create":
            kind = args.get("kind", "prompt")
            rule = rules.rule_from_args(args, datetime.now().astimezone().tzinfo)
            trigger = {"type": "time", "rule": rule}
            row = engine.add(
                args.get("name") or args.get("text") or "Routine",
                kind, args.get("text", ""), trigger,
                enabled=args.get("enabled", True), quiet=args.get("quiet"),
                deliver=args.get("deliver", "voice"), agent=args.get("agent", "general"),
                minutes=args.get("minutes", 10), builtin=args.get("builtin", ""),
            )
            return f"Created routine {row['name']} (ID {row['id']})."
        if op == "run":
            return f"Routine {args.get('id')}: {engine.run_now(args.get('id', ''))}"
        if op == "update":
            fields = {k: args[k] for k in ("name", "text", "enabled", "quiet", "deliver", "agent", "minutes") if k in args}
            if any(k in args for k in ("at", "time", "days", "monthly_day", "every_minutes", "between")):
                fields["trigger"] = {"type": "time", "rule": rules.rule_from_args(args, datetime.now().astimezone().tzinfo)}
            row = engine.update(args.get("id", ""), **fields)
            return f"Updated routine {row['name']} (ID {row['id']})."
        if op == "remove":
            return "Routine removed." if engine.remove(args.get("id", "")) else "Routine not found."
        if op == "quiet_hours":
            value = engine.set_quiet_hours(args.get("quiet_hours", ""))
            return f"Quiet hours set to {value or 'disabled'}."
        return "Unsupported routine operation."
    except (ValueError, TypeError, KeyError) as exc:
        return f"Could not manage routine: {exc}"

TOOL["handler"] = _handler
