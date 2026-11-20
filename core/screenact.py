"""
core/screenact.py — the hands. The twin of core/screenview.py, which is the eyes.

THE ONE RULE THIS FILE EXISTS TO ENFORCE
    A click lands on an ELEMENT, not on a coordinate a model made up.

    That sentence is the difference between a screen agent that works and one
    that occasionally buys something. A language model asked "where is the send
    button" answers with two numbers, and those two numbers are a guess made
    from a downscaled picture. When the guess is off by forty pixels it does not
    fail — it clicks whatever is forty pixels away, reports success, and there
    is no undo for a click.

    So the model's job is reduced to naming what it wants. Finding it is
    screenview's job, and it does that from the accessibility tree, which knows
    the exact rectangle. This module then acts on that rectangle. A raw
    coordinate can still be used — the vision fallback has to put one somewhere
    — but it arrives as a Point that must declare where it came from, and every
    result says which it was. Nothing here accepts an anonymous pair of numbers.

WHY PERCEPTION AND ACTUATION ARE SEPARATE FILES BUT THE SAME PAIR
    They share one contract and it is easy to break: every rectangle, every
    click, every drag in this project is in LOGICAL coordinates, the space the
    mouse is driven in, never the physical pixels a screenshot is made of.
    screenview converts on the way out. This file assumes that and converts
    nothing, which is why there is no scale factor anywhere below.

    Keeping them apart is what makes perception free of side effects: look() can
    be called in a loop, in a test, on a timer, and nothing moves.

WHAT IT REFUSES TO DO, AND WHY REFUSING IS THE FEATURE
    * A disabled control. Clicking one does nothing and the agent then believes
      it did something.
    * A control that is off screen. It needs scrolling into view first, and
      clicking where it "would be" hits whatever is actually there.
    * A control with no real area.
    * Typing into a field the operating system marks as secret. Not because a
      label said "password" — labels are in every language and can lie — but
      because the platform itself flagged it. This one is unconditional: there
      is no parameter that turns it off.
    * Anything at all, on a system that does not permit it. Wayland and an
      ungranted macOS both accept synthetic input and silently discard it, so
      here it is an honest failure instead of a click that never happened.

EVERY RESULT IS AN OBSERVATION, NOT AN INTENTION
    An Act says what was attempted, whether it went, and what could be checked.
    `ok` means the input was delivered. It does NOT mean the application did
    what you hoped — nothing at this layer can know that, and pretending
    otherwise is exactly the false success core/outcome.py was built to end.
    Whether the goal advanced is the executor's question, answered by looking
    again.

NO LANGUAGE, ANYWHERE
    Key names are ASCII identifiers from the pyautogui vocabulary plus aliases
    for what people actually say. Modifier names are mapped per platform, so
    "ctrl+s" is Command+S on a Mac without any caller knowing. Text is sent as
    CHARACTERS through each platform's Unicode input, never as key presses,
    because a key-press typist produces what a US keyboard produces and most of
    the world does not use one.
"""
from __future__ import annotations

import os
import platform as _platform
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from core.lazyimport import LazyModule, installed
from core.screenview import Element

_HAS_PYAUTOGUI = installed("pyautogui")
_HAS_PYPERCLIP = installed("pyperclip")


def _configure_pyautogui(module: Any) -> None:
    # Slamming the mouse into the top-left corner aborts whatever is running.
    # It is the only stop control that needs no software to be listening, which
    # makes it the only one that still works when everything else is wedged.
    module.FAILSAFE = True
    # Settling is this module's job and it does it deliberately, per action.
    # A blanket pause would be paid by every keystroke of a long paste.
    module.PAUSE = 0.0


pyautogui = LazyModule("pyautogui", _configure_pyautogui)
pyperclip = LazyModule("pyperclip")


# ---------------------------------------------------------------------------
# Timing.
#
# These are small on purpose. The executor waits for the SCREEN to settle after
# an action, which is a measurement; these are only the millisecond-scale
# waits that the input stack itself needs, and making them generous here would
# be paying twice.
# ---------------------------------------------------------------------------
MOVE_S = 0.16          # cursor travel, long enough for hover states to fire
HOVER_S = 0.05         # let a hover-activated control notice the cursor
AFTER_CLICK_S = 0.05
DRAG_HOLD_S = 0.12     # held still at the end of a drag before letting go
AFTER_KEY_S = 0.03
CHORD_GAP_S = 0.025    # between the keys of a chord, going down and coming up
KEY_HOLD_S = 0.05      # how long the last key is held
CLIPBOARD_S = 0.07     # the clipboard is asynchronous on every platform
ASCII_INTERVAL = 0.012


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Point:
    """A bare coordinate, and a statement of where it came from.

    `origin` is not decoration. When something clicks the wrong thing, the
    first question is whether the coordinate was read from the accessibility
    tree or guessed by a model, and a log that cannot answer that is a log that
    cannot be debugged. It also lets a caller apply a stricter policy to
    guessed points than to known ones without inspecting types.

    Accepted origins:
        "element"   the centre of a real control (built by from_element)
        "vision"    a model looked at a marked-up screenshot and chose a box
        "user"      a person pointed at it
        "relative"  computed from another element, e.g. a drag destination
    """

    x: int
    y: int
    origin: str = "vision"
    label: str = ""

    @staticmethod
    def from_element(el: Element) -> "Point":
        cx, cy = el.center
        return Point(cx, cy, origin="element", label=el.label)


Target = Union[Element, Point]


@dataclass
class Act:
    """What happened when we tried.

    Deliberately shaped like core/outcome.py's vocabulary without importing it:
    this layer reports facts, and the tool that called it turns those into the
    done / unverified / failed line the model reads. Two translations of the
    same event would be one too many.
    """

    ok: bool
    action: str
    detail: str = ""
    error: str = ""
    target: str = ""          # what we aimed at, in the screen's own language
    origin: str = ""          # where the coordinate came from
    point: tuple[int, int] = (0, 0)
    warnings: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok

    def __str__(self) -> str:
        head = self.action if self.ok else f"{self.action} FAILED"
        bits = [head]
        if self.target:
            bits.append(repr(self.target[:48]))
        if self.detail:
            bits.append(self.detail)
        if self.error:
            bits.append(f"({self.error})")
        if self.warnings:
            bits.append("warn: " + "; ".join(self.warnings))
        return " ".join(bits)


def _fail(action: str, error: str, **kw) -> Act:
    return Act(ok=False, action=action, error=error, **kw)


# ---------------------------------------------------------------------------
# Can this machine be driven at all?
#
# Asked once and cached. platform_support.detect() reads the environment and
# the OS, neither of which changes inside a session, and asking it before every
# keystroke would put a filesystem probe in the input path.
# ---------------------------------------------------------------------------
_support: Any = None
_support_lock = threading.Lock()


def _permitted() -> tuple[bool, str]:
    """(may we drive input, why not). Never raises."""
    global _support
    with _support_lock:
        if _support is None:
            try:
                from core import platform_support
                _support = platform_support.detect()
            except Exception:
                _support = False        # sentinel: could not tell, assume yes
    if _support is False or _support is None:
        return True, ""
    if getattr(_support, "can_control_input", True):
        return True, ""
    notes = "; ".join(getattr(_support, "notes", []) or [])
    return False, (notes or "this session does not permit programs to drive "
                            "the keyboard and mouse")


def available() -> tuple[bool, str]:
    """Is actuation possible here at all? ((True, "") when it is.)"""
    if not _HAS_PYAUTOGUI:
        return False, "pyautogui is not installed. Run: pip install pyautogui"
    return _permitted()


# ---------------------------------------------------------------------------
# Aiming
# ---------------------------------------------------------------------------
def _screen_size() -> tuple[int, int]:
    try:
        w, h = pyautogui.size()
        return int(w), int(h)
    except Exception:
        return 0, 0


