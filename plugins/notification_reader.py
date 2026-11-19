# ==============================================================================
# JARVIS Plugin: Notification Reader
# Brand: Gemind
# Author: upgraderguy777
# Repository: https://github.com/upgraderguy777/jarvis-plugins
# License: MIT License
# ==============================================================================

"""
JARVIS plugin — Notification Reader (via Windows' own notification center).

The user asks "did I get a reply on Discord", "any new Slack messages",
"check my notifications" — JARVIS reads whatever's currently sitting in
Windows' notification center (the flyout from the clock) and reports it,
optionally filtered to one app. It can also proactively WATCH for a
specific notification and speak up the moment it appears — "let me know
if I get a message from John", "alert me if Discord mentions the
meeting" — via a background poller, following the same
threaded-worker-with-persistent-state pattern as the pomodoro and
water_reminder plugins.

WHY THIS APPROACH: reading a user's actual DMs/mentions inside an app
like Discord requires being logged in as their account there. Scripting
their real session ("self-botting") violates Terms of Service. Reading
Windows' own notification center sidesteps that entirely.

LIMITS:
  - Windows 10 (Build 14393+) and Windows 11 only.
  - Requires a one-time Windows permission prompt:
    Settings > Privacy & security > Notifications.
"""

import asyncio
import platform
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta

PLUGIN = {
    "name": "notification_reader",
    "author": "Gemind (upgraderguy777)",
    "version": "1.0.0",
    "url": "https://github.com/upgraderguy777/jarvis-plugins",
    "license": "MIT",
    "description": (
        "Checks Windows' own notification center for recent notifications "
        "from ANY app — Discord, Slack, WhatsApp, Outlook, Teams, Gmail, "
        "etc — and reports them out loud, or WATCHES for a specific one and "
        "alerts the user the moment it appears. Use for: 'did I get a reply "
        "on Discord', 'any new Slack messages', 'check my notifications', "
        "'any new emails', 'alert me if I get a message from John', 'let me "
        "know if Discord mentions the meeting', 'stop watching for that', "
        "'what are you watching for', 'bildirimlerimi kontrol et'. Set "
        "'action' to 'check' (only notifications since last asked), 'list' "
        "(everything currently there), 'status' (whether permission has "
        "been granted), 'watch' (start alerting on a match), 'unwatch' "
        "(remove a watch, or clear all if no pattern/app given), or "
        "'watches' (list active watches). If the user names a specific app, "
        "put a short match for its name in 'app' (e.g. 'discord', 'slack', "
        "'outlook'). For 'watch'/'unwatch', put the keyword/sender/phrase "
        "to match in 'pattern'. Windows-only; requires the user to grant "
        "notification access once."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "One of: 'check', 'list', 'status', 'watch', 'unwatch', 'watches'.",
            },
            "app": {
                "type": "STRING",
                "description": (
                    "Optional app name filter, e.g. 'discord', 'slack', "
                    "'outlook'. Leave empty to check/watch every app at once."
                ),
            },
            "pattern": {
                "type": "STRING",
                "description": (
                    "For 'watch'/'unwatch': a keyword or phrase to match "
                    "against the notification's title/body, case-insensitive "
                    "(e.g. a sender's name, or a word like 'urgent'). Combine "
                    "with 'app' to scope it to one app. Leave both empty with "
                    "'unwatch' to clear ALL active watches."
                ),
            },
        },
        "required": ["action"],
    },
}

_WINRT_EPOCH = datetime(1601, 1, 1)

_seen_ids = set()
_seen_lock = threading.Lock()

_WATCH_POLL_SECONDS = 10
_watch_lock = threading.Lock()
_watch_state = {"running": False, "thread": None, "stop": None}
_watches = []
_watches_lock = threading.Lock()
_watch_seen_ids = set()
_watch_seen_lock = threading.Lock()
_id_lock = threading.Lock()
_id_counter = 0


