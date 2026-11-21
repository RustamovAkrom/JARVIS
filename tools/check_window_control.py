"""window_control on a real window that this test makes itself - a small
window with a random, unique title - every action read back from Windows,
then closed. It never names an app the user may have open: Windows 11's
Notepad, for one, is a single program, and a test "on Notepad" reached the
user's own window too (2026-11-03).

    python tools/test_window_control.py
"""
import ctypes
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

if sys.platform != "win32":
    print("SKIPPED - the window checks here are Windows only")
    sys.exit(0)

# Measured as the app is: Jarvis's window (Qt) is per-monitor DPI aware.
try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    pass

import psutil                              # noqa: E402
from actions import window_control as W   # noqa: E402

fails = 0


def check(name, cond, detail=""):
    global fails
    fails += not cond
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not cond else ""))


TITLE = f"JarvisWindowTest {uuid.uuid4().hex[:10]}"
script = Path(tempfile.mkdtemp()) / "test_window.pyw"
script.write_text(
    "import tkinter as tk, sys\n"
    "r = tk.Tk(); r.title(sys.argv[1]); r.geometry('640x420+120+120')\n"
    "tk.Label(r, text='window_control test - closes by itself').pack(expand=True)\n"
    "r.after(90000, r.destroy); r.mainloop()\n", encoding="utf-8")
pyw = Path(sys.executable).with_name("pythonw.exe")
# Started through `start`, so it is not a child of this process: the tool
# never acts on its own process tree, and a child would be part of it.
subprocess.run(["cmd", "/c", "start", "", str(pyw if pyw.exists() else sys.executable),
                str(script), TITLE], check=False)


def mine():
    return [r for r in W._windows_of(TITLE) if r.get("title") == TITLE]


rows = []
for _ in range(60):
    time.sleep(0.25)
    rows = mine()
    if rows:
        break
if not rows:
    print("SKIPPED - the test window did not appear")
    sys.exit(0)
h, pid = rows[0]["hwnd"], rows[0]["pid"]
print(f"[its own window: '{TITLE}', pid {pid}]")


def do(**p):
    return W.window_control({"app": TITLE, **p})


