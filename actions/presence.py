"""presence — the user says they are leaving; status and settings of
core/presence.py (noticing when the user is away and back)."""
from __future__ import annotations


def presence(parameters: dict, player=None) -> str:
    from core import presence as P
    a = parameters or {}
    action = str(a.get("action") or "status").strip().lower()
    p = P.get()
    try:
        if action == "leaving":
            if p is None or not p.enabled:
                return "Presence is switched off, so I will speak as things come in."
            p.declare_away()
            return ("Noted: the user is away. Anything that comes in is held and told when they "
                    "are back. Say a short goodbye.")
        if action == "status":
            if p is None:
                return "Presence is not running."
            s = p.status()
            if not s["enabled"]:
                return "Presence is switched off."
            how = []
            how.append("keyboard and mouse" if s["idle_detection"] else
                       "not keyboard and mouse (this system does not report them)")
            if s["lock_detection"]:
                how.append("the screen lock")
            where = (f"The user is away ({s['reason']}, {s['away_minutes']:.0f} min), "
                     f"{s['held']} thing(s) held." if s["away"] else "The user is here.")
            from memory.config_manager import get_presence
            return (f"{where} Away after {get_presence()['away_minutes']} quiet minutes; "
                    f"watching {', '.join(how)} and voice. No camera, nothing recorded.")
        if action == "settings":
            from memory.config_manager import save_presence, get_presence
            changes = {}
            if a.get("enabled") is not None:
                changes["enabled"] = bool(a["enabled"])
            if a.get("away_minutes"):
                changes["away_minutes"] = int(a["away_minutes"])
            if a.get("greet") is not None:
                changes["greet"] = bool(a["greet"])
            if not changes:
                return "Say what to change: on/off, the minutes, or the welcome back."
            save_presence(**changes)
            cur = get_presence()
            if p is not None:
                p.enabled, p.away_minutes = cur["enabled"], cur["away_minutes"]
            return (f"Presence is {'on' if cur['enabled'] else 'off'}; away after "
                    f"{cur['away_minutes']} quiet minutes; welcome back "
                    f"{'on' if cur['greet'] else 'off'}.")
        return f"Unknown action '{action}'."
    except Exception as e:
        return f"Presence could not do that: {type(e).__name__}: {e}"


TOOL = {
    "name": "presence",
    "description": (
        "Knowing whether the user is at the computer. leaving: the user says they are going "
        "away for a while ('I'm heading out', 'back in an hour', 'going to sleep') - you then "
        "hold anything unprompted and tell them when they are back. status: is the user "
        "considered here, what is being held, how it is noticed. settings: enabled "
        "(on/off), away_minutes (quiet minutes before away), greet (welcome back on/off)."),
    "brief": "User leaving / presence status and settings.",
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