def _on_screen(x: int, y: int) -> bool:
    """Is this point on ANY monitor?

    The primary monitor alone was the old test, so every control on a second
    screen was refused as "outside the visible screen" — a whole display the
    agent could see in a window list and never touch.
    """
    try:
        from core import screenview
        rect = screenview.virtual_rect()
        left, top = int(rect["left"]), int(rect["top"])
        width, height = int(rect["width"]), int(rect["height"])
    except Exception:
        left, top = 0, 0
        width, height = _screen_size()
    if not (width and height):
        return True               # nobody could say; the OS clamps anyway
    return left <= x < left + width and top <= y < top + height


# ---------------------------------------------------------------------------
# The assistant's own windows are never a target
#
# The agent's HUD sits on the same desktop it works on, shows the goal and a log
# of every step, and is very often the window in front when a task starts. A
# click that lands on it, or keystrokes delivered while it has the keyboard, do
# something to the assistant instead of to the task — and a vision model shown
# the HUD is shown exactly the words it is looking for. So these two checks run
# before input is delivered, on every platform, and refuse rather than warn.
# ---------------------------------------------------------------------------
SELF_POINT = ("that spot is on the assistant's own window, which you cannot click, move or "
              "minimise; reach what is under it another way (the file manager's folder, or "
              "focus/open the program you need)")
SELF_FOCUS = ("the assistant's own window has the keyboard, so keys would go to "
              "the assistant; bring the program the task is in to the front "
              "first")


def _lands_on_self(x: int, y: int) -> bool:
    try:
        from core import screenview
        pid = screenview.window_pid_at(int(x), int(y))
        return bool(pid) and screenview.is_own_pid(pid)
    except Exception:
        return False


def _self_has_keyboard() -> bool:
    try:
        from core import screenview
        pid = screenview.foreground_pid()
        return bool(pid) and screenview.is_own_pid(pid)
    except Exception:
        return False


def _resolve(target: Target, action: str, *, for_typing: bool = False
             ) -> tuple[Optional[Point], Optional[Act]]:
    """A target to a point, or a refusal that says exactly what was wrong.

    Every guard here answers a failure that has actually been seen in screen
    agents, and each one is cheap: none of them costs a model call, a
    screenshot or a round trip.
    """
    if isinstance(target, Point):
        # A bare point has already skipped every check an element allows, which
        # is precisely why its origin is recorded and surfaced.
        if not _on_screen(target.x, target.y):
            return None, _fail(action, "that point is off the screen",
                               origin=target.origin, point=(target.x, target.y))
        return target, None

    el = target
    if el.width < 2 or el.height < 2:
        return None, _fail(action, "that control has no clickable area",
                           target=el.label, origin="element")

    if not el.enabled:
        return None, _fail(
            action,
            "that control is disabled, so clicking it would do nothing",
            target=el.label, origin="element")

    if for_typing and el.protected:
        # The one refusal with no override. See the module docstring.
        return None, _fail(
            action,
            "that is a password or other secret field; the user has to type "
            "in it themselves",
            target=el.label, origin="element")

    cx, cy = el.center
    if not _on_screen(cx, cy):
        return None, _fail(
            action,
            "that control is outside the visible screen; scroll it into view "
            "first",
            target=el.label, origin="element", point=(cx, cy))

    return Point.from_element(el), None


# A control this large is a container wearing a control's role: a page body, a
# canvas, an editor surface. Its centre is the middle of the screen and clicking
# there hits whatever happens to be under it. The aim is reported as imprecise
# rather than refused, because a maximised text editor really is one big field
# and clicking it to focus is a legitimate thing to want.
OVERSIZED_FRACTION = 0.6


def _aim_warnings(target: Target) -> list[str]:
    if not isinstance(target, Element):
        return []
    width, height = _screen_size()
    if not (width and height):
        return []
    if target.area >= OVERSIZED_FRACTION * width * height:
        return ["that control covers most of the screen, so its centre is not "
                "a precise place to click"]
    return []


def _occluding_process(point: Point) -> str:
    """The process owning whatever is actually at this point, when the platform
    will say cheaply. Empty string when it will not.

    A BEST EFFORT ON PURPOSE
        A control can be perfectly real, enabled and on screen and still be
        underneath something — a notification, a dropdown, a dialog that opened
        between the look and the click. Windows will name the window at a point
        for the cost of one call, which is worth taking.

        It is reported, never enforced. The check has false positives of its
        own (layered windows, a click-through overlay), and refusing to act on
        one would trade a rare misclick for a common refusal. The executor sees
        the warning next to the outcome and decides.
    """
    try:
        from core import screenview
        pid = screenview.window_pid_at(int(point.x), int(point.y))
        if not pid:
            return ""
        import psutil
        return str(psutil.Process(int(pid)).name() or "")
    except Exception:
        return ""


# How far the cursor may end up from where it was sent before the move counts
# as having landed somewhere else. A pixel or two is rounding; more means the
# mouse and the screenshot are not in the same coordinate space.
LANDING_SLACK_PX = 3


# The error every action reports when pyautogui's corner stop fired. Callers
# test for it (a run must end, not retry) - one string, one place.
FAILSAFE = "aborted by the mouse-corner failsafe"


def _off_the_stop(x: int, y: int) -> tuple[int, int]:
    """A point one pixel inside a screen corner instead of on it. pyautogui
    stops at the exact corner pixels; a target there (a model's guess, a hot
    corner) would park the cursor on the stop and lock every later action."""
    try:
        points = {(int(a), int(b)) for a, b in getattr(pyautogui, "FAILSAFE_POINTS", [])}
    except Exception:
        points = {(0, 0)}
    if (x, y) not in points:
        return x, y
    w, h = pyautogui.size()
    return (x + 1 if x <= 0 else x - 1 if x >= w - 1 else x,
            y + 1 if y <= 0 else y - 1 if y >= h - 1 else y)


class OffTarget(RuntimeError):
    """The cursor did not arrive where it was sent."""


def _place(x: int, y: int, duration: float = MOVE_S) -> None:
    """Move the cursor to (x, y) and make sure it got there.

    WHY IT IS CHECKED
        A click is only as good as the agreement between the picture's
        coordinates and the mouse's. On a scaled display, or across monitors
        with different scaling, a process in the wrong DPI mode moves the
        cursor to the right NUMBERS and the wrong PLACE, and nothing says so:
        the click lands on a neighbour. Reading the cursor back costs
        microseconds and turns that silent misclick into a refusal the agent
        is told about. Same pixel space first (core/screenview), then check.
    """
    from core import screenview
    screenview.ensure_per_monitor_dpi()
    x, y = _off_the_stop(int(x), int(y))
    # A JUMP, not a glide (beta 2026-10-12). Gliding from "Mesh" to
    # "Cylinder" in a cascading menu crossed "Curve" and "Surface" on the way,
    # which swapped the submenu, and the click found nothing; the same closes a
    # desktop's New submenu. Jumping sends the program one move, at the target,
    # and a one-pixel nudge sends a second there so a hover is noticed.
    if duration:
        pyautogui.moveTo(x + (1 if x > 0 else -1), y, duration=0)
    pyautogui.moveTo(x, y, duration=0)
    try:
        at_x, at_y = pyautogui.position()
    except Exception:
        return                       # the platform will not say; nothing to check
    if abs(int(at_x) - int(x)) > LANDING_SLACK_PX or abs(int(at_y) - int(y)) > LANDING_SLACK_PX:
        raise OffTarget(f"the cursor was sent to {x},{y} but landed at {at_x},{at_y}; "
                        f"the screen's scaling or a locked cursor is in the way, so "
                        f"nothing was pressed")


def _move_to(point: Point) -> None:
    _place(point.x, point.y)
    if HOVER_S:
        time.sleep(HOVER_S)


