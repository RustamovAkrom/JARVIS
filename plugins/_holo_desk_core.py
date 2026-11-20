"""
_holo_desk_core.py — the Holo Desk: the camera picture fills the screen and
glass panels float in it, moved by hand (holo_desk.py is the plugin).

HANDS (_hands.py, the same shared camera as gesture control)
    The cursor sits on the hand in the picture, where its pinch lands (Hand.aim:
    from the bones that stay still while the fingers move, so it neither jumps
    when you grab nor pushes when you let go) - what you see is your own
    fingers taking hold.
      pinch a panel's title bar   take it; move; open the fingers to let go
      pinch inside a list         scroll it (anywhere, when it has nothing to scroll)
      let go while moving fast    it flies on and slows down; thrown off the
                                  screen it closes, let go slowly at an edge it
                                  comes back in
      pull it towards the camera  it grows; push it away, it shrinks (depth)
      both hands on one panel     pull apart / together: bigger / smaller
      a quick pinch (a tap)       presses what is under it: a headline opens, a
                                  note opens, a video pauses, ✕ closes
      pinch a dock button         tap: a panel flies out; pull: it comes out in
                                  your hand
      point at a panel            it is "this": "close this", "read this",
                                  "make this bigger" (holo_desk.py)
      turn the hand while holding the 3D model: it turns with it
    The mouse does all of it too (drag, right-drag turns, wheel = size).

FORGIVING TARGETS
    A hand is not a mouse: its point wanders a little, and a pinch moves it.
    So every target is bigger than it looks (panels by 18 px, ✕ and the dock
    by more), the cursor is drawn towards a button it is close to, the thing
    under the hand stays the same until the hand clearly leaves it (no
    flicker between overlapping panels), and a grab needs the pinch in two
    frames running (a blink of the tracker is not a grab).

SMOOTH
    The window repaints at the screen's rate (a 16 ms timer); the hands come in
    at the camera's rate and are carried forward between frames by their
    velocity. Panels never jump: they are pulled to where they should be
    (exponential approach), thrown panels keep their speed and lose it to
    friction, and a panel let go near another lines up with it. The camera
    picture becomes an image once per camera frame, never per repaint.

PANELS
    A panel kind is a factory registered with register_panel(); the built-in
    ones read real data (clock, system, weather, news, notes, routines, a 3D
    model, a picture, a video, any text). Another plugin can add its own kind
    the same way. Data from the network or the disk is fetched off the window's
    thread. Each panel can say what it shows in words (describe) - that is how
    "read this" works. The layout is kept, and whole desks can be saved by name.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from core.qt.QtCore import QObject, QPointF, QRectF, Qt, QTimer, QUrl, Signal, Slot
from core.qt.QtGui import (QBrush, QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPixmap, QRadialGradient)
from core.qt.QtWidgets import QApplication, QWidget

try:
    from plugins import _hands as H
except ImportError:             # loaded outside the package
    import _hands as H  # type: ignore

CYAN = QColor(0, 212, 255)
CYAN_DIM = QColor(0, 122, 153)
TEXT = QColor(200, 248, 255)
TEXT_DIM = QColor(120, 190, 210)
GREEN = QColor(0, 255, 136)
ORANGE = QColor(255, 140, 0)
AMBER = QColor(255, 204, 0)
RED = QColor(255, 51, 85)

FOLLOW = 24.0           # how fast a held panel reaches the hand (1/s)
FRICTION = 3.0          # how fast a thrown panel slows (1/s)
# Speeds are screen widths per second: the same hand movement on any screen.
# A throw: the PEAK speed of the last THROW_WINDOW before the fingers opened
# is over THROW_SPEED, and the hand still carries FOLLOW_THROUGH of it as it
# lets go (a throw goes through the release; a drag that stopped first is a
# put-down). Grips shorter than GRIP_MIN never throw (a blur "pinch").
THROW_SPEED = 0.5
THROW_WINDOW = 0.22     # s
FOLLOW_THROUGH = 0.5
GRIP_MIN = 0.12         # s
STRETCH_QUIET = 0.7     # s after a two-hand resize a release never throws (the hands end fast)
FLICK_SPEED = 2.2       # while still holding, over FLICK_FRAMES camera frames in one direction
FLICK_FRAMES = 3
# A pinch born on a hand moving faster than GHOST_SPEED is motion blur, not a
# pinch: it grabs nothing until the hand slows below GHOST_HEAL.
GHOST_SPEED = 0.5
GHOST_HEAL = 0.3
# The clap ("catch a fly"), fitted to a real recording (2026-10-28, 12
# frames a second): hands that close fast blur - they read as "pinch" or
# "fist" on the way, not "open" - and at the touch the camera mostly loses
# one or both of them (6.1 -> 3.1 hand sizes in 0.08 s, then gone). So the
# clap is what can be measured: within CLAP_TIME the hands went from
# CLAP_APART hand sizes apart, not pinching and holding nothing, to either
#   touching: wrists or knuckles within CLAP_TOUCH, or
#   merging: lost from view right after a frame CLAP_MERGE close or nearer
#            that was closing faster than CLAP_CLOSING hand sizes a second,
# closing on average faster than CLAP_CLOSING from that start, and with BOTH
# hands having moved CLAP_EACH towards each other (one hand swept onto a still
# one is no clap). Measured: claps close at 19-36 hand sizes a second, hands
# brought together at ~3.
CLAP_APART = 3.0
CLAP_TIME = 0.35        # s
CLAP_TOUCH = 1.5
CLAP_MERGE = 4.5
CLAP_CLOSING = 10.0     # hand sizes a second
CLAP_EACH = 0.4
OUT_SPEED = 0.33        # a panel leaving the screen faster than this is thrown away
GLIDE = 0.25            # how much of the hand's speed a put-down panel keeps (it settles, it does not fly)
DEPTH_DEAD = 0.15       # a held hand's size must change by this much (log) before the panel grows or shrinks
THROWN_FRICTION = 1.6   # a thrown panel glides further than one put down
RING_HOLD = 0.45        # s: a pinch held still on empty space opens the panel ring
RING_R = 150.0          # px: the ring's radius
PAIR_FAR, PAIR_NEAR = 0.40, 0.16   # of the width: two open hands apart / together
TAP_SECONDS = 0.45      # from the pinch to the fingers opening
TAP_MOVE = 0.022        # of the screen's width
HEADER = 38.0
MIN_SCALE, MAX_SCALE = 0.5, 2.6
GRAB_MARGIN = 18.0      # px around a panel that still take it
HOVER_KEEP = 34.0       # px a hand must leave a target by before another is hot
MAGNET = 46.0           # px: the cursor leans onto a button this close
SNAP = 26.0             # px: a panel let go this close to another lines up
SNAP_GAP = 16.0
FOCUS_SECONDS = 5.0     # "this" stays the last panel pointed at this long
DEPTH = 1.4             # how strongly pulling a held panel towards you grows it (past DEPTH_DEAD)


def _font(size: float, bold: bool = False) -> QFont:
    try:
        from gui.style import hud_font
        return hud_font(size, QFont.Weight.Bold if bold else None)
    except Exception:
        f = QFont()
        f.setPointSizeF(size)
        f.setBold(bold)
        return f


def _say(english: str, **values) -> str:
    """English in the interface language (core/i18n.phrase), placeholders filled."""
    try:
        from core import i18n
        text = i18n.phrase(english)
    except Exception:
        text = english
    try:
        return text.format(**values) if values else text
    except Exception:
        return english.format(**values) if values else english


def _background(fn: Callable[[], None], name: str = "holo-data") -> None:
    threading.Thread(target=fn, daemon=True, name=name).start()


def _ease(cur: float, target: float, rate: float, dt: float) -> float:
    return cur + (target - cur) * (1 - math.exp(-rate * dt))


# ── on the window's thread ───────────────────────────────────────────────────

_calls: set = set()


def on_gui(job: Callable[[], object], timeout: float = 10.0):
    """Run `job` on Qt's thread (windows are made and changed only there)."""
    app = QApplication.instance()
    if app is None:
        return None
    if threading.current_thread() is threading.main_thread():
        return job()

    class _Call(QObject):
        fire = Signal()

        def __init__(self):
            super().__init__()
            self.done = threading.Event()
            self.result = None

        @Slot()
        def run(self):
            try:
                self.result = job()
            except Exception as e:
                print(f"[HoloDesk] {type(e).__name__}: {e}")
            finally:
                self.done.set()
                _calls.discard(self)

    call = _Call()
    call.moveToThread(app.thread())
    call.fire.connect(call.run, Qt.ConnectionType.QueuedConnection)
    _calls.add(call)
    call.fire.emit()
    call.done.wait(timeout)
    return call.result


# ── sounds ───────────────────────────────────────────────────────────────────

