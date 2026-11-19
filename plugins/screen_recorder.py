# ==============================================================================
# JARVIS Plugin — Screen Recorder
# Brand: Gemind
# Author: upgraderguy777 (https://github.com/upgraderguy777/jarvis-plugins)
# License: MIT License
# Notice: Vibecoded with precision for free-tier users.
# ==============================================================================

"""
JARVIS plugin — Screen Recorder (start / stop / pause / resume / status).

Voice control works cross-platform. System-wide background hotkeys
(Win+Alt+R, Win+Alt+P) are powered by Win32 APIs (ctypes) on Windows without
installing any extra keyboard hooks.
"""

import platform
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

PLUGIN = {
    "name": "screen_recorder",
    "version": "1.0.0",
    "author": "Gemind (upgraderguy777)",
    "license": "MIT",
    "repository": "https://github.com/upgraderguy777/jarvis-plugins",
    "description": (
        "Starts, stops, pauses, resumes, or checks on a REAL screen "
        "recording (a video file over time, not a single screenshot) of "
        "the user's screen. Use for: 'start recording my screen', 'record "
        "my screen', 'pause the recording', 'resume recording', 'stop "
        "recording', 'how long have you been recording', 'ekranımı "
        "kaydet', 'kaydı durdur'. Set 'action' to 'start', 'stop', "
        "'pause', 'resume', or 'status' based on what the user wants. If "
        "they mention wanting their voice/narration/microphone included, "
        "set 'with_audio' to true. The user can also control this "
        "directly from the keyboard at any time with Win+Alt+R (toggle "
        "start/stop) and Win+Alt+P (toggle pause/resume). This "
        "captures the screen as VIDEO over time — use screenshot_annotate "
        "instead for a single still image with something circled."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "One of: 'start', 'stop', 'pause', 'resume', 'status'.",
            },
            "with_audio": {
                "type": "BOOLEAN",
                "description": (
                    "True if the user wants microphone audio recorded "
                    "alongside the video. Only relevant for 'start'."
                ),
            },
            "filename": {
                "type": "STRING",
                "description": "Optional file name (without extension). Only relevant for 'start'.",
            },
        },
        "required": ["action"],
    },
}

_FPS                = 12
_FOURCC             = "mp4v"
_MAX_RECORD_SECONDS = 30 * 60
_AUDIO_SAMPLE_RATE  = 44100

_state = {"active": False, "paused": False}
_state_lock = threading.Lock()
_last_player = {"player": None}


def _output_dir() -> Path:
    downloads = Path.home() / "Downloads"
    return downloads if downloads.is_dir() else Path.home()


def _safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "", (name or "").strip()) or "screen_recording"
    return name[:50]


def _open_file(path: Path) -> bool:
    import os
    try:
        system = platform.system()
        if system == "Windows":
            os.startfile(str(path))
        elif system == "Darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True
    except Exception:
        return False


def _speak_if_possible(player, text: str) -> None:
    if not player or not text:
        return
    if hasattr(player, "request_say"):
        try:
            player.request_say(text)
            return
        except Exception:
            pass
    try:
        player.write_log(f"JARVIS: {text}")
    except Exception:
        pass


def _record_loop(writer, stop_event: threading.Event,
                  pause_event: threading.Event, monitor: dict) -> None:
    import mss
    frame_interval = 1.0 / _FPS
    t_start = time.time()
    with mss.mss() as sct:
        while not stop_event.is_set():
            loop_t0 = time.time()
            if loop_t0 - t_start > _MAX_RECORD_SECONDS:
                stop_event.set()
                break
            if pause_event.is_set():
                time.sleep(0.1)
                continue
            try:
                frame = np.array(sct.grab(monitor))[:, :, :3]
                writer.write(frame)
            except Exception:
                break
            elapsed = time.time() - loop_t0
            sleep_left = frame_interval - elapsed
            if sleep_left > 0:
                time.sleep(sleep_left)


def _start_audio():
    import sounddevice as sd
    frames = []

    def _callback(indata, frame_count, time_info, status):
        frames.append(indata.copy())

    stream = sd.InputStream(samplerate=_AUDIO_SAMPLE_RATE, channels=1, callback=_callback)
    stream.start()
    return stream, frames, _AUDIO_SAMPLE_RATE


def _mux(video_path: Path, audio_path: Path, final_path: Path) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        subprocess.run(
            [ffmpeg, "-y", "-i", str(video_path), "-i", str(audio_path),
             "-c:v", "copy", "-c:a", "aac", "-shortest", str(final_path)],
            check=True, capture_output=True, timeout=120,
        )
        return final_path.exists()
    except Exception:
        return False