# ---------------------------------------------------------------------------
# Pointer
# ---------------------------------------------------------------------------
def click(target: Target, *, button: str = "left", clicks: int = 1,
          expect_process: str = "", hold: tuple = ()) -> Act:
    """Click a control.

    `expect_process` is the executable the caller believes owns this control.
    When given, and when the platform can answer, the process actually at that
    point is compared and a mismatch is attached as a warning. It is how "a
    notification slid over the button" stops being invisible.
    """
    action = {1: "click", 2: "double_click", 3: "triple_click"}.get(clicks, "click")
    if button == "right":
        action = "right_click"

    ok, why = available()
    if not ok:
        return _fail(action, why)

    point, refusal = _resolve(target, action)
    if refusal is not None:
        return refusal
    assert point is not None

    warnings: list[str] = _aim_warnings(target)
    try:
        _move_to(point)

        if _lands_on_self(point.x, point.y):
            return _fail(action, SELF_POINT, target=point.label,
                         origin=point.origin, point=(point.x, point.y))

        if expect_process:
            actual = _occluding_process(point)
            if actual and _differs(actual, expect_process):
                warnings.append(
                    f"the window at that point belongs to {actual}, not "
                    f"{expect_process}; something may be covering it")

        held = _hold_keys(hold)
        try:
            pyautogui.click(button=button, clicks=max(1, clicks),
                            interval=0.06 if clicks > 1 else 0.0)
        finally:
            _release_keys(held)
        time.sleep(AFTER_CLICK_S)
    except Exception as e:
        if type(e).__name__ == "FailSafeException":
            return _fail(action, FAILSAFE,
                         target=point.label, origin=point.origin,
                         point=(point.x, point.y))
        return _fail(action, f"the click could not be delivered: {e}",
                     target=point.label, origin=point.origin,
                     point=(point.x, point.y))

    return Act(ok=True, action=action, target=point.label, origin=point.origin,
               point=(point.x, point.y), warnings=warnings,
               detail=f"at {point.x},{point.y}")


def double_click(target: Target, **kw) -> Act:
    return click(target, clicks=2, **kw)


def right_click(target: Target, **kw) -> Act:
    return click(target, button="right", **kw)


def minimize_window_at(x: Optional[int] = None, y: Optional[int] = None) -> bool:
    """Minimize the top-level window under a point (or the front window): what
    a person does with a window in the way. Reversible - it stays open, on the
    taskbar. Never the assistant's own window, an administrator's, or one
    without a minimize button (a dialog). True when it was minimized.
    Windows only; elsewhere False."""
    if _OS != "windows":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        from core import screenview
        user32 = ctypes.WinDLL("user32")
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.WindowFromPoint.argtypes = (wintypes.POINT,)
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetForegroundWindow.restype = wintypes.HWND
        if x is None or y is None:
            hwnd = user32.GetForegroundWindow()
        else:
            hwnd = user32.GetAncestor(user32.WindowFromPoint(wintypes.POINT(int(x), int(y))), 2)
        if not hwnd or user32.IsIconic(hwnd):
            return False
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if screenview.is_own_pid(int(pid.value)) or screenview.outranks_us(int(pid.value)):
            return False
        user32.GetWindowLongW.restype = ctypes.c_long
        if not user32.GetWindowLongW(hwnd, -16) & 0x00020000:        # WS_MINIMIZEBOX
            return False
        user32.ShowWindow(hwnd, 6)                                    # SW_MINIMIZE
        time.sleep(0.3)
        return bool(user32.IsIconic(hwnd))
    except Exception:
        return False


def step_aside() -> bool:
    """When the assistant's own window is in front (the user just spoke to
    it), bring the window the user was working in before to the front - the
    task is almost always about that one. The assistant's window is not
    touched. True when another window came forward. Windows only."""
    if _OS != "windows":
        return False
    try:
        from core import screenview
        windows = screenview.front_windows(30)
        if not windows or not screenview.is_own_pid(int(windows[0][0])):
            return False
        for pid, exe, title in windows[1:]:
            if pid and not screenview.is_own_pid(int(pid)) and title:
                return bool(focus_window(int(pid), exe, title).ok)
    except Exception:
        return False
    return False


SMALL_SHARE = 0.5     # a window under half the screen's width or height is "small"


def maximize_front_if_small() -> bool:
    """Maximize the front window when it is small and can be maximized (it has
    a maximize button) - what a person does before working in a window that
    hides its own controls. Measured more than once: a file manager opened
    small over a full-screen window, its ribbon collapsed, and the agent spent
    up to ten steps there before maximizing it (2026-10-13/14). The
    assistant's own windows and dialogs (no maximize button) are left alone.
    True when it was maximized. Windows only; elsewhere False."""
    if _OS != "windows":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        from core import screenview
        user32 = ctypes.WinDLL("user32")
        user32.GetForegroundWindow.restype = wintypes.HWND
        hwnd = user32.GetForegroundWindow()
        if not hwnd or user32.IsZoomed(hwnd) or user32.IsIconic(hwnd):
            return False
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if screenview.is_own_pid(int(pid.value)) or screenview.outranks_us(int(pid.value)):
            return False
        user32.GetWindowLongW.restype = ctypes.c_long
        style = user32.GetWindowLongW(hwnd, -16)                         # GWL_STYLE
        if not (style & 0x00010000) or not (style & 0x00040000):        # MAXIMIZEBOX, THICKFRAME
            return False
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        width, height = rect.right - rect.left, rect.bottom - rect.top
        mons = screenview.monitors()
        screens = mons[1:] or mons
        cx, cy = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
        mon = next((m for m in screens if m["left"] <= cx < m["left"] + m["width"]
                    and m["top"] <= cy < m["top"] + m["height"]), screens[0] if screens else None)
        if not mon or (width >= SMALL_SHARE * mon["width"] and height >= SMALL_SHARE * mon["height"]):
            return False
        user32.ShowWindow(hwnd, 3)                                       # SW_MAXIMIZE
        time.sleep(0.3)
        return bool(user32.IsZoomed(hwnd))
    except Exception:
        return False


def set_value(target: Target, value: float) -> Act:
    """Set a slider's (or any ranged control's) number through the platform's
    accessibility interface, and read it back.

    WHY
        A settings slider ignored clicks on its track and every drag
        (2026-10-13); the arrow keys move it one step per press, which for a
        far value is dozens of steps. The control itself offers "set to N"
        to assistive technology - the same way a screen reader user sets it -
        and it takes effect like a person's change (measured: the setting the
        slider controls changed with it). Windows (UI Automation RangeValue)
        today; elsewhere the answer says to use the keyboard.
    """
    ok, why = available()
    if not ok:
        return _fail("set_value", why)
    point, refusal = _resolve(target, "set_value")
    if refusal is not None:
        return refusal
    assert point is not None
    if _OS != "windows":
        return _fail("set_value", "not available on this system: click the control, then "
                     "use the arrow keys", target=point.label)
    try:
        from ctypes import wintypes
        from pywinauto.uia_defines import IUIA
        import comtypes.gen.UIAutomationClient as UIA
        element = IUIA().iuia.ElementFromPoint(wintypes.POINT(int(point.x), int(point.y)))
        raw = element.GetCurrentPattern(UIA.UIA_RangeValuePatternId) if element else None
        if not raw:
            return _fail("set_value", "that control has no number to set; use the keyboard "
                         "or the mouse", target=point.label, point=(point.x, point.y))
        ranged = raw.QueryInterface(UIA.IUIAutomationRangeValuePattern)
        low, high = ranged.CurrentMinimum, ranged.CurrentMaximum
        if not low <= value <= high:
            return _fail("set_value", f"{value:g} is outside this control's range "
                         f"({low:g} to {high:g})", target=point.label, point=(point.x, point.y))
        if ranged.CurrentIsReadOnly:
            return _fail("set_value", "this control is read-only", target=point.label)
        framework = str(getattr(element, "CurrentFrameworkId", "") or "").lower()
        if framework in ("win32", "winform"):
            # A classic slider takes the value through accessibility but does
            # not tell its dialog, so OK applied the old one (measured,
            # 2026-10-14). The keyboard does what a person does and the dialog
            # hears it: to the start, then one step at a time.
            step = float(ranged.CurrentSmallChange or 1) or 1.0
            presses = int(round((value - low) / step))
            if presses > 400:
                return _fail("set_value", "too many steps for this slider; use its arrow keys",
                             target=point.label)
            element.SetFocus()
            time.sleep(0.1)
            press("home")
            for _ in range(presses):
                press("right")
        else:
            ranged.SetValue(float(value))
        time.sleep(0.3)
        now = ranged.CurrentValue
        if abs(now - value) > max(1e-6, (high - low) * 0.001):
            return _fail("set_value", f"asked for {value:g}, the control now says {now:g}",
                         target=point.label, point=(point.x, point.y))
        return Act(ok=True, action="set_value", target=point.label, origin=point.origin,
                   point=(point.x, point.y), detail=f"set to {now:g} (range {low:g}-{high:g}), read back")
    except Exception as e:
        return _fail("set_value", f"the value could not be set: {e}", target=point.label)


