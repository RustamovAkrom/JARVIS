"""
JARVIS plugin — 3D Printer Control (Phase 1).

Voice control for any Bambu Lab printer (and, later, Creality/Klipper) over the
local network: ask for status, pause / resume / stop the print, toggle the
chamber light, or have JARVIS proactively tell you when a print finishes or fails.

This plugin is for EVERYONE, not one machine — it holds no printer-specific
values. Connection details (brand, IP, access code, serial) are entered by each
user in the ⚙ plugin-settings tab and read back through plugins._printer_core.
Spoken return strings are English on purpose: JARVIS re-speaks them in the
user's own language (project rule).

Requires:  pip install paho-mqtt   (only needed to actually reach a Bambu; the
plugin still loads and explains this if the package is missing).
"""
from __future__ import annotations

import datetime as _dt
import threading
import time

from plugins._printer_core import (
    get_active_backend,
    get_active_printer,
    build_backend,
    open_camera,
    PrinterError,
    PrinterConfigError,
    PrinterConnectionError,
    PrinterDependencyError,
)


# ─────────────────────────────────────────────────────────────────────────────
# Tool declaration
# ─────────────────────────────────────────────────────────────────────────────
PLUGIN = {
    "name": "printer_control",
    "description": (
        "Full voice control of the user's 3D PRINTER over the local network. "
        "Use for ANYTHING about their physical 3D printer or print job: reporting "
        "status/progress/time-left, pausing/resuming/stopping the print, setting "
        "the nozzle or bed temperature, cooling down, changing the print-speed "
        "profile, the part-cooling fan, the chamber light, homing the head, "
        "showing the printer's own camera live on the display, and starting "
        "proactive updates. Examples: 'how is the print going', 'how "
        "much time is left', 'pause/resume/stop the print', 'heat the nozzle to "
        "220', 'set the bed to 60', 'cool it down', 'set speed to sport', 'turn "
        "the fan to 50 percent', 'turn the light on', 'home the printer', 'show "
        "me inside the printer', 'open the printer camera', 'close the printer "
        "camera', 'let me know when it's done'. This is about a PHYSICAL 3D "
        "printer only — never "
        "use it for computer/screen tasks. "
        "CONFIRMATION RULE: for destructive actions ('stop', 'home') call the "
        "tool first WITHOUT confirm; it returns a question — relay it, and only "
        "call again with confirm=true once the user clearly agrees."
    ),
    # Used in place of `description` only when a small model is on a tool
    # budget - Gemini still gets the full text. See providers/schema.py.
    "brief": (
        "Controls the 3D printer: status, live camera, pause/stop, heat, light."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "What to do. Reporting: 'status'. Job: 'pause', 'resume', "
                    "'stop' (cancels the print). Temperature: 'set_nozzle_temp', "
                    "'set_bed_temp' (put °C in 'value'), 'cooldown' (all heaters "
                    "off). Speed: 'set_speed' (put 1-4 in 'value'). Fan: 'fan_on' "
                    "(optional 0-100 percent in 'value'), 'fan_off'. Light: "
                    "'light_on', 'light_off'. Motion: 'home'. Camera: "
                    "'camera_on' (live view of the inside of the printer on the "
                    "display), 'camera_off'. Proactive updates: 'monitor_on', "
                    "'monitor_off'."
                ),
                "enum": ["status", "pause", "resume", "stop",
                         "set_nozzle_temp", "set_bed_temp", "cooldown",
                         "set_speed", "fan_on", "fan_off",
                         "light_on", "light_off", "home",
                         "camera_on", "camera_off",
                         "monitor_on", "monitor_off"],
            },
            "value": {
                "type": "NUMBER",
                "description": (
                    "Numeric argument when the action needs one: temperature in "
                    "°C for set_nozzle_temp/set_bed_temp; fan speed percent (0-100) "
                    "for fan_on; speed profile for set_speed where 1=silent, "
                    "2=standard, 3=sport, 4=ludicrous."
                ),
            },
            "confirm": {
                "type": "BOOLEAN",
                "description": (
                    "Set to true ONLY after the user has clearly confirmed a "
                    "destructive action (stop, home). Leave unset otherwise."
                ),
            },
        },
        "required": ["action"],
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Settings schema — rendered by the generic ⚙ plugin-settings tab (no core edit).
# Namespace is shared with any future printer plugin so a user enters their
# connection details once. Today 'printer_control' is the only one that
# exists; nothing else needs downloading.
# "printer", so this one form configures all of them.
# ─────────────────────────────────────────────────────────────────────────────
def _test_connection(values: dict):
    """Called by the settings tab's 'Connect & Test' button. Builds a backend
    straight from the entered values, tries to connect, and reports back.
    Returns (ok: bool, message: str) — message is shown verbatim in the panel."""
    try:
        backend = build_backend(values or {})
    except PrinterError as e:
        return False, str(e)
    try:
        ok = backend.connect()
        line = backend.get_status().one_line() if ok else ""
    except PrinterDependencyError as e:
        return False, f"Missing dependency: {e}"
    except PrinterError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Unexpected error: {e}"
    finally:
        try:
            backend.disconnect()
        except Exception:
            pass
    if ok:
        return True, f"Connected ✓  —  {line}"
    return False, ("Reached the printer but no status arrived. "
                   "Double-check the access code and that the printer is on.")


