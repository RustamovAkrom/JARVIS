"""
JARVIS plugin — Pomodoro focus timer.

Runs the classic Pomodoro technique entirely in the background: a focus
block (default 25 min), then a short break (default 5 min), with a longer
break after every 4th focus block. When each phase ends JARVIS *speaks* —
"time for a break", "back to work" — through the live speech channel, so
the user never has to watch a clock.

Every completed focus block is logged to memory/pomodoro_state.json, so the
user can ask "how much have I focused today?" and JARVIS remembers across
sessions — the kind of small, persistent detail Gemini alone can't keep.

Pure Python, no third-party dependency, works on every OS and in every
language (the spoken lines are phrased by Gemini in the user's language;
the HUD panel stays language-neutral: emojis + numbers).
"""

import json
import threading
import time
from datetime import date
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent.parent / "memory" / "pomodoro_state.json"

PLUGIN = {
    "name": "pomodoro",
    "description": (
        "Runs a POMODORO focus timer in the background and speaks when it is "
        "time for a break or time to get back to work, and remembers how much "
        "the user has focused today. Use for: 'start a pomodoro', 'start a "
        "50 minute focus session', 'stop the timer', 'how much have I focused "
        "today', 'how many minutes are left'. This is a repeating focus/break "
        "timer with study tracking — do "
        "NOT use the 'reminder' tool (that is for one-off OS notifications) or "
        "'agent_task' for these requests."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "What to do: 'start' to begin a focus timer, 'stop' to end "
                    "it, 'status' for how much time is left in the current "
                    "phase, 'stats' for today's total focus. Default 'start'."
                ),
            },
            "work_minutes": {
                "type": "INTEGER",
                "description": "Length of a focus block in minutes (default 25).",
            },
            "break_minutes": {
                "type": "INTEGER",
                "description": "Length of a short break in minutes (default 5).",
            },
            "task": {
                "type": "STRING",
                "description": "Optional: what the user is focusing on (logged per task).",
            },
        },
        "required": [],
    },
}

# ── module-level live state (one timer per process) ──────────────────────────
_lock = threading.Lock()
_state = {
    "running":   False,
    "thread":    None,
    "stop":      None,     # threading.Event
    "phase":     None,     # "work" | "break"
    "phase_end": 0.0,      # epoch seconds
    "work_min":  25,
    "break_min": 5,
    "long_min":  15,
    "task":      "",
    "completed": 0,        # focus blocks completed this run
}


# ── persistent daily stats ────────────────────────────────────────────────────

def _load_stats() -> dict:
    """Load today's stats, rolling over to a fresh record at midnight."""
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    today = date.today().isoformat()
    if data.get("date") != today:
        data = {"date": today, "sessions": 0, "minutes": 0, "tasks": {}}
    return data


