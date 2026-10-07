"""
Live Panels' core — the system diagnostics and the weather, drawn live
where the avatar sits.

A shared driver for live_panels.py (the leading underscore keeps the plugin
loader from treating it as a skill), imported only when a board is first
opened. Self-contained: it needs nothing from main.py or actions/, so the two
files can be dropped into any JARVIS's plugins folder.

    System      rings, per-core load, 60-second history, network and the
                heaviest processes, sampled every second and animated between
                samples. Click a ring to graph that metric, the tabs to switch
                between history and cores, a process for its details.
    Weather     now, the next hours and seven days for a city; the local clock
                ticks, the icon moves, rain or snow falls behind it. Click a day
                to see its hours, hover the curve to read any hour.

Both are laid out on a 1600 × 1000 canvas (the size of Planet Watch's
pictures) and scaled to whatever the HUD's centre is, so a click is mapped
back into that canvas. The × closes a board; so does "close it" (the plugin
wraps the UI's stop_video the first time a board opens), and so does anything
else that takes the centre — a video, the camera, a picture.

PyQt6 ends the process on an exception escaping a Qt callback, so every paint,
input and timer handler here catches everything.
"""

from __future__ import annotations

import ctypes
import json
import math
import random
import re
import threading
import time
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlencode

import psutil
from PyQt6 import sip
from PyQt6.QtCore import (
    QEvent,
    QLineF,
    QObject,
    QPointF,
    QRectF,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PyQt6.QtWidgets import QWidget

W, H = 1600.0, 1000.0
BG = (6, 10, 16)
TEXT = (226, 238, 248)
DIM = (118, 138, 158)
WARN = (255, 196, 64)
HOT = (255, 84, 96)
SUN = (255, 206, 84)

BASE_DIR = Path(__file__).resolve().parent.parent

_errors: set = set()


def accent() -> tuple[int, int, int]:
    """The accent the user picked for the HUD (⚙ → colour), cyan otherwise."""
    try:
        cfg = json.loads(
            (BASE_DIR / "config" / "api_keys.json").read_text(encoding="utf-8")
        )
        colour = str(cfg.get("ui_color") or "")
        if re.fullmatch(r"#[0-9a-fA-F]{6}", colour):
            return tuple(int(colour[i : i + 2], 16) for i in (1, 3, 5))
    except Exception:
        pass
    return (56, 214, 255)


# ─────────────────────────────────────────────────────────────────────────────
# Data: GPU load, weather
# ─────────────────────────────────────────────────────────────────────────────

_nvml: Any = None  # None = untried, False = unavailable, else (lib, handle, kind)


def gpu_load() -> Optional[float]:
    """NVIDIA GPU utilisation in %, None without an NVIDIA GPU. Initialised once."""
    global _nvml
    if _nvml is False:
        return None
    try:
        if _nvml is None:
            try:
                import warnings

                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    import pynvml  # type: ignore
                pynvml.nvmlInit()
                _nvml = (pynvml, pynvml.nvmlDeviceGetHandleByIndex(0), "py")
            except Exception:
                load = ctypes.WinDLL if hasattr(ctypes, "WinDLL") else ctypes.CDLL
                lib = None
                for name in (
                    "nvml",
                    r"C:\Windows\System32\nvml.dll",
                    "libnvidia-ml.so.1",
                ):
                    try:
                        lib = load(name)
                        lib.nvmlInit_v2()
                        break
                    except Exception:
                        lib = None
                if lib is None:
                    _nvml = False
                    return None
                dev = ctypes.c_void_p()
                lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
                _nvml = (lib, dev, "c")
        lib, dev, kind = _nvml
        if kind == "py":
            return float(lib.nvmlDeviceGetUtilizationRates(dev).gpu)

        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        u = _Util()
        lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
        return float(u.gpu)
    except Exception:
        _nvml = False
        return None


_GEO = "https://geocoding-api.open-meteo.com/v1/search"
_METEO = "https://api.open-meteo.com/v1/forecast"


def _get(url: str, params: dict, timeout: float = 8) -> dict:
    req = urllib.request.Request(
        f"{url}?{urlencode(params)}", headers={"User-Agent": "JARVIS-live-panels/1.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def geocode(city: str) -> Optional[dict]:
    """The place a person most likely means: the most populous match, asked in
    English and in the user's own spelling, so "Londra" is London and not a
    village called Londru."""
    found: dict = {}
    for lang in ("en", "tr", "de", "fr", "es"):
        try:
            for r in (
                _get(_GEO, {"name": city, "count": 5, "language": lang}).get("results")
                or []
            ):
                found.setdefault(r.get("id"), r)
        except Exception:
            continue
        if found and lang == "tr":
            break  # English + the local spelling is usually enough
    if not found:
        return None
    exact = [
        r
        for r in found.values()
        if str(r.get("name", "")).casefold() == city.casefold()
    ]
    pool = exact or list(found.values())
    return max(pool, key=lambda r: r.get("population") or 0)


def forecast(lat: float, lon: float) -> dict:
    return _get(
        _METEO,
        {
            "latitude": lat,
            "longitude": lon,
            "timezone": "auto",
            "forecast_days": 7,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
            "weather_code,wind_speed_10m,wind_direction_10m,is_day,pressure_msl",
            "hourly": "temperature_2m,precipitation_probability",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
            "precipitation_probability_max,sunrise,sunset,uv_index_max",
        },
    )


def _once(where: str, exc: BaseException) -> None:
    if where not in _errors:
        _errors.add(where)
        print(f"[HUD live] {where}: {type(exc).__name__}: {exc}")


def _q(rgb, a: int = 255) -> QColor:
    return QColor(int(rgb[0]), int(rgb[1]), int(rgb[2]), int(a))


def _mix(a, b, t: float):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _level(pct: float, acc):
    if pct >= 90:
        return HOT
    if pct >= 70:
        return WARN
    return acc


def _approach(cur: float, target: float, dt: float, rate: float = 7.0) -> float:
    return cur + (target - cur) * min(1.0, dt * rate)


# ─────────────────────────────────────────────────────────────────────────────
# GUI thread
# ─────────────────────────────────────────────────────────────────────────────

_calls: set = set()


def _gui(job: Callable[[], Any], timeout: float = 10.0) -> Any:
    """Run `job` on the GUI thread and return its result (None on failure)."""
    from PyQt6.QtCore import QThread, pyqtSlot

    app = QGuiApplication.instance()
    if app is None:
        return None
    if app.thread() is QThread.currentThread():
        return job()

    class _Call(QObject):
        fire = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.done = threading.Event()
            self.result = None

        @pyqtSlot()
        def run(self):
            try:
                self.result = job()
            except Exception as exc:
                _once("gui job", exc)
            finally:
                self.done.set()
                _calls.discard(self)

    call = _Call()
    call.moveToThread(app.thread())
    call.fire.connect(call.run, Qt.ConnectionType.QueuedConnection)
    _calls.add(call)
    call.fire.emit()
    if not call.done.wait(timeout):
        return None
    return call.result


# ─────────────────────────────────────────────────────────────────────────────
# The frame every board shares
# ─────────────────────────────────────────────────────────────────────────────


class Board(QWidget):
    """Background, header, ×, fade, scaling and click targets."""

    def __init__(self, acc, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.acc = acc
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAutoFillBackground(False)
        self.setMinimumSize(320, 220)
        self.fade, self.fade_from, self.fade_to, self.fade_t0 = (
            0.0,
            0.0,
            1.0,
            time.time(),
        )
        self.t_last = time.time()
        self.mouse = QPointF(-1, -1)  # in canvas coordinates
        self.hits: list = []  # [(QRectF, key)] rebuilt each paint
        self.hover_key: Optional[str] = None
        self._fonts: dict = {}
        self._metrics: dict = {}
        self._text_cache: dict = {}  # rendered text (with its glow) -> QPixmap
        self._bg: Optional[QPixmap] = (
            None  # grid, gradient, corners: drawn once per size
        )
        self._bg_key: Any = None
        self.last_input = 0.0
        # WHY THE FRAME RATE MOVES
        #     JARVIS's voice is written to the speaker from Python, and so is
        #     every frame here: while a frame is being painted the voice thread
        #     waits for the interpreter. Painting 30 frames a second of a board
        #     that is mostly standing still is what made the voice stutter. So
        #     frames come fast only while something moves (a ring settling, the
        #     mouse, a fade) and slowly otherwise.
        self.idle_ms = 200
        self.frame = QTimer(self)
        self.frame.setInterval(33)
        self.frame.timeout.connect(self.update)

    # ── fonts / drawing helpers (canvas coordinates) ─────────────────────────
    def font(self, kind: str, size: float) -> QFont:
        key = (kind, int(size))
        f = self._fonts.get(key)
        if f is None:
            fam = {
                "display": "Bahnschrift",
                "mono": "Consolas",
                "body": "Segoe UI",
            }.get(kind, "Segoe UI")
            f = QFont(fam)
            f.setPixelSize(max(6, int(size)))
            if kind == "mono":
                f.setBold(True)
            self._fonts[key] = f
        return f

    def text(
        self,
        p: QPainter,
        x,
        y,
        s: str,
        font: QFont,
        col,
        align="l",
        valign="top",
        glow: bool = False,
        alpha: int = 255,
    ) -> float:
        """Draw text from a cache of rendered pixmaps: a glowing label is nine
        drawText calls, and most labels are the same from one frame to the next."""
        fm = self._metrics.get(id(font))
        if fm is None:
            fm = self._metrics[id(font)] = QFontMetricsF(font)
        asc, desc = fm.ascent(), fm.descent()
        sc = p.transform().m11()
        key = (s, id(font), tuple(col), glow, alpha, round(sc, 3))
        hit = self._text_cache.get(key)
        if hit is None:
            w = fm.horizontalAdvance(s)
            pad = 4.0 if glow else 1.0
            dpr = max(1.0, self.devicePixelRatioF())
            lw, lh = (w + 2 * pad) * sc, (asc + desc + 2 * pad) * sc
            pix = QPixmap(max(1, math.ceil(lw * dpr)), max(1, math.ceil(lh * dpr)))
            pix.setDevicePixelRatio(dpr)
            pix.fill(Qt.GlobalColor.transparent)
            qp = QPainter(pix)
            qp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            qp.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            qp.scale(sc, sc)
            qp.setFont(font)
            bx, by = pad, pad + asc
            if glow:
                for d, a in ((3, 40), (1.5, 70)):
                    qp.setPen(_q(col, a * alpha // 255))
                    for ox, oy in ((-d, 0), (d, 0), (0, -d), (0, d)):
                        qp.drawText(QPointF(bx + ox, by + oy), s)
            qp.setPen(_q(col, alpha))
            qp.drawText(QPointF(bx, by), s)
            qp.end()
            if len(self._text_cache) > 600:
                self._text_cache.clear()
            hit = self._text_cache[key] = (pix, w, pad, lw / sc, lh / sc)
        pix, w, pad, cw, ch = hit
        if align == "c":
            x -= w / 2
        elif align == "r":
            x -= w
        if valign == "top":
            top = y
        elif valign == "mid":
            top = y + (asc - desc) / 2 - asc
        else:  # baseline
            top = y - asc
        p.drawPixmap(
            QRectF(x - pad, top - pad, cw, ch),
            pix,
            QRectF(0, 0, pix.width(), pix.height()),
        )
        return w

    def stroke(
        self,
        p: QPainter,
        draw: Callable[[], None],
        col,
        width: float,
        glow: bool = True,
        cap=Qt.PenCapStyle.RoundCap,
    ) -> None:
        p.setBrush(Qt.BrushStyle.NoBrush)
        if glow:
            for extra, a in ((width * 2.2 + 8, 22), (width + 4, 55)):
                p.setPen(
                    QPen(
                        _q(col, a),
                        extra,
                        Qt.PenStyle.SolidLine,
                        cap,
                        Qt.PenJoinStyle.RoundJoin,
                    )
                )
                draw()
        p.setPen(
            QPen(_q(col), width, Qt.PenStyle.SolidLine, cap, Qt.PenJoinStyle.RoundJoin)
        )
        draw()

    def panel(
        self, p: QPainter, r: QRectF, alpha: float = 0.10, active: bool = False
    ) -> None:
        p.setPen(
            QPen(_q(_mix(BG, self.acc, 0.6 if active else 0.35)), 1.5 if active else 1)
        )
        p.setBrush(_q(_mix(BG, self.acc, alpha)))
        p.drawRect(r)
        p.setPen(QPen(_q(self.acc), 3))
        p.drawLine(QPointF(r.left(), r.top()), QPointF(r.left() + 40, r.top()))

    def hit(self, r: QRectF, key: str) -> bool:
        """Register a click target; True if the mouse is over it now."""
        self.hits.append((r, key))
        return r.contains(self.mouse)

    # ── geometry ─────────────────────────────────────────────────────────────
    def _xf(self) -> tuple[float, float, float]:
        s = min(self.width() / W, self.height() / H)
        return s, (self.width() - W * s) / 2, (self.height() - H * s) / 2

    def _to_canvas(self, pt) -> QPointF:
        s, ox, oy = self._xf()
        return QPointF((pt.x() - ox) / s, (pt.y() - oy) / s)

    # ── fade ─────────────────────────────────────────────────────────────────
    def fade_in(self) -> None:
        self.fade_from = self.fade if self.isVisible() else 0.0
        self.fade_to, self.fade_t0 = 1.0, time.time()
        self.frame.start()
        self.on_open()

    def close_view(self) -> None:
        if self.isVisible() and self.fade_to != 0.0:
            self.fade_from, self.fade_to, self.fade_t0 = self.fade, 0.0, time.time()
            self.frame.setInterval(33)
            self.update()
            # The fade advances in paintEvent, and Qt does not paint a board that
            # another one now covers — so make sure it ends even unseen.
            QTimer.singleShot(400, self._finish_close)
        self.on_close()

    def _finish_close(self) -> None:
        try:
            if not sip.isdeleted(self) and self.fade_to == 0.0 and self.isVisible():
                self.fade = 0.0
                self.frame.stop()
                self.hide()
        except Exception as exc:
            _once("finish close", exc)

    def _fade_step(self) -> None:
        t = max(0.0, min(1.0, (time.time() - self.fade_t0) / 0.3))
        e = 1 - (1 - t) ** 3
        self.fade = self.fade_from + (self.fade_to - self.fade_from) * e
        opaque = self.fade >= 1.0
        if self.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent) != opaque:
            self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, opaque)
        if t >= 1.0 and self.fade_to == 0.0 and self.isVisible():
            self.frame.stop()
            self.hide()

    # ── hooks for subclasses ─────────────────────────────────────────────────
    def title(self) -> tuple[str, str]:
        return "", ""

    def draw(self, p: QPainter, dt: float, now: float) -> None:
        pass

    def clicked(self, key: str) -> None:
        pass

    def on_open(self) -> None:
        pass

    def on_close(self) -> None:
        pass

    # ── Qt events ────────────────────────────────────────────────────────────
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        try:
            now = time.time()
            dt = min(0.1, max(0.0, now - self.t_last))
            self.t_last = now
            self._fade_step()
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            p.setOpacity(max(0.0, min(1.0, self.fade)))
            p.fillRect(self.rect(), _q(BG))
            p.drawPixmap(0, 0, self._backdrop_pixmap())
            s, ox, oy = self._xf()
            p.translate(ox, oy)
            p.scale(s, s)
            self.hits = []
            self.draw(p, dt, now)
            self._header(p, now)
            hover = None
            for r, key in self.hits:
                if r.contains(self.mouse):
                    hover = key
            if hover != self.hover_key:
                self.hover_key = hover
                self.setCursor(
                    Qt.CursorShape.PointingHandCursor
                    if hover
                    else Qt.CursorShape.ArrowCursor
                )
            want = 33 if self._busy(now) else self.idle_ms
            if self.frame.interval() != want:
                self.frame.setInterval(want)
        except Exception as exc:
            _once(f"paint {type(self).__name__}", exc)
        finally:
            p.end()

    def _busy(self, now: float) -> bool:
        return (
            self.fade != self.fade_to or now - self.last_input < 0.8 or self.animating()
        )

    def animating(self) -> bool:
        """Subclasses: True while something on the board is still moving."""
        return False

    def _backdrop_pixmap(self) -> QPixmap:
        dpr = max(1.0, self.devicePixelRatioF())
        key = (self.width(), self.height(), dpr)
        if self._bg is None or self._bg_key != key:
            pix = QPixmap(
                max(1, math.ceil(self.width() * dpr)),
                max(1, math.ceil(self.height() * dpr)),
            )
            pix.setDevicePixelRatio(dpr)
            pix.fill(_q(BG))
            qp = QPainter(pix)
            qp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            s, ox, oy = self._xf()
            qp.translate(ox, oy)
            qp.scale(s, s)
            self._backdrop(qp)
            qp.end()
            self._bg, self._bg_key = pix, key
        return self._bg

    def _backdrop(self, p: QPainter) -> None:
        g = QRadialGradient(QPointF(W * 0.5, H * 0.35), W * 0.75)
        g.setColorAt(0.0, _q(_mix(BG, self.acc, 0.16)))
        g.setColorAt(1.0, _q(BG))
        p.fillRect(QRectF(0, 0, W, H), QBrush(g))
        p.setPen(QPen(_q(_mix(BG, self.acc, 0.07)), 1))
        for x in range(0, int(W) + 1, 40):
            p.drawLine(QPointF(x, 0), QPointF(x, H))
        for y in range(0, int(H) + 1, 40):
            p.drawLine(QPointF(0, y), QPointF(W, y))
        L, m = 60, 24
        for x, y, sx, sy2 in (
            (m, m, 1, 1),
            (W - m, m, -1, 1),
            (m, H - m, 1, -1),
            (W - m, H - m, -1, -1),
        ):
            path = QPainterPath(QPointF(x, y + sy2 * L))
            path.lineTo(QPointF(x, y))
            path.lineTo(QPointF(x + sx * L, y))
            self.stroke(
                p,
                lambda path=path: p.drawPath(path),
                self.acc,
                4,
                cap=Qt.PenCapStyle.SquareCap,
            )

    def _header(self, p: QPainter, now: float) -> None:
        title, sub = self.title()
        self.text(p, 70, 48, title.upper(), self.font("display", 54), TEXT, glow=True)
        if sub:
            self.text(p, 72, 116, sub, self.font("mono", 22), DIM)
        p.setPen(QPen(_q(_mix(BG, self.acc, 0.45)), 2))
        p.drawLine(QPointF(70, 160), QPointF(W - 70, 160))
        p.fillRect(QRectF(70, 157, 260, 5), _q(self.acc))
        # LIVE badge
        blink = 1.0 if int(now * 2) % 2 == 0 else 0.35
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_q(HOT, int(255 * blink)))
        p.drawEllipse(QPointF(1330, 84), 7, 7)
        p.setBrush(_q(HOT, int(60 * blink)))
        p.drawEllipse(QPointF(1330, 84), 14, 14)
        self.text(p, 1346, 84, "LIVE", self.font("mono", 22), TEXT, valign="mid")
        # close
        r = QRectF(1470, 60, 50, 50)
        over = self.hit(r, "close")
        self.panel(p, r, 0.3 if over else 0.12, active=over)
        c = HOT if over else TEXT
        self.stroke(
            p,
            lambda: (
                p.drawLine(QPointF(1483, 73), QPointF(1507, 97)),
                p.drawLine(QPointF(1507, 73), QPointF(1483, 97)),
            ),
            c,
            3,
            glow=over,
        )

    def mouseMoveEvent(self, e) -> None:
        try:
            self.mouse = self._to_canvas(e.position())
            self.last_input = time.time()
            if self.frame.interval() != 33:
                self.frame.setInterval(33)
                self.update()
        except Exception as exc:
            _once("mouse", exc)

    def leaveEvent(self, _e) -> None:
        self.mouse = QPointF(-1, -1)

    def mousePressEvent(self, e) -> None:
        try:
            pt = self._to_canvas(e.position())
            self.mouse = pt
            self.last_input = time.time()
            self.update()
            for r, key in reversed(self.hits):
                if r.contains(pt):
                    if key == "close":
                        self.close_view()
                    else:
                        self.clicked(key)
                    return
        except Exception as exc:
            _once("click", exc)

    # ── shared widgets ───────────────────────────────────────────────────────
    def ring(
        self,
        p: QPainter,
        cx,
        cy,
        r,
        pct,
        label,
        value,
        sub="",
        selected=False,
        hover=False,
    ) -> None:
        col = _level(pct, self.acc)
        wdt = max(10.0, r / 7)
        box = QRectF(cx - r, cy - r, 2 * r, 2 * r)
        if selected or hover:
            gr = QRadialGradient(QPointF(cx, cy), r * 1.5)
            gr.setColorAt(0.55, _q(col, 55 if selected else 28))
            gr.setColorAt(1.0, _q(col, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(gr))
            p.drawEllipse(QPointF(cx, cy), r * 1.5, r * 1.5)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_q(_mix(BG, self.acc, 0.18)), wdt))
        p.drawEllipse(box)
        lit = int(round(min(pct, 100) / 100 * 60))
        groups: dict = {"on": [], "major": [], "minor": []}
        for i in range(60):
            a = math.radians(i * 6 - 90)
            r0, r1 = r + 10, r + (20 if i % 5 == 0 else 14)
            g = "on" if i < lit else ("major" if i % 5 == 0 else "minor")
            groups[g].append(
                QLineF(
                    cx + r0 * math.cos(a),
                    cy + r0 * math.sin(a),
                    cx + r1 * math.cos(a),
                    cy + r1 * math.sin(a),
                )
            )
        for g, c in (
            ("on", _q(col, 200)),
            ("major", _q(_mix(BG, self.acc, 0.5))),
            ("minor", _q(_mix(BG, self.acc, 0.25))),
        ):
            if groups[g]:
                p.setPen(QPen(c, 2))
                p.drawLines(groups[g])
        if pct > 0.3:
            span = -int(min(pct, 100) * 3.6 * 16)
            self.stroke(
                p,
                lambda: p.drawArc(box, 90 * 16, span),
                col,
                wdt,
                cap=Qt.PenCapStyle.FlatCap,
            )
        ri = r - wdt - 14
        p.setPen(QPen(_q(_mix(BG, self.acc, 0.3)), 1))
        p.drawEllipse(QPointF(cx, cy), ri, ri)
        self.text(
            p,
            cx,
            cy - 8,
            value,
            self.font("display", r / 2),
            TEXT,
            align="c",
            valign="mid",
            glow=True,
        )
        self.text(
            p,
            cx,
            cy + r / 3 + 4,
            label,
            self.font("mono", max(16, r / 7)),
            col,
            align="c",
            valign="mid",
        )
        if sub:
            self.text(
                p,
                cx,
                cy + r + 44,
                sub,
                self.font("mono", 20),
                DIM,
                align="c",
                valign="mid",
            )

    def graph(
        self,
        p: QPainter,
        r: QRectF,
        values,
        lo: float,
        hi: float,
        col,
        fmt: Callable,
        labels: Optional[list] = None,
        bars: Optional[list] = None,
        dots_every: int = 0,
    ) -> None:
        """An area chart with a hover crosshair; values drawn left → right across r."""
        n = len(values)
        if n < 2:
            return
        rng = max(hi - lo, 1e-6)
        pts = [
            QPointF(
                r.left() + r.width() * i / (n - 1),
                r.bottom() - r.height() * (v - lo) / rng,
            )
            for i, v in enumerate(values)
        ]
        if bars:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_q(self.acc, 70))
            bw = max(3.0, r.width() / n * 0.5)
            for i, b in enumerate(bars[:n]):
                if b:
                    h = 34 * b / 100
                    p.drawRect(QRectF(pts[i].x() - bw / 2, r.bottom() + 28 - h, bw, h))
        area = QPainterPath(QPointF(r.left(), r.bottom()))
        for pt in pts:
            area.lineTo(pt)
        area.lineTo(QPointF(r.right(), r.bottom()))
        area.closeSubpath()
        g = QLinearGradient(0, r.top(), 0, r.bottom())
        g.setColorAt(0, _q(col, 70))
        g.setColorAt(1, _q(col, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawPath(area)
        line = QPainterPath(pts[0])
        for pt in pts[1:]:
            line.lineTo(pt)
        self.stroke(p, lambda: p.drawPath(line), col, 3.5)
        if dots_every:
            for i in range(0, n, dots_every):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_q(TEXT))
                p.drawEllipse(pts[i], 4.5, 4.5)
                self.text(
                    p,
                    pts[i].x(),
                    pts[i].y() - 12,
                    fmt(values[i]),
                    self.font("mono", 17),
                    TEXT,
                    align="c",
                    valign="base",
                )
        if labels:
            step = max(1, n // 8)
            for i in range(0, n, step):
                self.text(
                    p,
                    pts[i].x(),
                    r.bottom() + 34,
                    labels[i],
                    self.font("mono", 15),
                    DIM,
                    align="c",
                )
        # hover
        hr = QRectF(r.left() - 10, r.top() - 30, r.width() + 20, r.height() + 60)
        if hr.contains(self.mouse):
            i = int(round((self.mouse.x() - r.left()) / r.width() * (n - 1)))
            i = max(0, min(n - 1, i))
            pt = pts[i]
            p.setPen(QPen(_q(TEXT, 110), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(pt.x(), r.top() - 10), QPointF(pt.x(), r.bottom()))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_q(col, 90))
            p.drawEllipse(pt, 12, 12)
            p.setBrush(_q(TEXT))
            p.drawEllipse(pt, 6, 6)
            tag = fmt(values[i]) + (f"  ·  {labels[i]}" if labels else "")
            fm = QFontMetricsF(self.font("mono", 20))
            tw = fm.horizontalAdvance(tag) + 24
            tx = max(r.left(), min(pt.x() - tw / 2, r.right() - tw))
            ty = max(r.top() - 44, pt.y() - 56)
            box = QRectF(tx, ty, tw, 34)
            p.setPen(QPen(_q(col), 1.5))
            p.setBrush(_q(_mix(BG, col, 0.2), 235))
            p.drawRoundedRect(box, 6, 6)
            self.text(
                p,
                box.center().x(),
                box.center().y(),
                tag,
                self.font("mono", 20),
                TEXT,
                align="c",
                valign="mid",
            )


# ─────────────────────────────────────────────────────────────────────────────
# System diagnostics
# ─────────────────────────────────────────────────────────────────────────────

# WHY THE PROCESS LIST IS READ BY ANOTHER PROCESS
#     psutil keeps Python's interpreter lock while Windows answers it, and a few
#     processes (antivirus, protected services) take 20-30 ms each. JARVIS's
#     voice is written to the speaker from Python too, so a walk over ~300
#     processes inside JARVIS was heard as the voice stuttering. A separate
#     process has its own interpreter lock: it can take as long as it likes.
#     It prints one JSON line of the five busiest processes every 2.5 s and ends
#     by itself when JARVIS does (or when the board closes and it is killed).
_PROC_SCRIPT = r"""
import json, sys, time, psutil
parent = int(sys.argv[1]); ncpu = psutil.cpu_count() or 1
prev = {}; cache = {}
while psutil.pid_exists(parent):
    now = time.time(); rows = []; nxt = {}
    for pid in psutil.pids():
        if pid == 0:
            continue
        try:
            p = cache.get(pid) or cache.setdefault(pid, psutil.Process(pid))
            with p.oneshot():
                name = p.name(); ct = p.cpu_times(); mi = p.memory_info()
        except Exception:
            cache.pop(pid, None); continue
        if not name or name.lower() in ("system idle process", "idle"):
            continue
        tot = ct.user + ct.system; nxt[pid] = (tot, now); last = prev.get(pid); cpu = None
        if last and now > last[1]:
            cpu = max(0.0, (tot - last[0]) / (now - last[1]) * 100 / ncpu)
        rows.append({"pid": pid, "name": name, "cpu": cpu, "mb": mi.rss / 1048576})
    first = not prev; prev = nxt
    for pid in list(cache):
        if pid not in nxt:
            cache.pop(pid, None)
    rows.sort(key=(lambda r: r["mb"]) if first else (lambda r: (r["cpu"] or 0.0, r["mb"])), reverse=True)
    try:
        print(json.dumps(rows[:5]), flush=True)
    except Exception:
        break
    time.sleep(0.3 if first else 2.5)
"""


class _Sampler:
    """psutil in two threads while the board is open: the vitals once a second,
    and the process list on its own — walking ~300 processes takes 1-2 s on
    Windows, which must never hold the rings back."""

    def __init__(self):
        self.lock = threading.Lock()
        self.data: dict = {}
        self.top: list = []
        self.hist = {
            k: deque([0.0] * 60, maxlen=60)
            for k in ("cpu", "ram", "gpu", "disk", "down", "up")
        }
        self.stop = threading.Event()
        self.threads: list = []
        self.prev: dict = {}  # pid -> (cpu seconds, when)
        self.procs: dict = {}  # pid -> psutil.Process, reused between walks
        self.slow: set = set()  # pids too slow to ask (see _scan)

    def start(self) -> None:
        if not self.stop.is_set() and any(t.is_alive() for t in self.threads):
            return
        self.stop = stop = (
            threading.Event()
        )  # threads of an earlier open keep their own
        self.threads = [
            threading.Thread(
                target=self._run, args=(stop,), name="hud-vitals", daemon=True
            ),
            threading.Thread(
                target=self._run_procs, args=(stop,), name="hud-procs", daemon=True
            ),
        ]
        for t in self.threads:
            t.start()

    def _run(self, stop: threading.Event) -> None:
        psutil.cpu_percent(None)
        psutil.cpu_percent(None, percpu=True)
        net0, t0 = psutil.net_io_counters(), time.time()
        stop.wait(0.35)  # a short first window: numbers at once
        tick = 0
        disk: dict = {}
        while not stop.is_set():
            try:
                if tick % 10 == 0:
                    du = psutil.disk_usage("C:\\" if psutil.WINDOWS else "/")
                    disk = {"disk": du.percent, "disk_free": du.free / 1024**3}
                cpu = psutil.cpu_percent(None)
                cores = psutil.cpu_percent(None, percpu=True)
                vm = psutil.virtual_memory()
                g = gpu_load()
                gpu = g if g is not None else -1.0
                net1, t1 = psutil.net_io_counters(), time.time()
                dt = max(0.2, t1 - t0)
                down = (net1.bytes_recv - net0.bytes_recv) * 8 / dt / 1e6
                up = (net1.bytes_sent - net0.bytes_sent) * 8 / dt / 1e6
                net0, t0 = net1, t1
                freq = psutil.cpu_freq()
                d = {
                    "cpu": cpu,
                    "cores": cores,
                    "ram": vm.percent,
                    "ram_used": vm.used / 1024**3,
                    "ram_total": vm.total / 1024**3,
                    "gpu": gpu if gpu >= 0 else None,
                    "down": down,
                    "up": up,
                    "ghz": freq.current / 1000 if freq else None,
                    "nproc": len(psutil.pids()),
                    "uptime": time.time() - psutil.boot_time(),
                    **disk,
                }
                try:
                    b = psutil.sensors_battery()
                    if b:
                        d["batt"], d["plugged"] = b.percent, b.power_plugged
                except Exception:
                    pass
                with self.lock:
                    self.data = d
                    if tick == 0:  # start the history level, not from zero
                        for k, v in (
                            ("cpu", cpu),
                            ("ram", vm.percent),
                            ("gpu", max(gpu, 0)),
                            ("disk", disk.get("disk", 0)),
                        ):
                            self.hist[k].extend([v] * 59)
                    self.hist["cpu"].append(cpu)
                    self.hist["ram"].append(vm.percent)
                    self.hist["gpu"].append(gpu if gpu >= 0 else 0)
                    self.hist["disk"].append(disk.get("disk", 0))
                    self.hist["down"].append(down)
                    self.hist["up"].append(up)
            except Exception as exc:
                _once("sampler", exc)
            tick += 1
            stop.wait(1.0)

    def _run_procs(self, stop: threading.Event) -> None:
        """The process list comes from a small helper process (see _PROC_SCRIPT);
        this thread only reads its lines. If the helper cannot start, the walk
        runs here instead, pausing often so the voice still gets through."""
        import json as _json
        import subprocess
        import sys

        child = None
        try:
            exe = sys.executable
            if exe.lower().endswith("pythonw.exe"):
                alt = exe[: -len("pythonw.exe")] + "python.exe"
                exe = alt if Path(alt).exists() else exe
            flags = 0x08000000 if psutil.WINDOWS else 0  # CREATE_NO_WINDOW
            child = subprocess.Popen(
                [exe, "-c", _PROC_SCRIPT, str(psutil.Process().pid)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=flags,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            def reap():
                stop.wait()
                try:
                    child.kill()
                except Exception:
                    pass

            threading.Thread(target=reap, name="hud-procs-reaper", daemon=True).start()
            for line in child.stdout:
                if stop.is_set():
                    break
                try:
                    rows = _json.loads(line)
                    with self.lock:
                        self.top = rows
                except Exception:
                    continue
            if stop.is_set():
                return
        except Exception as exc:
            _once("process helper", exc)
        finally:
            if child is not None and child.poll() is None:
                try:
                    child.kill()
                except Exception:
                    pass
        # fallback: in this process, politely
        while not stop.is_set():
            try:
                rows = self._scan(stop)
                with self.lock:
                    self.top = rows
            except Exception as exc:
                _once("processes", exc)
            stop.wait(2.5)

    def _scan(self, stop: Optional[threading.Event] = None) -> list:
        """The five busiest processes.

        WHY IT IS WRITTEN LIKE THIS
            psutil keeps the interpreter locked while it asks Windows about a
            process, and a few processes (antivirus, protected services) take
            20-30 ms each to answer. Walked in one go, the ~300 of them held
            JARVIS's voice thread off the speaker long enough to be heard. So
            the walk pauses every few processes to let the voice through, and a
            process that is slow to answer once is not asked again.

        CPU % comes from the change in each process's CPU time since the last
        walk, so the very first walk (no history yet) is ordered by memory, and
        shows at once.
        """
        ncpu = psutil.cpu_count() or 1
        rows, prev, seen = [], {}, set()
        for n, pid in enumerate(psutil.pids()):
            if stop is not None and stop.is_set():
                return self.top
            if n % 8 == 7:
                time.sleep(0.002)  # let the voice thread in
            if pid == 0 or pid in self.slow:
                continue
            seen.add(pid)
            t0 = time.perf_counter()
            try:
                proc = self.procs.get(pid)
                if proc is None:
                    proc = self.procs[pid] = psutil.Process(pid)
                with proc.oneshot():
                    name = proc.name()
                    ct = proc.cpu_times()
                    mi = proc.memory_info()
            except Exception:
                self.procs.pop(pid, None)
                continue
            finally:
                if time.perf_counter() - t0 > 0.006:
                    self.slow.add(pid)
            if not name or name.lower() in ("system idle process", "idle"):
                continue
            now = time.time()
            total = ct.user + ct.system
            prev[pid] = (total, now)
            last = self.prev.get(pid)
            cpu = None
            if last and now > last[1]:
                cpu = max(0.0, (total - last[0]) / (now - last[1]) * 100 / ncpu)
            rows.append({"pid": pid, "name": name, "cpu": cpu, "mb": mi.rss / 1024**2})
        for pid in list(self.procs):
            if pid not in seen:
                self.procs.pop(pid, None)
        first = not self.prev
        self.prev = prev
        if first:
            rows.sort(key=lambda r: r["mb"], reverse=True)
        else:
            rows.sort(key=lambda r: (r["cpu"] or 0.0, r["mb"]), reverse=True)
        return rows[:5]

    def snapshot(self) -> tuple[dict, dict]:
        with self.lock:
            return {**self.data, "top": list(self.top)}, {
                k: list(v) for k, v in self.hist.items()
            }


class SystemBoard(Board):
    METRICS = ("cpu", "ram", "gpu", "disk")

    def __init__(self, acc, parent=None):
        super().__init__(acc, parent)
        self.sampler = _Sampler()
        self.shown = {k: 0.0 for k in self.METRICS}
        self.shown_cores: list = []
        self.selected = "cpu"
        self.tab = "history"
        self.proc_sel: Optional[int] = None
        self.proc_info: dict = {}

    def on_open(self) -> None:
        self.sampler.start()

    def animating(self) -> bool:
        return getattr(self, "_moving", True)

    def on_close(self) -> None:
        self.sampler.stop.set()

    def title(self):
        d, _ = self.sampler.snapshot()
        up = d.get("uptime", 0)
        import platform

        return (
            "System Diagnostics",
            f"{platform.node()}  ·  {platform.system()} {platform.release()}  ·  "
            f"uptime {int(up // 3600)}h {int(up % 3600 // 60):02d}m  ·  {time.strftime('%H:%M:%S')}",
        )

    def clicked(self, key: str) -> None:
        if key.startswith("ring:"):
            self.selected = key[5:]
            self.tab = "history"
        elif key.startswith("tab:"):
            self.tab = key[4:]
        elif key.startswith("proc:"):
            pid = int(key[5:])
            self.proc_sel = None if self.proc_sel == pid else pid
            self.proc_info = {}
            if self.proc_sel is not None:
                try:
                    pr = psutil.Process(pid)
                    with pr.oneshot():
                        self.proc_info = {
                            "threads": pr.num_threads(),
                            "since": time.strftime(
                                "%H:%M", time.localtime(pr.create_time())
                            ),
                            "status": pr.status(),
                        }
                except Exception:
                    self.proc_info = {"status": "ended"}

    def draw(self, p: QPainter, dt: float, now: float) -> None:
        d, hist = self.sampler.snapshot()
        moving = False
        for k in self.METRICS:
            v = float(d.get(k) or 0)
            self.shown[k] = _approach(self.shown[k], v, dt)
            moving = moving or abs(self.shown[k] - v) > 0.3
        cores = d.get("cores") or []
        if self.tab == "cores" and len(self.shown_cores) == len(cores):
            moving = moving or any(
                abs(a - b) > 0.5 for a, b in zip(self.shown_cores, cores)
            )
        self._moving = moving
        rings = [
            ("cpu", "CPU", f"{d['ghz']:.2f} GHz" if d.get("ghz") else ""),
            (
                "ram",
                "RAM",
                f"{d.get('ram_used', 0):.1f} / {d.get('ram_total', 0):.1f} GB",
            ),
        ]
        if d.get("gpu") is not None or not d:
            rings.append(("gpu", "GPU", "NVIDIA"))
        rings.append(("disk", "DISK", f"{d.get('disk_free', 0):.1f} GB free"))
        n = len(rings)
        for i, (k, label, sub) in enumerate(rings):
            cx = 70 + 1460 * (i + 0.5) / n
            r = 125
            over = self.hit(
                QRectF(cx - r - 20, 345 - r - 20, 2 * r + 40, 2 * r + 80), f"ring:{k}"
            )
            self.ring(
                p,
                cx,
                345,
                r,
                self.shown[k],
                label,
                f"{self.shown[k]:.0f}%",
                sub,
                selected=(k == self.selected),
                hover=over,
            )

        # ── left panel: history of the selected metric, or the cores ────────
        L = QRectF(70, 560, 720, 370)
        self.panel(p, L)
        tx = 100
        for tab, name in (
            ("history", f"HISTORY · {self.selected.upper()}"),
            ("cores", "CORES"),
        ):
            fm = QFontMetricsF(self.font("mono", 22))
            tr = QRectF(tx - 10, 574, fm.horizontalAdvance(name) + 20, 38)
            over = self.hit(tr, f"tab:{tab}")
            on = self.tab == tab
            if on or over:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_q(self.acc, 45 if on else 22))
                p.drawRoundedRect(tr, 5, 5)
            self.text(
                p,
                tx,
                593,
                name,
                self.font("mono", 22),
                self.acc if on else DIM,
                valign="mid",
            )
            tx += tr.width() + 20
        if self.tab == "history":
            vals = hist.get(self.selected, [])
            col = _level(vals[-1] if vals else 0, self.acc)
            self.graph(
                p, QRectF(100, 660, 660, 230), vals, 0, 100, col, lambda v: f"{v:.0f}%"
            )
            self.text(p, 100, 900, "60 s", self.font("mono", 15), DIM)
            self.text(p, 760, 900, "now", self.font("mono", 15), DIM, align="r")
        else:
            cores = d.get("cores") or []
            if len(self.shown_cores) != len(cores):
                self.shown_cores = [0.0] * len(cores)
            self.shown_cores = [
                _approach(a, b, dt) for a, b in zip(self.shown_cores, cores)
            ]
            if cores:
                cols = min(len(cores), 16)
                rows = math.ceil(len(cores) / cols)
                bw = (660 - (cols - 1) * 8) / cols
                bh = (250 - (rows - 1) * 22) / rows
                for i, v in enumerate(self.shown_cores):
                    rr, k = divmod(i, cols)
                    x = 100 + k * (bw + 8)
                    yb = 640 + (rr + 1) * bh + rr * 22
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(_q(_mix(BG, self.acc, 0.12)))
                    p.drawRect(QRectF(x, yb - bh, bw, bh))
                    h = bh * v / 100
                    col = _level(v, self.acc)
                    g = QLinearGradient(0, yb - bh, 0, yb)
                    g.setColorAt(0, _q(col))
                    g.setColorAt(1, _q(col, 120))
                    p.setBrush(QBrush(g))
                    p.drawRect(QRectF(x, yb - h, bw, h))
                    self.text(
                        p,
                        x + bw / 2,
                        yb + 4,
                        str(i),
                        self.font("mono", 13),
                        DIM,
                        align="c",
                    )

        # ── right panel: network, counters, processes ───────────────────────
        R = QRectF(820, 560, 710, 370)
        self.panel(p, R)
        self.text(p, 850, 582, "LIVE", self.font("mono", 22), self.acc)
        stats = [
            ("▼ DOWN", f"{d.get('down', 0):.2f} Mb/s", hist.get("down")),
            ("▲ UP", f"{d.get('up', 0):.2f} Mb/s", hist.get("up")),
            ("PROC", str(d.get("nproc", "–")), None),
        ]
        if d.get("batt") is not None:
            stats.append(
                ("BATT", f"{d['batt']:.0f}%{' +' if d.get('plugged') else ''}", None)
            )
        cw = 650 / len(stats)
        for i, (k, v, h) in enumerate(stats):
            x = 850 + i * cw
            self.text(p, x, 622, k, self.font("mono", 17), DIM)
            self.text(p, x, 644, v, self.font("display", 30), TEXT, glow=True)
            if h:
                hi = max(max(h[-30:]), 0.5)
                pts = [
                    QPointF(x + (cw - 30) * j / 29, 704 - 18 * val / hi)
                    for j, val in enumerate(h[-30:])
                ]
                path = QPainterPath(pts[0])
                for q in pts[1:]:
                    path.lineTo(q)
                self.stroke(
                    p, lambda path=path: p.drawPath(path), self.acc, 1.8, glow=False
                )

        self.text(
            p,
            850,
            722,
            "TOP PROCESSES  ·  click for details",
            self.font("mono", 16),
            DIM,
        )
        top = d.get("top") or []
        for i, pr in enumerate(top[:5]):
            y = 748 + i * 32
            r = QRectF(840, y - 3, 670, 30)
            over = self.hit(r, f"proc:{pr['pid']}")
            sel = pr["pid"] == self.proc_sel
            if over or sel:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_q(self.acc, 45 if sel else 20))
                p.drawRoundedRect(r, 4, 4)
            self.text(
                p,
                852,
                y + 12,
                pr["name"][:26],
                self.font("mono", 20),
                TEXT,
                valign="mid",
            )
            bar = QRectF(1190, y + 6, 130, 12)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_q(_mix(BG, self.acc, 0.15)))
            p.drawRect(bar)
            cpu = pr["cpu"]
            if cpu is not None:
                p.setBrush(_q(_level(cpu * 4, self.acc)))
                p.drawRect(
                    QRectF(
                        bar.left(),
                        bar.top(),
                        bar.width() * min(1, cpu * 4 / 100),
                        bar.height(),
                    )
                )
            self.text(
                p,
                1500,
                y + 12,
                f"{'–' if cpu is None else f'{cpu:.1f}%'}  {pr['mb']:.0f} MB",
                self.font("mono", 18),
                DIM,
                align="r",
                valign="mid",
            )
        if self.proc_sel is not None:
            info = self.proc_info
            line = f"PID {self.proc_sel}"
            if info.get("threads") is not None:
                line += f"  ·  {info['threads']} threads  ·  since {info['since']}  ·  {info['status']}"
            elif info.get("status"):
                line += f"  ·  {info['status']}"
            self.text(p, 852, 912, line, self.font("mono", 17), self.acc, valign="mid")


# ─────────────────────────────────────────────────────────────────────────────
# Weather
# ─────────────────────────────────────────────────────────────────────────────

_WMO = {
    0: ("Clear", "sun"),
    1: ("Mostly clear", "sun"),
    2: ("Partly cloudy", "partly"),
    3: ("Overcast", "cloud"),
    45: ("Fog", "fog"),
    48: ("Rime fog", "fog"),
    51: ("Light drizzle", "rain"),
    53: ("Drizzle", "rain"),
    55: ("Heavy drizzle", "rain"),
    56: ("Freezing drizzle", "rain"),
    57: ("Freezing drizzle", "rain"),
    61: ("Light rain", "rain"),
    63: ("Rain", "rain"),
    65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "rain"),
    67: ("Freezing rain", "rain"),
    71: ("Light snow", "snow"),
    73: ("Snow", "snow"),
    75: ("Heavy snow", "snow"),
    77: ("Snow grains", "snow"),
    80: ("Showers", "rain"),
    81: ("Showers", "rain"),
    82: ("Violent showers", "rain"),
    85: ("Snow showers", "snow"),
    86: ("Snow showers", "snow"),
    95: ("Thunderstorm", "storm"),
    96: ("Thunderstorm, hail", "storm"),
    99: ("Thunderstorm, hail", "storm"),
}


class TradingBoard(Board):
    def __init__(self, acc, parent=None):
        super().__init__(acc, parent)
        self.symbol = ""
        self.price = None
        self.change_pct = None
        self.bias = "Neutral"
        self.summary = ""
        self.headlines: list = []

    def _fit_text(
        self, text: str, font: QFont, max_width: float, suffix: str = "..."
    ) -> str:
        if max_width <= 0:
            return ""
        fm = QFontMetricsF(font)
        raw = str(text or "")
        if fm.horizontalAdvance(raw) <= max_width:
            return raw
        compact = raw.rstrip()
        while compact and fm.horizontalAdvance(compact + suffix) > max_width:
            compact = compact[:-1].rstrip()
        return (compact + suffix) if compact else suffix

    def set_data(self, payload: dict) -> None:
        self.symbol = str(payload.get("symbol") or "").upper()
        self.price = payload.get("price")
        self.change_pct = payload.get("change_pct")
        self.bias = str(payload.get("bias") or "Neutral")
        self.summary = str(payload.get("summary") or "")
        self.headlines = payload.get("headlines") or []

    def title(self):
        if not self.symbol:
            return ("Trading", "Market intelligence")
        return (
            self.symbol,
            f"{self.price if self.price is not None else '--'}  ·  {self.bias}",
        )

    def draw(self, p: QPainter, dt: float, now: float) -> None:
        self.text(p, 70, 220, "MARKET", self.font("mono", 22), DIM)
        if self.symbol:
            self.text(
                p, 70, 270, self.symbol, self.font("display", 86), TEXT, glow=True
            )
        price = self.price if self.price is not None else "N/A"
        self.text(p, 70, 355, str(price), self.font("display", 54), self.acc, glow=True)
        if self.change_pct is not None:
            pct = float(self.change_pct)
            sign = "+" if pct >= 0 else ""
            col = HOT if pct < 0 else (56, 214, 255)
            self.text(
                p, 430, 355, f"{sign}{pct:.2f}%", self.font("mono", 22), col, glow=True
            )

        panel = QRectF(70, 430, 700, 190)
        self.panel(p, panel, 0.14, active=True)
        self.text(p, 90, 455, "TRADE VIEW", self.font("mono", 16), DIM)
        self.text(
            p, 90, 490, self.bias.upper(), self.font("display", 30), self.acc, glow=True
        )
        if self.summary:
            summary = self._fit_text(
                self.summary, self.font("mono", 18), panel.width() - 50
            )
            self.text(p, 90, 535, summary, self.font("mono", 18), TEXT)

        right = QRectF(820, 220, 690, 400)
        self.panel(p, right, 0.12, active=False)
        self.text(p, 850, 250, "HEADLINES", self.font("mono", 18), DIM)
        rows = self.headlines[:5]
        y = 290
        p.save()
        p.setClipRect(right)
        for idx, item in enumerate(rows, 1):
            title = str(item.get("title") or "Untitled")
            date = str(item.get("date") or "recent")
            if y > 570:
                break
            title_text = self._fit_text(
                f"{idx}. {title}", self.font("mono", 18), right.width() - 40
            )
            self.text(p, 850, y, title_text, self.font("mono", 18), TEXT)
            self.text(
                p,
                850,
                y + 24,
                self._fit_text(date, self.font("mono", 12), right.width() - 40),
                self.font("mono", 12),
                DIM,
            )
            y += 58
        if not rows:
            self.text(
                p,
                850,
                310,
                "No recent headlines for this symbol.",
                self.font("mono", 18),
                DIM,
            )
        p.restore()

    def on_open(self) -> None:
        pass

    def on_close(self) -> None:
        pass


class WeatherBoard(Board):
    def __init__(self, acc, parent=None):
        super().__init__(acc, parent)
        self.place: dict = {}
        self.fc: dict = {}
        self.day = 0
        self.curve: list = []
        self.particles: list = []
        self.idle_ms = 100  # the icon still moves, gently
        self.refresh = QTimer(self)
        self.refresh.setInterval(10 * 60 * 1000)
        self.refresh.timeout.connect(self._refetch)

    def animating(self) -> bool:
        return bool(self.particles) or getattr(self, "_moving", True)

    def set_data(self, place: dict, fc: dict) -> None:
        self.place, self.fc, self.day = place, fc, 0
        self.particles = []

    def on_open(self) -> None:
        self.refresh.start()

    def on_close(self) -> None:
        self.refresh.stop()

    def _refetch(self) -> None:
        place = dict(self.place)

        def job():
            try:
                fc = forecast(place["latitude"], place["longitude"])
                _gui(
                    lambda: self.place.get("name") == place.get("name")
                    and setattr(self, "fc", fc)
                )
            except Exception as exc:
                _once("weather refresh", exc)

        threading.Thread(target=job, daemon=True).start()

    def _local(self) -> time.struct_time:
        return time.gmtime(time.time() + float(self.fc.get("utc_offset_seconds") or 0))

    def title(self):
        name = self.place.get("name", "")
        region = ", ".join(
            x for x in (self.place.get("admin1"), self.place.get("country")) if x
        )
        return (
            f"Weather · {name}",
            f"{region}  ·  local {time.strftime('%a %d %b  %H:%M:%S', self._local())}  ·  Open-Meteo",
        )

    def clicked(self, key: str) -> None:
        if key.startswith("day:"):
            self.day = int(key[4:])

    # ── icons (animated) ─────────────────────────────────────────────────────
    def icon(
        self, p: QPainter, kind: str, cx, cy, s, now: float, night: bool = False
    ) -> None:
        cloud_col = _mix(TEXT, self.acc, 0.25)

        def sun(x, y, r):
            if night:
                p.setPen(Qt.PenStyle.NoPen)
                g = QRadialGradient(QPointF(x, y), r * 2)
                g.setColorAt(0.4, QColor(160, 170, 220, 70))
                g.setColorAt(1, QColor(160, 170, 220, 0))
                p.setBrush(QBrush(g))
                p.drawEllipse(QPointF(x, y), r * 2, r * 2)
                moon = QPainterPath()
                moon.addEllipse(QPointF(x, y), r, r)
                cut = QPainterPath()
                cut.addEllipse(QPointF(x + r * 0.5, y - r * 0.35), r * 0.85, r * 0.85)
                p.setBrush(QColor(222, 230, 255))
                p.drawPath(moon.subtracted(cut))
                return
            rot = now * 20
            for i in range(8):
                a = math.radians(i * 45 + rot)
                pulse = 1 + 0.08 * math.sin(now * 3 + i)
                self.stroke(
                    p,
                    lambda a=a, pulse=pulse: p.drawLine(
                        QPointF(x + r * 1.35 * math.cos(a), y + r * 1.35 * math.sin(a)),
                        QPointF(
                            x + r * 1.8 * pulse * math.cos(a),
                            y + r * 1.8 * pulse * math.sin(a),
                        ),
                    ),
                    SUN,
                    max(2.0, r / 5),
                    glow=False,
                )
            g = QRadialGradient(QPointF(x, y), r * 1.6)
            g.setColorAt(0.6, _q(SUN, 90))
            g.setColorAt(1, _q(SUN, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawEllipse(QPointF(x, y), r * 1.6, r * 1.6)
            p.setBrush(_q(SUN))
            p.drawEllipse(QPointF(x, y), r, r)

        def cloud(x, y, r):
            x += math.sin(now * 0.8) * r * 0.06
            path = QPainterPath()
            path.addRect(QRectF(x - 0.55 * r, y + 0.1 * r, 1.15 * r, 0.6 * r))
            for dx, dy, rr in ((-0.55, 0.15, 0.55), (0.0, -0.2, 0.75), (0.6, 0.1, 0.6)):
                blob = QPainterPath()
                blob.addEllipse(QPointF(x + dx * r, y + dy * r), rr * r, rr * r)
                path = path.united(blob)
            p.setPen(QPen(_q(cloud_col, 60), max(4.0, r / 8)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
            g = QLinearGradient(0, y - r, 0, y + r * 0.7)
            g.setColorAt(0, _q(_mix(cloud_col, (255, 255, 255), 0.4)))
            g.setColorAt(1, _q(cloud_col))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawPath(path)

        if kind == "sun":
            sun(cx, cy, s * 0.55)
        elif kind == "partly":
            sun(cx + s * 0.35, cy - s * 0.3, s * 0.42)
            cloud(cx - s * 0.1, cy + s * 0.15, s * 0.6)
        elif kind == "cloud":
            cloud(cx, cy, s * 0.75)
        elif kind == "fog":
            cloud(cx, cy - s * 0.2, s * 0.6)
            for k in range(3):
                y = cy + s * (0.45 + k * 0.2)
                off = math.sin(now * 1.5 + k) * s * 0.12
                p.setPen(QPen(_q(DIM), max(2.0, s / 12), cap=Qt.PenCapStyle.RoundCap))
                p.drawLine(
                    QPointF(cx - s * 0.7 + off, y), QPointF(cx + s * 0.7 + off, y)
                )
        else:
            base = cy + s * 0.4
            if kind == "rain":
                for j, k in enumerate((-0.4, 0.0, 0.4)):
                    ph = (now * 1.6 + j * 0.33) % 1.0
                    x = cx + k * s - ph * s * 0.12
                    y = base + ph * s * 0.45
                    self.stroke(
                        p,
                        lambda x=x, y=y: p.drawLine(
                            QPointF(x, y), QPointF(x - s * 0.08, y + s * 0.2)
                        ),
                        self.acc,
                        max(2.0, s / 11),
                    )
            elif kind == "snow":
                for j, k in enumerate((-0.4, 0.0, 0.4)):
                    ph = (now * 0.6 + j * 0.33) % 1.0
                    x = cx + k * s + math.sin(now * 2 + j) * s * 0.06
                    y = base + ph * s * 0.5
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(_q(TEXT, int(255 * (1 - ph))))
                    p.drawEllipse(QPointF(x, y), s * 0.08, s * 0.08)
            elif kind == "storm":
                if (now % 2.2) < 0.18:
                    pts = [
                        QPointF(cx + s * 0.05, base - s * 0.05),
                        QPointF(cx - s * 0.2, base + s * 0.3),
                        QPointF(cx + s * 0.05, base + s * 0.3),
                        QPointF(cx - s * 0.12, base + s * 0.62),
                    ]
                    path = QPainterPath(pts[0])
                    for q in pts[1:]:
                        path.lineTo(q)
                    self.stroke(
                        p, lambda: p.drawPath(path), (255, 220, 80), max(3.0, s / 8)
                    )
            cloud(cx, cy - s * 0.25, s * 0.65)

    def _weather_fx(self, p: QPainter, kind: str, dt: float) -> None:
        """Rain or snow across the whole board, behind everything else."""
        if kind not in ("rain", "snow", "storm"):
            self.particles = []
            return
        target = 70 if kind != "snow" else 60
        while len(self.particles) < target:
            self.particles.append(
                [random.uniform(0, W), random.uniform(-H, H), random.uniform(0.5, 1.0)]
            )
        snow = kind == "snow"
        for pt in self.particles:
            if snow:
                pt[1] += 70 * pt[2] * dt
                pt[0] += math.sin(pt[1] / 40) * 20 * dt
            else:
                pt[1] += 900 * pt[2] * dt
                pt[0] -= 120 * pt[2] * dt
            if pt[1] > H:
                pt[0], pt[1] = random.uniform(0, W + 200), random.uniform(-80, 0)
        if snow:
            p.setPen(Qt.PenStyle.NoPen)
            for x, y, z in self.particles:
                p.setBrush(_q(TEXT, int(90 * z)))
                p.drawEllipse(QPointF(x, y), 2.5 * z, 2.5 * z)
        else:
            p.setPen(QPen(_q(self.acc, 55), 1.4))
            p.drawLines(
                [QLineF(x, y, x + 5 * z, y - 38 * z) for x, y, z in self.particles]
            )

    def draw(self, p: QPainter, dt: float, now: float) -> None:
        fc = self.fc
        if not fc:
            return
        cur, day, hr = fc["current"], fc["daily"], fc["hourly"]
        label, kind = _WMO.get(cur.get("weather_code", 0), ("—", "cloud"))
        night = not cur.get("is_day", 1)
        self._weather_fx(p, kind, dt)

        # ── now ──────────────────────────────────────────────────────────────
        self.icon(
            p, kind, 230, 330, 130, now, night=night and kind in ("sun", "partly")
        )
        self.text(
            p,
            415,
            200,
            f"{round(cur['temperature_2m'])}°",
            self.font("display", 200),
            TEXT,
            glow=True,
        )
        self.text(
            p,
            425,
            440,
            label.upper(),
            self.font("display", 38),
            self.acc,
            valign="mid",
            glow=True,
        )
        self.text(
            p,
            425,
            478,
            f"feels like {round(cur['apparent_temperature'])}°",
            self.font("mono", 22),
            DIM,
        )

        # ── details: now, or the selected day ───────────────────────────────
        i = self.day
        wd = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][
            int(((cur.get("wind_direction_10m") or 0) + 22.5) // 45) % 8
        ]
        uv = day.get("uv_index_max", [None] * 7)[i]
        if i == 0:
            details = [
                ("HUMIDITY", f"{cur.get('relative_humidity_2m', 0)}%"),
                ("WIND", f"{round(cur.get('wind_speed_10m', 0))} km/h {wd}"),
                ("PRESSURE", f"{round(cur.get('pressure_msl', 0))} hPa"),
            ]
        else:
            details = [
                ("HIGH", f"{round(day['temperature_2m_max'][i])}°"),
                ("LOW", f"{round(day['temperature_2m_min'][i])}°"),
                ("RAIN", f"{day['precipitation_probability_max'][i] or 0}%"),
            ]
        details += [
            ("UV MAX", f"{uv:.0f}" if uv is not None else "—"),
            ("SUNRISE", (day["sunrise"][i] or "")[-5:]),
            ("SUNSET", (day["sunset"][i] or "")[-5:]),
        ]
        self.panel(p, QRectF(950, 200, 580, 310))
        dname = (
            "NOW"
            if i == 0
            else time.strftime("%A", time.strptime(day["time"][i], "%Y-%m-%d")).upper()
        )
        self.text(p, 1510, 214, dname, self.font("mono", 16), self.acc, align="r")
        for j, (k, v) in enumerate(details):
            x, y = 985 + (j % 2) * 280, 228 + (j // 2) * 95
            self.text(p, x, y, k, self.font("mono", 18), DIM)
            self.text(p, x, y + 24, v, self.font("display", 38), TEXT, glow=True)

        # ── hourly curve: the next 24 h, or the selected day ────────────────
        times = hr.get("time", [])
        if i == 0:
            ct = cur.get("time", "")[:13]
            start = next((k for k, t in enumerate(times) if t[:13] >= ct), 0)
        else:
            start = i * 24
        temps = hr["temperature_2m"][start : start + 24]
        rain = hr["precipitation_probability"][start : start + 24]
        labels = [t[-5:] for t in times[start : start + 24]]
        if len(self.curve) != len(temps):
            self.curve = list(temps)
        self.curve = [_approach(a, b, dt, 6) for a, b in zip(self.curve, temps)]
        self._moving = any(abs(a - b) > 0.05 for a, b in zip(self.curve, temps))
        if len(self.curve) >= 2:
            lo, hi = min(temps) - 1, max(temps) + 1
            self.graph(
                p,
                QRectF(90, 575, 1420, 100),
                self.curve,
                lo,
                hi,
                self.acc,
                lambda v: f"{v:.0f}°",
                labels=labels,
                bars=rain,
                dots_every=3,
            )

        # ── 7 days, clickable ───────────────────────────────────────────────
        days = day["time"]
        n = len(days)
        wlo, whi = min(day["temperature_2m_min"]), max(day["temperature_2m_max"])
        cw = 1440 / n
        for k in range(n):
            x = 80 + k * cw
            box = QRectF(x + 6, 750, cw - 12, 200)
            over = self.hit(box, f"day:{k}")
            sel = k == i
            lift = -6 if over and not sel else 0
            box.translate(0, lift)
            self.panel(
                p, box, 0.2 if sel else (0.13 if over else 0.07), active=sel or over
            )
            mx = box.center().x()
            dn = (
                "TODAY"
                if k == 0
                else time.strftime("%a", time.strptime(days[k], "%Y-%m-%d")).upper()
            )
            self.text(
                p,
                mx,
                766 + lift,
                dn,
                self.font("mono", 20),
                self.acc if sel else DIM,
                align="c",
            )
            self.icon(
                p,
                _WMO.get(day["weather_code"][k], ("", "cloud"))[1],
                mx,
                830 + lift,
                36,
                now if (sel or over) else 0.0,
            )
            tmax, tmin = day["temperature_2m_max"][k], day["temperature_2m_min"][k]
            bx0, bx1 = box.left() + 16, box.right() - 16
            span = max(whi - wlo, 1)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_q(_mix(BG, self.acc, 0.2)))
            p.drawRoundedRect(QRectF(bx0, 896 + lift, bx1 - bx0, 6), 3, 3)
            a = bx0 + (bx1 - bx0) * (tmin - wlo) / span
            b = bx0 + (bx1 - bx0) * (tmax - wlo) / span
            g = QLinearGradient(a, 0, max(b, a + 6), 0)
            g.setColorAt(0, _q(_mix(self.acc, (120, 160, 255), 0.5)))
            g.setColorAt(1, _q(_mix(self.acc, SUN, 0.35)))
            p.setBrush(QBrush(g))
            p.drawRoundedRect(QRectF(a, 895 + lift, max(b - a, 6), 8), 4, 4)
            self.text(p, bx0, 912 + lift, f"{round(tmin)}°", self.font("mono", 20), DIM)
            self.text(
                p,
                bx1,
                910 + lift,
                f"{round(tmax)}°",
                self.font("display", 24),
                TEXT,
                align="r",
            )
            pr = day["precipitation_probability_max"][k]
            if pr:
                self.text(
                    p,
                    mx,
                    876 + lift,
                    f"RAIN {pr}%",
                    self.font("mono", 15),
                    self.acc,
                    align="c",
                    valign="mid",
                )


# ─────────────────────────────────────────────────────────────────────────────
# Placement on the HUD
# ─────────────────────────────────────────────────────────────────────────────


class _HostWatch(QObject):
    def __init__(self, host: QWidget):
        super().__init__(host)
        self.host = host

    def eventFilter(self, obj, ev) -> bool:
        try:
            if ev.type() == QEvent.Type.Resize:
                for b in _boards.values():
                    if not sip.isdeleted(b):
                        b.setGeometry(self.host.rect())
        except Exception as exc:
            _once("host resize", exc)
        return False

    def page_changed(self, *_a) -> None:
        # a video, the camera or a picture took the centre
        try:
            for b in _boards.values():
                if not sip.isdeleted(b) and b.isVisible():
                    b.close_view()
        except Exception as exc:
            _once("host page", exc)


_boards: dict = {}
_watch: Optional[_HostWatch] = None


def _host(player) -> Optional[QWidget]:
    win = getattr(player, "_win", None) if player is not None else None
    host = getattr(win, "_hud_cam_stack", None)
    if isinstance(host, QWidget) and not sip.isdeleted(host):
        return host
    return None


def _open(
    player,
    kind: str,
    factory: Callable[[Any, Optional[QWidget]], Board],
    setup: Optional[Callable[[Board], None]] = None,
) -> bool:
    global _watch
    host = _host(player)
    if host is None:
        return False
    if hasattr(host, "currentIndex") and host.currentIndex() != 0:
        # a picture, video or the camera was up: it gives way, sound and all
        try:
            player.stop_video()
        except Exception:
            pass
        host.setCurrentIndex(0)  # now, before the board shows, whatever the UI queued
    acc = accent()
    b = _boards.get(kind)
    if b is not None and (sip.isdeleted(b) or b.acc != acc):
        if not sip.isdeleted(b):
            b.deleteLater()
        b = None
    if b is None:
        b = factory(acc, host)
        _boards[kind] = b
    if _watch is None or sip.isdeleted(_watch):
        _watch = _HostWatch(host)
        host.installEventFilter(_watch)
        if hasattr(host, "currentChanged"):
            host.currentChanged.connect(_watch.page_changed)
    for k, other in _boards.items():  # one board at a time
        if k != kind and not sip.isdeleted(other) and other.isVisible():
            other.close_view()
    try:  # and not under Planet Watch's globe
        import sys

        live = sys.modules.get("plugins._planet_live")
        v = live.current() if live else None
        if v is not None and v.isVisible():
            v.close_view()
    except Exception:
        pass
    if setup:
        setup(b)
    b.setGeometry(host.rect())
    if not b.isVisible() or b.fade_to == 0.0:
        b.fade_in()
    b.show()
    b.raise_()
    b.setFocus()
    return True


def open_panel(
    player,
    panel_id: str,
    factory: Callable[[Any, Optional[QWidget]], Board],
    setup: Optional[Callable[[Board], None]] = None,
) -> bool:
    """Open a plugin-owned Board in the HUD's shared live-panel surface.

    This is the public extension point for visual plugins.  The core owns only
    placement, scaling, theming, lifecycle and GUI-thread dispatch; a plugin
    owns its Board subclass, data collection and any setup callback.
    """
    clean_id = str(panel_id or "").strip().lower()
    if not clean_id:
        return False

    # Mini mode is intentionally only a compact, always-on-top JARVIS
    # presence indicator.  A dashboard squeezed into it is unreadable and its
    # QWidget minimum size can spill over neighbouring applications.  Keep the
    # orb undisturbed; the user can expand it with F9/double-click and rerun the
    # visual request when they want the full interactive panel.
    try:
        compact_state = getattr(player, "is_compact_mode", getattr(player, "is_mini", False))
        is_compact = compact_state() if callable(compact_state) else bool(compact_state)
        if is_compact:
            log = getattr(player, "write_log", None)
            if callable(log):
                log("SYS: Visual dashboard not opened in Mini Orb mode — expand JARVIS first.")
            return False
    except Exception:
        return False
    return bool(_gui(lambda: _open(player, clean_id, factory, setup)))


def panel_status(panel_id: str) -> bool:
    """Return whether a named shared live panel is currently visible."""

    clean_id = str(panel_id or "").strip().lower()

    def job() -> bool:
        board = _boards.get(clean_id)
        return bool(board is not None and not sip.isdeleted(board) and board.isVisible())

    return bool(clean_id and _gui(job, timeout=3.0))


def close_panel(panel_id: str) -> bool:
    """Close one shared live panel without affecting other panel types."""

    clean_id = str(panel_id or "").strip().lower()

    def job() -> bool:
        board = _boards.get(clean_id)
        if board is None or sip.isdeleted(board):
            return False
        was_visible = board.isVisible()
        board.close_view()
        return bool(was_visible)

    return bool(clean_id and _gui(job, timeout=3.0))


def show_system(player) -> bool:
    """Open the live diagnostics board. Safe from any thread."""
    return open_panel(player, "system", SystemBoard)


def system_status() -> bool:
    """Return whether the live system board is visible. Safe from any thread."""

    return panel_status("system")


def close_system() -> bool:
    """Close only the live system board. Safe from any thread."""

    return close_panel("system")


def show_weather(player, place: dict, fc: dict) -> bool:
    """Open (or re-point) the live weather board. Safe from any thread."""
    return open_panel(player, "weather", WeatherBoard, lambda b: b.set_data(place, fc))


def close_weather() -> bool:
    """Close only the weather board, leaving another live panel untouched."""

    return close_panel("weather")


def show_trading(player, payload: dict) -> bool:
    """Open (or re-point) the live trading board. Safe from any thread."""
    return open_panel(player, "trading", TradingBoard, lambda b: b.set_data(payload))


def close_all() -> None:
    """Close whichever board is open. Safe from any thread."""

    def job():
        for b in _boards.values():
            if not sip.isdeleted(b) and b.isVisible():
                b.close_view()

    if _boards:
        _gui(job, timeout=3.0)