def hover(target: Target) -> Act:
    """Move the cursor onto something without pressing anything.

    Menus that open on hover, tooltips that hold the only readable label, a
    control that only appears once the row under the cursor lights up: all of
    them need this, and none of them needs a click that might commit to
    something.
    """
    ok, why = available()
    if not ok:
        return _fail("hover", why)
    point, refusal = _resolve(target, "hover")
    if refusal is not None:
        return refusal
    assert point is not None
    try:
        _move_to(point)
    except Exception as e:
        return _fail("hover", f"the cursor could not be moved: {e}",
                     target=point.label, origin=point.origin)
    return Act(ok=True, action="hover", target=point.label, origin=point.origin,
               point=(point.x, point.y))


def drag(start: Target, end: Target, *, duration: float = 0.45) -> Act:
    ok, why = available()
    if not ok:
        return _fail("drag", why)

    a, refusal = _resolve(start, "drag")
    if refusal is not None:
        return refusal
    b, refusal = _resolve(end, "drag")
    if refusal is not None:
        return refusal
    assert a is not None and b is not None

    try:
        # The destination is checked before anything is pressed: a drag that
        # has started cannot be taken back halfway.
        if _lands_on_self(b.x, b.y):
            return _fail("drag", SELF_POINT, target=b.label, origin=b.origin)
        _place(a.x, a.y)
        if _lands_on_self(a.x, a.y):
            return _fail("drag", SELF_POINT, target=a.label, origin=a.origin)
        time.sleep(HOVER_S)
        # Pressed, carried through a few points, held still at the end, then
        # released. pyautogui.dragTo released the moment the cursor arrived,
        # before the program had handled the last move: measured in a text
        # box, a drag to the end of "beta" selected only up to "bet".
        pyautogui.mouseDown(button="left")
        try:
            steps = max(4, int(max(0.1, duration) / 0.03))
            for i in range(1, steps + 1):
                pyautogui.moveTo(a.x + (b.x - a.x) * i / steps, a.y + (b.y - a.y) * i / steps,
                                 duration=0)
                time.sleep(max(0.1, duration) / steps)
            pyautogui.moveTo(b.x, b.y, duration=0)
            time.sleep(DRAG_HOLD_S)
        finally:
            pyautogui.mouseUp(button="left")
    except Exception as e:
        if type(e).__name__ == "FailSafeException":
            return _fail("drag", FAILSAFE)
        return _fail("drag", f"the drag could not be delivered: {e}",
                     target=a.label, origin=a.origin)
    return Act(ok=True, action="drag", target=a.label, origin=a.origin,
               point=(b.x, b.y), detail=f"{a.x},{a.y} -> {b.x},{b.y}")


# What one notch of a mouse wheel is, in the unit each platform's scroll call
# counts in. pyautogui passes its number straight through, and the three
# platforms read it three different ways:
#
#     Windows   wheel delta; one notch is 120 (WHEEL_DELTA)
#     macOS     lines; a notch is about three
#     X11       button 4/5 clicks; one click is one notch
#
# The old code sent 120 everywhere, which was right on Windows and asked macOS
# for 360 lines and X11 for 360 separate wheel clicks per notch.
_NOTCH = {"windows": 120, "mac": 3, "linux": 1}


def _wheel(direction: str, notches: int) -> None:
    """Turn the wheel `notches` times, in any of the four directions."""
    d = direction.strip().lower()
    horizontal = d.startswith(("l", "r"))
    # Up and right are positive on every platform's scroll API.
    sign = 1 if d.startswith(("u", "r")) else -1
    amount = sign * notches * _NOTCH.get(_OS, 1)
    if not horizontal:
        pyautogui.scroll(amount)
        return
    if _OS == "windows":
        # pyautogui's Windows hscroll is literally its vertical scroll, so a
        # horizontal request scrolled the page down. MOUSEEVENTF_HWHEEL is the
        # real horizontal wheel.
        import ctypes
        ctypes.WinDLL("user32").mouse_event(0x01000, 0, 0, int(amount), 0)
        return
    pyautogui.hscroll(amount)


def scroll(direction: str = "down", amount: int = 3, *,
           target: Optional[Target] = None) -> Act:
    """Scroll, optionally with the cursor parked over a particular container.

    Where the cursor is matters: a page with a scrollable sidebar scrolls
    whichever of the two the pointer is over, and an agent that scrolls the
    wrong panel then reports that the item it wanted is still not visible.
    """
    ok, why = available()
    if not ok:
        return _fail("scroll", why)

    label, origin = "", ""
    if target is not None:
        point, refusal = _resolve(target, "scroll")
        if refusal is not None:
            return refusal
        assert point is not None
        label, origin = point.label, point.origin
        try:
            _place(point.x, point.y)
        except Exception as e:
            return _fail("scroll", f"the cursor could not be moved: {e}")
        if _lands_on_self(point.x, point.y):
            return _fail("scroll", SELF_POINT, target=label, origin=origin)

    clicks = max(1, min(20, int(amount or 3)))
    try:
        _wheel(str(direction or "down"), clicks)
        time.sleep(AFTER_KEY_S)
    except Exception as e:
        return _fail("scroll", f"the scroll could not be delivered: {e}")

    return Act(ok=True, action="scroll", target=label, origin=origin,
               detail=f"{direction} x{clicks}")


# ---------------------------------------------------------------------------
# Keyboard
#
# The alias table maps what people and models actually write onto what
# pyautogui accepts. Everything in it is an ASCII identifier, not a word in
# anybody's language: "enter" is the name of a key in the library's vocabulary,
# the same way "Button" is the name of a role in UIA's.
# ---------------------------------------------------------------------------
_OS = {"Windows": "windows", "Darwin": "mac"}.get(_platform.system(), "linux")

_KEY_ALIASES = {
    "return": "enter", "cr": "enter", "newline": "enter",
    "escape": "esc",
    "del": "delete", "ins": "insert",
    "pgup": "pageup", "pgdn": "pagedown", "pagedn": "pagedown",
    "control": "ctrl", "ctl": "ctrl",
    "option": "alt", "opt": "alt",
    "cmd": "command", "meta": "command", "apple": "command",
    "super": "winleft", "win": "winleft", "windows": "winleft", "start": "winleft",
    "spacebar": "space", "spc": "space",
    "caps": "capslock",
    "back": "backspace", "bksp": "backspace",
    "arrowup": "up", "arrowdown": "down",
    "arrowleft": "left", "arrowright": "right",
}


def normalise_key(name: str) -> str:
    """One key name, in pyautogui's vocabulary, correct for this platform.

    The platform swap is the point. A caller writes "ctrl+s" once and it is
    Command+S on macOS and Control+S everywhere else, so no plugin, prompt or
    plan ever has to branch on the operating system to save a file.

    That promise was only half kept: "cmd" became Control off the Mac, but
    "ctrl" stayed Control ON the Mac, so every "ctrl+s", "ctrl+a" and "ctrl+v" a
    planner wrote did nothing there. "ctrl" and "cmd" are now both the
    platform's shortcut modifier. The rare shortcut that needs the physical
    Control key on a Mac is written "macctrl".
    """
    key = str(name or "").strip().lower()
    if key == "macctrl":
        return "ctrl"
    key = _KEY_ALIASES.get(key, key)
    if key in ("ctrl", "command"):
        return "command" if _OS == "mac" else "ctrl"
    if key == "winleft" and _OS == "mac":
        key = "command"
    return key


