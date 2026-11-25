# ==============================================================================
# JARVIS Plugin: YouTube Controller
# Brand: Gemind
# License: MIT License
# ==============================================================================

"""
JARVIS plugin — YouTube Controller (Keybind-driven, zero-token).

Controls an active YouTube tab or browser window using native YouTube
keyboard shortcuts and Windows automation — zero Gemini API calls,
zero tokens, and instant response.

Supported Controls:
  - Play / Pause / Resume ('k')
  - Toggle Closed Captions / Subtitles ('c')
  - Next / Previous Video (Shift+N, Shift+P)
  - Forward & Rewind by dynamic seconds (greedy 10s 'l'/'j' + 5s Right/Left)
  - Skip Advertisements (UIA button detection with Tab+Enter fallback)
  - Volume Up / Down / Mute (Up, Down, 'm')
  - Playback Speed Adjustments (Shift+., Shift+,)
  - Display Modes (Fullscreen 'f', Theater Mode 't', Miniplayer 'i')
  - Seek / Jump to % of video (0 through 9)

Requirements:
  pip install pyautogui pygetwindow
  (Optional, for enhanced ad detection: pip install pywinauto)
"""

import time
from typing import Optional

PLUGIN = {
    "name": "youtube_controller",
    "description": (
        "Controls playback, navigation, captions, volume, speed, display modes, "
        "and ads on an active YouTube browser window or tab using pure keyboard "
        "shortcuts and zero API calls. Use this tool for requests such as: "
        "'pause the video', 'resume youtube', 'unpause', 'toggle captions', "
        "'turn on subtitles', 'next video', 'previous video', 'skip forward 30 seconds', "
        "'fast forward 15 seconds', 'rewind 10 seconds', 'go back 20 seconds', "
        "'skip ad', 'skip the advertisement', 'mute youtube', 'unmute', 'turn the volume up', "
        "'volume down', 'speed up the video', 'slow down the video', 'go fullscreen', "
        "'theater mode', 'toggle miniplayer', 'restart the video', 'start from the beginning', "
        "'jump to 50% of the video'. Do NOT use this tool to search for or open new "
        "YouTube videos."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "The playback or navigation action to perform. One of: "
                    "'play_pause', 'pause', 'resume', 'captions', 'next', 'previous', "
                    "'forward', 'rewind', 'skip_ad', 'mute', 'volume_up', 'volume_down', "
                    "'speed_up', 'speed_down', 'fullscreen', 'theater_mode', 'miniplayer', "
                    "'restart', 'seek'."
                ),
            },
            "seconds": {
                "type": "INTEGER",
                "description": (
                    "Number of seconds to jump forward or rewind (e.g. 5, 10, 15, 30, 60). "
                    "Defaults to 10."
                ),
            },
            "percent": {
                "type": "INTEGER",
                "description": (
                    "Percentage of video to jump to (0 to 90, e.g. 0 for beginning, 50 for halfway). "
                    "Only used when action is 'seek'."
                ),
            },
            "steps": {
                "type": "INTEGER",
                "description": (
                    "Number of increments for volume or speed adjustments (defaults to 2 for volume "
                    "[approx 10%], 1 for speed [0.25x])."
                ),
            },
        },
        "required": ["action"],
    },
}

_ACTION_ALIASES = {
    "pause": "pause",
    "resume": "resume",
    "unpause": "resume",
    "play": "resume",
    "toggle_play": "play_pause",
    "play_pause": "play_pause",
    "captions": "captions",
    "subtitles": "captions",
    "subtitle": "captions",
    "cc": "captions",
    "next": "next",
    "next_video": "next",
    "prev": "previous",
    "previous": "previous",
    "previous_video": "previous",
    "forward": "forward",
    "fast_forward": "forward",
    "skip_forward": "forward",
    "rewind": "rewind",
    "backward": "rewind",
    "back": "rewind",
    "skip_backward": "rewind",
    "skip_ad": "skip_ad",
    "skip_ads": "skip_ad",
    "skip_advertisement": "skip_ad",
    "mute": "mute",
    "unmute": "mute",
    "volume_up": "volume_up",
    "louder": "volume_up",
    "volume_down": "volume_down",
    "quieter": "volume_down",
    "softer": "volume_down",
    "speed_up": "speed_up",
    "faster": "speed_up",
    "speed_down": "speed_down",
    "slower": "speed_down",
    "slow_down": "speed_down",
    "fullscreen": "fullscreen",
    "full_screen": "fullscreen",
    "theater": "theater_mode",
    "theatre": "theater_mode",
    "theater_mode": "theater_mode",
    "theatre_mode": "theater_mode",
    "miniplayer": "miniplayer",
    "mini_player": "miniplayer",
    "restart": "restart",
    "start_over": "restart",
    "beginning": "restart",
    "seek": "seek",
    "jump": "seek",
}


def _ensure_dpi_aware() -> None:
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _find_youtube_window():
    try:
        import pygetwindow as gw
    except ImportError:
        return None, "I need the 'pygetwindow' library to find YouTube. Please run: pip install pygetwindow"

    try:
        windows = gw.getAllWindows()
    except Exception as e:
        return None, f"Couldn't enumerate active windows: {e}"

    # Priority 1: non-minimized windows with 'youtube' in the title
    for w in windows:
        title = (w.title or "").lower()
        if "youtube" in title and not w.isMinimized:
            return w, ""

    # Priority 2: minimized windows with 'youtube'
    for w in windows:
        title = (w.title or "").lower()
        if "youtube" in title:
            return w, ""

    return None, "Sir, I couldn't find an open YouTube window or tab."


