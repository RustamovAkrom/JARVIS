"""
actions/window_control.py — an application's windows, by its name: minimise,
maximise, restore, snap to a half, bring to the front, move to another
monitor, the sound of one app, and closing everything but what the user keeps.

WHY THESE ARE NOT KEYBOARD SHORTCUTS
    computer_settings did "minimise" as Win+Down and "snap left" as Win+Left:
    a chord sent to whichever window had focus. While somebody is talking to
    the assistant that is very often the assistant - the same bug that once
    made "close the app" close Jarvis itself (computer_control._close_app).
    And "minimise Spotify" could not say WHICH window. So here the user names
    the application; its own windows are found (never the assistant's), the
    change is made to those windows, and read back afterwards: a window that
    did not move is reported as not moved.

    With no application named, "this window" is the frontmost window that is
    NOT the assistant's.

WHERE IT WORKS
    Windows: all of it, through the window manager itself (any number of
    monitors, any scaling; the invisible resize border Windows 10/11 adds is
    taken into account, so a half really is a half). Linux (X11): with wmctrl
    and xdotool installed. macOS: through System Events (minimise, maximise,
    halves, front). What a system cannot do is said, not pretended.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import time

from core import outcome

_OS = platform.system()
_ACTIONS = ("list", "focus", "minimize", "maximize", "restore", "snap_left", "snap_right",
            "move_to_monitor", "volume", "close_others")


# ── finding the windows ───────────────────────────────────────────────────────

def _windows_of(app: str) -> list[dict]:
    """The named application's windows, front to back (Windows), never the
    assistant's own. [] when it has none open."""
    from core import appidentity, screenview
    # Never a window of the assistant's own process tree - which includes a
    # terminal it was started from: minimising that is harmless, closing it
    # ends the assistant (measured: the terminal running the test was offered).
    own = appidentity.own_pids()
    rows = [r for r in screenview.taskbar_windows() if r.get("pid") not in own]
    if not app:
        return rows[:1]                        # the frontmost window that is not ours
    pids = {m.pid for m in appidentity.running(app) if not m.is_self}
    want = appidentity.fold(app)
    out = [r for r in rows if r.get("pid") in pids]
    if not out and want:
        # A program whose process name is not what people call it
        # ("Word" = WINWORD.EXE): its own product name, or the window's title
        # ("minimise the YouTube window").
        out = [r for r in rows
               if want in appidentity.fold(str(r.get("product") or ""))
               or want in appidentity.fold(str(r.get("process") or ""))
               or want in appidentity.fold(str(r.get("title") or ""))]
    return out


def _label(row: dict) -> str:
    """What to call it out loud: the program's own name ("Microsoft Word"),
    unless that is a generic label of the system's ("Microsoft Windows
    Operating System" for Notepad) - then the program file's name."""
    product = str(row.get("product") or "")
    process = str(row.get("process") or "").removesuffix(".exe").removesuffix(".EXE")
    if not product or "operating system" in product.lower():
        return process or product or "the window"
    return product


# ── Windows: the window manager itself ────────────────────────────────────────

if _OS == "Windows":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:
        _dwm = ctypes.WinDLL("dwmapi")
    except OSError:
        _dwm = None
    _SW_MAXIMIZE, _SW_MINIMIZE, _SW_RESTORE = 3, 6, 9
    _SWP_NOZORDER, _SWP_NOACTIVATE = 0x0004, 0x0010

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    _user32.MonitorFromWindow.restype = wintypes.HMONITOR
    _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO)]


def _rect(hwnd) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _frame_margins(hwnd) -> tuple[int, int, int, int]:
    """The invisible border Windows 10/11 draws around a window (left, top,
    right, bottom): placing GetWindowRect where you want the window leaves a
    gap of ~7 px. The visible frame comes from DWM."""
    if _dwm is None:
        return 0, 0, 0, 0
    vis = wintypes.RECT()
    if _dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(vis), ctypes.sizeof(vis)) != 0:
        return 0, 0, 0, 0
    l, t, r, b = _rect(hwnd)
    return vis.left - l, vis.top - t, r - vis.right, b - vis.bottom


