"""
JARVIS Clap Launcher — waits in the background and starts JARVIS on a double clap.

This is NOT JARVIS. It is a small program of its own: an icon in the system
tray (notification area / menu bar), the microphone and core/clap.py. When it
hears the pattern it starts main.py, which greets with the welcome line, and
then gets out of the way.

WHAT IT DOES WITH THE MICROPHONE
    Listens while JARVIS is closed, in 16 ms pieces that are analysed for the
    shape of a clap and dropped. Nothing is recorded, stored or sent anywhere.
    The microphone is released the moment JARVIS runs (started from here or any
    other way) and taken back when it closes — two programs on one microphone
    is how a voice assistant starts mishearing. "Pause listening" releases it
    on demand; the tray icon dims while it is not listening.

ALWAYS VISIBLE, ALWAYS STOPPABLE
    The tray menu's first line says what it is doing right now. It can be
    stopped from that menu (Quit), from JARVIS (SETUP -> CLAP LAUNCHER), and
    opening the shortcut again while it runs says it is already listening
    instead of starting a second copy.

INSTANT START (optional, on by default with 6 GB of memory or more)
    A copy of JARVIS that has done its importing and built its window waits
    hidden, with no microphone and no connection open, and a clap only has to
    show it: a tenth of a second instead of three. About 250 MB of memory.

RUN IT
    The desktop shortcut from JARVIS's SETUP drawer, or:  python clap_launcher.pyw
    (pythonw on Windows, so no console window). "Start when I log in" in the
    menu keeps it there from then on.

LOGS
    logs/clap_launcher.log  — what it heard and did (the detector's decisions too)
    logs/jarvis.log         — everything JARVIS printed when started from here
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
_LOG = LOG_DIR / "clap_launcher.log"
_REARM_S = 8.0          # after JARVIS closes, this long before listening again:
                        # talking while closing it must not start it straight back
_APP_ID = "JARVIS_ClapLauncher"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    try:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    if sys.stdout is not None:
        try:
            print(line, flush=True)
        except Exception:
            pass


# ── platform ──────────────────────────────────────────────────────────────────
def python_exe() -> str:
    """The interpreter to start JARVIS with — pythonw on Windows (no console)."""
    exe = Path(sys.executable)
    if sys.platform == "win32":
        w = exe.with_name("pythonw.exe")
        if w.exists():
            return str(w)
    return str(exe)


def _no_window() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def jarvis_running(exclude=()) -> bool:
    """Is a JARVIS from THIS folder running, however it was started? `exclude`:
    pids that do not count — the launcher's own standby copy."""
    import psutil
    main = os.path.normcase(str(ROOT / "main.py"))
    root = os.path.normcase(str(ROOT))
    # Names first: asking every process for its command line and folder took
    # 1.6 s on Windows; only the Python processes are asked.
    for p in psutil.process_iter(["name"]):
        try:
            if p.pid in exclude or not (p.info.get("name") or "").lower().startswith("python"):
                continue
            cmd = [os.path.normcase(str(c)) for c in p.cmdline()]
            if main in cmd:
                return True
            if any(c.endswith("main.py") for c in cmd) and os.path.normcase(p.cwd()) == root:
                return True
        except Exception:
            continue
    return False