def _do_start(filename: str = None, with_audio: bool = False, player=None) -> str:
    with _state_lock:
        if _state.get("active"):
            return "I'm already recording your screen — say stop or press Win+Alt+R when you're done."

        try:
            import cv2
        except ImportError:
            return "I need opencv-python to record video. Run: pip install opencv-python"
        try:
            import mss as mss_mod
        except ImportError:
            return "I need mss to capture the screen. Run: pip install mss"

        with mss_mod.mss() as sct:
            monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        w, h = monitor["width"], monitor["height"]

        fname = _safe_name(filename or "screen_recording")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = _output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        video_path = out_dir / f"{fname}_{stamp}.mp4"

        fourcc = cv2.VideoWriter_fourcc(*_FOURCC)
        writer = cv2.VideoWriter(str(video_path), fourcc, _FPS, (w, h))
        if not writer.isOpened():
            return "I couldn't start the video writer — recording failed to start."

        audio_stream = audio_frames = audio_path = sample_rate = None
        if with_audio:
            try:
                audio_stream, audio_frames, sample_rate = _start_audio()
                audio_path = out_dir / f"{fname}_{stamp}_audio.wav"
            except Exception:
                with_audio = False
                audio_stream = None

        stop_event = threading.Event()
        pause_event = threading.Event()
        thread = threading.Thread(
            target=_record_loop, args=(writer, stop_event, pause_event, monitor),
            daemon=True, name="screen-recorder",
        )
        thread.start()

        _state.clear()
        _state.update({
            "active": True,
            "paused": False,
            "thread": thread,
            "stop_event": stop_event,
            "pause_event": pause_event,
            "writer": writer,
            "video_path": video_path,
            "with_audio": with_audio,
            "audio_stream": audio_stream,
            "audio_frames": audio_frames,
            "audio_path": audio_path,
            "sample_rate": sample_rate,
            "start_time": time.time(),
            "total_paused": 0.0,
            "pause_started_at": None,
        })

    if player:
        try:
            player.write_log(f"JARVIS: Screen recording started -> {video_path.name}")
        except Exception:
            pass
    return ("Recording your screen now" + (" with audio" if with_audio else "")
            + ". Say stop or press Win+Alt+R when you're done.")


def _do_stop(player=None) -> str:
    with _state_lock:
        if not _state.get("active"):
            return "I'm not currently recording anything."
        if _state.get("paused") and _state.get("pause_started_at"):
            _state["total_paused"] += time.time() - _state["pause_started_at"]
        _state["stop_event"].set()
        thread       = _state["thread"]
        writer       = _state["writer"]
        video_path   = _state["video_path"]
        with_audio   = _state["with_audio"]
        audio_stream = _state["audio_stream"]
        audio_frames = _state["audio_frames"]
        audio_path   = _state["audio_path"]
        sample_rate  = _state["sample_rate"]
        start_time   = _state["start_time"]
        total_paused = _state["total_paused"]
        _state["active"] = False

    thread.join(timeout=5)
    try:
        writer.release()
    except Exception:
        pass

    final_path = video_path
    audio_saved = False
    if with_audio and audio_stream is not None:
        try:
            audio_stream.stop()
            audio_stream.close()
            import soundfile as sf
            if audio_frames:
                data = np.concatenate(audio_frames, axis=0)
                sf.write(str(audio_path), data, sample_rate)
                audio_saved = True
        except Exception:
            audio_saved = False

    muxed = False
    if audio_saved:
        muxed_path = video_path.with_name(video_path.stem + "_final.mp4")
        if _mux(video_path, audio_path, muxed_path):
            final_path = muxed_path
            muxed = True
            try:
                video_path.unlink(missing_ok=True)
                audio_path.unlink(missing_ok=True)
            except Exception:
                pass

    active_elapsed = int((time.time() - start_time) - total_paused)
    mins, secs = divmod(max(active_elapsed, 0), 60)
    opened = _open_file(final_path)

    if player:
        try:
            player.write_log(f"JARVIS: Screen recording stopped -> {final_path.name} "
                              f"({mins}m{secs}s){' — opened' if opened else ''}.")
        except Exception:
            pass

    spoken = (f"Recording stopped — {mins} minute{'s' if mins != 1 else ''} and "
              f"{secs} seconds recorded, saved to your Downloads as {final_path.name}.")
    if with_audio and not audio_saved:
        spoken += " I couldn't capture the microphone audio, so it's video only."
    elif with_audio and audio_saved and not muxed:
        spoken += " Audio was recorded separately since ffmpeg isn't available to merge it in."
    if opened:
        spoken += " Opening it now."
    return spoken