def _ensure_winsdk() -> bool:
    """Checks if winsdk is installed; if not, attempts automatic installation via pip."""
    try:
        import winsdk  # noqa: F401
        return True
    except ImportError:
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "winsdk"])
            import winsdk  # noqa: F401
            return True
        except Exception:
            return False


def _next_id() -> int:
    global _id_counter
    with _id_lock:
        _id_counter += 1
        return _id_counter


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


def _panel(player, title: str, text: str) -> None:
    if player:
        try:
            player.show_content(title, text)
        except Exception:
            pass


def _winrt_dt_to_ts(dt) -> float:
    try:
        return (_WINRT_EPOCH + timedelta(microseconds=dt.universal_time / 10)).timestamp()
    except Exception:
        return time.time()


def _run_async(coro):
    result = {}

    def _runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result["value"] = loop.run_until_complete(coro)
        except Exception as e:
            result["error"] = e
        finally:
            loop.close()

    t = threading.Thread(target=_runner, daemon=True, name="notification-check")
    t.start()
    t.join(timeout=20)
    if "error" in result:
        raise result["error"]
    return result.get("value")


async def _get_notifications():
    from winsdk.windows.ui.notifications.management import (
        UserNotificationListener, UserNotificationListenerAccessStatus,
    )
    from winsdk.windows.ui.notifications import NotificationKinds

    listener = UserNotificationListener.current
    status = await listener.request_access_async()
    status_name = str(getattr(status, "name", status)).upper()

    if status != UserNotificationListenerAccessStatus.ALLOWED:
        return [], status_name

    raw = await listener.get_notifications_async(NotificationKinds.TOAST)

    items = []
    for n in raw:
        try:
            app_name = "Unknown app"
            if n.app_info and n.app_info.display_info:
                app_name = n.app_info.display_info.display_name or app_name

            lines = []
            toast = n.notification
            if toast and toast.visual and toast.visual.bindings:
                for t in toast.visual.bindings[0].get_text_elements():
                    if t.text:
                        lines.append(t.text)

            title = lines[0] if lines else "(notification)"
            body = " — ".join(lines[1:]) if len(lines) > 1 else ""
            items.append({
                "id": n.id,
                "app": app_name,
                "title": title,
                "body": body,
                "ts": _winrt_dt_to_ts(n.creation_time),
            })
        except Exception:
            continue

    items.sort(key=lambda it: it["ts"])
    return items, status_name


def _watch_worker(player, stop: threading.Event) -> None:
    try:
        while not stop.is_set():
            with _watches_lock:
                active = list(_watches)

            if active:
                try:
                    items, status_name = _run_async(_get_notifications())
                except Exception:
                    items, status_name = [], "ERROR"

                if status_name == "ALLOWED":
                    for it in items:
                        with _watch_seen_lock:
                            if it["id"] in _watch_seen_ids:
                                continue

                        matched = None
                        with _watches_lock:
                            for w in _watches:
                                if w["app"] and w["app"] not in it["app"].lower():
                                    continue
                                if w["pattern"] and w["pattern"] not in (
                                        it["title"] + " " + it["body"]).lower():
                                    continue
                                matched = w
                                break

                        if matched:
                            with _watch_seen_lock:
                                _watch_seen_ids.add(it["id"])
                            text = f"{it['app']}: {it['title']}" + (
                                f" — {it['body']}" if it["body"] else "")
                            _log(player, f"JARVIS: Watched notification matched -> {text}")
                            _panel(player, "🔔 MATCH", text)
                            _say(player,
                                 "Tell the user, briefly and naturally, that a "
                                 "notification just came in matching something "
                                 f"they asked to be alerted about: \"{text}\". "
                                 "One short sentence, in their language.")

            if stop.wait(_WATCH_POLL_SECONDS):
                break
    finally:
        with _watch_lock:
            _watch_state["running"] = False


def _ensure_watch_thread(player) -> None:
    with _watch_lock:
        if _watch_state["running"]:
            return
        stop = threading.Event()
        thread = threading.Thread(target=_watch_worker, args=(player, stop),
                                  daemon=True, name="notification-watcher")
        _watch_state.update({"running": True, "thread": thread, "stop": stop})
        thread.start()