# Keys that sit in the extended block: without the flag their scan code is the
# number pad's (an arrow would arrive as 4, 6, 8, 2 with Num Lock on).
_EXTENDED_VK = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x2D, 0x2E,
                0x5B, 0x5C, 0x5D, 0x6F, 0x90, 0xA3, 0xA5}


# Keys a click may be made with: Ctrl adds an item to a selection, Shift
# selects a range, Alt (Option) and the platform key change what a click does.
# Measured (2026-10-14): the plan was "hold Ctrl and click the other two
# files", the click could not hold anything, and each click replaced the
# selection instead.
HOLDABLE = ("ctrl", "shift", "alt", "cmd", "win")


def _hold_keys(keys) -> list:
    """Press and keep down the given modifier keys; returns what to release."""
    held = []
    for raw in keys or ():
        key = normalise_key(str(raw))
        name = {"control": "ctrl", "command": "cmd", "option": "alt", "winleft": "win"}.get(key, key)
        if name not in HOLDABLE:
            continue
        # "ctrl" is the platform's selection key: Command on macOS.
        pkey = {"ctrl": "command" if _OS == "mac" else "ctrl", "shift": "shift",
                "alt": "option" if _OS == "mac" else "alt",
                "cmd": "command" if _OS == "mac" else "winleft", "win": "winleft"}[name]
        pyautogui.keyDown(pkey)
        held.append(pkey)
    if held:
        time.sleep(0.05)
    return held


def _release_keys(held: list) -> None:
    for pkey in reversed(held or []):
        try:
            pyautogui.keyUp(pkey)
        except Exception:
            pass


def _send_keys(events: list) -> bool:
    """Windows SendInput for [(vk, scan, flags), ...]; True when all went in."""
    import ctypes
    from ctypes import wintypes

    class KI(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class MI(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class U(ctypes.Union):
        _fields_ = [("ki", KI), ("mi", MI)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", U)]

    for vk, scan, flags in events:
        one = INPUT(type=1, u=U(ki=KI(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)))
        if ctypes.windll.user32.SendInput(1, ctypes.byref(one), ctypes.sizeof(INPUT)) != 1:
            return False
        time.sleep(CHORD_GAP_S if not (flags & 2) else CHORD_GAP_S / 2)
    return True


def _scan_of(vk: int) -> int:
    import ctypes
    return int(ctypes.windll.user32.MapVirtualKeyW(vk, 0)) & 0xFF   # MAPVK_VK_TO_VSC


def _chord_windows(parts: list) -> bool:
    """A key or a chord as a keyboard sends it, on Windows. False = not handled
    here (another OS, or a key this cannot name), and pyautogui does it.

    WHY (beta 2026-10-12): Shift+A did not open Blender's Add menu; the user
    holding the same keys did. pyautogui sends each key with scan code 0 and no
    pause, so a program reading the keyboard itself (raw input: 3-D tools,
    games) cannot tell which Shift that was and sees a bare "a". Here every key
    carries its scan code, modifiers go down first with a short gap, the last
    key is held a moment, and all come up in reverse order. The key is still
    named by its virtual key, so the user's keyboard layout decides the letter.
    """
    if _OS != "windows":
        return False
    try:
        from pyautogui import _pyautogui_win as W
        vks = []
        for k in parts:
            code = W.keyboardMapping.get(k)
            if code is None:
                return False
            mods, vk = divmod(int(code), 0x100)
            if mods or not vk:
                return False            # a character that needs Shift itself: keep pyautogui's way
            vks.append(vk)
        down = [(vk, _scan_of(vk), 1 if vk in _EXTENDED_VK else 0) for vk in vks]
        up = [(vk, scan, flags | 2) for vk, scan, flags in reversed(down)]   # KEYEVENTF_KEYUP
        if not _send_keys(down):
            _send_keys(up)              # never leave a modifier held down
            return False
        time.sleep(KEY_HOLD_S)
        _send_keys(up)
        return True                     # it was pressed: never press it a second time
    except Exception:
        return False


def press(keys: str, *, presses: int = 1) -> Act:
    """A key or a chord: "enter", "esc", "ctrl+s", "alt+tab", "ctrl+shift+n"."""
    ok, why = available()
    if not ok:
        return _fail("press", why)

    parts = [normalise_key(k) for k in re.split(r"[+\s]+", str(keys or "")) if k.strip()]
    if not parts:
        return _fail("press", "no key was given")
    if _self_has_keyboard():
        return _fail("press", SELF_FOCUS, target="+".join(parts))

    presses = max(1, min(50, int(presses or 1)))
    try:
        for _ in range(presses):
            if not _chord_windows(parts):
                if len(parts) == 1:
                    pyautogui.press(parts[0])
                else:
                    pyautogui.hotkey(*parts, interval=CHORD_GAP_S)
            time.sleep(AFTER_KEY_S)
    except Exception as e:
        if type(e).__name__ == "FailSafeException":
            return _fail("press", FAILSAFE)
        return _fail("press", f"the keystroke could not be delivered: {e}")

    shown = "+".join(parts)
    return Act(ok=True, action="press", target=shown,
               detail=shown if presses == 1 else f"{shown} x{presses}")


# ---------------------------------------------------------------------------
# Typing
# ---------------------------------------------------------------------------
def _is_ascii(text: str) -> bool:
    try:
        text.encode("ascii")
        return True
    except Exception:
        return False


def type_text(text: str, *, target: Optional[Target] = None,
              clear_first: bool = False) -> Act:
    """Type into the focused field, or into `target` after clicking it.

    TEXT IS SENT AS CHARACTERS, NOT AS KEYS
        pyautogui types by pressing the keys a US keyboard would need, and asks
        the active layout which key that is — then adds Shift whenever the
        character needs Shift on a US keyboard. Measured on a Turkish Q layout:
        "@" went out as Ctrl+Alt+Shift+Q and '"' as Shift+the key left of 1,
        both wrong characters, silently. A web address with an @ in it could not
        be typed at all. The same happens on German, French, Nordic and most
        other layouts, and on every non-Latin one.

        So text never goes through key presses. Each platform has a way to
        deliver a character itself, independent of the layout:

            Windows   SendInput with KEYEVENTF_UNICODE
            macOS     a keyboard event carrying a Unicode string
            X11       xdotool type, when it is installed

        When that route is unavailable the clipboard carries it instead, and
        the clipboard the user had is put back afterwards.

    CLEARING A FIELD NEEDS A FIELD
        `clear_first` selects everything and deletes it. With a named field it
        empties that field. Without one it would select all of whatever has the
        keyboard — every file in a folder window, a whole document — and delete
        it. So without a target nothing is cleared, and the result says so.

    A field the platform marks as secret is refused before any of this, and
    that refusal has no override.
    """
    ok, why = available()
    if not ok:
        return _fail("type", why)

    text = str(text if text is not None else "")
    label, origin = "", ""
    warnings: list[str] = []

    if target is not None:
        point, refusal = _resolve(target, "type", for_typing=True)
        if refusal is not None:
            return refusal
        assert point is not None
        label, origin = point.label, point.origin
        clicked = click(target)
        if not clicked.ok:
            return _fail("type", f"the field could not be focused: {clicked.error}",
                         target=label, origin=origin)
        time.sleep(HOVER_S)
    elif _self_has_keyboard():
        return _fail("type", SELF_FOCUS)

    if clear_first:
        if target is None:
            warnings.append("nothing was cleared first, because no field was "
                            "named and select-all would have hit whatever has "
                            "the keyboard")
        else:
            cleared = press("ctrl+a")
            if cleared.ok:
                press("delete")

    try:
        how, error = _deliver_text(text)
    except Exception as e:
        if type(e).__name__ == "FailSafeException":
            return _fail("type", FAILSAFE)
        how, error = "", str(e)
    if error:
        return _fail("type", f"the text could not be typed: {error}",
                     target=label, origin=origin)

    shown = text if len(text) <= 40 else text[:40] + "..."
    return Act(ok=True, action="type", target=label, origin=origin,
               warnings=warnings,
               detail=f"typed {shown!r} ({len(text)} chars, {how})")


def _deliver_text(text: str) -> tuple[str, str]:
    """(how it was delivered, "") or ("", why it could not be)."""
    if not text:
        return "nothing to type", ""
    reasons: list[str] = []
    native = {"windows": _type_windows, "mac": _type_mac,
              "linux": _type_linux}.get(_OS)
    if native is not None:
        try:
            problem = native(text)
        except Exception as e:
            problem = str(e)
        if problem is None:
            return "unicode input", ""
        reasons.append(problem)

    problem = _paste(text)
    if problem is None:
        return "clipboard", ""
    reasons.append(problem)

    # The last resort, and only for plain ASCII: key presses, which are right
    # on a US layout and may alter symbols on others. Said in the result.
    if _is_ascii(text):
        pyautogui.write(text, interval=ASCII_INTERVAL)
        return "key presses (symbols may differ on non-US keyboard layouts)", ""
    return "", "; ".join(reasons)


def _layout_keys():
    """ch -> (virtual key, scan code, needs Shift) on the keyboard layout of the
    window in front, or None when the character is better sent as Unicode:
    not on this layout, needs AltGr/Ctrl/Alt (those combinations are shortcuts
    in many programs), is a dead key (it would wait for the next character),
    or is a letter while Caps Lock is on (it would come out in the other case).
    Asking the layout keeps every language right - the old failure was
    assuming a US keyboard."""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32")
        user32.VkKeyScanExW.argtypes = (wintypes.WCHAR, ctypes.c_void_p)
        user32.VkKeyScanExW.restype = ctypes.c_short
        user32.MapVirtualKeyExW.argtypes = (wintypes.UINT, wintypes.UINT, ctypes.c_void_p)
        user32.MapVirtualKeyExW.restype = wintypes.UINT
        user32.GetKeyboardLayout.restype = ctypes.c_void_p
        thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        hkl = user32.GetKeyboardLayout(thread)
        caps = bool(user32.GetKeyState(0x14) & 1)
    except Exception:
        return lambda ch: None

    def as_key(ch: str):
        if len(ch) != 1 or ord(ch) > 0xFFFF or (caps and ch.isalpha()):
            return None
        code = user32.VkKeyScanExW(ch, hkl)
        if code == -1:
            return None
        vk, mods = code & 0xFF, (code >> 8) & 0xFF
        if mods & ~1:                     # anything but Shift: AltGr, Ctrl, Alt
            return None
        if user32.MapVirtualKeyExW(vk, 2, hkl) & 0x80000000:      # a dead key
            return None
        scan = user32.MapVirtualKeyExW(vk, 0, hkl) & 0xFF
        return vk, scan, bool(mods & 1)
    return as_key


def _type_windows(text: str) -> Optional[str]:
    """SendInput with KEYEVENTF_UNICODE. None on success, else why not.

    Every character goes as its UTF-16 code units, so a character outside the
    Basic Multilingual Plane (most emoji) travels as its surrogate pair, which
    is exactly what the receiving window expects. Line breaks and tabs are sent
    as the real Enter and Tab keys: a Unicode "\\n" is not a keystroke to most
    applications.
    """
    import ctypes
    from ctypes import wintypes

    ulong_ptr = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ulong_ptr)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr)]

    class _UNION(ctypes.Union):
        # MOUSEINPUT is the largest member, and the union has to be that size
        # or SendInput rejects every record with ERROR_INVALID_PARAMETER.
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]

    INPUT_KEYBOARD, KEYUP, UNICODE = 1, 0x0002, 0x0004
    VK_RETURN, VK_TAB, VK_SHIFT, SCAN_SHIFT = 0x0D, 0x09, 0x10, 0x2A

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT

    records: list[INPUT] = []
    as_key = _layout_keys()

    def key(vk: int = 0, scan: int = 0, flags: int = 0) -> None:
        record = INPUT(type=INPUT_KEYBOARD)
        record.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0,
                               dwExtraInfo=0)
        records.append(record)

    for ch in text.replace("\r\n", "\n").replace("\r", "\n"):
        if ch == "\n" or ch == "\t":
            vk = VK_RETURN if ch == "\n" else VK_TAB
            key(vk=vk)
            key(vk=vk, flags=KEYUP)
            continue
        found = as_key(ch)
        if found is not None:
            # The key this layout has for the character, as a person presses
            # it: some programs (Blender, games, some Java ones) ignore
            # Unicode-only input - a "2" typed into a Blender field never
            # arrived (2026-10-13).
            vk, scan, shift = found
            if shift:
                key(vk=VK_SHIFT, scan=SCAN_SHIFT)
            key(vk=vk, scan=scan)
            key(vk=vk, scan=scan, flags=KEYUP)
            if shift:
                key(vk=VK_SHIFT, scan=SCAN_SHIFT, flags=KEYUP)
            continue
        data = ch.encode("utf-16-le")
        for i in range(0, len(data), 2):
            unit = int.from_bytes(data[i:i + 2], "little")
            key(scan=unit, flags=UNICODE)
            key(scan=unit, flags=UNICODE | KEYUP)

    # In batches, with a breath between them: an application reading its
    # message queue slowly drops characters from one enormous burst.
    batch = 64
    for start in range(0, len(records), batch):
        chunk = records[start:start + batch]
        array = (INPUT * len(chunk))(*chunk)
        sent = user32.SendInput(len(chunk), array, ctypes.sizeof(INPUT))
        if sent != len(chunk):
            return (f"Windows accepted {sent} of {len(chunk)} input records "
                    f"(error {ctypes.get_last_error()})")
        time.sleep(0.01)
    return None