def _do_pause(player=None) -> str:
    with _state_lock:
        if not _state.get("active"):
            return "I'm not recording anything to pause."
        if _state.get("paused"):
            return "Already paused."
        _state["pause_event"].set()
        _state["paused"] = True
        _state["pause_started_at"] = time.time()
    if player:
        try:
            player.write_log("JARVIS: Screen recording paused.")
        except Exception:
            pass
    return "Recording paused. Press Win+Alt+P or say resume to continue."


def _do_resume(player=None) -> str:
    with _state_lock:
        if not _state.get("active"):
            return "I'm not recording anything to resume."
        if not _state.get("paused"):
            return "It isn't paused."
        _state["total_paused"] += time.time() - _state["pause_started_at"]
        _state["pause_event"].clear()
        _state["paused"] = False
        _state["pause_started_at"] = None
    if player:
        try:
            player.write_log("JARVIS: Screen recording resumed.")
        except Exception:
            pass
    return "Recording resumed."


def _do_status() -> str:
    with _state_lock:
        if not _state.get("active"):
            return "I'm not currently recording your screen."
        paused = _state.get("paused")
        elapsed = (time.time() - _state["start_time"]) - _state["total_paused"]
        if paused and _state.get("pause_started_at"):
            elapsed -= (time.time() - _state["pause_started_at"])
    mins, secs = divmod(int(max(elapsed, 0)), 60)
    status = "paused" if paused else "recording"
    return f"I'm currently {status} — {mins}m {secs}s of footage so far."


def _hotkey_toggle_record() -> None:
    with _state_lock:
        active = _state.get("active")
    player = _last_player.get("player")
    msg = _do_stop(player=player) if active else _do_start(player=player)
    _speak_if_possible(player, msg)


def _hotkey_toggle_pause() -> None:
    with _state_lock:
        active = _state.get("active")
        paused = _state.get("paused")
    if not active:
        return
    player = _last_player.get("player")
    msg = _do_resume(player=player) if paused else _do_pause(player=player)
    _speak_if_possible(player, msg)


def _hotkey_message_loop() -> None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                    ctypes.c_uint, ctypes.c_uint]
    user32.GetMessageW.restype = ctypes.c_int

    MOD_ALT, MOD_WIN, MOD_NOREPEAT = 0x0001, 0x0008, 0x4000
    VK_R, VK_P = 0x52, 0x50
    ID_TOGGLE, ID_PAUSE = 1, 2
    WM_HOTKEY = 0x0312

    ok_toggle = user32.RegisterHotKey(None, ID_TOGGLE, MOD_WIN | MOD_ALT | MOD_NOREPEAT, VK_R)
    ok_pause = user32.RegisterHotKey(None, ID_PAUSE, MOD_WIN | MOD_ALT | MOD_NOREPEAT, VK_P)
    if not (ok_toggle or ok_pause):
        return

    msg = wintypes.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY:
                if msg.wParam == ID_TOGGLE:
                    _hotkey_toggle_record()
                elif msg.wParam == ID_PAUSE:
                    _hotkey_toggle_pause()
    finally:
        if ok_toggle:
            user32.UnregisterHotKey(None, ID_TOGGLE)
        if ok_pause:
            user32.UnregisterHotKey(None, ID_PAUSE)


def _setup_hotkeys() -> None:
    if platform.system() != "Windows":
        return
    try:
        threading.Thread(target=_hotkey_message_loop, daemon=True,
                          name="screen-recorder-hotkeys").start()
    except Exception:
        pass


_setup_hotkeys()


def run(parameters: dict, player=None, session_memory=None) -> str:
    _last_player["player"] = player

    action = (parameters.get("action") or "").strip().lower()
    if action == "status":
        return _do_status()
    if action == "start":
        return _do_start(filename=parameters.get("filename"),
                          with_audio=bool(parameters.get("with_audio")),
                          player=player)
    if action == "stop":
        return _do_stop(player=player)
    if action == "pause":
        return _do_pause(player=player)
    if action == "resume":
        return _do_resume(player=player)
    return "Should I start, stop, pause, resume, or check the status of the screen recording?"