try:
    check("found by its title alone, and nothing else matches it", len(mine()) == 1
          and len(W._windows_of(TITLE)) == 1, [r.get("title") for r in W._windows_of(TITLE)])

    print("[each action, read back from Windows]")
    r = do(action="minimize")
    check("minimize: done, and the window really is minimized",
          r.startswith("status=done") and W._user32.IsIconic(h), r[:140])
    r = do(action="restore")
    check("restore: done, neither minimized nor maximized",
          r.startswith("status=done") and not W._user32.IsIconic(h) and not W._user32.IsZoomed(h), r[:140])
    r = do(action="maximize")
    check("maximize: done, and maximized", r.startswith("status=done") and W._user32.IsZoomed(h), r[:140])
    work = W._work_area(h)
    mid = (work[0] + work[2]) // 2
    r = do(action="snap_left")
    ml, mt, mr, mb = W._frame_margins(h)
    l, t, rr, b = W._rect(h)
    check("snap_left from maximized: the visible frame is the left half of the work area",
          r.startswith("status=done") and abs(l + ml - work[0]) <= 8 and abs(rr - mr - mid) <= 8
          and abs(t + mt - work[1]) <= 8 and abs(b - mb - work[3]) <= 8, (r[:110], (l, t, rr, b), work))
    r = do(action="snap_right")
    ml, mt, mr, mb = W._frame_margins(h)
    l, t, rr, b = W._rect(h)
    check("snap_right: the right half", r.startswith("status=done")
          and abs(l + ml - mid) <= 8 and abs(rr - mr - work[2]) <= 8, (r[:110], (l, t, rr, b), work))
    r = do(action="focus")
    check("focus: it is the foreground window", r.startswith("status=done")
          and int(W._user32.GetForegroundWindow()) == int(h), r[:140])

    print("[another monitor]")
    r = do(action="move_to_monitor", monitor="next")
    if len(W._monitors()) < 2:
        check("one monitor here: said plainly, nothing pretended", r.startswith("status=failed")
              and "only one monitor" in r, r[:140])
    else:
        check("moved, and read back on the next monitor", r.startswith("status=done"), r[:140])
    # The moving itself, also on a computer with one screen: a second monitor
    # is pretended to the right of the real one (only this test's window moves).
    real = W._monitors
    one = real()[0]
    fake = [one, (one[2], one[1], one[2] + (one[2] - one[0]), one[3])]
    W._monitors = lambda: fake
    try:
        do(action="restore")
        r = do(action="move_to_monitor", monitor="next")
        l, t, rr, b = W._rect(h)
        check("moved to the (pretended) second monitor, and read back there",
              r.startswith("status=done") and "monitor 2" in r and (l + rr) // 2 >= fake[1][0], (r[:110], (l, t, rr, b)))
        check("...the same size, inside that monitor", rr - l <= fake[1][2] - fake[1][0]
              and l >= fake[1][0] - 16, (l, rr, fake[1]))
        r = do(action="move_to_monitor", monitor="1")
        l, t, rr, b = W._rect(h)
        check("and back to monitor 1 by its number", r.startswith("status=done")
              and (l + rr) // 2 < fake[0][2], (r[:110], (l, rr)))
        r = do(action="move_to_monitor", monitor="1")
        check("asked for the monitor it is already on: says so, no harm", "already on monitor 1" in r, r[:120])
    finally:
        W._monitors = real
    r = do(action="move_to_monitor", monitor="7")
    check("a monitor that does not exist: refused, says how many there are",
          r.startswith("status=failed"), r[:140])

    print("[what it refuses]")
    r = W.window_control({"action": "minimize", "app": "no-such-app-" + uuid.uuid4().hex[:6]})
    check("an app with no window: failed, says so", r.startswith("status=failed") and "no open window" in r, r[:140])
    r = W.window_control({"action": "fly"})
    check("an unknown action: failed, lists the real ones", r.startswith("status=failed")
          and "move_to_monitor" in r, r[:160])
    r = do(action="volume", value="20")
    check("one app's volume, for an app making no sound: says it has none to set",
          r.startswith("status=failed") and "not playing any sound" in r, r[:160])

    print("[never the assistant's own process tree]")
    from core import appidentity
    own = appidentity.own_pids()
    offered = W._windows_of("") + W._windows_of("python") + W._windows_of("cmd") + W._windows_of("terminal")
    check("no window of this process or what it runs in is offered",
          all(x["pid"] not in own for x in offered), [(x["pid"], x.get("process")) for x in offered
                                                    if x["pid"] in own])

    print("[close everything else: a confirmation on screen, nothing closed before it]")
    from core import confirm
    shown = []
    confirm.bind(lambda title, detail: shown.append((title, detail)), lambda: None)
    r = W.window_control({"action": "close_others", "keep": ["Spotify"]})
    check("a confirmation is put on screen instead of closing anything",
          "[CONFIRMATION_PENDING]" in r and shown and shown[0][0].startswith("Close"), (r[:120], shown[:1]))
    check("...it lists what it would close, and keeps what was named",
          shown and "Spotify" in shown[0][1] and "kept: Spotify" in shown[0][1], shown[:1])
    confirm.resolve(False)
    time.sleep(0.3)
    check("cancelled: nothing closed (the test window is still there)", bool(W._user32.IsWindow(h)))

    print("[closing one app by name: computer_control close_app]")
    from actions.computer_control import _close_app
    r = _close_app(TITLE)
    for _ in range(20):
        if not W._user32.IsWindow(h):
            break
        time.sleep(0.25)
    check("closed politely, and the window is really gone", not W._user32.IsWindow(h)
          and not r.startswith("status=failed"), r[:160])
finally:
    try:
        psutil.Process(pid).terminate()
    except Exception:
        pass

print(f"\n{'ALL PASSED' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