def _monitors() -> list[tuple[int, int, int, int]]:
    """Work areas (without the taskbar) of every monitor, left to right."""
    found = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HMONITOR, wintypes.HDC,
                              ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def visit(hmon, _hdc, _rc, _p):
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if _user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            w = info.rcWork
            found.append((w.left, w.top, w.right, w.bottom))
        return True
    _user32.EnumDisplayMonitors(None, None, proc(visit), 0)
    return sorted(found, key=lambda r: (r[0], r[1]))


def _work_area(hwnd) -> tuple[int, int, int, int]:
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    _user32.GetMonitorInfoW(_user32.MonitorFromWindow(hwnd, 2), ctypes.byref(info))
    w = info.rcWork
    return w.left, w.top, w.right, w.bottom


def _place(hwnd, area: tuple[int, int, int, int]) -> None:
    """Put the window's VISIBLE frame exactly on `area`. A maximised or
    minimised window is restored first, and Windows animates that: a move
    sent during the animation is overwritten by it (measured), so it waits,
    and places twice - the frame margins are only right once restored."""
    if _user32.IsZoomed(hwnd) or _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, _SW_RESTORE)
        time.sleep(0.35)
    l, t, r, b = area
    for _ in range(2):
        ml, mt, mr, mb = _frame_margins(hwnd)
        _user32.SetWindowPos(hwnd, None, l - ml, t - mt, (r - l) + ml + mr, (b - t) + mt + mb,
                             _SWP_NOZORDER | _SWP_NOACTIVATE)
        time.sleep(0.1)


def _monitor_index(hwnd, monitors) -> int:
    l, t, r, b = _rect(hwnd)
    cx, cy = (l + r) // 2, (t + b) // 2
    for i, (ml, mt, mr, mb) in enumerate(monitors):
        if ml <= cx < mr and mt <= cy < mb:
            return i
    return 0


def _win_do(action: str, hwnd, monitor: str = "") -> tuple[bool, str]:
    """(it happened, what to say). Read back after the change."""
    if action == "minimize":
        _user32.ShowWindow(hwnd, _SW_MINIMIZE)
        time.sleep(0.25)
        return bool(_user32.IsIconic(hwnd)), "minimized"
    if action == "maximize":
        _user32.ShowWindow(hwnd, _SW_MAXIMIZE)
        time.sleep(0.25)
        return bool(_user32.IsZoomed(hwnd)), "maximized"
    if action == "restore":
        _user32.ShowWindow(hwnd, _SW_RESTORE)
        time.sleep(0.25)
        return not _user32.IsIconic(hwnd) and not _user32.IsZoomed(hwnd), "restored"
    if action == "focus":
        from core import appidentity
        appidentity.raise_hwnd(int(hwnd))
        time.sleep(0.25)
        return int(_user32.GetForegroundWindow() or 0) == int(hwnd), "brought to the front"
    if action in ("snap_left", "snap_right"):
        l, t, r, b = _work_area(hwnd)
        mid = (l + r) // 2
        area = (l, t, mid, b) if action == "snap_left" else (mid, t, r, b)
        _place(hwnd, area)
        time.sleep(0.25)
        ml, mt, mr, mb = _frame_margins(hwnd)
        wl, wt, wr, wb = _rect(hwnd)
        got = (wl + ml, wt + mt, wr - mr, wb - mb)
        ok = all(abs(a - b) <= 8 for a, b in zip(got, area))
        return ok, "on the left half" if action == "snap_left" else "on the right half"
    if action == "move_to_monitor":
        monitors = _monitors()
        if len(monitors) < 2:
            return False, "there is only one monitor"
        here = _monitor_index(hwnd, monitors)
        want = str(monitor or "next").strip().lower()
        if want in ("next", "other", "second", ""):
            target = (here + 1) % len(monitors)
        elif want in ("previous", "prev"):
            target = (here - 1) % len(monitors)
        elif want.isdigit() and 1 <= int(want) <= len(monitors):
            target = int(want) - 1
        else:
            return False, f"there is no monitor '{monitor}' (this computer has {len(monitors)}, numbered left to right)"
        if target == here:
            return True, f"already on monitor {here + 1}"
        was_max = bool(_user32.IsZoomed(hwnd))
        sl, st, sr, sb = monitors[here]
        tl, tt, tr, tb = monitors[target]
        wl, wt, wr, wb = _rect(hwnd)
        # The same place on the other screen, scaled to its size, and kept on it.
        fx = (wl - sl) / max(1, sr - sl)
        fy = (wt - st) / max(1, sb - st)
        w = min(wr - wl, tr - tl)
        h = min(wb - wt, tb - tt)
        x = max(tl, min(tl + int(fx * (tr - tl)), tr - w))
        y = max(tt, min(tt + int(fy * (tb - tt)), tb - h))
        _user32.ShowWindow(hwnd, _SW_RESTORE)
        _user32.SetWindowPos(hwnd, None, x, y, w, h, _SWP_NOZORDER | _SWP_NOACTIVATE)
        if was_max:
            _user32.ShowWindow(hwnd, _SW_MAXIMIZE)
        time.sleep(0.3)
        return _monitor_index(hwnd, _monitors()) == target, f"on monitor {target + 1}"
    return False, f"no window action called {action!r}"