PLUGIN_SETTINGS = {
    "namespace": "printer",
    "title": "🖨  3D PRINTER",
    "fields": [
        {"key": "brand", "label": "Brand", "type": "choice",
         "options": ["bambu", "creality"], "default": "bambu"},
        {"key": "model", "label": "Model", "type": "text",
         "placeholder": "e.g. P1S / X1C / K1  (optional)"},
        {"key": "ip", "label": "IP Address", "type": "text",
         "placeholder": "e.g. 192.168.1.50  (printer screen → network)"},
        {"key": "access_code", "label": "Access Code  (Bambu)", "type": "password",
         "placeholder": "8-digit code from the printer's LAN screen"},
        {"key": "serial", "label": "Serial Number  (Bambu)", "type": "text",
         "placeholder": "printer → Settings → Device, or the sticker"},
    ],
    "action": {"label": "CONNECT & TEST", "run": _test_connection},
}


# ─────────────────────────────────────────────────────────────────────────────
# Proactive monitor — a background thread that watches the print and speaks up
# on the events that matter (finished / failed). Opt-in via 'monitor_on'.
# ─────────────────────────────────────────────────────────────────────────────
_monitor_thread: threading.Thread | None = None
_monitor_stop = threading.Event()
_announced: set = set()          # milestone keys already spoken this print
_POLL_SECONDS = 30


def _eta_clock(remaining_min) -> str | None:
    """Turn 'minutes left' into a wall-clock finish time like '15:40'."""
    try:
        m = int(remaining_min)
    except (TypeError, ValueError):
        return None
    if m <= 0:
        return None
    return (_dt.datetime.now() + _dt.timedelta(minutes=m)).strftime("%H:%M")


def _say(player, directive: str) -> None:
    """Ask JARVIS to speak (it phrases the English directive in the user's
    language). Safe no-op if the host doesn't expose request_say."""
    if player is None:
        return
    fn = getattr(player, "request_say", None)
    if callable(fn):
        try:
            fn(directive)
        except Exception:
            pass


def _log(player, msg: str) -> None:
    print(f"[Printer] {msg}")
    if player is not None:
        try:
            player.write_log(f"JARVIS: {msg}")
        except Exception:
            pass


def _monitor_loop(player):
    """Watch the print and speak up at the moments that matter — halfway, almost
    done (with time left), finished, failed. Each milestone is announced once;
    terminal states stop the loop; connection hiccups are tolerated silently."""
    while not _monitor_stop.is_set():
        try:
            st = get_active_backend().get_status()
            state = st.state

            if state == "finished":
                _say(player, "Tell the user their 3D print has finished successfully.")
                break
            if state == "failed":
                _say(player, "Warn the user that their 3D print has failed or was stopped.")
                break

            if state == "printing":
                prog = st.progress or 0
                rem  = st.remaining_min
                if prog >= 50 and "half" not in _announced:
                    _announced.add("half")
                    eta = _eta_clock(rem)
                    tail = f" It should be done around {eta}." if eta else ""
                    _say(player, "Tell the user their 3D print is halfway done, at about "
                                 f"{int(prog)} percent.{tail}")
                if ((rem is not None and rem <= 10) or prog >= 90) and "almost" not in _announced:
                    _announced.add("almost")
                    if rem is not None and rem > 0:
                        _say(player, "Tell the user their 3D print is almost finished — "
                                     f"about {int(rem)} minutes left.")
                    else:
                        _say(player, "Tell the user their 3D print is almost finished.")
        except PrinterError:
            # Not set up / unreachable — keep quiet and retry; the user may just
            # be powering the printer on.
            pass
        except Exception:
            pass
        _monitor_stop.wait(_POLL_SECONDS)