class Autostart:
    """Start this launcher at login: the Run key on Windows, a LaunchAgent on
    macOS, an XDG autostart entry on Linux."""
    _plist = Path.home() / "Library" / "LaunchAgents" / "com.jarvis.claplauncher.plist"
    _desktop = Path.home() / ".config" / "autostart" / "jarvis-clap-launcher.desktop"

    @classmethod
    def enabled(cls) -> bool:
        try:
            if sys.platform == "win32":
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                    r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
                    winreg.QueryValueEx(k, _APP_ID)
                    return True
            if sys.platform == "darwin":
                return cls._plist.exists()
            return cls._desktop.exists()
        except Exception:
            return False

    @classmethod
    def set(cls, on: bool) -> None:
        script = str(ROOT / "clap_launcher.pyw")
        if sys.platform == "win32":
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                                winreg.KEY_ALL_ACCESS) as k:
                if on:
                    winreg.SetValueEx(k, _APP_ID, 0, winreg.REG_SZ,
                                      f'"{python_exe()}" "{script}"')
                else:
                    try:
                        winreg.DeleteValue(k, _APP_ID)
                    except FileNotFoundError:
                        pass
            return
        if sys.platform == "darwin":
            if on:
                cls._plist.parent.mkdir(parents=True, exist_ok=True)
                cls._plist.write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict>\n'
                    '  <key>Label</key><string>com.jarvis.claplauncher</string>\n'
                    '  <key>ProgramArguments</key><array>\n'
                    f'    <string>{python_exe()}</string><string>{script}</string>\n'
                    '  </array>\n'
                    f'  <key>WorkingDirectory</key><string>{ROOT}</string>\n'
                    '  <key>RunAtLoad</key><true/>\n'
                    '</dict></plist>\n', encoding="utf-8")
            elif cls._plist.exists():
                cls._plist.unlink()
            return
        if on:
            cls._desktop.parent.mkdir(parents=True, exist_ok=True)
            cls._desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=JARVIS Clap Launcher\n"
                f'Exec="{python_exe()}" "{script}"\nPath={ROOT}\n'
                "X-GNOME-Autostart-enabled=true\nTerminal=false\n", encoding="utf-8")
        elif cls._desktop.exists():
            cls._desktop.unlink()


def open_file(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))                       # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:
        log(f"could not open {path.name}: {e!r}")


