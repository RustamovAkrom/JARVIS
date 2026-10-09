"""A visual, live battery dashboard for the JARVIS HUD."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Optional

import psutil
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import QWidget

try:
    from plugins import _live_panels_core as _live_panels
except ImportError:
    import _live_panels_core as _live_panels  # type: ignore


PLUGIN = {
    "name": "battery_dashboard",
    "description": (
        "Open a live battery dashboard in the JARVIS HUD. "
        "Use this when the user asks to see, show, open or visualize "
        "battery level, charge, remaining time, power status, "
        "laptop battery or charging status."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["open", "close", "status"],
                "description": "open (default), close, or status.",
            },
        },
        "required": [],
    },
    "behavior": "NON_BLOCKING",
    "scheduling": "SILENT",
}


class _BatterySampler:
    def __init__(self):
        self.lock = threading.Lock()
        self.data: dict = {}
        self.hist = deque([None] * 60, maxlen=60)
        self.stop = threading.Event()
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop = threading.Event()
        self.thread = threading.Thread(
            target=self._run, name="battery-sampler", daemon=True
        )
        self.thread.start()

    def _run(self) -> None:
        tick = 0
        while not self.stop.is_set():
            try:
                b = psutil.sensors_battery()
                if b is None:
                    d = {"present": False}
                else:
                    secs = b.secsleft
                    if secs in (getattr(psutil, "POWER_TIME_UNLIMITED", -1), -1, -2):
                        remaining = None
                    else:
                        remaining = max(0, int(secs))

                    d = {
                        "present": True,
                        "percent": float(b.percent),
                        "plugged": bool(b.power_plugged),
                        "secsleft": remaining,
                    }

                    try:
                        extra = self._windows_extra()
                        d.update(extra)
                    except Exception:
                        pass

                with self.lock:
                    self.data = d
                    if d.get("present"):
                        pct = d["percent"]
                        if tick == 0:
                            # fill history with current value so graph is not empty
                            self.hist = deque([pct] * 60, maxlen=60)
                        else:
                            self.hist.append(pct)

            except Exception as exc:
                _live_panels._once("battery sampler", exc)

            tick += 1
            self.stop.wait(5.0)

    def _windows_extra(self) -> dict:
        extra = {}
        try:
            import wmi  # type: ignore

            c = wmi.WMI()
            for bat in c.Win32_Battery():
                if getattr(bat, "EstimatedRunTime", None) not in (None, 71582788):
                    extra["wmi_minutes"] = int(bat.EstimatedRunTime)
                if getattr(bat, "DesignCapacity", None):
                    extra["design_capacity"] = int(bat.DesignCapacity)
                if getattr(bat, "FullChargeCapacity", None):
                    extra["full_capacity"] = int(bat.FullChargeCapacity)
                break
        except Exception:
            pass

        design = extra.get("design_capacity")
        full = extra.get("full_capacity")
        if design and full and design > 0:
            extra["health"] = round(full / design * 100, 1)
        return extra

    def snapshot(self) -> tuple[dict, list]:
        with self.lock:
            return dict(self.data), list(self.hist)


class BatteryBoard(_live_panels.Board):
    def __init__(self, acc, parent: Optional[QWidget] = None):
        super().__init__(acc, parent)
        self.sampler = _BatterySampler()
        self.shown_pct = 0.0
        self._moving = True

    def on_open(self) -> None:
        self.sampler.start()

    def on_close(self) -> None:
        self.sampler.stop.set()

    def animating(self) -> bool:
        return getattr(self, "_moving", True)

    def title(self):
        d, _ = self.sampler.snapshot()
        if not d.get("present"):
            return ("Battery", "No battery detected")
        status = "Charging" if d.get("plugged") else "On battery"
        return ("Battery Status", f"{status}  ·  {time.strftime('%H:%M:%S')}")

    def _format_time(self, secs: Optional[int], plugged: bool, pct: float) -> str:
        if plugged and pct >= 99.5:
            return "Full"
        if secs is None:
            return "—"
        h = secs // 3600
        m = (secs % 3600) // 60
        if h > 0:
            return f"{h}h {m:02d}m"
        return f"{m} min"

    def _battery_color(self, pct: float, plugged: bool):
        """Battery colour logic (opposite of CPU): high = good."""
        if pct <= 15 and not plugged:
            return _live_panels.HOT
        if pct <= 30 and not plugged:
            return _live_panels.WARN
        return self.acc

    def _draw_battery_ring(self, p: QPainter, cx, cy, r, pct, label, value, sub, col):
        """Custom ring that uses the correct battery colour (not the CPU _level)."""
        wdt = max(10.0, r / 7)
        box = QRectF(cx - r, cy - r, 2 * r, 2 * r)

        # glow when high
        gr = QRadialGradient(QPointF(cx, cy), r * 1.45)
        gr.setColorAt(0.55, _live_panels._q(col, 40))
        gr.setColorAt(1.0, _live_panels._q(col, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(gr))
        p.drawEllipse(QPointF(cx, cy), r * 1.45, r * 1.45)

        # background track
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(
            QPen(
                _live_panels._q(_live_panels._mix(_live_panels.BG, self.acc, 0.18)), wdt
            )
        )
        p.drawEllipse(box)

        # ticks
        lit = int(round(min(pct, 100) / 100 * 60))
        groups = {"on": [], "major": [], "minor": []}
        for i in range(60):
            a = math.radians(i * 6 - 90)
            r0, r1 = r + 10, r + (20 if i % 5 == 0 else 14)
            g = "on" if i < lit else ("major" if i % 5 == 0 else "minor")
            groups[g].append(
                QPointF(cx + r0 * math.cos(a), cy + r0 * math.sin(a)),
            )
            # we need lines, so rebuild properly
        # redraw ticks properly
        from PyQt6.QtCore import QLineF

        groups = {"on": [], "major": [], "minor": []}
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
            ("on", _live_panels._q(col, 200)),
            (
                "major",
                _live_panels._q(_live_panels._mix(_live_panels.BG, self.acc, 0.5)),
            ),
            (
                "minor",
                _live_panels._q(_live_panels._mix(_live_panels.BG, self.acc, 0.25)),
            ),
        ):
            if groups[g]:
                p.setPen(QPen(c, 2))
                p.drawLines(groups[g])

        # arc
        if pct > 0.3:
            span = -int(min(pct, 100) * 3.6 * 16)
            self.stroke(
                p,
                lambda: p.drawArc(box, 90 * 16, span),
                col,
                wdt,
                cap=Qt.PenCapStyle.FlatCap,
            )

        # inner circle
        ri = r - wdt - 14
        p.setPen(
            QPen(_live_panels._q(_live_panels._mix(_live_panels.BG, self.acc, 0.3)), 1)
        )
        p.drawEllipse(QPointF(cx, cy), ri, ri)

        # text
        self.text(
            p,
            cx,
            cy - 8,
            value,
            self.font("display", r / 2.1),
            _live_panels.TEXT,
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
                _live_panels.DIM,
                align="c",
                valign="mid",
            )

    def draw(self, p: QPainter, dt: float, now: float) -> None:
        d, hist = self.sampler.snapshot()

        if not d.get("present"):
            self.text(
                p,
                800,
                420,
                "NO BATTERY DETECTED",
                self.font("display", 42),
                _live_panels.DIM,
                align="c",
                glow=True,
            )
            self.text(
                p,
                800,
                490,
                "This PC does not report a battery (desktop or virtual machine).",
                self.font("mono", 22),
                _live_panels.DIM,
                align="c",
            )
            return

        pct = float(d.get("percent") or 0)
        self.shown_pct = _live_panels._approach(self.shown_pct, pct, dt, rate=6.0)
        self._moving = abs(self.shown_pct - pct) > 0.15

        plugged = bool(d.get("plugged"))
        secsleft = d.get("secsleft")
        col = self._battery_color(pct, plugged)

        # ── custom battery ring (correct colours) ──────────────────────────
        self._draw_battery_ring(
            p,
            420,
            380,
            210,
            self.shown_pct,
            "CHARGE",
            f"{self.shown_pct:.0f}%",
            "Charging" if plugged else "Discharging",
            col,
        )

        # ── right cards ────────────────────────────────────────────────────
        cards = []

        cards.append(
            (
                "STATUS",
                "CHARGING" if plugged else "ON BATTERY",
                self.acc if plugged else _live_panels.WARN,
            )
        )

        time_label = "TIME TO FULL" if plugged else "REMAINING"
        time_val = self._format_time(secsleft, plugged, pct)
        cards.append((time_label, time_val, _live_panels.TEXT))

        health = d.get("health")
        if health is not None:
            hcol = (
                _live_panels.HOT
                if health < 70
                else self.acc if health >= 85 else _live_panels.WARN
            )
            cards.append(("HEALTH", f"{health:.0f}%", hcol))
        else:
            cards.append(("HEALTH", "—", _live_panels.DIM))

        cards.append(
            ("POWER", "AC Adapter" if plugged else "Battery", _live_panels.TEXT)
        )

        for i, (label, value, c) in enumerate(cards):
            y = 200 + i * 110
            box = QRectF(780, y, 700, 95)
            self.panel(p, box, 0.11)
            self.text(p, 810, y + 22, label, self.font("mono", 18), _live_panels.DIM)
            self.text(
                p, 810, y + 58, str(value), self.font("display", 36), c, glow=True
            )

        # ── history ────────────────────────────────────────────────────────
        L = QRectF(70, 680, 1460, 250)
        self.panel(p, L)
        self.text(
            p, 100, 705, "CHARGE HISTORY (last ~5 min)", self.font("mono", 20), self.acc
        )

        clean = [v if v is not None else pct for v in hist]
        if len(clean) >= 2:
            lo = max(0, min(clean) - 2)
            hi = min(100, max(clean) + 2)
            if hi - lo < 5:  # avoid flat line looking broken
                lo, hi = max(0, pct - 5), min(100, pct + 5)
            self.graph(
                p,
                QRectF(100, 745, 1400, 150),
                clean,
                lo,
                hi,
                col,
                lambda v: f"{v:.0f}%",
            )

        if pct <= 20 and not plugged:
            self.text(
                p,
                800,
                640,
                "⚠ LOW BATTERY — consider plugging in",
                self.font("mono", 22),
                _live_panels.HOT,
                align="c",
                glow=True,
            )


def run(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "open").strip().lower()

    if action == "status":
        try:
            open_now = _live_panels.panel_status("battery")
        except Exception as exc:
            return f"I couldn't check the battery dashboard: {exc}"
        return (
            "Battery dashboard is currently open."
            if open_now
            else "Battery dashboard is closed."
        )

    if action == "close":
        try:
            closed = _live_panels.close_panel("battery")
        except Exception as exc:
            return f"I couldn't close the battery dashboard: {exc}"
        return (
            "Battery dashboard closed."
            if closed
            else "The battery dashboard is not open."
        )

    if action not in {"open", "refresh"}:
        return "Battery dashboard action must be open, close, or status."

    if player is None:
        return "The battery dashboard needs the JARVIS HUD to be running."

    compact_state = getattr(
        player, "is_compact_mode", getattr(player, "is_mini", False)
    )
    is_compact = compact_state() if callable(compact_state) else bool(compact_state)
    if is_compact:
        return (
            "Battery dashboard is unavailable in Mini Orb or Top Dock mode. "
            "Expand JARVIS with F9 or the FULL button first."
        )

    try:
        ok = _live_panels.open_panel(player, "battery", BatteryBoard)
        if not ok:
            return "I couldn't open the battery dashboard."
        return "Battery dashboard opened."
    except Exception as exp:
        return f"I couldn't open the battery dashboard: {exc}"