class Sounds:
    """Short sounds made here (no files shipped): a tick to take, a soft drop,
    a whoosh to throw, a chime to open, a falling note to close, a click to
    tap. Written once as WAV into the data folder and played by Qt; silent
    where Qt has no sound."""

    RATE = 44100

    def __init__(self, folder: Path, enabled: bool = True):
        self.enabled = enabled
        self._fx: dict = {}
        if not enabled:
            return
        try:
            from core.qt.QtMultimedia import QSoundEffect
        except Exception:
            self.enabled = False
            return
        folder.mkdir(parents=True, exist_ok=True)
        for name, wave in self._waves().items():
            path = folder / f"{name}.wav"
            try:
                if not path.is_file():
                    self._write(path, wave)
                fx = QSoundEffect()
                fx.setSource(QUrl.fromLocalFile(str(path)))
                fx.setVolume(0.35)
                self._fx[name] = fx
            except Exception as e:
                print(f"[HoloDesk] sound {name}: {e}")

    def _tone(self, freqs, seconds, attack=0.004, decay=None, noise=0.0, sweep=None):
        n = int(self.RATE * seconds)
        t = np.arange(n) / self.RATE
        w = np.zeros(n)
        for f in freqs:
            f_t = f if sweep is None else f * (sweep ** (t / seconds))
            w += np.sin(2 * np.pi * np.cumsum(np.full(n, 1.0) * f_t) / self.RATE)
        if noise:
            w += noise * np.random.default_rng(1).normal(0, 1, n)
        env = np.minimum(1.0, t / attack) * np.exp(-t / (decay or seconds / 3))
        return w / max(1e-9, np.abs(w).max()) * env

    def _waves(self) -> dict:
        return {
            "grab": self._tone([1320], 0.05, decay=0.015),
            "drop": self._tone([660], 0.07, decay=0.025),
            "tap": self._tone([1760, 2640], 0.035, decay=0.008),
            "open": np.concatenate([self._tone([880], 0.07, decay=0.05), self._tone([1320], 0.12, decay=0.06)]),
            "close": self._tone([990], 0.16, decay=0.06, sweep=0.5),
            "throw": self._tone([200], 0.22, decay=0.09, noise=1.6, sweep=3.0) * 0.6,
        }

    def _write(self, path: Path, wave: np.ndarray) -> None:
        import wave as wavmod
        pcm = (np.clip(wave, -1, 1) * 32000).astype("<i2").tobytes()
        with wavmod.open(str(path), "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(self.RATE)
            f.writeframes(pcm)

    def play(self, name: str) -> None:
        fx = self._fx.get(name) if self.enabled else None
        if fx is not None:
            try:
                fx.play()
            except Exception:
                pass


# ── panels ───────────────────────────────────────────────────────────────────

class Panel:
    """A floating glass panel. Subclasses draw their content (paint_body),
    answer a tap (tap), may fetch data (load: off the window's thread, every
    `refresh` seconds; -1 never), and say in words what they show (describe)."""

    kind = "panel"
    title = "Panel"
    size = (380.0, 260.0)
    refresh = 0.0
    scrollable = False

    def __init__(self, x: float, y: float, **options):
        self.x, self.y = float(x), float(y)
        self.tx = self.ty = None               # where it is settling (snap, back on screen)
        self.vx = self.vy = 0.0
        self.scale = 0.25
        self.scale_target = 1.0
        self.alpha = 0.0
        self.lift = 0.0                        # 0..1: hovered or held (it rises a little)
        self.closing = False
        self.held_by: list[int] = []
        self.focused = 0.0                     # when it was last pointed at
        self.options = dict(options)
        self.data = None
        self.scroll = 0.0
        self.scroll_v = 0.0
        self.focus_row = -1                    # the list row pointed at (for "read this")
        self._loaded_at = -1e9
        self._loading = False
        self._tries = 0
        self.born = time.monotonic()

    # geometry
    @property
    def shown_scale(self) -> float:
        return self.scale * (1 + 0.035 * self.lift)

    def rect(self, resting: bool = False) -> QRectF:
        """Where it is drawn; `resting`: its size once settled (no hover lift)."""
        s = self.scale_target if resting else self.shown_scale
        w, h = self.size[0] * s, self.size[1] * s
        return QRectF(self.x - w / 2, self.y - h / 2, w, h)

    def grab_rect(self) -> QRectF:
        return self.rect().adjusted(-GRAB_MARGIN, -GRAB_MARGIN, GRAB_MARGIN, GRAB_MARGIN)

    def contains(self, x: float, y: float, margin: float = 0.0) -> bool:
        return self.rect().adjusted(-margin, -margin, margin, margin).contains(QPointF(x, y))

    def close_box(self) -> QRectF:
        r = self.rect()
        s = HEADER * self.shown_scale
        return QRectF(r.right() - s, r.top(), s, s)

    def header_rect(self) -> QRectF:
        r = self.rect()
        return QRectF(r.left(), r.top(), r.width(), HEADER * self.shown_scale)

    def local(self, x: float, y: float) -> QPointF:
        """A screen point in the panel's own unscaled coordinates."""
        r = self.rect()
        s = self.shown_scale
        return QPointF((x - r.left()) / s, (y - r.top()) / s)

    def body(self) -> QRectF:
        return QRectF(16, HEADER + 10, self.size[0] - 32, self.size[1] - HEADER - 22)

    # data
    RETRY_FIRST = 4.0                          # s: a failed or empty fetch is tried again,
                                               # doubling, up to `refresh`

    def incomplete(self) -> bool:
        """The last fetch failed (or, for a list, found nothing)."""
        return isinstance(self.data, dict) and bool(self.data.get("error"))

    def tick(self, now: float) -> None:
        if self.refresh < 0 or self._loading:
            return
        wait = self.refresh
        if self.data is not None and self.incomplete():
            wait = min(self.refresh or 1e9, self.RETRY_FIRST * 2 ** min(6, max(0, self._tries - 1)))
        if self.data is None or (wait and now - self._loaded_at > wait):
            self._loading = True

            def work():
                try:
                    self.data = self.load()
                except Exception as e:
                    self.data = {"error": f"{type(e).__name__}: {e}"}
                finally:
                    self._tries = self._tries + 1 if self.incomplete() else 0
                    self._loaded_at = time.monotonic()
                    self._loading = False
            _background(work, f"holo-{self.kind}")

    def load(self):
        return {}

    def content_height(self) -> float:
        """How tall the content is (more than the body: it scrolls)."""
        return 0.0

    def can_scroll(self) -> bool:
        return self.scrollable and self.content_height() > self.body().height() + 4

    def step(self, dt: float) -> None:
        """Each frame: scrolling glides and stops at the ends."""
        if not self.scrollable:
            return
        top = max(0.0, self.content_height() - self.body().height())
        self.scroll += self.scroll_v * dt
        self.scroll_v *= math.exp(-4.0 * dt)
        if self.scroll < 0 or self.scroll > top:
            edge = 0.0 if self.scroll < 0 else top
            self.scroll = _ease(self.scroll, edge, 18, dt)
            self.scroll_v *= 0.5

    def describe(self) -> str:
        """What this panel shows, in words, for "read this" / "what is this"."""
        return ""

    # drawing
    def paint(self, p: QPainter, t: float, hot: bool, focus: bool) -> None:
        r = self.rect()
        s = self.shown_scale
        p.save()
        p.setOpacity(max(0.0, min(1.0, self.alpha)))
        if self.lift > 0.02:                                   # it rises: a shadow under it
            sh = QPainterPath()
            sh.addRoundedRect(r.translated(0, 10 * self.lift), 14 * s, 14 * s)
            p.fillPath(sh, QColor(0, 0, 0, int(90 * self.lift)))
        path = QPainterPath()
        path.addRoundedRect(r, 14 * s, 14 * s)
        grad = QLinearGradient(r.topLeft(), r.bottomRight())
        grad.setColorAt(0, QColor(10, 40, 60, 190))
        grad.setColorAt(1, QColor(2, 14, 24, 170))
        p.fillPath(path, QBrush(grad))
        edge = QColor(GREEN if self.held_by else AMBER if focus else CYAN)
        edge.setAlpha(255 if (hot or self.held_by or focus) else 140)
        p.setPen(QPen(edge, 2.4 if (hot or self.held_by or focus) else 1.3))
        p.drawPath(path)
        if focus:                                              # "this": a pulse around it
            pulse = QColor(AMBER)
            pulse.setAlpha(int(70 + 60 * math.sin(t * 6)))
            p.setPen(QPen(pulse, 1.5))
            p.drawRoundedRect(r.adjusted(-7, -7, 7, 7), 18 * s, 18 * s)
        p.translate(r.topLeft())
        p.scale(s, s)
        # header: the grip, the title, the ✕
        p.setPen(QPen(CYAN_DIM, 1))
        p.drawLine(QPointF(14, HEADER), QPointF(self.size[0] - 14, HEADER))
        p.setBrush(QBrush(CYAN_DIM))
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            for j in range(2):
                p.drawEllipse(QPointF(16 + j * 6, 13 + i * 6), 1.6, 1.6)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(CYAN)
        p.setFont(_font(10, True))
        p.drawText(QRectF(34, 0, self.size[0] - 80, HEADER), Qt.AlignmentFlag.AlignVCenter,
                   _say(self.title).upper())
        cx, cy = self.size[0] - HEADER / 2, HEADER / 2
        if getattr(self, "_close_hot", False):
            p.setBrush(QBrush(QColor(255, 51, 85, 70)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(cx, cy), 14, 14)
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(RED if getattr(self, "_close_hot", False) else TEXT_DIM, 1.8))
        p.drawLine(QPointF(cx - 6, cy - 6), QPointF(cx + 6, cy + 6))
        p.drawLine(QPointF(cx - 6, cy + 6), QPointF(cx + 6, cy - 6))
        body = self.body()
        p.setClipRect(body.adjusted(-4, -4, 4, 4))
        try:
            if isinstance(self.data, dict) and self.data.get("error"):
                self._message(p, body, self.data["error"])
            elif self.data is None and self.refresh >= 0:
                self._message(p, body, _say("Loading…"))
            else:
                self.paint_body(p, body, t)
        except Exception as e:
            self._message(p, body, f"{type(e).__name__}")
        p.setClipping(False)
        if self.can_scroll():                                  # where in the list
            top = max(1.0, self.content_height() - body.height())
            frac = max(0.0, min(1.0, self.scroll / top))
            bar_h = max(24.0, body.height() * body.height() / self.content_height())
            y = body.top() + (body.height() - bar_h) * frac
            p.setPen(QPen(CYAN_DIM, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(self.size[0] - 8, y), QPointF(self.size[0] - 8, y + bar_h))
        p.restore()

    @staticmethod
    def _message(p: QPainter, body: QRectF, text: str) -> None:
        p.setPen(TEXT_DIM)
        p.setFont(_font(9))
        p.drawText(body, int(Qt.AlignmentFlag.AlignCenter) | int(Qt.TextFlag.TextWordWrap), text)

    def paint_body(self, p: QPainter, body: QRectF, t: float) -> None:
        pass

    def tap(self, pt: QPointF) -> None:
        """A tap at `pt` (the panel's own coordinates)."""

    def point_at(self, pt: Optional[QPointF]) -> None:
        """The hand points at `pt` inside the panel (None: not any more)."""

    def twist(self, angle: float) -> None:
        """The holding hand turned by `angle` radians."""

    def close(self) -> None:
        """Let go of whatever it holds (a video stops)."""

    def state(self) -> dict:
        """What to keep of this panel between openings (with its kind and place)."""
        return dict(self.options)


class ListPanel(Panel):
    """Rows of text that scroll: news, notes, what's coming up."""

    scrollable = True
    row_h = 52.0

    def rows(self) -> list[dict]:
        return list((self.data or {}).get("rows") or [])

    def incomplete(self) -> bool:
        return super().incomplete() or (isinstance(self.data, dict) and not self.rows())

    def content_height(self) -> float:
        return len(self.rows()) * self.row_h

    def row_at(self, pt: QPointF) -> int:
        body = self.body()
        if not body.contains(pt):
            return -1
        i = int((pt.y() - body.top() + self.scroll) // self.row_h)
        return i if 0 <= i < len(self.rows()) else -1

    def point_at(self, pt):
        self.focus_row = self.row_at(pt) if pt is not None else -1

    def paint_body(self, p, body, t):
        rows = self.rows()
        if not rows:
            self._message(p, body, self.empty())
            return
        for i, row in enumerate(rows):
            top = body.top() + i * self.row_h - self.scroll
            if top > body.bottom() or top + self.row_h < body.top():
                continue
            r = QRectF(body.left(), top, body.width() - 10, self.row_h)
            if i == self.focus_row:
                p.fillRect(r.adjusted(-6, 1, 6, -1), QColor(255, 204, 0, 28))
            self.paint_row(p, r, row)

    def empty(self) -> str:
        return ""

    def paint_row(self, p: QPainter, r: QRectF, row: dict) -> None:
        pass

    def row_text(self, row: dict) -> str:
        return " - ".join(str(v) for k, v in row.items() if k in ("title", "name", "when", "line", "source") and v)

    def describe(self) -> str:
        rows = self.rows()
        if 0 <= self.focus_row < len(rows):
            return self.row_text(rows[self.focus_row])
        return "\n".join(self.row_text(r) for r in rows)


# ── the built-in kinds ───────────────────────────────────────────────────────

class ClockPanel(Panel):
    kind, title, size, refresh = "clock", "Clock", (360.0, 230.0), -1

    def paint_body(self, p, body, t):
        from datetime import datetime
        try:
            from core.qt.QtCore import QLocale
            loc = QLocale.system()
        except Exception:
            loc = None
        now = datetime.now()
        p.setPen(TEXT)
        p.setFont(_font(40, True))
        p.drawText(QRectF(body.left(), body.top(), body.width(), 86), Qt.AlignmentFlag.AlignCenter,
                   now.strftime("%H:%M"))
        p.setPen(TEXT_DIM)
        p.setFont(_font(11))
        date = loc.toString(now.date(), QLocale.FormatType.LongFormat) if loc else now.strftime("%A, %d %B %Y")
        p.drawText(QRectF(body.left(), body.top() + 86, body.width(), 30), Qt.AlignmentFlag.AlignCenter, date)
        frac = (now.second + now.microsecond / 1e6) / 60.0
        r = QRectF(body.center().x() - 120, body.bottom() - 26, 240, 8)
        p.setPen(QPen(CYAN_DIM, 2))
        p.drawLine(QPointF(r.left(), r.center().y()), QPointF(r.right(), r.center().y()))
        p.setPen(QPen(CYAN, 4))
        p.drawLine(QPointF(r.left(), r.center().y()), QPointF(r.left() + r.width() * frac, r.center().y()))

    def describe(self):
        from datetime import datetime
        return f"The time is {datetime.now().strftime('%H:%M')}, {datetime.now().strftime('%A %d %B %Y')}."


def _ring(p: QPainter, c: QPointF, radius: float, value: float, label: str, text: str) -> None:
    p.setPen(QPen(QColor(0, 60, 80), 7))
    p.drawEllipse(c, radius, radius)
    col = RED if value > 0.9 else ORANGE if value > 0.75 else CYAN
    p.setPen(QPen(col, 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawArc(QRectF(c.x() - radius, c.y() - radius, 2 * radius, 2 * radius), 90 * 16,
              int(-360 * 16 * max(0.0, min(1.0, value))))
    p.setPen(TEXT)
    p.setFont(_font(12, True))
    p.drawText(QRectF(c.x() - radius, c.y() - 14, 2 * radius, 22), Qt.AlignmentFlag.AlignCenter, text)
    p.setPen(TEXT_DIM)
    p.setFont(_font(7))
    p.drawText(QRectF(c.x() - radius - 10, c.y() + radius + 6, 2 * radius + 20, 16),
               Qt.AlignmentFlag.AlignCenter, label)


class SystemPanel(Panel):
    kind, title, size, refresh = "system", "System", (460.0, 230.0), 1.0

    def load(self):
        import psutil
        out = {"cpu": psutil.cpu_percent(interval=None) / 100.0,
               "ram": psutil.virtual_memory().percent / 100.0}
        try:
            from plugins import _live_panels_core as lb
            out["disk"] = psutil.disk_usage(lb.system_drive()).percent / 100.0
            g = lb.gpu_load()
            if g is not None:
                out["gpu"] = g / 100.0
        except Exception:
            pass
        return out

    def paint_body(self, p, body, t):
        d = self.data or {}
        items = [(k, d.get(k.lower())) for k in ("CPU", "RAM", "GPU", "DISK")]
        items = [(k, v) for k, v in items if v is not None]
        if not items:
            return
        step = body.width() / len(items)
        for i, (label, v) in enumerate(items):
            c = QPointF(body.left() + step * (i + 0.5), body.top() + body.height() * 0.42)
            _ring(p, c, min(46.0, step * 0.36), v, label, f"{v * 100:.0f}%")

    def describe(self):
        d = self.data or {}
        return ", ".join(f"{k.upper()} {v * 100:.0f}%" for k, v in d.items() if isinstance(v, float))


_SYMBOL_GLYPH = (("thunder", "⛈"), ("snow", "❄"), ("sleet", "🌨"), ("rain", "🌧"), ("fog", "🌫"),
                 ("partlycloudy", "⛅"), ("fair", "🌤"), ("cloudy", "☁"), ("clearsky", "☀"))


def _glyph(symbol: str) -> str:
    s = str(symbol or "").lower()
    if "night" in s and ("clearsky" in s or "fair" in s):
        return "☾"
    return next((g for k, g in _SYMBOL_GLYPH if k in s), "·")


class WeatherPanel(Panel):
    kind, title, size, refresh = "weather", "Weather", (440.0, 270.0), 600.0

    def load(self):
        city = str(self.options.get("city") or "").strip()
        try:
            from actions import weather_report
        except Exception:
            weather_report = None
        if not city and weather_report is not None:
            city = weather_report.last_city()
        self._city_shown = city
        if not city:
            return {"error": _say("Say which city: \"show the weather in Paris on the holo desk\"")}
        from core import cloud
        fc = cloud.forecast_days(city)
        if weather_report is not None and self.options.get("city"):
            weather_report.remember_asked(city)
        now_t = str(fc["now"].get("time") or "")
        hours = [h for h in fc["hourly"] if str(h.get("time") or "") >= now_t][:12]
        return {"place": str(fc.get("place") or city).split(",")[0], "now": fc["now"], "hours": hours,
                "days": fc["daily"][:4]}

    def paint_body(self, p, body, t):
        d = self.data
        now = d["now"]
        p.setPen(TEXT)
        p.setFont(_font(34))
        p.drawText(QRectF(body.left(), body.top(), 120, 70), Qt.AlignmentFlag.AlignCenter,
                   _glyph(now.get("symbol")))
        p.setFont(_font(30, True))
        temp = now.get("temp_c")
        p.drawText(QRectF(body.left() + 110, body.top(), 160, 70),
                   int(Qt.AlignmentFlag.AlignVCenter) | int(Qt.AlignmentFlag.AlignLeft),
                   f"{temp:.0f}°" if isinstance(temp, (int, float)) else "–")
        p.setPen(TEXT_DIM)
        p.setFont(_font(11, True))
        p.drawText(QRectF(body.left() + 230, body.top(), body.width() - 230, 70),
                   int(Qt.AlignmentFlag.AlignVCenter) | int(Qt.AlignmentFlag.AlignRight), d["place"])
        temps = [h.get("temp_c") for h in d["hours"] if isinstance(h.get("temp_c"), (int, float))]
        if len(temps) >= 2:
            g = QRectF(body.left() + 6, body.top() + 86, body.width() - 12, body.height() - 112)
            lo, hi = min(temps), max(temps)
            span = max(1.0, hi - lo)
            path = QPainterPath()
            for i, v in enumerate(temps):
                x = g.left() + g.width() * i / (len(temps) - 1)
                y = g.bottom() - g.height() * (v - lo) / span
                path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
            p.setPen(QPen(CYAN, 2.4))
            p.drawPath(path)
            p.setFont(_font(7))
            p.setPen(TEXT_DIM)
            for i, h in enumerate(d["hours"][:len(temps)]):
                if i % 3:
                    continue
                x = g.left() + g.width() * i / (len(temps) - 1)
                p.drawText(QRectF(x - 24, g.bottom() + 4, 48, 14), Qt.AlignmentFlag.AlignCenter,
                           str(h.get("time") or "")[11:16])
                p.drawText(QRectF(x - 24, g.top() - 16, 48, 14), Qt.AlignmentFlag.AlignCenter,
                           f"{temps[i]:.0f}°")

    def tick(self, now: float) -> None:
        # Without a city of its own it shows the city asked about last - and
        # follows it: asked about another city, it changes within two seconds.
        if not self.options.get("city") and not self._loading and now - getattr(self, "_city_seen", -1e9) > 2.0:
            self._city_seen = now
            try:
                from actions import weather_report
                want = weather_report.last_city()
            except Exception:
                want = ""
            if want and want != getattr(self, "_city_shown", None) and self.data is not None:
                self._loaded_at = -1e9               # fetch again now
        super().tick(now)

    def describe(self):
        d = self.data or {}
        if not d.get("now"):
            return ""
        now = d["now"]
        hours = ", ".join(f"{str(h.get('time'))[11:16]} {h.get('temp_c')}°C" for h in d.get("hours", [])[:8])
        return (f"Weather in {d.get('place')}: now {now.get('temp_c')}°C, {now.get('symbol')}, "
                f"feels like {now.get('feels_c')}°C, wind {now.get('wind_kmh')} km/h. Next hours: {hours}.")


class NewsPanel(ListPanel):
    kind, title, size, refresh = "news", "News", (520.0, 340.0), 900.0
    row_h = 58.0

    def load(self):
        from actions import web_search
        from core import briefing
        try:
            from core import i18n
            lang = i18n.current()
        except Exception:
            lang = ""
        country = briefing.country()
        topic = str(self.options.get("topic") or "")
        rows = web_search.news_results(briefing.news_query(topic, country, lang), 12,
                                       region=country, language=lang)
        return {"rows": [{"title": str(r.get("title") or "").strip(), "source": str(r.get("source") or ""),
                          "snippet": str(r.get("snippet") or "").strip(), "url": str(r.get("url") or "")}
                         for r in rows if r.get("title")][:12]}

    def empty(self):
        return _say("No news right now.")

    def paint_row(self, p, r, row):
        p.setPen(QPen(CYAN_DIM, 1))
        p.drawLine(QPointF(r.left(), r.bottom() - 1), QPointF(r.right(), r.bottom() - 1))
        p.setPen(TEXT)
        p.setFont(_font(9, True))
        p.drawText(QRectF(r.left(), r.top() + 3, r.width(), r.height() - 19),
                   int(Qt.AlignmentFlag.AlignLeft) | int(Qt.TextFlag.TextWordWrap), row["title"])
        p.setPen(TEXT_DIM)
        p.setFont(_font(7))
        p.drawText(QRectF(r.left(), r.bottom() - 17, r.width(), 14), Qt.AlignmentFlag.AlignLeft, row["source"])

    def row_text(self, row):
        return f"{row.get('title')} ({row.get('source')}). {row.get('snippet') or ''} {row.get('url') or ''}".strip()

    def tap(self, pt):
        i = self.row_at(pt)
        rows = self.rows()
        if 0 <= i < len(rows) and rows[i].get("url", "").startswith(("http://", "https://")):
            import webbrowser
            webbrowser.open(rows[i]["url"])


class NotesPanel(ListPanel):
    kind, title, size, refresh = "notes", "Notes", (440.0, 330.0), 60.0
    row_h = 56.0

    def load(self):
        try:
            from plugins import obsidian_vault as ov
            vault = ov._vault()
        except Exception:
            vault = None
        if vault is None:
            return {"error": _say("Connect your Obsidian vault in Obsidian's plugin settings to see your notes here.")}
        files = sorted(ov._walk(vault), key=ov._mtime, reverse=True)[:15]
        out = []
        for f in files:
            text = " ".join(ov._read_text(f).split())
            out.append({"name": f.stem, "line": text[:110], "text": text[:4000],
                        "path": ov._rel(vault, f), "vault": vault.name})
        return {"rows": out}

    def empty(self):
        return _say("No notes yet.")

    def paint_row(self, p, r, row):
        p.setPen(TEXT)
        p.setFont(_font(9, True))
        p.drawText(QRectF(r.left(), r.top() + 2, r.width(), 18), Qt.AlignmentFlag.AlignLeft, row["name"])
        p.setPen(TEXT_DIM)
        p.setFont(_font(7))
        p.drawText(QRectF(r.left(), r.top() + 20, r.width(), r.height() - 22),
                   int(Qt.AlignmentFlag.AlignLeft) | int(Qt.TextFlag.TextWordWrap), row["line"])

    def row_text(self, row):
        return f"Note '{row.get('name')}': {row.get('text') or row.get('line')}"

    def describe(self):
        rows = self.rows()
        if 0 <= self.focus_row < len(rows):
            return self.row_text(rows[self.focus_row])
        return "Recent notes: " + "; ".join(r.get("name", "") for r in rows)

    def tap(self, pt):
        from urllib.parse import quote
        i = self.row_at(pt)
        rows = self.rows()
        if 0 <= i < len(rows):
            import webbrowser
            webbrowser.open(f"obsidian://open?vault={quote(rows[i]['vault'])}&file={quote(rows[i]['path'])}")


class RoutinesPanel(ListPanel):
    kind, title, size, refresh = "routines", "Coming up", (420.0, 300.0), 30.0
    row_h = 34.0

    def load(self):
        from datetime import datetime
        from core.routines import desktop
        eng = desktop.engine()
        if eng is None:
            return {"rows": []}
        rows = []
        for r in eng.list():
            if not r.get("enabled") or r.get("system") or not r.get("next_ts"):
                continue
            rows.append((float(r["next_ts"]), r.get("name") or r.get("text") or ""))
        rows.sort()
        return {"rows": [{"when": datetime.fromtimestamp(ts).strftime("%a %H:%M"), "name": n}
                         for ts, n in rows[:20]]}

    def empty(self):
        return _say("Nothing scheduled. Say: \"remind me at 6 to call mom\".")

    def paint_row(self, p, r, row):
        p.setPen(CYAN)
        p.setFont(_font(9, True))
        p.drawText(QRectF(r.left(), r.top(), 90, r.height()), Qt.AlignmentFlag.AlignVCenter, row["when"])
        p.setPen(TEXT)
        p.setFont(_font(9))
        p.drawText(QRectF(r.left() + 96, r.top(), r.width() - 96, r.height()), Qt.AlignmentFlag.AlignVCenter,
                   row["name"])


class ModelPanel(Panel):
    """A 3D model as a hologram: Torvis's own head unless a .obj is given.
    It turns slowly by itself, and with the hand that holds it."""

    kind, title, size, refresh = "model", "3D model", (420.0, 420.0), 0.0

    def __init__(self, x, y, **options):
        super().__init__(x, y, **options)
        self.yaw = 0.0
        self.pitch = -0.15
        self.spin = 0.35
        self._mesh = None

    def load(self):
        """Built off the window's thread: a first frame that builds a mesh stutters."""
        return {"mesh": self._load_mesh()}

    def _load_mesh(self):
        if self._mesh is not None:
            return self._mesh
        path = str(self.options.get("path") or "")
        verts = edges = None
        if path and Path(path).is_file():
            verts, faces = _read_obj(Path(path))
            if verts is not None:
                edges = _edges(faces)
                self.title = Path(path).stem
        if verts is None:
            from core import avatar_mesh
            m = avatar_mesh.get_head_mesh()
            verts, edges = m["verts"].astype(np.float64), m["edges"]
            self.title = "Torvis"
        verts = verts - verts.mean(axis=0)
        verts /= max(1e-9, float(np.abs(verts).max()))
        if len(edges) > 9000:                               # keep it light to draw
            edges = edges[np.linspace(0, len(edges) - 1, 9000).astype(int)]
        self._mesh = (verts, edges)
        return self._mesh

    def twist(self, angle):
        self.yaw += angle * 1.6
        self.spin = 0.0

    def paint_body(self, p, body, t):
        verts, edges = self.data["mesh"]
        if not self.held_by:
            self.spin = min(0.35, self.spin + 0.002)
            self.yaw += self.spin / 60.0
        if not len(edges):
            return
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        rot = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]) @ np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
        v = verts @ rot.T
        s = min(body.width(), body.height()) * 0.42
        z = v[:, 2]
        persp = 1.0 / (1.0 + 0.35 * z)
        xs = body.center().x() + v[:, 0] * s * persp
        ys = body.center().y() - v[:, 1] * s * persp
        depth = (z[edges[:, 0]] + z[edges[:, 1]]) / 2
        near = depth < np.percentile(depth, 55)
        # Every line in one OpenCV call into a transparent picture: thousands of
        # separate QPainter lines cost a whole frame.
        w, h = int(body.width()), int(body.height())
        buf = np.zeros((h, w, 4), np.uint8)
        seg = np.stack([np.c_[xs[edges[:, 0]] - body.left(), ys[edges[:, 0]] - body.top()],
                        np.c_[xs[edges[:, 1]] - body.left(), ys[edges[:, 1]] - body.top()]], axis=1)
        seg = np.round(seg * 4).astype(np.int32)          # 2 bits of sub-pixel precision
        cv2 = H.cv2
        cv2.polylines(buf, list(seg[~near]), False, (255, 212, 0, 60), 1, cv2.LINE_AA, shift=2)
        cv2.polylines(buf, list(seg[near]), False, (255, 212, 0, 170), 1, cv2.LINE_AA, shift=2)
        img = QImage(buf.data, w, h, 4 * w, QImage.Format.Format_ARGB32).copy()
        p.drawImage(QRectF(body.left(), body.top(), w, h), img)
        band = body.top() + body.height() * ((t * 0.35) % 1.0)
        g = QLinearGradient(0, band - 30, 0, band + 4)
        c0, c1 = QColor(CYAN), QColor(CYAN)
        c0.setAlpha(0)
        c1.setAlpha(70)
        g.setColorAt(0, c0)
        g.setColorAt(1, c1)
        p.fillRect(QRectF(body.left(), band - 30, body.width(), 34), QBrush(g))

    def describe(self):
        return f"A 3D model: {self.title}."


def _read_obj(path: Path):
    verts, faces = [], []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("v "):
            verts.append([float(x) for x in line.split()[1:4]])
        elif line.startswith("f "):
            idx = [int(tok.split("/")[0]) for tok in line.split()[1:]]
            idx = [i - 1 if i > 0 else len(verts) + i for i in idx]
            for k in range(1, len(idx) - 1):
                faces.append([idx[0], idx[k], idx[k + 1]])
    if not verts or not faces:
        return None, None
    return np.array(verts, np.float64), np.array(faces, np.int64)


def _edges(faces: np.ndarray) -> np.ndarray:
    e = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    e.sort(axis=1)
    return np.unique(e, axis=0)


class ImagePanel(Panel):
    kind, title, size, refresh = "image", "Picture", (480.0, 360.0), 0

    def load(self):
        path = str(self.options.get("path") or "")
        img = QImage(path)
        if img.isNull():
            return {"error": _say("This picture could not be opened.")}
        self.title = Path(path).name
        return {"image": img}

    def paint_body(self, p, body, t):
        img = self.data["image"]
        s = min(body.width() / img.width(), body.height() / img.height())
        w, h = img.width() * s, img.height() * s
        p.drawImage(QRectF(body.center().x() - w / 2, body.center().y() - h / 2, w, h), img)

    def describe(self):
        return f"A picture: {Path(str(self.options.get('path') or '')).name}."


class VideoPanel(Panel):
    """A video playing inside the panel (a file or a direct video link); a tap
    pauses and plays. Sound plays; the panel closing stops it."""

    kind, title, size, refresh = "video", "Video", (560.0, 360.0), -1

    def __init__(self, x, y, **options):
        super().__init__(x, y, **options)
        self.frame: Optional[QImage] = None
        self.player = self.sink = self.audio = None
        self.error = ""
        src = str(options.get("path") or options.get("url") or "").strip()
        try:
            from core.qt.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
            self.player = QMediaPlayer()
            self.sink = QVideoSink()
            self.audio = QAudioOutput()
            self.audio.setVolume(0.8)
            self.player.setAudioOutput(self.audio)
            self.player.setVideoSink(self.sink)
            self.sink.videoFrameChanged.connect(self._frame)
            self.player.errorOccurred.connect(lambda *_a: setattr(self, "error", self.player.errorString()))
            url = QUrl(src) if re.match(r"^https?://", src) else QUrl.fromLocalFile(src)
            self.player.setSource(url)
            self.player.play()
            self.title = str(options.get("title") or Path(url.path()).name or "Video")[:60]
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"

    def _frame(self, frame):
        try:
            img = frame.toImage()
            if not img.isNull():
                self.frame = img
        except Exception:
            pass

    def paint_body(self, p, body, t):
        if self.frame is None:
            self._message(p, body, self.error or _say("Loading…"))
            return
        img = self.frame
        s = min(body.width() / img.width(), body.height() / img.height())
        w, h = img.width() * s, img.height() * s
        p.drawImage(QRectF(body.center().x() - w / 2, body.center().y() - h / 2, w, h), img)
        if self.player is not None:
            from core.qt.QtMultimedia import QMediaPlayer
            if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                p.setPen(QPen(TEXT, 3))
                c = body.center()
                p.drawLine(QPointF(c.x() - 8, c.y() - 14), QPointF(c.x() - 8, c.y() + 14))
                p.drawLine(QPointF(c.x() + 8, c.y() - 14), QPointF(c.x() + 8, c.y() + 14))
            dur = max(1, self.player.duration())
            frac = self.player.position() / dur
            p.setPen(QPen(CYAN_DIM, 3))
            p.drawLine(QPointF(body.left(), body.bottom() + 6), QPointF(body.right(), body.bottom() + 6))
            p.setPen(QPen(CYAN, 3))
            p.drawLine(QPointF(body.left(), body.bottom() + 6),
                       QPointF(body.left() + body.width() * frac, body.bottom() + 6))

    def tap(self, pt):
        if self.player is None:
            return
        from core.qt.QtMultimedia import QMediaPlayer
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def close(self):
        if self.player is not None:
            try:
                self.player.stop()
                self.player.setSource(QUrl())
            except Exception:
                pass

    def describe(self):
        return f"A video: {self.title}."


class TextPanel(Panel):
    """Anything the assistant puts on the desk: a recipe, a list, an answer."""

    kind, title, size, refresh = "text", "Note", (460.0, 340.0), -1
    scrollable = True

    def __init__(self, x, y, **options):
        super().__init__(x, y, **options)
        self.title = str(options.get("title") or "Note")[:60]
        self._height = 0.0

    def content_height(self):
        return self._height

    def paint_body(self, p, body, t):
        p.setPen(TEXT)
        p.setFont(_font(10))
        text = str(self.options.get("text") or "")
        flags = int(Qt.AlignmentFlag.AlignLeft) | int(Qt.TextFlag.TextWordWrap)
        need = p.boundingRect(QRectF(0, 0, body.width() - 10, 100000), flags, text)
        self._height = need.height()
        p.drawText(QRectF(body.left(), body.top() - self.scroll, body.width() - 10, need.height() + 4), flags, text)

    def describe(self):
        return f"{self.title}: {self.options.get('text') or ''}"


GUIDE = (
    ("🤏", "Pinch a panel's title bar to pick it up, open your fingers to let go"),
    ("💨", "Fling it while holding - off the screen, it closes"),
    ("↕", "Pinch inside a list and move up or down to scroll"),
    ("🙌", "Pinch one panel with both hands, pull apart or together to resize"),
    ("⤢", "Pull a held panel towards you to make it bigger"),
    ("👏", "Clap both open hands together fast, like catching a fly, to close everything; pull them apart to bring it back"),
    ("⏳", "Pinch and hold on empty space: a ring of panels opens around your hand"),
    ("👆", "A quick pinch presses buttons, headlines, notes and ✕"),
    ("👉", "Point at a panel and say \"close this\", \"read this\" or \"make this bigger\""),
    ("🔄", "Turn your hand while holding the 3D model to rotate it"),
    ("◐", "Overlay: the panels float over your own screen and the mouse is yours"),
    ("⌨", "Alt+Tab switches to other programs; Esc or ✕ leaves the desk"),
)


class GuidePanel(Panel):
    """How the desk is used: every gesture, drawn and said (it opens itself the
    first time, and with the ? button)."""

    kind, title, size, refresh = "help", "How to use the desk", (600.0, 520.0), -1
    scrollable = True
    row_h = 36.0

    def content_height(self):
        return len(GUIDE) * self.row_h

    def paint_body(self, p, body, t):
        for i, (icon, line) in enumerate(GUIDE):
            y = body.top() + i * self.row_h - self.scroll
            if y > body.bottom() or y + self.row_h < body.top():
                continue
            p.setPen(CYAN)
            p.setFont(_font(14))
            p.drawText(QRectF(body.left(), y, 40, self.row_h), Qt.AlignmentFlag.AlignCenter, icon)
            p.setPen(TEXT)
            p.setFont(_font(9))
            p.drawText(QRectF(body.left() + 48, y, body.width() - 58, self.row_h),
                       int(Qt.AlignmentFlag.AlignVCenter) | int(Qt.TextFlag.TextWordWrap), _say(line))

    def describe(self):
        return "How the Holo Desk is used: " + " ".join(f"{line}." for _i, line in GUIDE)


KINDS: dict[str, dict] = {}


def register_panel(kind: str, label: str, icon: str, factory: Callable[..., Panel],
                   in_dock: bool = True) -> None:
    """Add a kind of panel (another plugin may). factory(x, y, **options) -> Panel."""
    KINDS[kind] = {"label": label, "icon": icon, "factory": factory, "dock": in_dock}


for _cls, _icon in ((ClockPanel, "◷"), (SystemPanel, "▣"), (WeatherPanel, "☀"), (NewsPanel, "≣"),
                    (NotesPanel, "✎"), (RoutinesPanel, "⏰"), (ModelPanel, "◈")):
    register_panel(_cls.kind, _cls.title, _icon, _cls)
register_panel("image", "Picture", "▧", ImagePanel, in_dock=False)
register_panel("video", "Video", "▶", VideoPanel, in_dock=False)
register_panel("text", "Note", "▤", TextPanel, in_dock=False)
register_panel("help", "Guide", "?", GuidePanel, in_dock=False)

# The dock's own buttons, after the panel kinds.
DOCK_EXTRA = (("__help__", "?", "Guide"), ("__mode__", "◐", "Overlay"), ("__close__", "✕", "Close"))


# ── the desk ─────────────────────────────────────────────────────────────────

class _Grip:
    """One hand's pinch: what it holds and how it started."""

    def __init__(self, hand_id, x, y, t, target):
        self.hand_id = hand_id
        self.start = (x, y, t)
        self.target = target                   # ("dock", kind) | ("close", panel) | (part, panel) | None
        self.panel: Optional[Panel] = None
        self.mode = ""                         # "move" | "scroll" | "dock" | "close"
        self.offset = (0.0, 0.0)
        self.moved = False
        self.roll = None
        self.size0 = None                      # the hand's size at the grab (depth)
        self.scale0 = 1.0
        self.last_y = y
        self.pair = None


class HoloDesk(QWidget):

    def __init__(self, layout_file: Path, on_close: Callable[[], None], sounds: bool = True,
                 depth: bool = True, mode: str = "camera", on_focus: Optional[Callable[[bool], None]] = None,
                 show_help: bool = False):
        super().__init__(None)
        self.setWindowTitle("Holo Desk")
        self.mode = mode if mode in ("camera", "overlay") else "camera"
        self.on_focus = on_focus
        self.show_help = show_help
        self._active = True
        self._stash: list[dict] = []           # what the two-hand squeeze put away
        self._stash_at = 0.0
        self._pair_hist: list = []             # (t, distance, midpoint, both open) of two free hands
        self._pair_fired = 0.0
        self._clap_hist: list = []             # (t, wrists apart, knuckles apart, open, wrist A, wrist B)
        self._waves: list[dict] = []           # the squeeze's and the spread's rings
        self._apply_mode()
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.BlankCursor)
        self.layout_file = layout_file
        self.on_close = on_close
        self.depth = depth
        self.sounds = Sounds(layout_file.parent / "sounds", sounds)
        self.panels: list[Panel] = []
        self.grips: dict[int, _Grip] = {}
        self.cursors: dict[int, dict] = {}
        self.sparks: list[dict] = []
        self.ripples: list[dict] = []
        self.focus: Optional[Panel] = None
        self._frame_t = 0.0
        self._frame_hw = (480, 640)
        self._map = (1.0, 0.0, 0.0)            # shown width, dx, dy: picture -> screen
        self._bg: Optional[QImage] = None      # the finished background, one per camera frame
        self._bg_lock = threading.Lock()
        self._bg_size = (0, 0)
        self._shade = None                     # (multiply, add): tint, vignette and grid
        self._bg_frame = None
        self._bg_new = threading.Event()
        self._bg_stop = threading.Event()
        self._mouse = {"down": False, "panel": None, "offset": (0, 0), "pos": (0.0, 0.0), "t": 0.0}
        self._last = time.monotonic()
        self._t0 = self._last
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._step)

    # ── the window ─────────────────────────────────────────────────────────
    def _apply_mode(self) -> None:
        """camera: a normal full-screen window - Alt+Tab, the taskbar and the
        Windows key work, and while another program is in front the desk
        rests. overlay: transparent, above everything and click-through - your
        own screen under the panels, the mouse for your own programs, the
        hands for the desk."""
        overlay = self.mode == "overlay"
        flags = Qt.WindowType.FramelessWindowHint
        if overlay:
            flags |= (Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
                      | Qt.WindowType.WindowTransparentForInput)
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, overlay)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, not overlay)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, overlay)

    def set_mode(self, mode: str) -> None:
        if mode not in ("camera", "overlay") or mode == self.mode:
            return
        screen = self.screen()
        self.mode = mode
        self._apply_mode()
        if screen is not None:
            self.setGeometry(screen.geometry())
        self.showFullScreen()
        if mode == "camera":
            self.raise_()
            self.activateWindow()
        self.sounds.play("open")

    # ── opening and closing ────────────────────────────────────────────────
    def open_on(self, screen) -> None:
        if screen is not None:
            self.setGeometry(screen.geometry())
        self.showFullScreen()
        if self.mode == "camera":
            self.raise_()
            self.activateWindow()
        self._bg_size = (self.width(), self.height())
        self._restore()
        if self.show_help:
            self.add("help", self.width() / 2, self.height() * 0.42, sound=False)
        H.SERVICE.subscribe(self._on_camera)
        threading.Thread(target=self._bg_loop, daemon=True, name="holo-background").start()
        self._timer.start(16)
        self.sounds.play("open")

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.on_close()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._bg_size = (self.width(), self.height())

    def shutdown(self) -> None:
        H.SERVICE.unsubscribe(self._on_camera)
        self._bg_stop.set()
        self._save()
        for pn in self.panels:
            pn.close()
        self._timer.stop()
        self.hide()
        self.deleteLater()

    def _rows(self) -> list[dict]:
        w, h = max(1, self.width()), max(1, self.height())
        return [{"kind": pn.kind, "x": round(pn.x / w, 4), "y": round(pn.y / h, 4),
                 "scale": round(pn.scale_target, 3),
                 "options": {k: v for k, v in pn.state().items() if isinstance(v, (str, int, float, bool))}}
                for pn in self.panels if not pn.closing and pn.kind in KINDS]

    def _save(self, path: Optional[Path] = None) -> None:
        try:
            target = path or self.layout_file
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({"panels": self._rows()}, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[HoloDesk] layout not saved: {e}")

    def _restore(self, path: Optional[Path] = None, replace: bool = False) -> bool:
        try:
            rows = json.loads((path or self.layout_file).read_text(encoding="utf-8")).get("panels") or []
        except Exception:
            rows = []
        if path is not None and not rows:
            return False
        if replace:
            self.clear()
        if not rows:
            rows = [{"kind": "clock", "x": 0.2, "y": 0.28}, {"kind": "system", "x": 0.8, "y": 0.28},
                    {"kind": "model", "x": 0.8, "y": 0.62}]
        for i, row in enumerate(rows):
            pn = self.add(row.get("kind"), row.get("x", 0.5) * self.width(), row.get("y", 0.5) * self.height(),
                          sound=False, **(row.get("options") or {}))
            if pn is not None:
                pn.scale_target = float(row.get("scale") or 1.0)
                pn.born += i * 0.07                        # they come in one after another
        return True

    def save_as(self, path: Path) -> int:
        self._save(path)
        return len(self._rows())

    def open_saved(self, path: Path) -> bool:
        return self._restore(path, replace=True)

    def _free_spot(self) -> tuple[float, float]:
        """Where a new panel can go without covering another (a coarse grid)."""
        best, best_cost = (self.width() / 2, self.height() * 0.42), 1e18
        for gy in (0.28, 0.5, 0.68):
            for gx in (0.2, 0.4, 0.6, 0.8):
                x, y = gx * self.width(), gy * self.height()
                # a panel still flying in counts where it is going
                cost = sum(max(0.0, 1 - math.hypot((pn.x if pn.tx is None else pn.tx) - x,
                                                   (pn.y if pn.ty is None else pn.ty) - y) / 420.0)
                           for pn in self.panels if not pn.closing)
                cost += abs(gx - 0.5) * 0.2
                if cost < best_cost:
                    best, best_cost = (x, y), cost
        return best

    def add(self, kind: str, x: float | None = None, y: float | None = None, sound: bool = True,
            origin: tuple[float, float] | None = None, **options) -> Optional[Panel]:
        """A new panel. It flies from `origin` (its dock button, by default)
        to (x, y) (a free spot, by default) and grows to its size."""
        spec = KINDS.get(str(kind or ""))
        if spec is None:
            return None
        if x is None or y is None:
            x, y = self._free_spot()
        if origin is None:
            origin = next(((r.center().x(), r.center().y()) for k, r in self._dock_items() if k == kind),
                          (self.width() / 2, self.height() * 0.9))
        pn = spec["factory"](origin[0], origin[1], **options)
        pn.tx, pn.ty = x, y
        self.panels.append(pn)
        if sound:
            self.sounds.play("open")
        return pn

    def show_kind(self, kind: str, **options) -> Optional[Panel]:
        """A panel of `kind` to the front: the one already on the desk (with
        these options, its data fetched again when they changed), else a new
        one - asked twice for the weather, the desk shows one weather panel."""
        live = [pn for pn in self.panels if pn.kind == kind and not pn.closing]
        if not live:
            return self.add(kind, **options)
        pn = live[-1]
        if any(pn.options.get(k) != v for k, v in options.items()):
            pn.options.update(options)
            pn.data, pn._loaded_at, pn._tries = None, -1e9, 0
        self.panels.remove(pn)
        self.panels.append(pn)
        pn.focused = time.monotonic()
        self.focus = pn
        return pn

    def clear(self) -> None:
        """Every panel away - kept, so "bring them back" (or pulling two hands
        apart) returns them."""
        self.squeeze((self.width() / 2, self.height() * 0.45))

    def squeeze(self, mid) -> None:
        """Both hands pressed together: every panel falls into the point
        between them and is put away (the last such set is kept)."""
        rows = self._rows()
        if not rows:
            return
        self._stash, self._stash_at = rows, time.monotonic()
        for pn in list(self.panels):
            if pn.closing:
                continue
            pn.tx, pn.ty = mid
            pn.scale_target = pn.scale_target * 0.15
            self._close(pn, sound=False)
        self._waves.append({"x": mid[0], "y": mid[1], "t": time.monotonic(), "inward": True})
        self.sounds.play("close")

    def spread(self, mid) -> bool:
        """Both hands pulled apart: what the last squeeze put away comes back
        out of the point between them, each to where it was."""
        if not self._stash:
            return False
        for i, row in enumerate(self._stash):
            pn = self.add(row.get("kind"), row.get("x", 0.5) * self.width(), row.get("y", 0.5) * self.height(),
                          sound=False, origin=mid, **(row.get("options") or {}))
            if pn is not None:
                pn.scale_target = float(row.get("scale") or 1.0)
                pn.born += i * 0.04
        self._stash = []
        self._waves.append({"x": mid[0], "y": mid[1], "t": time.monotonic(), "inward": False})
        self.sounds.play("open")
        return True

    def _close(self, pn: Panel, sound: bool = True, thrown: bool = False) -> None:
        if pn.closing:
            return
        pn.closing = True
        pn.close()
        pn.held_by.clear()
        if self.focus is pn:
            self.focus = None
        for i in range(14):                                   # a small burst where it was
            a = i / 14 * 2 * math.pi
            sp = 220 + 160 * ((i * 7) % 5) / 5
            self.sparks.append({"x": pn.x, "y": pn.y, "vx": math.cos(a) * sp, "vy": math.sin(a) * sp,
                                "life": 0.5})
        if sound:
            self.sounds.play("throw" if thrown else "close")

    # ── the hands ──────────────────────────────────────────────────────────
    def _to_screen(self, x: float, y: float) -> tuple[float, float]:
        """A point of the picture (0..1) where the picture shows it on screen:
        the cursor sits on the hand you see."""
        w, dx, dy = self._map
        return x * w + dx, y * w * self._aspect() + dy

    def _aspect(self) -> float:
        h, w = self._frame_hw
        return h / w if w else 0.75

    def _shading(self, W: int, H_: int):
        """The dark tint, the vignette and the grid as one multiply and one add
        (uint8, W x H) - made once per window size."""
        if self._shade is not None and self._shade[0].shape[:2] == (H_, W):
            return self._shade
        yy, xx = np.mgrid[0:H_, 0:W].astype(np.float32)
        d = np.hypot(xx - W / 2, yy - H_ / 2) / (max(W, H_) * 0.7)
        vig = 1.0 - np.clip((d - 0.55) / 0.45, 0, 1) * 0.78
        keep = 0.53 * vig
        mul = np.repeat((keep * 255).astype(np.uint8)[:, :, None], 3, axis=2)
        add = np.zeros((H_, W, 3), np.float32)
        add[:] = np.array([18, 10, 0], np.float32) * 0.47      # BGR tint
        add *= vig[:, :, None]
        grid = np.zeros((H_, W), bool)
        grid[:, ::64] = True
        grid[::64, :] = True
        add[grid] += np.array([255, 212, 0], np.float32) * 0.07
        self._shade = (mul, np.clip(add, 0, 255).astype(np.uint8))
        return self._shade

    def _on_camera(self, hands, frame, t) -> None:
        """On the hand tracker's thread: hand the newest picture to the
        background's own thread (tracking never waits for it)."""
        self._bg_frame = frame
        self._bg_new.set()

    def _bg_loop(self) -> None:
        while not self._bg_stop.is_set():
            if self._bg_new.wait(0.5):
                self._bg_new.clear()
                if not self._active:
                    continue                           # another program is in front: rest
                try:
                    self._compose(self._bg_frame)
                except Exception as e:
                    print(f"[HoloDesk] background: {type(e).__name__}: {e}")

    def _compose(self, frame) -> None:
        """The camera picture made into the finished background - scaled to
        cover the window, tinted, vignetted, in the screen's own pixel format -
        so a repaint only copies it."""
        W, H_ = self._bg_size
        if W <= 0 or H_ <= 0 or frame is None:
            return
        cv2 = H.cv2
        h, w = frame.shape[:2]
        s = max(W / w, H_ / h)
        if self.mode == "overlay":                     # no picture: only where hands are
            with self._bg_lock:
                self._frame_hw = (h, w)
                self._map = (s * w, (W - s * w) / 2, (H_ - s * h) / 2)
            return
        sw, sh = int(round(w * s)), int(round(h * s))
        big = cv2.resize(frame, (sw, sh), interpolation=cv2.INTER_LINEAR)
        x0, y0 = (sw - W) // 2, (sh - H_) // 2
        big = big[y0:y0 + H_, x0:x0 + W]
        if big.shape[:2] != (H_, W):
            return
        mul, add = self._shading(W, H_)
        out = cv2.add(cv2.multiply(big, mul, scale=1 / 255), add)
        bgra = cv2.cvtColor(out, cv2.COLOR_BGR2BGRA)
        img = QImage(bgra.data, W, H_, 4 * W, QImage.Format.Format_RGB32).copy()
        with self._bg_lock:
            self._bg = img
            self._frame_hw = (h, w)
            self._map = (s * w, (W - s * w) / 2, (H_ - s * h) / 2)

    def _hands_now(self, now: float) -> list[tuple]:
        hands, _frame, t = H.SERVICE.latest()
        out = []
        ahead = max(0.0, min(0.05, now - t)) if t else 0.0
        for hd in hands:
            ax, ay = hd.aim
            out.append((hd, *self._to_screen(ax + hd.velocity[0] * ahead, ay + hd.velocity[1] * ahead)))
        return out

    def _magnet(self, x: float, y: float) -> tuple[float, float]:
        """The cursor leans onto a button (dock, ✕) it is close to."""
        best, d_best = None, MAGNET
        for _k, r in self._dock_items():
            d = math.hypot(r.center().x() - x, r.center().y() - y) - r.width() / 2
            if d < d_best:
                best, d_best = r.center(), d
        for pn in self.panels:
            if pn.closing:
                continue
            c = pn.close_box().center()
            d = math.hypot(c.x() - x, c.y() - y) - 8
            if d < d_best:
                best, d_best = c, d
        if best is None:
            return x, y
        k = 0.55 * (1 - max(0.0, d_best) / MAGNET)
        return x + (best.x() - x) * k, y + (best.y() - y) * k

    def _target_at(self, x: float, y: float, keep=None):
        """What a hand at (x, y) is over: ("dock", kind), ("close", panel),
        ("header" | "body", panel) or None. `keep`: the target it was over -
        it stays until the hand clearly leaves it."""
        if keep is not None:
            kind, obj = keep
            if kind == "dock":
                r = dict(self._dock_items()).get(obj)
                if r is not None and r.adjusted(-HOVER_KEEP, -HOVER_KEEP, HOVER_KEEP, HOVER_KEEP).contains(QPointF(x, y)):
                    return keep
            elif kind == "close":
                if not obj.closing and obj.close_box().adjusted(-HOVER_KEEP / 2, -HOVER_KEEP / 2, HOVER_KEEP / 2,
                                                                HOVER_KEEP / 2).contains(QPointF(x, y)):
                    return keep
            elif obj in self.panels and not obj.closing and obj.contains(x, y, HOVER_KEEP):
                part = "header" if obj.header_rect().adjusted(0, -GRAB_MARGIN, 0, 6).contains(QPointF(x, y)) \
                    else "body"
                return (part, obj)
        for k, r in self._dock_items():
            if r.adjusted(-14, -14, 14, 14).contains(QPointF(x, y)):
                return ("dock", k)
        for pn in reversed(self.panels):
            if pn.closing:
                continue
            if pn.close_box().adjusted(-10, -10, 10, 10).contains(QPointF(x, y)):
                return ("close", pn)
            if pn.grab_rect().contains(QPointF(x, y)):
                part = "header" if pn.header_rect().adjusted(0, -GRAB_MARGIN, 0, 6).contains(QPointF(x, y)) \
                    else "body"
                return (part, pn)
        return None

    def _update_hands(self, now: float, dt: float) -> None:
        seen = set()
        pointing = None
        for hd, sx, sy in self._hands_now(now):
            seen.add(hd.id)
            cur = self.cursors.setdefault(hd.id, {"x": sx, "y": sy, "vx": 0.0, "vy": 0.0, "pinch_n": 0,
                                                  "target": None, "hist": []})
            k = 1 - math.exp(-40 * dt)
            rx, ry = cur["x"] + (sx - cur["x"]) * k, cur["y"] + (sy - cur["y"]) * k
            cur["vx"] = 0.7 * cur["vx"] + 0.3 * (rx - cur["x"]) / max(dt, 1e-3)
            cur["vy"] = 0.7 * cur["vy"] + 0.3 * (ry - cur["y"]) / max(dt, 1e-3)
            mx, my = (rx, ry) if self.grips.get(hd.id) else self._magnet(rx, ry)
            cur.update(x=rx, y=ry, mx=mx, my=my, pinch=hd.pinching, gap=hd.pinch, roll=hd.roll(),
                       pts=hd.pts, seen=now, size=hd.size, pose=hd.pose)
            # How fast the hand moves: from the camera's own frames (the screen
            # draws each one twice or more) and never across a moment it was
            # not seen - a hand found again is not a hand thrown.
            ft = getattr(hd, "frame_t", None) or now
            if getattr(hd, "gap", 0.0) > 0:
                cur["hist"] = []
            if not cur["hist"] or ft > cur["hist"][-1][0]:
                hx, hy = self._to_screen(*hd.aim)          # where it is, not where it is heading
                cur["hist"] = [h_ for h_ in cur["hist"] if ft - h_[0] < 0.4] + [(ft, hx, hy)]
            # for the clap: wrist and knuckles on screen, the hand's own size
            # there, whether it is open (three fingers out, thumb away)
            cur["wrist"] = self._to_screen(*hd.pts[0, :2])
            cur["knuckle"] = self._to_screen(*hd.pts[9, :2])
            cur["hand_px"] = max(1.0, math.hypot(cur["knuckle"][0] - cur["wrist"][0],
                                                 cur["knuckle"][1] - cur["wrist"][1]))
            if getattr(hd, "extended", 4 if hd.pose == "open" else 0) >= 3 and hd.pinch > 0.7:
                cur["open_at"] = now
            if hd.pinching:
                cur["pinch_at"] = now
            if not self.grips.get(hd.id):
                cur["target"] = self._target_at(mx, my, cur.get("target"))
            cur["pinch_n"] = cur["pinch_n"] + 1 if hd.pinching else 0
            # a pinch born at speed is blur: it waits until the hand settles
            W = max(1, self.width())
            if not hd.pinching:
                cur["ghost"] = False
            elif cur["pinch_n"] == 1:
                cur["ghost"] = math.hypot(*self._recent_speed(cur, now, 0.2)) > GHOST_SPEED * W
            elif cur.get("ghost") and math.hypot(*self._recent_speed(cur, now, 0.12)) < GHOST_HEAL * W:
                cur["ghost"] = False
            if hd.pose == "point":
                tip = self._to_screen(*hd.index_tip)
                t_ = self._target_at(*tip)
                if t_ is not None and t_[0] in ("header", "body", "close"):
                    pointing = (t_[1], tip)
            grip = self.grips.get(hd.id)
            if grip is None and cur["pinch_n"] >= 2 and not cur.get("ghost") \
                    and now - cur.get("flung", -9) > 0.4:
                self._grab(hd.id, mx, my, now, hd.roll(), hd.size, cur["target"])
            elif grip is not None and not hd.pinching:
                opened = getattr(hd, "let_go_at", None)
                self._release(grip, min(now, opened) if opened else now)
            elif grip is not None:
                self._drag(grip, rx, ry, now, hd.roll(), hd.size, dt)
        for hid in list(self.cursors):
            if hid not in seen and now - self.cursors[hid].get("seen", 0) > 0.25:
                grip = self.grips.get(hid)
                if grip is not None:
                    self._release(grip, now)
                del self.cursors[hid]
        self._two_hands()
        self._clap(now)
        self._pair(now)
        self._point(pointing, now)

    def _clap(self, now: float) -> None:
        """Both hands clapped together fast - catching a fly: every panel is
        put away (squeeze). In hand sizes, near the camera or far."""
        live = [c for c in self.cursors.values() if now - c.get("seen", 0) <= 0.25 and "wrist" in c]
        if (self.grips or now - self._pair_fired < 1.5 or len(live) != 2
                or not any(not p.closing for p in self.panels)):
            self._clap_hist.clear()
            return
        seen = [c for c in live if now - c["seen"] < 1e-6]   # in the newest camera frame
        if len(seen) == 2:
            # left and right on screen, not by id: a hand found again mid-clap
            # gets a new one
            a, b = sorted(seen, key=lambda c: c["wrist"][0])
            hand = (a["hand_px"] + b["hand_px"]) / 2
            wd = math.dist(a["wrist"], b["wrist"]) / hand
            kd = math.dist(a["knuckle"], b["knuckle"]) / hand
            pinched = bool(a.get("pinch") or b.get("pinch"))
            # one sample per CAMERA frame (the screen draws each one twice or
            # more: a copy would read as hands that stopped)
            ft = max((c["hist"][-1][0] if c.get("hist") else now) for c in seen)
            if not self._clap_hist or self._clap_hist[-1][0] < ft:
                self._clap_hist = [h for h in self._clap_hist if ft - h[0] <= CLAP_TIME + 0.3] + [
                    (ft, wd, kd, pinched, a["wrist"], b["wrist"], hand)]
            if min(wd, kd) < CLAP_TOUCH and self._clapped(len(self._clap_hist) - 1):
                self._clap_fire(a, b)
        elif self._clap_hist:
            # palm to palm: the camera sees one hand, or none
            last = self._clap_hist[-1]
            if now - last[0] > 0.2:
                self._clap_hist.clear()
                return
            prev = self._clap_hist[-2] if len(self._clap_hist) >= 2 else None
            closing = (prev[1] - last[1]) / (last[0] - prev[0]) if prev is not None else 0.0
            if last[1] < CLAP_MERGE and closing >= CLAP_CLOSING and self._clapped(len(self._clap_hist) - 1):
                self._clap_fire(*live)
                return
            self._clap_hist.clear()

    def _clapped(self, end: int) -> bool:
        """Within CLAP_TIME before sample `end` the hands were CLAP_APART apart
        and not pinching, and both travelled towards each other since."""
        last = self._clap_hist[end]
        for t, wd, _kd, pinched, wa, wb, hand in self._clap_hist[:end]:
            if last[0] - t > CLAP_TIME or wd < CLAP_APART or pinched:
                continue
            if (wd - min(last[1], last[2])) / max(1e-3, last[0] - t) < CLAP_CLOSING:
                continue                                  # brought together, not clapped
            ux, uy = (wb[0] - wa[0]) / max(1e-6, wd * hand), (wb[1] - wa[1]) / max(1e-6, wd * hand)
            a_in = ((last[4][0] - wa[0]) * ux + (last[4][1] - wa[1]) * uy) / hand
            b_in = -((last[5][0] - wb[0]) * ux + (last[5][1] - wb[1]) * uy) / hand
            if a_in >= CLAP_EACH and b_in >= CLAP_EACH:
                return True
        return False

    def _clap_fire(self, a: dict, b: dict) -> None:
        self._pair_fired = time.monotonic()
        self._clap_hist.clear()
        self._pair_hist.clear()
        self.squeeze(((a["wrist"][0] + b["wrist"][0]) / 2, (a["wrist"][1] + b["wrist"][1]) / 2))

    def _pair(self, now: float) -> None:
        """Two open hands that were together pulled apart: what the clap put
        away comes back between them (the spread)."""
        # In view = not gone longer than a cursor is kept (_update_hands).
        # Open where they are apart - the squeeze's start, the spread's end:
        # pressed together, hands show the camera only their edges, and read
        # as any pose but a pinch.
        free = [c for hid, c in self.cursors.items()
                if not c.get("pinch") and hid not in self.grips and now - c.get("seen", 0) <= 0.25]
        if len(free) != 2 or self.grips:
            self._pair_hist.clear()
            return
        a, b = free
        # where the hands really are (the cursors are smoothed: they trail)
        (ax, ay), (bx, by) = ((c["hist"][-1][1], c["hist"][-1][2]) if c.get("hist") else (c["x"], c["y"])
                              for c in free)
        d = math.hypot(ax - bx, ay - by)
        mid = ((ax + bx) / 2, (ay + by) / 2)
        both_open = all(c.get("pose") in ("open", "other") for c in free)
        self._pair_hist = [h for h in self._pair_hist if now - h[0] < 0.9] + [(now, d, mid, both_open)]
        if now - self._pair_fired < 1.5 or len(self._pair_hist) < 4:
            return
        W = max(1, self.width())
        first, started_open = self._pair_hist[0][1], self._pair_hist[0][3]
        # Together: within PAIR_NEAR of the width, or two hand sizes - pressed
        # palm to palm the tracker may see them as one, and the last place of
        # the one it lost is a hand's width away.
        if first < PAIR_NEAR * W and both_open and d > PAIR_FAR * W and self._stash \
                and now - self._stash_at < 600:
            self._pair_fired = now
            self._pair_hist.clear()
            self.spread(mid)

    def _point(self, pointing, now) -> None:
        """The panel a finger points at - or, failing that, the one a hand is
        over - is "this" for a few seconds (holo_desk.py: "close this"). The row
        pointed at stays chosen until another is: "read this" is said after
        the hand has come down."""
        target = None
        if pointing is not None:
            target = pointing
        else:
            for cur in self.cursors.values():
                t_ = cur.get("target")
                if t_ is not None and t_[0] in ("header", "body"):
                    target = (t_[1], (cur["x"], cur["y"]))
                    break
        if target is not None:
            pn, (x, y) = target
            if self.focus is not None and self.focus is not pn:
                self.focus.point_at(None)
            self.focus, pn.focused = pn, now
            if pn.local(x, y).y() > HEADER:
                pn.point_at(pn.local(x, y))
            return
        if self.focus is not None and (self.focus.closing or now - self.focus.focused > FOCUS_SECONDS):
            self.focus.point_at(None)
            self.focus = None

    def _grab(self, hand_id, x, y, now, roll, size, target):
        grip = _Grip(hand_id, x, y, now, target)
        grip.roll, grip.size0 = roll, size
        if target is None:
            grip.mode = "empty"                       # held still here, it opens the ring
        if target is not None:
            kind, obj = target
            if kind == "dock":
                grip.mode = "dock"
                self.ripples.append({"x": x, "y": y, "t": now})
            elif kind == "close":
                grip.mode, grip.panel = "close", obj
            else:
                pn = obj
                grip.panel = pn
                grip.mode = "scroll" if (kind == "body" and pn.can_scroll()) else "move"
                grip.offset = (pn.x - x, pn.y - y)
                grip.scale0 = pn.scale_target
                pn.held_by.append(hand_id)
                pn.tx = pn.ty = None
                pn.vx = pn.vy = 0.0
                self.panels.remove(pn)
                self.panels.append(pn)                    # to the front
                self.sounds.play("grab")
        self.grips[hand_id] = grip

    def _drag(self, grip: _Grip, x, y, now, roll, size, dt):
        sx, sy, t0 = grip.start
        if grip.mode == "ring":
            dx, dy = x - sx, y - sy
            items = self._ring_items()
            if math.hypot(dx, dy) > 45 and items:
                ang = math.atan2(dy, dx)
                grip.ring_sel = min(range(len(items)), key=lambda i: abs(
                    (items[i][1] - ang + math.pi) % (2 * math.pi) - math.pi))
            else:
                grip.ring_sel = None
            return
        if math.hypot(x - sx, y - sy) > TAP_MOVE * self.width():
            grip.moved = True
        if grip.mode == "empty":
            if grip.moved:
                grip.mode = ""
            elif now - t0 >= RING_HOLD:
                grip.mode, grip.ring_sel, grip.ring_t = "ring", None, now
                self.sounds.play("tap")
            return
        if grip.mode == "dock" and grip.moved:
            if str(grip.target[1]).startswith("__"):
                grip.mode = ""                             # the dock's own buttons are only tapped
                return
            # pulled out of the dock: a new panel, already in the hand
            pn = self.add(grip.target[1], x, y, origin=(x, y))
            if pn is not None:
                pn.tx = pn.ty = None
                grip.mode, grip.panel, grip.offset = "move", pn, (0.0, 0.0)
                grip.scale0 = pn.scale_target
                pn.held_by.append(grip.hand_id)
            else:
                grip.mode = ""
            return
        pn = grip.panel
        if pn is None or pn.closing:
            return
        if grip.mode == "scroll":
            dy = y - grip.last_y
            if grip.moved:
                pn.scroll -= dy / pn.shown_scale
                pn.scroll_v = -dy / max(dt, 1e-3) / pn.shown_scale
            grip.last_y = y
            return
        if grip.mode != "move":
            return
        if grip.roll is not None and len(pn.held_by) == 1:
            d = (roll - grip.roll + math.pi) % (2 * math.pi) - math.pi
            if abs(d) > 0.01:
                pn.twist(d)
            grip.roll = roll
        if self.depth and len(pn.held_by) == 1 and grip.size0:
            # Nearer the camera the hand looks bigger: the panel comes with it.
            # A hand that tilts or crosses the picture looks a little bigger or
            # smaller too - only a change past DEPTH_DEAD counts, and none while
            # the hand travels fast (then the size is re-based where it is).
            cur = self.cursors.get(grip.hand_id, {})
            vx, vy = self._recent_speed(cur, now)
            if math.hypot(vx, vy) > 0.5 * self.width():
                grip.size0, grip.scale0 = size, pn.scale_target
            else:
                lr = math.log(max(1e-6, size) / grip.size0)
                eff = math.copysign(max(0.0, abs(lr) - DEPTH_DEAD), lr)
                pn.scale_target = max(MIN_SCALE, min(MAX_SCALE, grip.scale0 * math.exp(DEPTH * eff)))
        if len(pn.held_by) == 1 and grip.moved:
            # a flick while still holding is a throw: people throw without
            # remembering to open their fingers first - but only a real one,
            # fast over several camera frames in one direction
            cur = self.cursors.get(grip.hand_id, {})
            flick = self._flick(cur)
            if flick is not None:
                vx, vy = flick
                self.grips.pop(grip.hand_id, None)
                pn.held_by.remove(grip.hand_id)
                cur["flung"] = now
                self._fling(pn, vx, vy)

    def _release(self, grip: _Grip, now):
        self.grips.pop(grip.hand_id, None)
        sx, sy, t0 = grip.start
        cur = self.cursors.get(grip.hand_id, {})
        tap = not grip.moved and now - t0 <= TAP_SECONDS
        if grip.mode == "ring":
            items = self._ring_items()
            sel = getattr(grip, "ring_sel", None)
            if sel is not None and 0 <= sel < len(items):
                self.add(items[sel][0], sx, sy, origin=(sx, sy))
            return
        if grip.mode in ("empty", ""):
            return
        if grip.mode == "dock":
            if tap:
                self._dock_tap(grip.target[1])
            return
        if grip.mode == "close":
            if tap and grip.panel is not None:
                self._close(grip.panel)
                self.sounds.play("tap")
            return
        pn = grip.panel
        if pn is None:
            return
        if grip.hand_id in pn.held_by:
            pn.held_by.remove(grip.hand_id)
        if tap:
            self._tap(pn, sx, sy)
            return
        if grip.mode == "scroll" or pn.held_by:
            return
        lost = now - cur.get("seen", now) > 0.05
        throw = self._throw(cur, cur.get("seen", now) if lost else now)
        if throw is not None and now - t0 >= GRIP_MIN and now - getattr(pn, "stretched_at", -9) > STRETCH_QUIET:
            self._fling(pn, *throw)
            return
        vx, vy = (0.0, 0.0) if lost else self._recent_speed(cur, now)
        speed = math.hypot(vx, vy)
        pn.vx, pn.vy = vx * GLIDE, vy * GLIDE
        if speed < 0.27 * self.width():
            self._snap(pn)
        self.sounds.play("drop")

    def _fling(self, pn: Panel, vx: float, vy: float) -> None:
        """Thrown: it glides on; leaving the screen fast, it closes (_step)."""
        pn.vx, pn.vy = vx * 1.15, vy * 1.15
        pn.tx = pn.ty = None
        pn.thrown = True
        self.sounds.play("throw")

    def _throw(self, cur: dict, at: float):
        """(vx, vy) when letting go at `at` is a throw, else None: the peak
        speed over THROW_WINDOW (each step two camera frames, steadier than
        one) past THROW_SPEED, and the last frames still FOLLOW_THROUGH of it."""
        hist = [h for h in cur.get("hist", []) if at - THROW_WINDOW <= h[0] <= at + 0.05]
        if len(hist) < 3:
            return None
        peak, best = 0.0, None
        for i in range(len(hist) - 1):
            a, b = hist[i], hist[min(i + 2, len(hist) - 1)]
            if b[0] <= a[0]:
                continue
            v = ((b[1] - a[1]) / (b[0] - a[0]), (b[2] - a[2]) / (b[0] - a[0]))
            if math.hypot(*v) > peak:
                peak, best = math.hypot(*v), v
        a, b = hist[-3], hist[-1]
        last = math.hypot(b[1] - a[1], b[2] - a[2]) / max(1e-3, b[0] - a[0])
        if best is None or peak < THROW_SPEED * self.width() or last < FOLLOW_THROUGH * peak:
            return None
        return best

    @staticmethod
    def _recent_speed(cur: dict, now: float, window: float = 0.1) -> tuple[float, float]:
        """How fast the hand is moving right now (the last `window` s)."""
        hist = [h for h in cur.get("hist", []) if now - h[0] <= window]
        if len(hist) >= 2 and hist[-1][0] > hist[0][0]:
            dt = hist[-1][0] - hist[0][0]
            return (hist[-1][1] - hist[0][1]) / dt, (hist[-1][2] - hist[0][2]) / dt
        return 0.0, 0.0

    def _flick(self, cur: dict, speed: float = FLICK_SPEED):
        """(vx, vy) when the last FLICK_FRAMES camera frames all moved faster
        than `speed` (screen widths a second), in one direction - else None."""
        hist = cur.get("hist", [])[-(FLICK_FRAMES + 1):]
        if len(hist) < FLICK_FRAMES + 1:
            return None
        steps = [((b[1] - a[1]) / max(1e-3, b[0] - a[0]), (b[2] - a[2]) / max(1e-3, b[0] - a[0]))
                 for a, b in zip(hist, hist[1:])]
        fast = speed * self.width()
        if any(math.hypot(*v) < fast for v in steps):
            return None
        mean = (sum(v[0] for v in steps) / len(steps), sum(v[1] for v in steps) / len(steps))
        m = math.hypot(*mean)
        if any((v[0] * mean[0] + v[1] * mean[1]) / (math.hypot(*v) * m) < 0.8 for v in steps):
            return None
        return mean

    def _snap(self, pn: Panel) -> None:
        """Let go near another panel: it lines up with it - beside or under it
        with a small gap, or edge to edge - each axis on its own."""
        r = pn.rect(resting=True)
        best = {"x": None, "y": None}
        for other in self.panels:
            if other is pn or other.closing:
                continue
            o = other.rect(resting=True)
            near_y = r.bottom() > o.top() - SNAP and r.top() < o.bottom() + SNAP
            near_x = r.right() > o.left() - SNAP and r.left() < o.right() + SNAP
            cands = []
            if near_y:
                cands += [("x", o.right() + SNAP_GAP - r.left()), ("x", o.left() - SNAP_GAP - r.right()),
                          ("y", o.top() - r.top()), ("y", o.bottom() - r.bottom())]
            if near_x:
                cands += [("y", o.bottom() + SNAP_GAP - r.top()), ("y", o.top() - SNAP_GAP - r.bottom()),
                          ("x", o.left() - r.left()), ("x", o.right() - r.right())]
            for axis, d in cands:
                if abs(d) < SNAP and (best[axis] is None or abs(d) < abs(best[axis])):
                    best[axis] = d
        if best["x"] is None and best["y"] is None:
            return
        pn.vx = pn.vy = 0.0
        pn.tx, pn.ty = pn.x + (best["x"] or 0.0), pn.y + (best["y"] or 0.0)

    def _tap(self, pn: Panel, x, y):
        self.sounds.play("tap")
        self.ripples.append({"x": x, "y": y, "t": time.monotonic()})
        if pn.close_box().adjusted(-10, -10, 10, 10).contains(QPointF(x, y)):
            self._close(pn)
            return
        lp = pn.local(x, y)
        if lp.y() > HEADER:
            pn.tap(lp)

    def _two_hands(self):
        """A panel pinched by two hands: their distance sets its size."""
        held: dict[int, list] = {}
        for g in self.grips.values():
            if g.panel is not None and g.mode == "move":
                held.setdefault(id(g.panel), []).append(g)
        for gs in held.values():
            if len(gs) < 2:
                for g in gs:
                    g.pair = None
                continue
            a, b = (self.cursors.get(g.hand_id) for g in gs[:2])
            if not a or not b:
                continue
            pn = gs[0].panel
            d = math.hypot(a["x"] - b["x"], a["y"] - b["y"])
            if gs[0].pair is None:
                gs[0].pair = (d, pn.scale_target)
                continue
            d0, s0 = gs[0].pair
            pn.scale_target = max(MIN_SCALE, min(MAX_SCALE, s0 * d / max(1.0, d0)))
            pn.stretched_at = time.monotonic()

    # ── "this" ─────────────────────────────────────────────────────────────
    def focused(self) -> Optional[Panel]:
        """The panel the user means by "this": the one in their hand, else the
        one pointed at or under the hand within the last few seconds, else the
        one in front."""
        held = [pn for pn in self.panels if pn.held_by and not pn.closing]
        if held:
            return held[-1]
        if self.focus is not None and not self.focus.closing:
            return self.focus
        live = [pn for pn in self.panels if not pn.closing]
        return live[-1] if live else None

    def act(self, op: str, pn: Optional[Panel] = None) -> str:
        """"this": close | bigger | smaller | front | center. What happened."""
        pn = pn or self.focused()
        if pn is None:
            return ""
        if op == "close":
            self._close(pn)
        elif op == "bigger":
            pn.scale_target = min(MAX_SCALE, pn.scale_target * 1.35)
        elif op == "smaller":
            pn.scale_target = max(MIN_SCALE, pn.scale_target / 1.35)
        elif op in ("front", "center"):
            self.panels.remove(pn)
            self.panels.append(pn)
            if op == "center":
                pn.tx, pn.ty = self.width() / 2, self.height() * 0.45
        return pn.title

    # ── the mouse: everything the hands do ─────────────────────────────────
    def mousePressEvent(self, e):
        x, y = e.position().x(), e.position().y()
        target = self._target_at(x, y)
        if target is not None and target[0] == "dock":
            self._dock_tap(target[1])
            return
        if target is not None and target[0] == "close":
            self._close(target[1])
            return
        pn = target[1] if target else None
        scroll = bool(pn and target[0] == "body" and pn.can_scroll())
        self._mouse.update(down=True, panel=pn, pos=(x, y), t=time.monotonic(), start=(x, y),
                           offset=(pn.x - x, pn.y - y) if pn else (0, 0), moved=False, scroll=scroll)
        if pn is not None:
            pn.held_by.append(-1)
            pn.tx = pn.ty = None
            self.panels.remove(pn)
            self.panels.append(pn)

    def mouseMoveEvent(self, e):
        x, y = e.position().x(), e.position().y()
        m = self._mouse
        if m["down"] and m["panel"] is not None:
            px, py = m["pos"]
            sx, sy = m.get("start", (x, y))
            if math.hypot(x - sx, y - sy) > 4:
                m["moved"] = True
            dt = max(1e-3, time.monotonic() - m["t"])
            if m.get("scroll"):
                m["panel"].scroll -= (y - py) / m["panel"].shown_scale
                m["panel"].scroll_v = -(y - py) / dt / m["panel"].shown_scale
            elif e.buttons() & Qt.MouseButton.RightButton:
                m["panel"].twist((x - px) / 160.0)
            m["vx"], m["vy"] = (x - px) / dt, (y - py) / dt
        m["pos"], m["t"] = (x, y), time.monotonic()
        m["hover"] = (x, y)

    def mouseReleaseEvent(self, e):
        m = self._mouse
        pn = m.get("panel")
        if pn is not None:
            if -1 in pn.held_by:
                pn.held_by.remove(-1)
            if not m.get("moved"):
                self._tap(pn, *m["pos"])
            elif not m.get("scroll"):
                pn.vx, pn.vy = m.get("vx", 0.0), m.get("vy", 0.0)
                if math.hypot(pn.vx, pn.vy) < 350:
                    self._snap(pn)
        m.update(down=False, panel=None)

    def wheelEvent(self, e):
        pn = next((p for p in reversed(self.panels) if not p.closing and p.contains(e.position().x(),
                                                                                     e.position().y())), None)
        if pn is None:
            return
        if pn.can_scroll() and not (e.modifiers() & Qt.KeyboardModifier.ControlModifier):
            pn.scroll -= e.angleDelta().y() / 2
        else:
            f = 1.1 if e.angleDelta().y() > 0 else 1 / 1.1
            pn.scale_target = max(MIN_SCALE, min(MAX_SCALE, pn.scale_target * f))

    # ── the dock ───────────────────────────────────────────────────────────
    def _dock_items(self) -> list[tuple[str, QRectF]]:
        kinds = [k for k, v in KINDS.items() if v["dock"]] + [k for k, _i, _l in DOCK_EXTRA]
        size = min(78.0, self.width() / (len(kinds) * 1.45))
        gap = size * 0.3
        total = len(kinds) * size + (len(kinds) - 1) * gap
        x0 = (self.width() - total) / 2
        y = self.height() * 0.80
        return [(k, QRectF(x0 + i * (size + gap), y, size, size)) for i, k in enumerate(kinds)]

    def _dock_tap(self, key: str) -> None:
        if key == "__close__":
            QTimer.singleShot(0, self.on_close)
        elif key == "__help__":
            live = [pn for pn in self.panels if pn.kind == "help" and not pn.closing]
            if live:
                self._close(live[0])
            else:
                self.add("help", self.width() / 2, self.height() * 0.42)
        elif key == "__mode__":
            QTimer.singleShot(0, lambda: self.set_mode("overlay" if self.mode == "camera" else "camera"))
        else:
            self.add(key)

    def _ring_items(self) -> list[tuple[str, float]]:
        """The ring around a held pinch: every dock kind, evenly round."""
        kinds = [k for k, v in KINDS.items() if v["dock"]]
        n = max(1, len(kinds))
        return [(k, -math.pi / 2 + 2 * math.pi * i / n) for i, k in enumerate(kinds)]

    # ── each frame ─────────────────────────────────────────────────────────
    def _step(self):
        now = time.monotonic()
        dt = max(1e-3, min(0.05, now - self._last))
        self._last = now
        active = self.mode == "overlay" or not self.isVisible() or (
            self.isActiveWindow() and not self.isMinimized())
        if active != self._active:
            self._active = active
            if not active:                                 # Alt+Tab away: the desk rests
                for g in list(self.grips.values()):
                    self._release(g, now)
                self.cursors.clear()
            if self.on_focus is not None:
                try:
                    self.on_focus(active)
                except Exception:
                    pass
        if not active:
            return
        self._update_hands(now, dt)
        W, H_ = self.width(), self.height()
        m = self._mouse
        hover = [(c.get("mx", c["x"]), c.get("my", c["y"])) for c in self.cursors.values()]
        if "hover" in m:
            hover.append(m["hover"])
        for pn in list(self.panels):
            pn.tick(now)
            pn.step(dt)
            pn.scale = _ease(pn.scale, pn.scale_target if not pn.closing else pn.scale_target * 0.6, 14, dt)
            if now >= pn.born:
                pn.alpha = _ease(pn.alpha, 0.0 if pn.closing else 1.0, 12 if pn.closing else 9, dt)
            hot = bool(pn.held_by) or any(pn.contains(x, y, GRAB_MARGIN) for x, y in hover)
            pn.lift = _ease(pn.lift, 1.0 if hot else 0.0, 12, dt)
            pn._close_hot = any(pn.close_box().adjusted(-10, -10, 10, 10).contains(QPointF(x, y)) for x, y in hover)
            holders = [g for g in self.grips.values() if g.panel is pn and g.mode == "move"]
            if m["down"] and m.get("panel") is pn and not m.get("scroll"):
                pn.x = _ease(pn.x, m["pos"][0] + m["offset"][0], FOLLOW, dt)
                pn.y = _ease(pn.y, m["pos"][1] + m["offset"][1], FOLLOW, dt)
            elif holders:
                pts = [(self.cursors[g.hand_id]["x"] + g.offset[0], self.cursors[g.hand_id]["y"] + g.offset[1])
                       for g in holders if g.hand_id in self.cursors]
                if pts:
                    pn.x = _ease(pn.x, sum(p[0] for p in pts) / len(pts), FOLLOW, dt)
                    pn.y = _ease(pn.y, sum(p[1] for p in pts) / len(pts), FOLLOW, dt)
            elif pn.tx is not None:
                pn.x = _ease(pn.x, pn.tx, 9, dt)
                pn.y = _ease(pn.y, pn.ty, 9, dt)
                if abs(pn.x - pn.tx) < 0.5 and abs(pn.y - pn.ty) < 0.5:
                    pn.tx = pn.ty = None
            else:
                pn.x += pn.vx * dt
                pn.y += pn.vy * dt
                decay = math.exp(-(THROWN_FRICTION if getattr(pn, "thrown", False) else FRICTION) * dt)
                pn.vx *= decay
                pn.vy *= decay
                speed = math.hypot(pn.vx, pn.vy)
                off = not (0 <= pn.x <= W and 0 <= pn.y <= H_)
                if not pn.closing and off and speed > OUT_SPEED * W:
                    self._close(pn, sound=False, thrown=True)  # thrown out of the screen
                elif not pn.closing and speed < 60:
                    pn.thrown = False
                    self._keep_on_screen(pn)
            if pn.closing and pn.alpha < 0.03:
                self.panels.remove(pn)
        for s in self.sparks:
            s["x"] += s["vx"] * dt
            s["y"] += s["vy"] * dt
            s["vx"] *= math.exp(-5 * dt)
            s["vy"] *= math.exp(-5 * dt)
            s["life"] -= dt
        self.sparks = [s for s in self.sparks if s["life"] > 0]
        self.ripples = [r for r in self.ripples if now - r["t"] < 0.5]
        self._waves = [w for w in self._waves if now - w["t"] < 0.7]
        self.update()

    def _keep_on_screen(self, pn: Panel) -> None:
        """A panel let go slowly never stays half off the screen: it comes back in."""
        r = pn.rect()
        m = 20.0
        tx = min(max(pn.x, r.width() / 2 + m), self.width() - r.width() / 2 - m) if r.width() < self.width() else pn.x
        ty = min(max(pn.y, r.height() / 2 + m), self.height() * 0.82 - r.height() / 2) \
            if r.height() < self.height() * 0.8 else pn.y
        if abs(tx - pn.x) > 1 or abs(ty - pn.y) > 1:
            pn.tx, pn.ty = tx, ty
            pn.vx = pn.vy = 0.0

    # ── drawing ────────────────────────────────────────────────────────────
    def paintEvent(self, e):
        now = time.monotonic()
        t = now - self._t0
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        with self._bg_lock:
            bg = self._bg
        if self.mode == "overlay":
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            p.fillRect(self.rect(), Qt.GlobalColor.transparent)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        elif bg is not None and bg.width() == self.width() and bg.height() == self.height():
            p.drawImage(0, 0, bg)
        else:
            p.fillRect(self.rect(), QColor(0, 6, 10))
        hover = [(c.get("mx", c["x"]), c.get("my", c["y"])) for c in self.cursors.values()]
        if "hover" in self._mouse:
            hover.append(self._mouse["hover"])
        hot_targets = {id(c["target"][1]) for c in self.cursors.values()
                       if c.get("target") and c["target"][0] in ("header", "body")}
        for pn in self.panels:
            hot = id(pn) in hot_targets or (bool(self._mouse.get("hover")) and pn.contains(*self._mouse["hover"]))
            pn.paint(p, t, hot, focus=pn is self.focus and not pn.held_by)
        self._paint_effects(p, now)
        self._paint_ring(p, now)
        self._paint_dock(p, hover)
        self._paint_cursors(p, t)
        self._paint_status(p)
        p.end()

    def _paint_ring(self, p: QPainter, now: float) -> None:
        """The panel ring around a held pinch: it opens out, the one the hand
        leans towards lights up, letting go there opens it."""
        for g in self.grips.values():
            if g.mode != "ring":
                continue
            cx, cy, _t = g.start
            k = min(1.0, (now - getattr(g, "ring_t", now)) / 0.18)
            r = RING_R * (0.4 + 0.6 * k)
            p.setPen(QPen(QColor(0, 212, 255, int(90 * k)), 2))
            p.setBrush(QBrush(QColor(0, 20, 32, int(140 * k))))
            p.drawEllipse(QPointF(cx, cy), r + 44, r + 44)
            p.setBrush(Qt.BrushStyle.NoBrush)
            for i, (kind, ang) in enumerate(self._ring_items()):
                x, y = cx + math.cos(ang) * r, cy + math.sin(ang) * r
                sel = getattr(g, "ring_sel", None) == i
                rad = 34 if sel else 28
                fill = QColor(GREEN if sel else CYAN)
                fill.setAlpha(int((90 if sel else 35) * k))
                p.setBrush(QBrush(fill))
                p.setPen(QPen(GREEN if sel else CYAN, 2.4 if sel else 1.4))
                p.drawEllipse(QPointF(x, y), rad, rad)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(TEXT)
                p.setFont(_font(15))
                p.drawText(QRectF(x - rad, y - rad, 2 * rad, 2 * rad), Qt.AlignmentFlag.AlignCenter,
                           KINDS[kind]["icon"])
                if sel:
                    p.setFont(_font(8, True))
                    p.drawText(QRectF(x - 80, y + rad + 2, 160, 18), Qt.AlignmentFlag.AlignCenter,
                               _say(KINDS[kind]["label"]))

    def _paint_effects(self, p: QPainter, now: float) -> None:
        for w in self._waves:
            k = (now - w["t"]) / 0.7
            radius = (1 - k) * 420 + 20 if w["inward"] else k * 420 + 20
            c = QColor(CYAN)
            c.setAlpha(int(200 * (1 - k)))
            p.setPen(QPen(c, 3))
            p.drawEllipse(QPointF(w["x"], w["y"]), radius, radius)
        for s in self.sparks:
            c = QColor(CYAN)
            c.setAlpha(int(255 * max(0.0, s["life"] / 0.5)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(c))
            p.drawEllipse(QPointF(s["x"], s["y"]), 2.5, 2.5)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for r in self.ripples:
            k = (now - r["t"]) / 0.5
            c = QColor(GREEN)
            c.setAlpha(int(200 * (1 - k)))
            p.setPen(QPen(c, 2))
            p.drawEllipse(QPointF(r["x"], r["y"]), 10 + 40 * k, 10 + 40 * k)

    def _paint_dock(self, p: QPainter, hover):
        for k, r in self._dock_items():
            d = min((math.hypot(r.center().x() - x, r.center().y() - y) for x, y in hover), default=1e9)
            grow = 1 + 0.3 * max(0.0, 1 - d / 170)           # buttons near the hand grow
            rr = QRectF(r.center().x() - r.width() * grow / 2, r.center().y() - r.height() * grow / 2,
                        r.width() * grow, r.height() * grow)
            hot = d < r.width() / 2 + 14
            extra = {key: (icon, label) for key, icon, label in DOCK_EXTRA}
            col = RED if k == "__close__" else CYAN
            p.setPen(QPen(col if hot else CYAN_DIM, 2.4 if hot else 1.4))
            fill = QColor(col)
            fill.setAlpha(70 if hot else 25)
            p.setBrush(QBrush(fill))
            p.drawEllipse(rr)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(TEXT if hot else TEXT_DIM)
            p.setFont(_font(16 * grow))
            icon = extra[k][0] if k in extra else KINDS[k]["icon"]
            p.drawText(rr, Qt.AlignmentFlag.AlignCenter, icon)
            p.setFont(_font(7))
            if k == "__mode__":
                label = _say("Camera" if self.mode == "overlay" else "Overlay")
            else:
                label = _say(extra[k][1]) if k in extra else _say(KINDS[k]["label"])
            p.drawText(QRectF(rr.left() - 24, rr.bottom() + 4, rr.width() + 48, 16), Qt.AlignmentFlag.AlignCenter,
                       label)

    def _paint_cursors(self, p: QPainter, t: float):
        for cur in self.cursors.values():
            pts = cur.get("pts")
            if pts is not None:
                col = QColor(CYAN)
                col.setAlpha(60)
                p.setPen(QPen(col, 2))
                sp = [QPointF(*self._to_screen(pt[0], pt[1])) for pt in pts]
                for a, b in H.BONES:
                    p.drawLine(sp[a], sp[b])
                if cur.get("pose") == "point":                 # pointing: a beam from the fingertip
                    tip = sp[H.INDEX_TIP]
                    c = QColor(AMBER)
                    c.setAlpha(180)
                    p.setPen(QPen(c, 2))
                    p.drawEllipse(tip, 9, 9)
            x, y = cur.get("mx", cur["x"]), cur.get("my", cur["y"])
            pinch = cur.get("pinch")
            close = 1.0 if pinch else max(0.0, min(1.0, 1 - (cur.get("gap", 1.0) - H.PINCH_ON)
                                                   / (H.PINCH_OFF * 1.6)))
            col = GREEN if pinch else CYAN
            glow = QRadialGradient(QPointF(x, y), 34)
            c0, c1 = QColor(col), QColor(col)
            c0.setAlpha(110)
            c1.setAlpha(0)
            glow.setColorAt(0, c0)
            glow.setColorAt(1, c1)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(glow))
            p.drawEllipse(QPointF(x, y), 34, 34)
            p.setBrush(Qt.BrushStyle.NoBrush)
            radius = 16 - 6 * close
            p.setPen(QPen(col, 2.2))
            p.drawEllipse(QPointF(x, y), radius, radius)
            p.setPen(QPen(col, 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(QRectF(x - 24, y - 24, 48, 48), int((90 + t * 120) * 16), int(-360 * 16 * close))

    def _paint_status(self, p: QPainter):
        p.setFont(_font(8, True))
        p.setPen(CYAN)
        p.drawText(QRectF(28, 18, 600, 24), Qt.AlignmentFlag.AlignLeft, "◈  HOLO DESK")
        note = H.SERVICE.error
        if note:
            p.setPen(ORANGE)
            p.drawText(QRectF(28, 40, self.width() - 56, 22), Qt.AlignmentFlag.AlignLeft, note)
        elif not self.cursors:
            p.setPen(TEXT_DIM)
            p.drawText(QRectF(0, self.height() * 0.80 - 30, self.width(), 22), Qt.AlignmentFlag.AlignCenter,
                       _say("Raise your hand - pinch a panel to move it, pinch a button to open one"))