def _type_mac(text: str) -> Optional[str]:
    """Quartz keyboard events carrying Unicode strings. None on success."""
    import Quartz

    RETURN, TAB = 36, 48          # virtual key codes; positions, not letters

    def post_string(chunk: str) -> None:
        units = len(chunk.encode("utf-16-le")) // 2
        for down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
            Quartz.CGEventKeyboardSetUnicodeString(event, units, chunk)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        time.sleep(0.008)

    def post_key(code: int) -> None:
        for down in (True, False):
            Quartz.CGEventPost(Quartz.kCGHIDEventTap,
                               Quartz.CGEventCreateKeyboardEvent(None, code, down))
        time.sleep(0.008)

    # macOS reads at most 20 UTF-16 units from one event, so text goes in short
    # runs that never split a character.
    run = ""
    for ch in text.replace("\r\n", "\n").replace("\r", "\n"):
        if ch in "\n\t":
            if run:
                post_string(run)
                run = ""
            post_key(RETURN if ch == "\n" else TAB)
            continue
        if len((run + ch).encode("utf-16-le")) // 2 > 16:
            post_string(run)
            run = ""
        run += ch
    if run:
        post_string(run)
    return None


def _type_linux(text: str) -> Optional[str]:
    """xdotool on X11. None on success, else why not."""
    import shutil
    import subprocess

    if os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
        return "a Wayland session does not accept typed input from programs"
    tool = shutil.which("xdotool")
    if not tool:
        return "xdotool is not installed"
    result = subprocess.run(
        [tool, "type", "--clearmodifiers", "--delay", "8", "--", text],
        capture_output=True, timeout=max(10.0, len(text) * 0.05))
    if result.returncode != 0:
        return (result.stderr or b"").decode("utf-8", "replace").strip() \
            or f"xdotool exited with {result.returncode}"
    return None