def _focus_window(win) -> Optional[str]:
    try:
        if win.isMinimized:
            win.restore()
            time.sleep(0.1)
        win.activate()
        time.sleep(0.15)
        return None
    except Exception as e:
        return f"Couldn't bring YouTube to the foreground: {e}"


def _calculate_seeks(seconds: int, forward: bool = True):
    """Calculates the optimal combination of 10s ('l'/'j') and 5s (Right/Left) keys."""
    if seconds <= 0:
        seconds = 10
    tens = seconds // 10
    rem = seconds % 10
    fives = 1 if rem >= 3 else 0

    key_ten = "l" if forward else "j"
    key_five = "right" if forward else "left"

    actions = [key_ten] * tens + [key_five] * fives
    return actions if actions else [key_ten]


def _try_click_skip_ad_uia(win) -> bool:
    """Attempts to locate and click 'Skip Ad' via Windows UI Automation."""
    try:
        from pywinauto import Desktop
        uia_win = Desktop(backend="uia").window(handle=win._hWnd)
        if not uia_win.exists(timeout=0.5):
            return False

        skip_keywords = ("skip ad", "skip ads", "skip", "atla")
        for btn in uia_win.descendants(control_type="Button"):
            try:
                name = (btn.window_text() or "").strip().lower()
            except Exception:
                continue
            if any(k in name for k in skip_keywords):
                rect = btn.rectangle()
                if rect.width() > 0 and rect.height() > 0:
                    btn.click_input()
                    return True
    except Exception:
        pass
    return False


def _skip_ad(win) -> str:
    import pyautogui

    # 1. Zero-disruption UIA button click
    if _try_click_skip_ad_uia(win):
        return "Skipped the advertisement."

    # 2. Player keyboard focus sequence fallback (Tab to interactive button -> Enter)
    try:
        pyautogui.press("tab")
        time.sleep(0.08)
        pyautogui.press("enter")
        return "Attempted to skip the ad via player controls."
    except Exception as e:
        return f"Failed to press skip shortcut: {e}"


def run(parameters: dict, player=None, session_memory=None) -> str:
    _ensure_dpi_aware()

    raw_action = (parameters.get("action") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not raw_action:
        return "What would you like me to do with YouTube, sir?"

    action = _ACTION_ALIASES.get(raw_action, raw_action)

    try:
        import pyautogui
    except ImportError:
        return "I need the 'pyautogui' library for YouTube shortcuts. Please run: pip install pyautogui"

    # Find and focus the YouTube browser window
    win, err = _find_youtube_window()
    if not win:
        return err

    focus_err = _focus_window(win)
    if focus_err:
        return focus_err

    result_text = ""

    try:
        if action == "pause":
            pyautogui.press("k")
            result_text = "Paused the video."

        elif action == "resume":
            pyautogui.press("k")
            result_text = "Resumed playback."

        elif action == "play_pause":
            pyautogui.press("k")
            result_text = "Toggled video playback."

        elif action == "captions":
            pyautogui.press("c")
            result_text = "Toggled closed captions."

        elif action == "next":
            pyautogui.hotkey("shift", "n")
            result_text = "Skipped to the next video."

        elif action == "previous":
            pyautogui.hotkey("shift", "p")
            result_text = "Returned to the previous video."

        elif action == "forward":
            sec = parameters.get("seconds") or 10
            keys = _calculate_seeks(sec, forward=True)
            for k in keys:
                pyautogui.press(k)
                time.sleep(0.05)
            result_text = f"Fast forwarded {sec} seconds."

        elif action == "rewind":
            sec = parameters.get("seconds") or 10
            keys = _calculate_seeks(sec, forward=False)
            for k in keys:
                pyautogui.press(k)
                time.sleep(0.05)
            result_text = f"Rewound {sec} seconds."

        elif action == "skip_ad":
            result_text = _skip_ad(win)

        elif action == "mute":
            pyautogui.press("m")
            result_text = "Toggled mute."

        elif action == "volume_up":
            steps = max(1, parameters.get("steps") or 2)
            for _ in range(steps):
                pyautogui.press("up")
                time.sleep(0.04)
            result_text = "Increased the volume."

        elif action == "volume_down":
            steps = max(1, parameters.get("steps") or 2)
            for _ in range(steps):
                pyautogui.press("down")
                time.sleep(0.04)
            result_text = "Decreased the volume."

        elif action == "speed_up":
            steps = max(1, parameters.get("steps") or 1)
            for _ in range(steps):
                pyautogui.hotkey("shift", ".")
                time.sleep(0.05)
            result_text = "Increased playback speed."

        elif action == "speed_down":
            steps = max(1, parameters.get("steps") or 1)
            for _ in range(steps):
                pyautogui.hotkey("shift", ",")
                time.sleep(0.05)
            result_text = "Decreased playback speed."

        elif action == "fullscreen":
            pyautogui.press("f")
            result_text = "Toggled fullscreen."

        elif action == "theater_mode":
            pyautogui.press("t")
            result_text = "Toggled theater mode."

        elif action == "miniplayer":
            pyautogui.press("i")
            result_text = "Toggled miniplayer."

        elif action == "restart":
            pyautogui.press("0")
            result_text = "Restarted the video from the beginning."

        elif action == "seek":
            pct = parameters.get("percent", 0)
            clamped = min(90, max(0, int(pct)))
            digit = str(int(round(clamped / 10.0)))
            pyautogui.press(digit)
            result_text = f"Jumped to {int(digit) * 10}% of the video."

        else:
            return f"Sir, I don't recognize the YouTube action '{raw_action}'."

    except Exception as e:
        return f"Failed to execute YouTube action: {e}"

    if player:
        try:
            player.write_log(f"JARVIS: {result_text}")
            player.show_content("▶️ YOUTUBE", result_text)
        except Exception:
            pass

    return result_text