def _save_stats(data: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _record_focus(minutes: int, task: str) -> None:
    data = _load_stats()
    data["sessions"] = data.get("sessions", 0) + 1
    data["minutes"] = data.get("minutes", 0) + minutes
    if task:
        data.setdefault("tasks", {})
        data["tasks"][task] = data["tasks"].get(task, 0) + minutes
    _save_stats(data)


# ── HUD helpers (all no-ops when player is None) ──────────────────────────────

def _log(player, msg: str) -> None:
    if player:
        try:
            player.write_log(msg)
        except Exception:
            pass


def _panel(player, title: str, text: str) -> None:
    if player:
        try:
            player.show_content(title, text)
        except Exception:
            pass


def _say(player, instruction: str) -> None:
    """Ask JARVIS to speak (thread-safe; phrased in the user's language)."""
    if player and hasattr(player, "request_say"):
        try:
            player.request_say(instruction)
        except Exception:
            pass


def _mmss(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _bar(frac: float, width: int = 18) -> str:
    """A language-neutral block progress bar."""
    frac = max(0.0, min(1.0, frac))
    filled = int(round(frac * width))
    return "▓" * filled + "░" * (width - filled)


def _remember_focus() -> None:
    """Overwrite a single rolling memory note with today's focus total, so JARVIS
    can answer 'how much did I focus' in a later conversation. One fixed key →
    it replaces the previous value instead of piling up (memory never bloats)."""
    try:
        from memory.memory_manager import remember
        s = _load_stats()
        if s.get("sessions"):
            remember("focus_today",
                     f"Focused for {s['minutes']} minutes across {s['sessions']} "
                     f"pomodoro session(s) on {s['date']}.", "notes")
    except Exception:
        pass


# ── the background timer loop (ticks every second → live UI countdown) ───────

def _run_phase(player, stop: threading.Event, phase: str, minutes: float,
               task: str) -> bool:
    """Count a single phase down second-by-second, refreshing the HUD panel.
    Returns True if it ran to completion, False if the user stopped it."""
    total = max(0.001, minutes * 60)
    end = time.time() + total
    icon, label = ("🎯", "FOCUS") if phase == "work" else ("☕", "BREAK")
    with _lock:
        _state["phase"] = phase
        _state["phase_end"] = end

    while True:
        remaining = end - time.time()
        if remaining <= 0:
            return True
        frac = 1.0 - remaining / total
        stats = _load_stats()
        body = (f"{icon}  {label}\n\n"
                f"      {_mmss(remaining)}\n\n"
                f"{_bar(frac)}  {int(frac * 100)}%")
        if task and phase == "work":
            body += f"\n\n📌 {task}"
        body += f"\n\n✅ Today: {stats['sessions']} sessions · {stats['minutes']} min"
        _panel(player, f"🍅 {label} · {_mmss(remaining)}", body)
        if stop.wait(min(1.0, remaining)):
            return False  # stopped mid-phase


def _worker(player, stop: threading.Event) -> None:
    cycle = 0
    try:
        while not stop.is_set():
            wmin = _state["work_min"]
            task = _state["task"]
            _log(player, f"JARVIS: Focus block started — {wmin} min.")
            if not _run_phase(player, stop, "work", wmin, task):
                break  # stopped mid-focus → not counted

            cycle += 1
            with _lock:
                _state["completed"] += 1
            _record_focus(wmin, task)
            _remember_focus()

            is_long = (cycle % 4 == 0)
            bmin = _state["long_min"] if is_long else _state["break_min"]
            _say(player,
                 f"A {wmin}-minute focus block just finished. Briefly congratulate "
                 f"the user and tell them to take a {bmin}-minute "
                 f"{'long ' if is_long else ''}break. Keep it to one short sentence.")
            if not _run_phase(player, stop, "break", bmin, ""):
                break

            _say(player,
                 "The break is over. Briefly and encouragingly tell the user it's "
                 "time to start the next focus block. One short sentence.")
    finally:
        with _lock:
            _state["running"] = False
            _state["phase"] = None
            _state["phase_end"] = 0.0
        _remember_focus()
        stats = _load_stats()
        _panel(player, "🍅 POMODORO",
               f"⏹ Stopped\n\n✅ Today: {stats['sessions']} sessions · {stats['minutes']} min")


# ── entry point ───────────────────────────────────────────────────────────────

def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "start").strip().lower()

    # -------- STATUS --------
    if action == "status":
        with _lock:
            running = _state["running"]
            phase = _state["phase"]
            end = _state["phase_end"]
        if not running or not phase:
            return "No pomodoro is running right now. Say 'start a pomodoro' to begin."
        remaining = _mmss(end - time.time())
        label = "focus block" if phase == "work" else "break"
        return f"You're in a {label} with {remaining} remaining."

    # -------- STATS --------
    if action == "stats":
        stats = _load_stats()
        if not stats.get("sessions"):
            return "You haven't completed any focus sessions yet today."
        msg = (f"Today you've completed {stats['sessions']} focus "
               f"{'session' if stats['sessions'] == 1 else 'sessions'}, "
               f"{stats['minutes']} minutes in total.")
        tasks = stats.get("tasks") or {}
        if tasks:
            top = ", ".join(f"{k}: {v} min" for k, v in
                            sorted(tasks.items(), key=lambda kv: -kv[1])[:3])
            msg += f" Breakdown — {top}."
        return msg

    # -------- STOP --------
    if action == "stop":
        with _lock:
            running = _state["running"]
            stop = _state["stop"]
            done = _state["completed"]
        if not running or stop is None:
            return "No pomodoro session is currently running."
        stop.set()
        return (f"Pomodoro stopped. You completed {done} focus "
                f"{'block' if done == 1 else 'blocks'} this run.")

    # -------- START --------
    with _lock:
        if _state["running"]:
            phase = _state["phase"] or "focus"
            return f"A pomodoro is already running — you're in a {phase} phase."

    def _clamp(val, default, lo, hi):
        try:
            return max(lo, min(hi, int(val)))
        except (TypeError, ValueError):
            return default

    work_min = _clamp(parameters.get("work_minutes"), 25, 1, 180)
    break_min = _clamp(parameters.get("break_minutes"), 5, 1, 60)
    task = (parameters.get("task") or "").strip()[:60]

    stop = threading.Event()
    with _lock:
        _state.update({
            "running": True, "stop": stop, "phase": None, "phase_end": 0.0,
            "work_min": work_min, "break_min": break_min, "long_min": max(break_min, 15),
            "task": task, "completed": 0,
        })
    thread = threading.Thread(target=_worker, args=(player, stop),
                              daemon=True, name="pomodoro-timer")
    with _lock:
        _state["thread"] = thread
    thread.start()

    return (f"Starting a {work_min}-minute focus block"
            + (f" on {task}" if task else "")
            + f". I'll let you know when it's time for your {break_min}-minute break.")