# ── the program ───────────────────────────────────────────────────────────────
def main() -> int:
    from PyQt6.QtCore import QObject, QTimer, pyqtSignal
    from PyQt6.QtGui import QAction, QActionGroup, QIcon
    from PyQt6.QtNetwork import QLocalServer
    from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

    from core import clap_ipc

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("JARVIS Clap Launcher")

    # One per user. A second copy tells the first to say where it is, and goes.
    if clap_ipc.ask("show") is not None:
        log("already running - told it to show itself; this copy exits")
        return 0
    server = QLocalServer()
    QLocalServer.removeServer(clap_ipc.server_name())     # a crashed copy's leftover
    if not server.listen(clap_ipc.server_name()):
        log(f"could not open the control channel: {server.errorString()}")

    import sounddevice as sd

    from core.clap import RATE, SENSITIVITY, ClapDetector, learn_level
    from memory.config_manager import (get_clap_launcher, get_input_device,
                                       save_clap_launcher)

    settings = get_clap_launcher()

    class Bridge(QObject):
        clap = pyqtSignal()
        heard = pyqtSignal(str)          # a detector decision worth showing
        running = pyqtSignal(bool)       # from the process check's thread
    bridge = Bridge()

    state = {"stream": None, "paused": False, "child": None, "launched_at": 0.0,
             "standby": None, "standby_at": 0.0, "standby_fails": 0, "running": False,
             "closed_at": 0.0, "learning": None}

    def pattern() -> str:
        n = settings["claps"]
        return "a clap" if n == 1 else ("a double clap" if n == 2 else f"{n} claps")

    def make_detector():
        return ClapDetector(claps=settings["claps"],
                            sensitivity=SENSITIVITY[settings["sensitivity"]],
                            level=settings["level"])

    detector = {"d": make_detector()}

    # ── tray icon and status ─────────────────────────────────────────────────
    icon_path = next((p for p in (ROOT / "config" / "jarvis.ico", ROOT / "config" / "jarvis.png")
                      if p.exists()), None)
    base_icon = (QIcon(str(icon_path)) if icon_path
                 else app.style().standardIcon(app.style().StandardPixmap.SP_MediaPlay))
    dim_icon = QIcon(base_icon.pixmap(64, 64, QIcon.Mode.Disabled))
    tray = QSystemTrayIcon(base_icon)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        log("no system tray on this desktop: running without an icon "
            "(stop it from JARVIS: SETUP -> CLAP LAUNCHER)")

    def notify(text: str, ms: int = 3000, critical: bool = False) -> None:
        tray.showMessage("JARVIS Clap Launcher", text,
                         QSystemTrayIcon.MessageIcon.Critical if critical
                         else QSystemTrayIcon.MessageIcon.Information, ms)

    def status_text() -> str:
        if state["learning"] is not None:
            return "Learning your clap..."
        if state["paused"]:
            return "Paused - claps are ignored"
        if state["running"]:
            return "JARVIS is running - not listening"
        if state["stream"] is None:
            return "Microphone unavailable"
        return f"Listening for {pattern()}"

    a_status = QAction("")
    a_status.setEnabled(False)

    def refresh() -> None:
        text = status_text()
        a_status.setText("●  " + text if state["stream"] is not None else "○  " + text)
        tray.setIcon(base_icon if state["stream"] is not None else dim_icon)
        tray.setToolTip(f"JARVIS Clap Launcher\n{text}\n"
                        "Sound is analysed on this computer only; nothing is recorded.")

    # ── microphone ───────────────────────────────────────────────────────────
    shown = {"reason": ""}

    def callback(indata, frames, time_info, status):
        try:
            d = detector["d"]
            fired = d.feed(indata[:, 0])
            reason = d.last_reason
            if reason and reason != shown["reason"]:
                # Every decision goes to the log, so a missed or unwanted
                # start can be explained instead of guessed at.
                shown["reason"] = reason
                log(f"heard: {reason}")
                bridge.heard.emit(reason)
            if fired:
                shown["reason"] = ""
                d.last_reason = ""
                bridge.clap.emit()
        except Exception as e:
            log(f"detector error: {e!r}")

    def find_input_device():
        name = ""
        try:
            name = get_input_device()
            if name:
                devices = list(sd.query_devices())
                for test in (lambda d: d["name"] == name,
                             lambda d: d["name"].startswith(name[:24]) or name.startswith(d["name"][:24])):
                    for i, d in enumerate(devices):
                        if d["max_input_channels"] > 0 and test(d):
                            return i, name
        except Exception:
            pass
        return None, name

    def open_mic() -> None:
        if state["stream"] is not None or state["paused"] or state["running"]:
            refresh()
            return
        device, name = find_input_device()
        try:
            s = sd.InputStream(samplerate=RATE, channels=1, dtype="float32",
                               blocksize=512, device=device, callback=callback)
            s.start()
            state["stream"] = s
            detector["d"].reset()
            log(f"listening for {pattern()} (microphone: {name if device is not None else 'system default'})")
        except Exception as e:
            log(f"could not open the microphone: {e!r}")
        refresh()

    def close_mic(reason: str) -> None:
        s, state["stream"] = state["stream"], None
        if s is not None:
            try:
                s.stop()
                s.close()
            except Exception:
                pass
            log(f"microphone released: {reason}")
        refresh()

    # ── the standby copy ─────────────────────────────────────────────────────
    def _env(standby: bool) -> dict:
        env = dict(os.environ, JARVIS_WELCOME="1" if settings["greet"] else "0",
                   PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        env.pop("JARVIS_STANDBY", None)
        if standby:
            env["JARVIS_STANDBY"] = "1"
        return env

    def _out(source: str):
        out = open(LOG_DIR / "jarvis.log", "a", encoding="utf-8")
        out.write(f"\n===== {source} at {time.ctime()} =====\n")
        out.flush()
        return out

    def standby_alive() -> bool:
        sb = state["standby"]
        return sb is not None and sb.poll() is None

    def ensure_standby() -> None:
        if (not settings["preload"] or standby_alive() or state["running"]
                or state["standby_fails"] >= 3):
            return
        sb = state["standby"]
        if sb is not None and time.monotonic() - state["standby_at"] < 30:
            # It died soon after starting: counted, so a JARVIS that cannot
            # start is not restarted for ever in the background.
            state["standby_fails"] += 1
            log(f"standby copy exited with code {sb.returncode} - see logs/jarvis.log")
            if state["standby_fails"] >= 3:
                log("instant start disabled after 3 failures - claps start JARVIS normally")
                return
        try:
            out = _out("standby copy started")
            state["standby"] = subprocess.Popen(
                [python_exe(), str(ROOT / "main.py")], cwd=str(ROOT), env=_env(True),
                stdin=subprocess.PIPE, stdout=out, stderr=subprocess.STDOUT, **_no_window())
            out.close()
            state["standby_at"] = time.monotonic()
            log(f"standby copy loading, pid {state['standby'].pid}")
        except Exception as e:
            state["standby"] = None
            log(f"could not start a standby copy: {e!r}")

    def stop_standby() -> None:
        sb, state["standby"] = state["standby"], None
        if sb is not None and sb.poll() is None:
            try:
                sb.stdin.close()            # end of input: it exits by itself
                sb.wait(3)
            except Exception:
                try:
                    sb.kill()
                except Exception:
                    pass

    # ── starting JARVIS ──────────────────────────────────────────────────────
    def launch(source: str) -> None:
        if time.monotonic() - state["launched_at"] < 8.0:
            return
        if state["running"]:
            notify("JARVIS is already running.", 2500)
            return
        close_mic("starting JARVIS")
        if standby_alive():
            try:
                sb = state["standby"]
                sb.stdin.write(b"go\n")
                sb.stdin.flush()
                state["child"], state["standby"] = sb, None
                state["launched_at"] = time.monotonic()
                state["running"] = True
                log(f"JARVIS started from standby ({source}), pid {sb.pid}")
                refresh()
                QTimer.singleShot(4000, check_started)
                return
            except Exception as e:
                log(f"standby copy did not take the start ({e!r}) - starting normally")
                stop_standby()
        try:
            out = _out(f"started by {source}")
            state["child"] = subprocess.Popen(
                [python_exe(), str(ROOT / "main.py")], cwd=str(ROOT), env=_env(False),
                stdout=out, stderr=subprocess.STDOUT, **_no_window())
            out.close()             # the child holds its own handle
            state["launched_at"] = time.monotonic()
            state["running"] = True
            log(f"JARVIS started ({source}), pid {state['child'].pid}")
            refresh()
            QTimer.singleShot(4000, check_started)
        except Exception as e:
            log(f"could not start JARVIS: {e!r}")
            notify(f"Could not start JARVIS: {e}", 5000, critical=True)
            open_mic()

    def check_started() -> None:
        # Never report success without checking: a JARVIS that dies during
        # start-up is said out loud, with where to look.
        child = state["child"]
        if child is not None and child.poll() is not None:
            log(f"JARVIS exited during start-up with code {child.returncode} - see logs/jarvis.log")
            notify(f"JARVIS stopped while starting (code {child.returncode}). "
                   f"Details: {LOG_DIR / 'jarvis.log'}", 6000, critical=True)
            state.update(child=None, running=False, launched_at=0.0)
            open_mic()
            ensure_standby()

    def on_clap() -> None:
        log("pattern heard")
        if not state["running"]:
            notify("Clap heard - starting JARVIS.", 2000)
        launch("a clap")

    def on_heard(reason: str) -> None:
        if settings["notify_heard"] and state["learning"] is None and (
                reason.startswith("clap") or reason.startswith("not quiet")
                or reason.startswith("too")):
            notify(f"Heard: {reason}", 1500)

    bridge.clap.connect(on_clap)
    bridge.heard.connect(on_heard)

    # ── keeping the microphone free while JARVIS runs ────────────────────────
    busy = {"checking": False}

    def poll() -> None:
        if busy["checking"]:
            return
        busy["checking"] = True
        sb = state["standby"]
        exclude = {sb.pid} if sb is not None else set()

        def work():
            try:
                running = jarvis_running(exclude)
            except Exception:
                running = False
            bridge.running.emit(running)

        threading.Thread(target=work, daemon=True).start()

    def apply(running: bool) -> None:
        busy["checking"] = False
        was = state["running"]
        if running:
            state["running"] = True
            if state["stream"] is not None:
                close_mic("JARVIS is running")
            refresh()
            return
        if time.monotonic() - state["launched_at"] < 10.0:
            return                        # just started: not visible to the check yet
        state["running"] = False
        child = state["child"]
        if (was or (child is not None and child.poll() is not None)) and not state["closed_at"]:
            state["closed_at"] = time.monotonic()
            state["child"] = None
            ensure_standby()              # the copy loads now; listening waits
        if time.monotonic() - (state["closed_at"] or 0.0) >= _REARM_S:
            state["closed_at"] = 0.0
            open_mic()
            ensure_standby()
        refresh()

    bridge.running.connect(apply)
    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(2000)

    # ── learning this microphone's clap ──────────────────────────────────────
    def learn() -> None:
        if state["running"] or state["paused"] or state["stream"] is None:
            notify("Learning needs the microphone: close JARVIS and un-pause first.", 4000)
            return
        state["learning"] = ClapDetector(learn=True)
        detector["d"] = state["learning"]
        refresh()
        notify("Clap 4 to 6 times, about a second apart, as you normally would.", 5000)
        log("learning the clap level")
        QTimer.singleShot(12000, finish_learning)

    def finish_learning() -> None:
        d, state["learning"] = state["learning"], None
        level = learn_level(d.heard_levels if d else [])
        if level:
            settings["level"] = level
            save_clap_launcher(level=level)
            log(f"learned clap level {level:.4f} from {len(d.heard_levels)} claps")
            notify(f"Learned - {len(d.heard_levels)} claps heard. Quieter sharp sounds "
                   f"will no longer count.", 4000)
        else:
            log(f"learning failed: {len(d.heard_levels) if d else 0} claps heard")
            notify("I did not hear enough claps. Move closer to the microphone or "
                   "raise its volume, then try again.", 5000)
        detector["d"] = make_detector()
        refresh()

    def forget() -> None:
        settings["level"] = None
        save_clap_launcher(level=None)
        detector["d"] = make_detector()
        log("learned clap level forgotten")
        notify("Back to the default clap detection.", 2500)

    # ── control channel (JARVIS's SETUP drawer, a second copy) ───────────────
    def on_connection() -> None:
        sock = server.nextPendingConnection()
        if sock is None:
            return

        def answer():
            line = bytes(sock.readAll()).decode("utf-8", "replace").strip().lower()
            if line == "status":
                reply = json.dumps({"listening": state["stream"] is not None,
                                    "paused": state["paused"], "jarvis": state["running"],
                                    "pattern": pattern(), "preloaded": standby_alive(),
                                    "text": status_text()})
            elif line == "show":
                notify(f"{status_text()}. To stop it: right-click this icon -> Quit, "
                       f"or in JARVIS: SETUP -> CLAP LAUNCHER.", 5000)
                reply = "ok"
            elif line == "quit":
                log("stopped from JARVIS")
                notify("Stopped. Open the shortcut to start it again.", 2500)
                QTimer.singleShot(300, app.quit)
                reply = "ok"
            elif line == "ping":
                reply = "pong"
            else:
                reply = "?"
            sock.write((reply + "\n").encode("utf-8"))
            sock.flush()
            sock.disconnectFromServer()

        sock.readyRead.connect(answer)

    server.newConnection.connect(on_connection)

    # ── tray menu ────────────────────────────────────────────────────────────
    menu = QMenu()
    menu.addAction(a_status)
    menu.addSeparator()
    a_start = menu.addAction("Start JARVIS now")
    a_start.triggered.connect(lambda: launch("the tray menu"))
    a_pause = menu.addAction("Pause listening")
    a_pause.setCheckable(True)

    def toggle_pause(on: bool) -> None:
        state["paused"] = on
        if on:
            close_mic("paused")
        else:
            open_mic()
    a_pause.toggled.connect(toggle_pause)

    m_pattern = menu.addMenu("Clap pattern")
    g_pattern = QActionGroup(m_pattern)
    for n, label in ((1, "One clap"), (2, "Two claps (recommended)")):
        a = m_pattern.addAction(label)
        a.setCheckable(True)
        a.setChecked(settings["claps"] == n)
        g_pattern.addAction(a)
        a.triggered.connect(lambda _c=False, n=n: set_setting("claps", n))

    m_sens = menu.addMenu("Sensitivity")
    g_sens = QActionGroup(m_sens)
    for key, label in (("low", "Low - only clear, close claps"), ("normal", "Normal"),
                       ("high", "High - softer or distant claps")):
        a = m_sens.addAction(label)
        a.setCheckable(True)
        a.setChecked(settings["sensitivity"] == key)
        g_sens.addAction(a)
        a.triggered.connect(lambda _c=False, key=key: set_setting("sensitivity", key))

    a_learn = menu.addAction("Learn my clap...")
    a_learn.triggered.connect(learn)
    a_forget = menu.addAction("Forget the learned clap")
    a_forget.triggered.connect(forget)
    a_notify = menu.addAction("Show each clap it hears")
    a_notify.setCheckable(True)
    a_notify.setChecked(settings["notify_heard"])
    a_notify.toggled.connect(lambda on: set_setting("notify_heard", on))
    menu.addSeparator()
    a_preload = menu.addAction("Instant start (keeps JARVIS ready, ~250 MB)")
    a_preload.setCheckable(True)
    a_preload.setChecked(settings["preload"])
    a_preload.toggled.connect(lambda on: set_setting("preload", on))
    a_greet = menu.addAction("Greet me when it starts")
    a_greet.setCheckable(True)
    a_greet.setChecked(settings["greet"])
    a_greet.toggled.connect(lambda on: set_setting("greet", on))
    a_auto = menu.addAction("Start when I log in")
    a_auto.setCheckable(True)
    a_auto.setChecked(Autostart.enabled())

    def toggle_auto(on: bool) -> None:
        try:
            Autostart.set(on)
            log(f"start at login: {'on' if on else 'off'}")
        except Exception as e:
            log(f"could not change start-at-login: {e!r}")
            a_auto.setChecked(Autostart.enabled())
    a_auto.toggled.connect(toggle_auto)
    a_log = menu.addAction("Open the log")
    a_log.triggered.connect(lambda: open_file(_LOG))
    menu.addSeparator()
    a_quit = menu.addAction("Quit")
    a_quit.triggered.connect(app.quit)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: launch("a double-click on the tray icon")
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)

    def set_setting(key: str, value) -> None:
        settings[key] = value
        try:
            save_clap_launcher(**{key: value})
        except Exception as e:
            log(f"setting not saved: {e!r}")
        log(f"setting {key} = {value}")
        if key in ("claps", "sensitivity") and state["learning"] is None:
            detector["d"] = make_detector()
        if key in ("preload", "greet"):
            # The waiting copy was started with the old choice.
            stop_standby()
            state["standby_fails"] = 0
            ensure_standby()
        refresh()

    app.aboutToQuit.connect(stop_standby)
    tray.show()

    state["running"] = jarvis_running()
    if not state["running"]:
        open_mic()
        ensure_standby()
        notify(f"Listening. {pattern()[0].upper() + pattern()[1:]} starts JARVIS. "
               "Sound is analysed on this computer only.")
    refresh()

    code = app.exec()
    close_mic("quitting")
    server.close()
    log("launcher stopped")
    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log("launcher crashed:\n" + traceback.format_exc())
        raise