def _autostart_monitor(player) -> None:
    """Start the proactive monitor silently (no spoken confirmation). Used when
    the user checks status during an active print, so alerts flow without them
    having to ask for monitoring explicitly."""
    global _monitor_thread
    if _monitor_thread is not None and _monitor_thread.is_alive():
        return
    _monitor_stop.clear()
    _announced.clear()
    _monitor_thread = threading.Thread(target=_monitor_loop, args=(player,), daemon=True)
    _monitor_thread.start()


def _start_monitor(player) -> str:
    if _monitor_thread is not None and _monitor_thread.is_alive():
        return "I'm already keeping an eye on the print and will tell you how it's going."
    _autostart_monitor(player)
    return ("Alright — I'll keep you posted: I'll tell you when it's halfway, "
            "when it's nearly done, and the moment it finishes or if anything goes wrong.")


def _stop_monitor() -> str:
    global _monitor_thread
    _monitor_stop.set()
    _monitor_thread = None
    return "Stopped watching the print."


# ─────────────────────────────────────────────────────────────────────────────
# Live camera — the printer's own camera where the avatar normally is, so the
# user never has to open the maker's app to look inside. It feeds the HUD's
# live-camera page (the same one pushup_counter uses), which means the page's
# ✕ CLOSE button, a video taking the centre, or 'camera_off' all end it.
# ─────────────────────────────────────────────────────────────────────────────
_cam_thread: threading.Thread | None = None
_cam_stop = threading.Event()
_CAM_FPS = 20
_CAM_PAGE = 1              # the live-camera page in the HUD's centre stack
_STATUS_EVERY = 2.0        # seconds between readout refreshes on the picture


def _hud(player):
    """The main window, if this player drives one that has the camera page.
    None when running headless or answering from the phone."""
    win = getattr(player, "_win", None) if player is not None else None
    needed = ("_cam_stream_sig", "_cam_frame_sig", "_cam_stop", "_hud_cam_stack")
    return win if win is not None and all(hasattr(win, a) for a in needed) else None