def field_text() -> Optional[str]:
    """What the focused text field holds, read back through the clipboard
    (select all, copy), the caret then put at the end; None when nothing could
    be read — a canvas program (a 3-D editor, a game) has no text to copy.

    WHY
        "I typed it" was the keyboard's word, not the field's: in a beta log the
        agent typed into a search box, the text never arrived, and it went on as
        if it had. Select-all-and-copy works in every program and every
        language, needs no picture of the screen and no model, so a weak
        vision model cannot get it wrong. The user's clipboard is put back.
    """
    if not _HAS_PYPERCLIP:
        return None
    previous = None
    try:
        previous = pyperclip.paste()
    except Exception:
        previous = None
    marker = "⁣" + str(time.monotonic_ns())       # invisible: tells "nothing was copied"
    try:
        pyperclip.copy(marker)
        time.sleep(CLIPBOARD_S)
        pyautogui.hotkey(normalise_key("ctrl"), "a")
        time.sleep(CLIPBOARD_S)
        pyautogui.hotkey(normalise_key("ctrl"), "c")
        time.sleep(CLIPBOARD_S * 2)
        got = pyperclip.paste()
        # Collapse the selection so the next keystroke does not replace it.
        pyautogui.hotkey(normalise_key("ctrl"), "end")
    except Exception:
        got = marker
    finally:
        if previous:
            try:
                pyperclip.copy(previous)
            except Exception:
                pass
    return None if got == marker else str(got)


def text_landed(wanted: str, field: str) -> bool:
    """Is what was typed in the field? Spaces and case aside, and as a part of
    it: a search box or an address bar may hold more than was typed."""
    def norm(s: str) -> str:
        return " ".join(str(s).split()).casefold()
    return bool(norm(wanted)) and norm(wanted) in norm(field)


def _paste(text: str) -> Optional[str]:
    """Put `text` in the field through the clipboard. None on success, else why not."""
    if not _HAS_PYPERCLIP:
        return "pyperclip is not installed. Run: pip install pyperclip"
    # A real import statement, so the build compiles the library in: it
    # bundles only what an import names (tools/build/build_msvc.ps1), and the
    # LazyModule above names none. The plugin whose import had carried it
    # (upload_video) was deleted in 0.4.2, and the built program's self-test
    # found pyperclip missing — long text typed on screen goes through here.
    import pyperclip as _clipboard  # noqa: F401

    previous = None
    try:
        previous = pyperclip.paste()
    except Exception:
        previous = None

    try:
        pyperclip.copy(text)
    except Exception as e:
        return f"the clipboard could not be written: {e}"

    time.sleep(CLIPBOARD_S)
    try:
        pyautogui.hotkey(normalise_key("ctrl"), "v")
        time.sleep(CLIPBOARD_S * 2)
    finally:
        # Restored in a finally block on purpose: a failed paste is no reason
        # to also keep somebody else's text on the user's clipboard. An empty
        # reading is not restored, because text is all pyperclip can read: an
        # image on the clipboard reads as "" and writing "" back would erase it.
        if previous:
            try:
                pyperclip.copy(previous)
            except Exception:
                pass
    return None


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------
def _differs(a: str, b: str) -> bool:
    from core.appidentity import fold
    sa = fold(str(a).rsplit(".", 1)[0])
    sb = fold(str(b).rsplit(".", 1)[0])
    if not sa or not sb:
        return False
    return sa not in sb and sb not in sa


def _wanted(name: str, process: str, title: str) -> bool:
    """Is this window the one somebody asked for, by process name or by title?"""
    from core.appidentity import fold
    want = fold(str(name).rsplit(".", 1)[0])
    if not want:
        return False
    stem = fold(str(process).rsplit(".", 1)[0])
    if stem and (want in stem or stem in want):
        return True
    return want in fold(title)


def _came_forward(name: str) -> bool:
    """Is a window answering to this name now the front one?

    Asked of the window stack rather than of the call that raised it. The call
    is not evidence: on Windows SetForegroundWindow returns success for a
    window the desktop is not showing, which is how "focus" reported done six
    times in a row while nothing moved.
    """
    try:
        from core import screenview
        for pid, process, title in screenview.front_windows(1):
            return _wanted(name, process, title)
    except Exception:
        pass
    return False


def _name_words(text: str) -> list[str]:
    """A name as whole words, each folded (core/appidentity.fold)."""
    from core.appidentity import fold
    return [w for w in (fold(part) for part in re.split(r"[\W_]+", str(text or ""))) if w]


def _holds_words(words: list[str], wanted: list[str]) -> bool:
    """Do `words` contain `wanted` as consecutive whole words, or as one word
    written together ("OperaGX" for "Opera GX")? Never part of a word:
    "opera" is not in "Operating"."""
    if not wanted:
        return False
    n, joined = len(wanted), "".join(wanted)
    for i in range(len(words)):
        if words[i:i + n] == wanted:
            return True
        for j in range(i + 1, len(words) + 1):
            run = "".join(words[i:j])
            if run == joined:
                return True
            if len(run) >= len(joined):
                break
    return False


def _taskbar_score(name: str, window: dict) -> int:
    """How surely a taskbar window is the program somebody named: 3 its own
    name for itself or its executable IS that name, 2 one holds the other as
    whole words ("Opera GX" in "Opera GX Internet Browser"; "opera" the file of
    "Opera GX"), 1 only the title holds it (a page or a document of that name),
    0 not it. Words are folded (case, accents and spacing do not matter) and
    never matched inside another word."""
    raw = str(name or "").strip()
    if raw.lower().endswith(".exe"):
        raw = raw[:-4]
    want = _name_words(raw)
    if not want or len("".join(want)) < 2:
        return 0
    stem = _name_words(str(window.get("process") or "").rsplit(".", 1)[0])
    products = [_name_words(p) for p in str(window.get("product") or "").split(" / ")]
    products = [p for p in products if p]
    joined = "".join(want)
    if joined == "".join(stem) or any(joined == "".join(p) for p in products):
        return 3
    for own in [stem] + products:
        if _holds_words(own, want):
            return 2
        if own and len("".join(own)) >= 3 and _holds_words(want, own):
            return 2
    title = _name_words(window.get("title") or "")
    if title == want:
        # The window is called exactly that: how a store app's frame names its
        # program ("Settings", "Calculator"), where the process is only the
        # frame host. A page whose title merely CONTAINS the words ("system
        # settings - Search") must not outrank it (2026-10-13).
        return 2
    return 1 if _holds_words(title, want) else 0


def _from_taskbar(name: str) -> Optional[Act]:
    """Bring a program forward the way a person does: find its window among the
    ones the taskbar shows (minimised too), by the program's own name, its
    executable or its title, and raise THAT window. Checked afterwards: done
    only when that very window is in front. None when no taskbar window answers
    to the name (the caller then tries by process), or off Windows."""
    from core import appidentity, screenview
    windows = screenview.taskbar_windows()
    scored = [(_taskbar_score(name, w), i, w) for i, w in enumerate(windows)]
    scored = [s for s in scored if s[0] > 0]
    if not scored:
        return None
    best = max(s[0] for s in scored)
    # The front-most of the best matches: the one the user used last.
    for _, _, window in sorted((s for s in scored if s[0] == best), key=lambda s: s[1])[:3]:
        appidentity.raise_hwnd(window["hwnd"])
        # The answer to the request is not evidence either way: measured with
        # an administrator window in front, Windows said "refused" and brought
        # the window forward half a second later. The window stack decides.
        if _until(lambda: _front_hwnd() == window["hwnd"] and not _iconic(window["hwnd"])):
            if _stays_cloaked(window["hwnd"]):
                # In front by every API and still not drawn: a suspended
                # store app's frame (measured: "focused", and not one pixel
                # of the screen changed). Starting it wakes it.
                return _fail("focus_app", "its window is kept hidden by the system "
                             "(a suspended app); start it instead", target=name)
            return Act(ok=True, action="focus_app", target=name,
                       detail="its window on the taskbar was brought to the front and checked")
    return None


FORWARD_WAIT_S = 1.2


def _until(condition, wait_s: float = FORWARD_WAIT_S) -> bool:
    """Poll `condition` until it holds or `wait_s` passes."""
    deadline = time.monotonic() + wait_s
    while True:
        try:
            if condition():
                return True
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def _iconic(hwnd: int) -> bool:
    """Still minimised - "the foreground window" and not on the screen."""
    try:
        import ctypes
        return bool(ctypes.WinDLL("user32").IsIconic(hwnd))
    except Exception:
        return False


