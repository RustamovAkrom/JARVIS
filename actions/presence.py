"""Core Presence action backed by the active process-scoped Presence engine."""
from __future__ import annotations


def presence(parameters: dict, player=None) -> str:
    from core.presence import get_presence_runtime
    from memory.config_manager import (
        get_presence_settings,
        save_presence_settings,
    )

    args = parameters if isinstance(parameters, dict) else {}
    action = str(args.get("action") or "status").strip().lower()
    engine = get_presence_runtime()

    try:
        if action == "leaving":
            if engine is None:
                return "Presence is not running in this session."
            if not engine.enabled:
                return "Presence is switched off; notifications will be delivered normally."
            engine.declare_away()
            return (
                "Noted: you are away. Unprompted notifications will be queued "
                "and summarized when you return."
            )

        if action == "status":
            if engine is None:
                return "Presence is not running in this session."
            runtime = engine.status()
            settings = get_presence_settings()
            if not runtime["enabled"]:
                return "Presence is switched off."
            if runtime["away"]:
                location = (
                    f"User is away ({runtime['reason']}, "
                    f"{runtime['away_minutes']:.0f} min); "
                    f"{runtime['held']} notification(s) queued."
                )
            else:
                location = "User is present."
            return (
                f"{location} Away threshold: {settings['away_minutes']} quiet minutes; "
                f"return greeting: {'on' if settings['greet'] else 'off'}. "
                "Presence uses supported OS idle/lock signals only; no camera or "
                "ambient audio recording is used."
            )

        if action == "settings":
            changes = {}
            if args.get("enabled") is not None:
                changes["enabled"] = bool(args["enabled"])
            if args.get("away_minutes") is not None:
                changes["away_minutes"] = args["away_minutes"]
            if args.get("greet") is not None:
                changes["greet"] = bool(args["greet"])
            if not changes:
                return "Provide at least one setting: enabled, away_minutes, or greet."

            saved = save_presence_settings(**changes)
            if engine is not None:
                engine.apply(saved)
            return (
                f"Presence is {'on' if saved['enabled'] else 'off'}; "
                f"away after {saved['away_minutes']} quiet minutes; "
                f"welcome back {'on' if saved['greet'] else 'off'}."
            )

        return f"Unknown Presence action '{action}'."
    except Exception as exc:
        return f"Presence could not do that: {type(exc).__name__}: {exc}"


TOOL = {
    "name": "presence",
    "description": (
        "Manage Presence awareness. leaving: declare that the user is going away; "
        "unprompted notifications are queued. status: check current away/present state, "
        "queued notifications, threshold and greeting setting. settings: update enabled, "
        "away_minutes (1-240), or greet (welcome back on/off)."
    ),
    "brief": "Presence status, away declaration and settings.",
    "tier": "core",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "enum": ["leaving", "status", "settings"]},
            "enabled": {"type": "BOOLEAN"},
            "away_minutes": {"type": "INTEGER"},
            "greet": {"type": "BOOLEAN"},
        },
        "required": ["action"],
    },
    "handler": presence,
}
