"""
core/openpages.py - the web address open on the screen.

"Summarize this video on the screen" had no way to reach the video: the model
answered from memory about the previous video, or described the screenshot
(2026-10-21). The address is copied from the address bar of the front-most
window of a program Windows registers as a web browser - the address bar's
own shortcuts, which every browser has (Ctrl+L, Ctrl+C) - and the user's
clipboard and front window are put back.

Reading it through UI Automation instead was tried and REMOVED: walking a
Chromium browser's accessibility tree from inside this process crashed the
whole assistant (access violation inside the browser's accessibility proxy,
2026-10-21), on any thread, in any COM apartment - a crash no try/except can
catch - and the browser switches that tree off when no assistive program is
in use anyway. Windows only; elsewhere the answer is [].
"""
from __future__ import annotations

import ctypes
import re
import sys
import time

_ADDRESS = re.compile(r"^(?:https?://)?(?:[\w-]+\.)+[a-z]{2,}(?::\d+)?(?:[/?#]\S*)?$", re.IGNORECASE)

def as_address(text: str) -> str:
    """A web address from what an address field shows ("" if it is not one)."""
    text = str(text or "").strip()
    if not text or " " in text or len(text) > 2000 or not _ADDRESS.match(text):
        return ""
    return text if re.match(r"^https?://", text, re.IGNORECASE) else "https://" + text


def _visible_windows() -> list[int]:
    """Top-level windows that are visible and not minimised, front to back."""
    user32 = ctypes.windll.user32
    out: list[int] = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            out.append(int(hwnd))
        return True
    user32.EnumWindows(proto(each), 0)          # EnumWindows walks in z-order, front first
    return out


def open_addresses(limit: int = 1, budget_s: float = 2.5, may_copy: bool = False) -> list[str]:
    """[the web address open in the front-most browser window], or [].
    `may_copy` must be True: the address is copied with the browser's own
    keys, so only when the user asked about something on the screen - never
    in the background. `limit` and `budget_s` are kept for callers."""
    if sys.platform != "win32" or not may_copy:
        return []
    found = copy_front_address()
    return [found] if found else []


def browser_programs() -> set:
    """The executables Windows lists as installed web browsers, lower-cased."""
    import winreg
    out = set()
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for sub in (r"Software\Clients\StartMenuInternet", r"Software\WOW6432Node\Clients\StartMenuInternet"):
            try:
                key = winreg.OpenKey(root, sub)
            except OSError:
                continue
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                try:
                    cmd = winreg.QueryValue(key, name + r"\shell\open\command")
                except OSError:
                    continue
                m = re.match(r'\s*"([^"]+)"|\s*(.+?\.exe)', cmd or "", re.IGNORECASE)
                exe = (m.group(1) or m.group(2)) if m else ""
                if exe:
                    out.add(exe.strip().lower())
    return out


def _program_of(hwnd: int) -> str:
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)       # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(len(buf))
        ok = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        return buf.value.lower() if ok else ""
    finally:
        kernel32.CloseHandle(handle)


def _open_clipboard(cb) -> None:
    """Open the clipboard, waiting a moment if another program has it open:
    Windows refuses it while anyone else holds it (a clipboard manager, the
    program that just copied), and the copy failed for that alone."""
    for _ in range(20):
        try:
            cb.OpenClipboard()
            return
        except Exception:
            time.sleep(0.05)
    cb.OpenClipboard()


def _clipboard_save():
    import win32clipboard as cb
    saved = []
    _open_clipboard(cb)
    try:
        fmt = cb.EnumClipboardFormats(0)
        while fmt:
            try:
                saved.append((fmt, cb.GetClipboardData(fmt)))
            except Exception:
                pass                                  # a handle-only format: cannot be kept
            fmt = cb.EnumClipboardFormats(fmt)
    finally:
        cb.CloseClipboard()
    return saved


def _clipboard_text() -> str:
    import win32clipboard as cb
    _open_clipboard(cb)
    try:
        return cb.GetClipboardData(cb.CF_UNICODETEXT) if cb.IsClipboardFormatAvailable(cb.CF_UNICODETEXT) else ""
    finally:
        cb.CloseClipboard()


def _clipboard_restore(saved) -> None:
    import win32clipboard as cb
    _open_clipboard(cb)
    try:
        cb.EmptyClipboard()
        for fmt, data in saved:
            try:
                cb.SetClipboardData(fmt, data)
            except Exception:
                pass
    finally:
        cb.CloseClipboard()


def copy_front_address() -> str:
    """The address in the front-most browser window, copied from its address
    bar. "" when there is no browser window, it could not be brought to the
    front, or what was copied is not a web address."""
    if sys.platform != "win32":
        return ""
    user32 = ctypes.windll.user32
    try:
        browsers = browser_programs()
        target = next((h for h in _visible_windows() if _program_of(h) in browsers), None)
    except Exception:
        return ""
    if not target:
        return ""
    before = user32.GetForegroundWindow()
    try:
        saved = _clipboard_save()
    except Exception:
        return ""                                     # the clipboard is busy: leave it alone
    from pywinauto import keyboard
    copied = ""
    try:
        user32.SetForegroundWindow(target)
        for _ in range(10):
            if user32.GetForegroundWindow() == target:
                break
            time.sleep(0.05)
        if user32.GetForegroundWindow() != target:
            return ""                                 # never type into a window we did not mean
        keyboard.send_keys("^l", pause=0.05)
        time.sleep(0.15)
        keyboard.send_keys("^c", pause=0.05)
        for _ in range(10):
            time.sleep(0.05)
            copied = _clipboard_text()
            if as_address(copied):
                break
        if user32.GetForegroundWindow() == target:
            keyboard.send_keys("{ESC}", pause=0.05)
    except Exception:
        copied = ""
    finally:
        try:
            _clipboard_restore(saved)
        except Exception:
            pass
        if before and before != target:
            user32.SetForegroundWindow(before)
    return as_address(copied)