CLOAK_WAIT_S = 1.5


def _stays_cloaked(hwnd: int) -> bool:
    """True when Windows keeps this window cloaked (not drawn) for longer than
    a waking app needs. False when it is shown, or when it cannot be asked."""
    try:
        import ctypes
        from ctypes import wintypes
        dwm = ctypes.WinDLL("dwmapi")
        deadline = time.monotonic() + CLOAK_WAIT_S
        while True:
            cloaked = wintypes.DWORD(0)
            if dwm.DwmGetWindowAttribute(wintypes.HWND(hwnd), 14, ctypes.byref(cloaked),
                                         ctypes.sizeof(cloaked)) != 0:
                return False
            if not cloaked.value:
                return False
            if time.monotonic() >= deadline:
                return True
            time.sleep(0.15)
    except Exception:
        return False


def _front_hwnd() -> int:
    """The top-level window in front now (Windows), 0 when unknown."""
    try:
        import ctypes
        user32 = ctypes.WinDLL("user32")
        hwnd = user32.GetForegroundWindow()
        root = user32.GetAncestor(hwnd, 2) if hwnd else 0          # GA_ROOT
        return int(root or hwnd or 0)
    except Exception:
        return 0


def focus_app(name: str) -> Act:
    """Bring an application's window to the front, by name, on any platform.

    WHICH application is delegated to core/appidentity.py: that module already
    matches a spoken name against what is actually running, already knows the
    Windows foreground-lock dance, and already refuses to touch the assistant's
    own processes.

    WHETHER IT CAME FORWARD IS CHECKED HERE, AND IT HAS TO BE
        A window can be present, visible by every flag, and still not on the
        desktop: a store app that the system suspended, a window on another
        virtual desktop, a window owned by a frame host that holds several
        applications. Measured: both of a suspended calculator's windows
        reported themselves visible, the raise returned success, and the
        screen did not change — so the agent was told "done" six times and
        went round in a circle.

        So the window stack is read back afterwards. If the front window is
        not this application, that is a failure with the one piece of advice
        that works: start it instead, which wakes a suspended application.
    """
    ok, why = available()
    if not ok:
        return _fail("focus_app", why)
    try:
        from core import appidentity, screenview
        # The taskbar first: what a person looks at to switch programs, and it
        # names a program by its own name ("Opera GX"), not by its file
        # (opera.exe) - matching by process alone missed minimised windows and
        # programs whose file is named differently (beta 2026-10-12).
        if _OS == "windows":
            raised = _from_taskbar(name)
            if raised is not None:
                return raised
        matches = appidentity.running(name)
        if not matches:
            return _fail("focus_app", f"nothing called {name!r} appears to be running",
                         target=name)
        on_screen = [w for w in screenview.front_windows(30)
                     if _wanted(name, w[1], w[2])]
        if not on_screen:
            return _fail(
                "focus_app",
                f"{name!r} is running but has no window on this desktop - it is "
                f"suspended, minimised to the tray, or on another desktop. "
                f"Start it instead of focusing it", target=name)
        raised = False
        # The processes that own the windows just seen come first. A browser,
        # an Electron app or a game launcher runs as many processes (one
        # browser here: 24), most with no window at all, and their order is
        # arbitrary — trying "the first three" could miss the one with the
        # window, and the program stayed behind (seen in a beta log).
        shown = {w[0] for w in on_screen}
        ordered = ([m for m in matches if m.pid in shown]
                   + [m for m in matches if m.pid not in shown])
        for match in ordered[:4]:
            raised = bool(appidentity.focus(match)) or raised
            if _until(lambda: _came_forward(name)):
                return Act(ok=True, action="focus_app", target=name,
                           detail="window raised and checked")
        if raised:
            return _fail("focus_app",
                         "the window was asked to come forward and did not; it "
                         "may be suspended or on another desktop. Start it "
                         "instead of focusing it", target=name)
        return _fail("focus_app",
                     "the window could not be raised; the system refused the "
                     "request", target=name)
    except Exception as e:
        return _fail("focus_app", f"the window could not be raised: {e}", target=name)


def focus_window(pid: int, name: str = "", title: str = "") -> Act:
    """Bring one particular process's window to the front.

    focus_app() answers a NAME a person said; this answers a pid the screen
    reported — "the window that was in front before the assistant" — so there
    is no name to match and nothing to guess. Refuses the assistant's own.
    """
    ok, why = available()
    if not ok:
        return _fail("focus_window", why)
    try:
        from core import appidentity, screenview
        if not pid or screenview.is_own_pid(int(pid)):
            return _fail("focus_window", "that window belongs to the assistant",
                         target=name or title)
        match = appidentity.Match(pid=int(pid), name=name or "", score=0,
                                  is_self=False, titles=[title] if title else [])
        if appidentity.focus(match):
            # Checked, not assumed — see focus_app.
            time.sleep(0.35)
            if _came_forward(name or title):
                return Act(ok=True, action="focus_window", target=name or title,
                           detail="window raised and checked")
            return _fail("focus_window",
                         "the window was asked to come forward and did not",
                         target=name or title)
        return _fail("focus_window", "the system refused to raise the window",
                     target=name or title)
    except Exception as e:
        return _fail("focus_window", f"the window could not be raised: {e}",
                     target=name or title)


# ---------------------------------------------------------------------------
# Self test — `python -m core.screenact`
#
# It moves the mouse and it never presses anything. That is the only honest way
# to check aim: a probe that clicks to prove it can click has already done the
# thing you were trying to find out whether it should do.
# ---------------------------------------------------------------------------
def _self_test() -> None:
    from core import screenview

    ok, why = available()
    print(f"actuation available: {ok}" + (f" ({why})" if why else ""))
    print(f"platform key mapping: ctrl+s -> "
          f"{'+'.join(normalise_key(k) for k in ('ctrl', 's'))}")
    print(f"                      cmd+q  -> "
          f"{'+'.join(normalise_key(k) for k in ('cmd', 'q'))}")
    if not ok:
        return

    view = screenview.look()
    print(f"\n{view.summary()}")

    buttons = [el for el in view.elements if el.interactable]
    if not buttons:
        print("nothing interactable to aim at; open a window with buttons in it")
        return

    # A named control of ordinary size. Picking the LARGEST was the first
    # version and it picked a full-window content surface every time, which is
    # exactly the target this module warns about rather than a fair test of aim.
    named = [el for el in buttons if el.label and el.area < 120_000] or buttons
    biggest = max(named, key=lambda el: el.area)
    print(f"\naiming at [{biggest.eid}] {biggest.role} {biggest.label[:50]!r} "
          f"rect={biggest.rect}")
    result = hover(biggest)
    print(f"  {result}")

    print("\nguards (nothing is clicked):")
    disabled = Element(999, screenview.ROLE_BUTTON, "pretend", "", 10, 10, 40, 20,
                       enabled=False)
    print(f"  disabled control -> {click(disabled)}")
    secret = Element(998, screenview.ROLE_EDIT, "pretend", "", 10, 10, 40, 20,
                     protected=True)
    print(f"  secret field     -> {type_text('hunter2', target=secret)}")
    offscreen = Element(997, screenview.ROLE_BUTTON, "pretend", "",
                        -5000, -5000, 40, 20)
    print(f"  off screen       -> {click(offscreen)}")

    # Resolved but NOT clicked. A self test that proves it can click by
    # clicking has already done the thing you were checking whether it should
    # do, and at these coordinates that would be a click on the user's desktop.
    guessed = Point(120, 120, origin="vision", label="a box a model chose")
    resolved, refusal = _resolve(guessed, "click")
    verdict = refusal.error if refusal else f"allowed, origin={resolved.origin}"
    print(f"  guessed point    -> {verdict}   [resolved only, not clicked]")


if __name__ == "__main__":
    _self_test()