# ── Linux (X11): wmctrl / xdotool ─────────────────────────────────────────────

def _linux_ids(app: str) -> list[str]:
    from core import appidentity
    out = subprocess.run(["wmctrl", "-lp"], capture_output=True, text=True, timeout=5).stdout
    pids = {m.pid for m in appidentity.running(app) if not m.is_self} if app else set()
    own = appidentity.own_pids()
    ids = []
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 3 or not parts[2].isdigit():
            continue
        pid = int(parts[2])
        if pid in own:
            continue
        title = parts[4] if len(parts) > 4 else ""
        if not app or pid in pids or appidentity.fold(app) in appidentity.fold(title):
            ids.append(parts[0])
    return ids


def _linux_do(action: str, wid: str, monitor: str = "") -> tuple[bool, str]:
    run = lambda *a: subprocess.run(list(a), capture_output=True, timeout=5).returncode == 0
    if action == "minimize":
        return run("xdotool", "windowminimize", wid), "minimized"
    if action == "maximize":
        return run("wmctrl", "-i", "-r", wid, "-b", "add,maximized_vert,maximized_horz"), "maximized"
    if action == "restore":
        return run("wmctrl", "-i", "-r", wid, "-b", "remove,maximized_vert,maximized_horz"), "restored"
    if action == "focus":
        return run("wmctrl", "-i", "-a", wid), "brought to the front"
    if action in ("snap_left", "snap_right"):
        geo = subprocess.run(["xdotool", "getdisplaygeometry"], capture_output=True, text=True,
                             timeout=5).stdout.split()
        if len(geo) != 2:
            return False, "the screen size could not be read"
        w, h = int(geo[0]), int(geo[1])
        x = 0 if action == "snap_left" else w // 2
        run("wmctrl", "-i", "-r", wid, "-b", "remove,maximized_vert,maximized_horz")
        return run("wmctrl", "-i", "-r", wid, "-e", f"0,{x},0,{w // 2},{h}"), \
            "on the left half" if action == "snap_left" else "on the right half"
    return False, "moving a window to another monitor is not supported on this system yet"


# ── macOS: System Events ──────────────────────────────────────────────────────

def _mac_do(action: str, app: str) -> tuple[bool, str]:
    if not app:
        return False, "on macOS, say which application"
    name = app.replace('"', "")
    scripts = {
        "minimize": f'tell application "System Events" to set value of attribute "AXMinimized" '
                    f'of every window of process "{name}" to true',
        "restore": f'tell application "System Events" to set value of attribute "AXMinimized" '
                   f'of every window of process "{name}" to false',
        "focus": f'tell application "{name}" to activate',
        "maximize": f'tell application "Finder" to set b to bounds of window of desktop\n'
                    f'tell application "{name}" to set bounds of front window to b',
        "snap_left": f'tell application "Finder" to set b to bounds of window of desktop\n'
                     f'tell application "{name}" to set bounds of front window to '
                     f'{{item 1 of b, item 2 of b, (item 3 of b) div 2, item 4 of b}}',
        "snap_right": f'tell application "Finder" to set b to bounds of window of desktop\n'
                      f'tell application "{name}" to set bounds of front window to '
                      f'{{(item 3 of b) div 2, item 2 of b, item 3 of b, item 4 of b}}',
    }
    if action not in scripts:
        return False, "moving a window to another monitor is not supported on macOS yet"
    ok = subprocess.run(["osascript", "-e", scripts[action]], capture_output=True,
                        timeout=10).returncode == 0
    return ok, {"minimize": "minimized", "restore": "restored", "focus": "brought to the front",
                "maximize": "maximized", "snap_left": "on the left half",
                "snap_right": "on the right half"}[action]