def run(parameters: dict, player=None, session_memory=None) -> str:
    if platform.system() != "Windows":
        return "Reading notifications only works on Windows (Windows 10/11), sorry."

    action = (parameters.get("action") or "check").strip().lower()
    app_filter = (parameters.get("app") or "").strip().lower()
    pattern = (parameters.get("pattern") or "").strip().lower()

    if not _ensure_winsdk():
        return ("I need the 'winsdk' library to read Windows notifications, but automatic installation "
                "failed. Please install it manually with: pip install winsdk")

    if action in ("watches", "list_watches"):
        with _watches_lock:
            if not _watches:
                return "I'm not watching for anything right now."
            lines = []
            for w in _watches:
                bits = []
                if w["app"]:
                    bits.append(f"app: {w['app']}")
                if w["pattern"]:
                    bits.append(f"text: {w['pattern']}")
                lines.append(", ".join(bits) or "anything")
        return "Currently watching for — " + "; ".join(lines)

    if action == "unwatch":
        with _watches_lock:
            if not pattern and not app_filter:
                removed = len(_watches)
                _watches.clear()
            else:
                before = len(_watches)
                _watches[:] = [w for w in _watches if not (
                    (not pattern or w["pattern"] == pattern) and
                    (not app_filter or w["app"] == app_filter))]
                removed = before - len(_watches)
            remaining = len(_watches)
        if remaining == 0:
            with _watch_lock:
                if _watch_state["stop"]:
                    _watch_state["stop"].set()
        if removed == 0:
            return "I couldn't find a matching watch to remove."
        return ("Stopped watching for that." if remaining
                else "Stopped watching for that. No watches left.")

    if action == "watch":
        if not pattern and not app_filter:
            return "What should I watch for — a keyword, a sender, or an app?"
        wid = _next_id()
        with _watches_lock:
            _watches.append({"id": wid, "pattern": pattern, "app": app_filter})
        _ensure_watch_thread(player)
        desc = " and ".join(filter(None, [
            f"app matches '{app_filter}'" if app_filter else "",
            f"text contains '{pattern}'" if pattern else "",
        ]))
        return f"Watching for notifications where {desc} — I'll let you know the moment one shows up."

    try:
        items, status_name = _run_async(_get_notifications())
    except Exception as e:
        return f"I couldn't read notifications: {e}"

    if action == "status":
        if status_name == "ALLOWED":
            return "Notification access is granted — I can read your Windows notifications."
        return (f"Notification access is {status_name.lower()}. Check Windows "
                "Settings > Privacy & security > Notifications and allow "
                "access for this app, then ask me again.")

    if status_name != "ALLOWED":
        return ("I don't have permission to read notifications yet. Windows "
                "should prompt you — otherwise check Settings > Privacy & "
                "security > Notifications and allow access, then ask again.")

    if app_filter:
        items = [it for it in items if app_filter in it["app"].lower()]

    with _seen_lock:
        if action == "list":
            relevant = items[-10:]
        else:
            relevant = [it for it in items if it["id"] not in _seen_ids]
            for it in relevant:
                _seen_ids.add(it["id"])

    who = f" from {parameters.get('app')}" if app_filter else ""
    if not relevant:
        return f"No new notifications{who}." if action == "check" else f"Nothing{who} right now."

    lines = [f"[{it['app']}] {it['title']}: {it['body']}" if it["body"]
             else f"[{it['app']}] {it['title']}" for it in relevant]

    if player:
        try:
            player.show_content("🔔 NOTIFICATIONS", "\n\n".join(lines))
        except Exception:
            pass

    if len(relevant) == 1:
        one = relevant[0]
        return f"{one['app']}: {one['title']}" + (f" — {one['body']}" if one["body"] else "")

    apps_involved = sorted({it["app"] for it in relevant})
    return (f"You've got {len(relevant)} new notification"
            f"{'s' if len(relevant) != 1 else ''} across {', '.join(apps_involved)}"
            f" — most recent: {lines[-1]}")
