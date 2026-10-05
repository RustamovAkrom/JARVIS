"""
JARVIS plugin — Water reminder & hydration tracker.

The "drink water" app, but as a voice command. JARVIS nudges the user to
drink at a chosen interval (default every 60 min) and *speaks* the reminder
through the live channel — "you've had 1.2 of your 2 litres, time for a
glass". The user logs sips by voice ("I drank a glass of water") and can ask how
much they've drunk today; the running total lives in memory/water_state.json
and resets each morning, so JARVIS remembers across sessions.

Pure Python, no third-party dependency, cross-OS, language-neutral (spoken
lines are phrased by Gemini in the user's language; the HUD panel is just
emojis + numbers).
"""

import json
import threading
import time
from datetime import date
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent.parent / "memory" / "water_state.json"

PLUGIN = {
    "name": "water_reminder",
    "description": (
        "Reminds the user to DRINK WATER at a regular interval (speaking the "
        "reminder out loud) and tracks how much water they have drunk today "
        "toward a daily goal. Use for: 'remind me to drink water', 'remind me "
        "to drink water every hour', 'I just drank a glass of water', 'log 500 "
        "ml of water', 'how much water have I had today', 'set my daily water "
        "goal to 3 litres', 'stop the water reminders'. This is a repeating "
        "hydration "
        "reminder with intake tracking — do NOT use the 'reminder' tool (that "
        "is for one-off OS notifications)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "What to do: 'start' begin periodic reminders, 'stop' end "
                    "them, 'drink' log that the user drank water, 'status' report "
                    "today's intake, 'goal' set the daily goal. Default 'start'."
                ),
            },
            "amount_ml": {
                "type": "INTEGER",
                "description": "For 'drink': how many ml drunk (default one glass = 250 ml).",
            },
            "interval_minutes": {
                "type": "INTEGER",
                "description": "For 'start': minutes between reminders (default 60).",
            },
            "goal_ml": {
                "type": "INTEGER",
                "description": "For 'goal': daily target in ml (default 2000).",
            },
        },
        "required": [],
    },
}

# ── module-level live state (one reminder loop per process) ──────────────────
_lock = threading.Lock()
_state = {"running": False, "thread": None, "stop": None}


# ── persistent state (settings persist; intake resets daily) ─────────────────

def _load() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    today = date.today().isoformat()
    if data.get("date") != today:
        data = {
            "date": today, "ml": 0,
            "goal": data.get("goal", 2000),
            "interval": data.get("interval", 60),
        }
    data.setdefault("ml", 0)
    data.setdefault("goal", 2000)
    data.setdefault("interval", 60)
    return data


def _save(data: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


# ── HUD helpers (no-ops when player is None) ─────────────────────────────────

def _log(player, msg: str) -> None:
    if player:
        try:
            player.write_log(msg)
        except Exception:
            pass


def _say(player, instruction: str) -> None:
    if player and hasattr(player, "request_say"):
        try:
            player.request_say(instruction)
        except Exception:
            pass


def _panel(player, ml: int, goal: int) -> None:
    if not player:
        return
    pct = min(100, int(ml * 100 / goal)) if goal else 0
    filled = pct // 10
    bar = "🟦" * filled + "⬜" * (10 - filled)
    try:
        player.show_content("💧 HYDRATION",
                            f"{bar}  {pct}%\n{ml} / {goal} ml today")
    except Exception:
        pass


def _clamp(val, default, lo, hi):
    try:
        return max(lo, min(hi, int(val)))
    except (TypeError, ValueError):
        return default


def _remember(data: dict) -> None:
    """Overwrite a single rolling memory note with today's water intake, so JARVIS
    can answer 'how much water did I drink' in a later conversation. One fixed key
    → it replaces the previous value each time (memory never accumulates)."""
    try:
        from memory.memory_manager import remember
        ml, goal = data["ml"], data["goal"]
        glasses = round(ml / 250)
        remember("water_today",
                 f"Drank {ml} ml (~{glasses} glass{'' if glasses == 1 else 'es'}) of "
                 f"water on {data['date']}; daily goal {goal} ml"
                 f"{' — goal reached' if ml >= goal else ''}.", "notes")
    except Exception:
        pass


# ── background reminder loop ─────────────────────────────────────────────────

def _worker(player, stop: threading.Event) -> None:
    try:
        while not stop.is_set():
            interval = _load().get("interval", 60)
            if stop.wait(interval * 60):
                break
            data = _load()
            ml, goal = data["ml"], data["goal"]
            if ml >= goal:
                _say(player,
                     "The user has already reached their daily water goal. "
                     "Briefly congratulate them and say you'll stop the water "
                     "reminders for today. One short sentence.")
                break
            remaining = goal - ml
            _say(player,
                 f"Remind the user to drink a glass of water now. Mention they've "
                 f"had {ml} of {goal} millilitres today, {remaining} to go. "
                 f"Keep it to one short, friendly sentence.")
            _log(player, f"JARVIS: Hydration reminder — {ml}/{goal} ml so far.")
    finally:
        with _lock:
            _state["running"] = False


# ── entry point ───────────────────────────────────────────────────────────────

def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "start").strip().lower()

    # -------- DRINK / LOG --------
    if action in ("drink", "log", "add"):
        data = _load()
        amount = _clamp(parameters.get("amount_ml"), 250, 10, 3000)
        data["ml"] += amount
        _save(data)
        _remember(data)
        _panel(player, data["ml"], data["goal"])
        ml, goal = data["ml"], data["goal"]
        if ml >= goal:
            return (f"Logged {amount} ml. That's {ml} ml — you've reached your "
                    f"{goal} ml goal for today. Well done!")
        return (f"Logged {amount} ml. You've had {ml} of {goal} ml today, "
                f"{goal - ml} ml to go.")

    # -------- STATUS --------
    if action in ("status", "stats"):
        data = _load()
        ml, goal = data["ml"], data["goal"]
        _panel(player, ml, goal)
        pct = int(ml * 100 / goal) if goal else 0
        running = " Reminders are active." if _state["running"] else ""
        if ml >= goal:
            return f"You've had {ml} ml today — {pct}% of your {goal} ml goal. Goal reached!{running}"
        return f"You've had {ml} of {goal} ml today ({pct}%), {goal - ml} ml to go.{running}"

    # -------- GOAL --------
    if action == "goal":
        data = _load()
        data["goal"] = _clamp(parameters.get("goal_ml"), 2000, 250, 10000)
        _save(data)
        _remember(data)
        _panel(player, data["ml"], data["goal"])
        return f"Daily water goal set to {data['goal']} ml. You're at {data['ml']} ml so far."

    # -------- STOP --------
    if action == "stop":
        with _lock:
            running, stop = _state["running"], _state["stop"]
        if not running or stop is None:
            return "Water reminders aren't currently running."
        stop.set()
        return "Water reminders stopped for now."

    # -------- START --------
    with _lock:
        if _state["running"]:
            return "Water reminders are already running."

    data = _load()
    if parameters.get("interval_minutes") is not None:
        data["interval"] = _clamp(parameters.get("interval_minutes"), 60, 5, 600)
        _save(data)
    interval = data["interval"]

    stop = threading.Event()
    with _lock:
        _state.update({"running": True, "stop": stop})
    thread = threading.Thread(target=_worker, args=(player, stop),
                              daemon=True, name="water-reminder")
    with _lock:
        _state["thread"] = thread
    thread.start()

    _panel(player, data["ml"], data["goal"])
    return (f"I'll remind you to drink water every {interval} minutes. "
            f"You've had {data['ml']} of {data['goal']} ml so far today.")