# ── one app's sound (Windows) ─────────────────────────────────────────────────

def _app_volume(app: str, value) -> str:
    if _OS != "Windows":
        return outcome.failed("window_control", "one app's volume can be set on Windows only; "
                                                "computer_settings sets the whole system's")
    if not app:
        return outcome.ask("window_control", "which app's sound")
    from core import appidentity
    from pycaw.pycaw import AudioUtilities
    pids = {m.pid for m in appidentity.running(app) if not m.is_self}
    want = appidentity.fold(app)
    sessions = [s for s in AudioUtilities.GetAllSessions()
                if s.Process and (s.Process.pid in pids or want in appidentity.fold(s.Process.name()))]
    if not sessions:
        return outcome.failed("window_control", f"{app} is not playing any sound right now, "
                                                f"so it has no volume to set", app=app)
    v = str(value if value is not None else "").strip().lower()
    for s in sessions:
        vol = s.SimpleAudioVolume
        if v in ("mute", "off", "0"):
            vol.SetMute(1, None)
        elif v in ("unmute", "on"):
            vol.SetMute(0, None)
        else:
            try:
                level = max(0, min(100, int(float(v.rstrip("%")))))
            except ValueError:
                return outcome.ask("window_control", "what level (0-100), mute or unmute")
            vol.SetMute(0, None)
            vol.SetMasterVolume(level / 100.0, None)
    got = sessions[0].SimpleAudioVolume
    return outcome.done("window_control", app=app, muted=bool(got.GetMute()),
                        volume=round(got.GetMasterVolume() * 100), verified=True)


# ── closing all but what the user keeps ───────────────────────────────────────

def _close_others(keep: list[str]) -> str:
    """Every open application except the ones named (and the assistant, and
    the desktop): put on screen to confirm first - closing many programs at
    once is not something to do on a misheard word - then closed politely one
    by one (computer_control._close_app: their own close, so unsaved work asks
    to be saved)."""
    if _OS != "Windows":
        return outcome.failed("window_control", "closing everything else is supported on Windows only")
    from core import appidentity, confirm, screenview
    folded = [appidentity.fold(k) for k in keep if str(k).strip()]
    own = appidentity.own_pids()
    apps: dict[str, str] = {}
    for row in screenview.taskbar_windows():
        if row.get("pid") in own:
            continue                           # the assistant, and what it runs in
        name = _label(row)
        names = appidentity.fold(f"{row.get('product', '')} {row.get('process', '')}")
        if any(k and k in names for k in folded):
            continue
        apps.setdefault(appidentity.fold(name), name)
    if not apps:
        return outcome.done("window_control", closed="nothing - no other apps are open")
    listed = ", ".join(sorted(apps.values()))

    def run() -> str:
        from actions.computer_control import _close_app
        results = [f"{n}: {_close_app(n)[:80]}" for n in sorted(apps.values())]
        return "; ".join(results)

    return confirm.request(key="close_others", title=f"Close {len(apps)} apps",
                           detail=f"{listed} (kept: {', '.join(keep) or 'nothing'})", run=run)


# ── the tool ──────────────────────────────────────────────────────────────────