def _readout(jpeg: bytes, st) -> bytes:
    """Lay a thin strip along the bottom of the frame: progress bar while it
    prints, then numbers only — %, layers, minutes left, nozzle and bed °C —
    so nothing on the picture needs translating."""
    if st is None or st.state == "offline":
        return jpeg
    try:
        import io
        from PIL import Image, ImageDraw, ImageFont
        img = Image.open(io.BytesIO(jpeg)).convert("RGB")
        w, h = img.size
        size = max(14, h // 30)
        try:
            font = ImageFont.load_default(size=size)
        except TypeError:                       # Pillow < 10.1: fixed bitmap font
            font = ImageFont.load_default()
        bits = []
        if st.state in ("printing", "paused") and st.progress is not None:
            bits.append(f"{st.progress:.0f}%")
            if st.layer is not None and st.total_layers:
                bits.append(f"{st.layer}/{st.total_layers}")
            if st.remaining_min:
                bits.append(f"~{st.remaining_min} min")
        if st.nozzle_temp is not None:
            bits.append(f"{st.nozzle_temp:.0f}°C")
        if st.bed_temp is not None:
            bits.append(f"{st.bed_temp:.0f}°C")
        strip = int(size * 2.2)
        shade = Image.new("RGBA", (w, strip), (0, 0, 0, 140))
        img.paste(shade, (0, h - strip), shade)
        d = ImageDraw.Draw(img)
        if st.state in ("printing", "paused") and st.progress is not None:
            d.rectangle((0, h - strip, int(w * st.progress / 100), h - strip + 3),
                        fill=(0, 200, 255) if st.state == "printing" else (255, 170, 0))
        d.text((size, h - strip + (strip - size) // 2), "   ·   ".join(bits),
               font=font, fill=(235, 245, 255))
        if st.error_code:
            d.ellipse((w - size * 2, h - strip + size // 2,
                       w - size, h - strip + size * 3 // 2), fill=(255, 60, 60))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=82)
        return out.getvalue()
    except Exception:
        return jpeg


def _camera_loop(player, win, feed, backend, lit_by_us: bool) -> None:
    shown = False          # has our page actually reached the screen yet?
    status, status_at = None, 0.0
    try:
        for n, jpeg in enumerate(feed.frames(_CAM_FPS)):
            if _cam_stop.is_set() or win._cam_stop.is_set():
                break
            if n == 0:
                win._cam_stream_sig.emit(True)
            try:
                page = win._hud_cam_stack.currentIndex()
            except Exception:
                page = _CAM_PAGE
            if page == _CAM_PAGE:
                shown = True
            elif shown:
                break      # a video or the webcam took the centre over
            if backend is not None and time.time() - status_at > _STATUS_EVERY:
                try:
                    status = backend.get_status()
                except Exception:
                    status = None
                status_at = time.time()
            win._cam_frame_sig.emit(_readout(jpeg, status))
        else:
            if not _cam_stop.is_set():
                _log(player, "The printer camera stream ended.")
    except Exception as e:
        _log(player, f"Camera stream error: {e}")
    finally:
        feed.close()
        try:
            still_ours = win._hud_cam_stack.currentIndex() == _CAM_PAGE
        except Exception:
            still_ours = True
        if still_ours or not shown:
            win._cam_stream_sig.emit(False)
        if lit_by_us and backend is not None:
            try:
                backend.set_light(False)   # put it back the way we found it
            except Exception:
                pass


def _stop_camera(wait: float = 3.0) -> bool:
    global _cam_thread
    running = _cam_thread is not None and _cam_thread.is_alive()
    _cam_stop.set()
    if running:
        _cam_thread.join(wait)
    _cam_thread = None
    return running


def _start_camera(player) -> str:
    global _cam_thread
    win = _hud(player)
    if win is None:
        return ("The live printer camera can only be shown on the desktop "
                "display, and there isn't one here.")
    _stop_camera()
    try:
        feed, backend = open_camera(player=player)
    except PrinterConfigError:
        return ("No printer is set up yet. Open the plugin settings and add your "
                "printer's brand, IP address and access code first.")
    except PrinterDependencyError as e:
        return f"I can't open the printer camera until a component is installed: {e}."
    except PrinterError as e:
        return f"I couldn't open the printer camera: {e}"

    # Something else on the live page (the webcam) is asked to let go first,
    # and the page's own stop flag is reset so its ✕ button can stop us.
    try:
        if win._hud_cam_stack.currentIndex() == _CAM_PAGE:
            win._cam_stop.set()
            time.sleep(0.5)
    except Exception:
        pass
    win._cam_stop.clear()

    # A dark chamber films as black. Light it for the viewing, and switch it
    # back off afterwards only if it was off to begin with.
    lit_by_us = False
    light = getattr(backend, "light_is_on", None)
    if callable(light) and light() is False:
        try:
            lit_by_us = bool(backend.set_light(True))
        except Exception:
            pass

    _cam_stop.clear()
    _cam_thread = threading.Thread(target=_camera_loop, name="printer-camera",
                                   args=(player, win, feed, backend, lit_by_us),
                                   daemon=True)
    _cam_thread.start()
    msg = "The printer camera is live on the display."
    if lit_by_us:
        msg += " I switched the chamber light on so you can see inside; it goes off again when you close the camera."
    _log(player, msg)
    return msg


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
_CRITICAL_PROMPTS = {
    "stop": ("Stopping will cancel the current print and it can't be resumed. "
             "Are you sure you want to stop it?"),
    "home": ("This moves the print head back to its home position — best done "
             "when nothing is printing. Do you want me to home it?"),
}
_SPEED_NAMES = {1: "silent", 2: "standard", 3: "sport", 4: "ludicrous"}


def _num(value, default=None):
    """Coerce Gemini's numeric argument to an int, or return `default`."""
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def run(parameters: dict, player=None, session_memory=None) -> str:
    action  = (parameters.get("action") or "").strip().lower()
    value   = parameters.get("value")
    confirm = bool(parameters.get("confirm"))

    if not action:
        return ("What would you like me to do with the printer — status, pause, "
                "resume, stop, temperature, speed, fan, or the light?")

    # Monitoring toggles don't need a live connection up front.
    if action == "monitor_on":
        msg = _start_monitor(player); _log(player, msg); return msg
    if action == "monitor_off":
        msg = _stop_monitor(); _log(player, msg); return msg

    # The camera opens its own connection (Klipper cameras need no control
    # link at all), so it is handled before the backend below.
    if action == "camera_on":
        return _start_camera(player)
    if action == "camera_off":
        return ("Closed the printer camera." if _stop_camera()
                else "The printer camera isn't open.")

    # Two-stage confirmation for destructive actions — ask first, act only once
    # the model re-calls with confirm=true after the user agrees.
    if action in _CRITICAL_PROMPTS and not confirm:
        return _CRITICAL_PROMPTS[action]

    # Everything else needs the printer. Turn connection problems into calm,
    # spoken guidance instead of raising. Passing `player` lets the connection
    # layer announce "trying again…" if it has to scan for a moved IP.
    try:
        backend = get_active_backend(player=player)
    except PrinterConfigError:
        return ("No printer is set up yet. Open the plugin settings and add your "
                "printer's brand, IP address and access code first.")
    except PrinterDependencyError as e:
        return f"I can't reach the printer until a component is installed: {e}."
    except PrinterConnectionError as e:
        return (f"I couldn't connect to the printer: {e}. "
                "Check that it's powered on and on the same network.")
    except PrinterError as e:
        return f"Printer error: {e}."

    try:
        if action == "status":
            st = backend.get_status()
            if st.state == "offline":
                return "The printer is online but hasn't reported its status yet — give it a moment."
            # While a print is running, quietly start proactive monitoring so the
            # user gets halfway / almost-done / finished alerts without asking.
            if st.state == "printing":
                _autostart_monitor(player)
            line = st.one_line()
            eta = _eta_clock(st.remaining_min)
            extra = f" Estimated finish around {eta}." if eta else ""
            _log(player, f"Status — {line}")
            return f"Here's the printer status: {line}.{extra}"

        if action == "pause":
            return ("Print paused." if backend.pause()
                    else "I couldn't send the pause command — please try again.")

        if action == "resume":
            return ("Print resumed." if backend.resume()
                    else "I couldn't send the resume command — please try again.")

        if action == "stop":
            return ("Print stopped." if backend.stop()
                    else "I couldn't send the stop command — please try again.")

        if action == "set_nozzle_temp":
            t = _num(value)
            if t is None or not (0 <= t <= 320):
                return "Tell me a nozzle temperature between 0 and 320 degrees."
            return (f"Setting the nozzle to {t}°C." if backend.set_temperature("nozzle", t)
                    else "I couldn't set the nozzle temperature — please try again.")

        if action == "set_bed_temp":
            t = _num(value)
            if t is None or not (0 <= t <= 120):
                return "Tell me a bed temperature between 0 and 120 degrees."
            return (f"Setting the bed to {t}°C." if backend.set_temperature("bed", t)
                    else "I couldn't set the bed temperature — please try again.")

        if action == "cooldown":
            ok = backend.set_temperature("nozzle", 0) and backend.set_temperature("bed", 0)
            return ("Cooling down — nozzle and bed heaters are off." if ok
                    else "I couldn't turn the heaters off — please try again.")

        if action == "set_speed":
            lvl = _num(value)
            if lvl not in (1, 2, 3, 4):
                return "Which speed profile — silent, standard, sport, or ludicrous?"
            return (f"Print speed set to {_SPEED_NAMES[lvl]}." if backend.set_speed(lvl)
                    else "I couldn't change the print speed — please try again.")

        if action == "fan_on":
            pct = max(0, min(100, _num(value, 100)))
            return (f"Part-cooling fan set to {pct}%." if backend.set_fan(True, pct)
                    else "I couldn't change the fan — this model may not support it.")

        if action == "fan_off":
            return ("Part-cooling fan turned off." if backend.set_fan(False)
                    else "I couldn't change the fan — this model may not support it.")

        if action in ("light_on", "light_off"):
            on = action == "light_on"
            return (f"Chamber light turned {'on' if on else 'off'}." if backend.set_light(on)
                    else "I couldn't change the light — this model may not support it.")

        if action == "home":
            st = backend.get_status()
            if st.state == "printing":
                return "I won't home the head while a print is running — that would ruin it."
            return ("Homing the print head." if backend.home()
                    else "I couldn't send the home command — please try again.")

        return f"I don't know the printer action '{action}'."
    except Exception as e:
        _log(player, f"Action '{action}' failed: {e}")
        return f"Sorry, the printer '{action}' command failed: {e}"