def window_control(parameters: dict, player=None, **_ignored) -> str:
    p = parameters or {}
    action = str(p.get("action") or "").strip().lower()
    app = str(p.get("app") or p.get("app_name") or "").strip()
    if action not in _ACTIONS:
        return outcome.failed("window_control", f"there is no action {action or '(empty)'!r}",
                              valid_actions=", ".join(_ACTIONS))
    try:
        if action == "volume":
            return _app_volume(app, p.get("value"))
        if action == "close_others":
            keep = p.get("keep") or []
            if isinstance(keep, str):
                keep = [k.strip() for k in keep.replace(" and ", ",").split(",")]
            return _close_others(list(keep))
        if action == "list":
            if _OS != "Windows":
                return outcome.failed("window_control", "listing windows is supported on Windows only")
            from core import screenview
            rows = screenview.taskbar_windows()
            return outcome.done("window_control", open=", ".join(
                f"{_label(r)}{' (minimized)' if r.get('minimized') else ''}" for r in rows[:20]) or "nothing")
        if _OS == "Darwin":
            ok, said = _mac_do(action, app)
            return outcome.done("window_control", app=app, now=said) if ok else \
                outcome.failed("window_control", said, app=app)
        if _OS == "Linux":
            if not (shutil.which("wmctrl") and shutil.which("xdotool")):
                return outcome.failed("window_control", "this needs wmctrl and xdotool installed "
                                                        "(sudo apt install wmctrl xdotool)")
            ids = _linux_ids(app)
            if not ids:
                return outcome.failed("window_control", f"no open window of {app or 'any app'} was found", app=app)
            ok, said = _linux_do(action, ids[0], str(p.get("monitor") or ""))
            return outcome.done("window_control", app=app, now=said) if ok else \
                outcome.failed("window_control", said, app=app)

        rows = _windows_of(app)
        if not rows:
            return outcome.failed("window_control",
                                  f"{app} has no open window" if app else "there is no window to act on "
                                  "(the assistant's own are never touched)", app=app)
        done, failed = [], []
        # Minimise / maximise / restore act on every window of the app the user
        # named; the rest on its front window.
        targets = rows if action in ("minimize", "maximize", "restore") and app else rows[:1]
        for row in targets:
            ok, said = _win_do(action, row["hwnd"], str(p.get("monitor") or ""))
            (done if ok else failed).append(said)
        who = _label(rows[0])
        if done and not failed:
            return outcome.done("window_control", app=who, now=done[0], windows=len(done), verified=True)
        if done:
            return outcome.unverified("window_control", f"{len(done)} of {len(targets)} windows are "
                                      f"{done[0]}; the others did not change", app=who)
        return outcome.failed("window_control", f"{who} is not {failed[0]} - the window did not "
                              f"change" if failed[0] in ("minimized", "maximized", "restored",
                              "brought to the front", "on the left half", "on the right half")
                              else failed[0], app=who)
    except Exception as e:                                          # noqa: BLE001
        return outcome.failed("window_control", f"{type(e).__name__}: {e}", app=app)


TOOL = {
    "name": "window_control",
    "description": (
        "An application's WINDOWS, by the app's name: minimize, maximize, restore, "
        "snap_left / snap_right (half the screen), focus (bring to the front), "
        "move_to_monitor (monitor: 'next', 'previous' or a number, left to right), "
        "list (what is open). Also volume: ONE app's own sound (value 0-100, 'mute', "
        "'unmute') - 'turn Spotify down', 'mute Discord'; the whole computer's volume is "
        "computer_settings. And close_others: close every open app except those in "
        "'keep' ('close everything except Spotify') - it asks the user on screen first. "
        "Put the app in 'app' as the user said it; leave it out for 'this window' (the "
        "frontmost that is not the assistant). Use this - not keyboard shortcuts - for "
        "any window request. Opening an app is open_app; closing ONE app is "
        "computer_control action='close_app'. 'done' was checked on screen; 'failed' "
        "means it did not happen - say so."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "enum": list(_ACTIONS),
                       "description": " | ".join(_ACTIONS)},
            "app": {"type": "STRING", "description": "The application, as the user named it"},
            "monitor": {"type": "STRING", "description": "For move_to_monitor: next | previous | 1, 2, 3..."},
            "value": {"type": "STRING", "description": "For volume: 0-100, mute or unmute"},
            "keep": {"type": "ARRAY", "items": {"type": "STRING"},
                     "description": "For close_others: the apps to keep open"},
        },
        "required": ["action"],
    },
    "handler": window_control,
}
