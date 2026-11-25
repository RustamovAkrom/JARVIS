"""
plugins/_electricity_cost_panel.py — Sci-Fi Arc Reactor Power & Electricity Cost HUD Panel for JARVIS.

Renders an animated circular Arc Reactor power meter, live wattage telemetry,
component draw breakdown (CPU, GPU, System Base), and real-time financial running cost projections.
"""
from __future__ import annotations

import math
import platform
import time
from typing import Optional, List

from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QConicalGradient, QPolygonF

from plugins import _live_panels_core as lb
from plugins import pc_electricity_cost as pec

_OS = platform.system()

# UI Color Palette
BG   = (6, 10, 16)
TEXT = (226, 238, 248)
DIM  = (118, 138, 158)
WARN = (255, 196, 64)
HOT  = (255, 84, 96)
OK   = (64, 230, 130)


def _q(rgb, a: int = 255) -> QColor:
    return QColor(int(rgb[0]), int(rgb[1]), int(rgb[2]), int(a))


class PowerCostBoard(lb.Board):
    """Futuristic Arc Reactor power gauge and electricity cost intelligence board."""

    def __init__(self, acc, parent=None):
        super().__init__(acc, parent)
        self.pulse_phase = 0.0
        self.mode = "8h"  # "8h", "12h", or "24h" projection mode

    def on_open(self) -> None:
        pass

    def on_close(self) -> None:
        pass

    def animating(self) -> bool:
        return True

    def title(self):
        data = pec._MONITOR.snapshot()
        curr = data["currency"]
        w = data["wall_watts"]
        c_hr = data["cost_per_hour"]
        kwh = data["session_kwh"]
        return (
            "PC Power & Electricity Cost",
            f"WALL DRAW: {w:.1f} W  ·  COST: {curr}{c_hr:.2f}/hr  ·  SESSION: {kwh:.3f} kWh  ·  {time.strftime('%H:%M:%S')}"
        )

    def clicked(self, key: str) -> None:
        if key in ("mode:8h", "mode:12h", "mode:24h"):
            self.mode = key.split(":")[1]

    def draw(self, p: QPainter, dt: float, now: float) -> None:
        data = pec._MONITOR.snapshot()
        self.pulse_phase = (self.pulse_phase + dt * 2.5) % (2.0 * math.pi)

        # ── 1. Left: Arc Reactor Power Meter (cx=420, cy=580, r=310) ─────────
        try:
            self._draw_arc_reactor(p, 420.0, 580.0, 310.0, now, data)
        except Exception as e:
            print(f"[PowerCost] Arc reactor paint error: {e}")

        # ── 2. Right Top: Cost Telemetry & Live Sparkline ────────────────────
        try:
            self._draw_cost_cards(p, 800.0, 180.0, 730.0, data)
        except Exception as e:
            print(f"[PowerCost] Cost cards paint error: {e}")

        # ── 3. Right Mid: Hardware Power Distribution ────────────────────────
        try:
            self._draw_hardware_bars(p, 800.0, 485.0, 730.0, data)
        except Exception as e:
            print(f"[PowerCost] Hardware bars paint error: {e}")

        # ── 4. Right Bottom: Session Energy & Projections ────────────────────
        try:
            self._draw_session_projections(p, 800.0, 720.0, 730.0, 210.0, data)
        except Exception as e:
            print(f"[PowerCost] Projections paint error: {e}")

    # ── 1. Arc Reactor Circular Power Meter ──────────────────────────────────
    def _draw_arc_reactor(self, p: QPainter, cx: float, cy: float, r: float, now: float, data: dict) -> None:
        w = data["wall_watts"]
        max_capacity = 250.0  # Reference 250W scale for desktop PC
        frac = min(1.0, max(0.05, w / max_capacity))

        # Outer Housing Ring
        p.setPen(QPen(_q(self.acc, 70), 2))
        p.setBrush(_q(BG, 220))
        p.drawEllipse(QPointF(cx, cy), r + 20, r + 20)

        # Concentric Reference Rings
        for radius, alpha in [(r, 40), (r * 0.82, 35), (r * 0.58, 50)]:
            p.setPen(QPen(_q(self.acc, alpha), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(cx, cy), radius, radius)

        # Radial Ticks & Scale Markers (every 30 degrees)
        for i in range(12):
            ang = i * (math.pi / 6.0)
            x1 = cx + math.cos(ang) * (r - 12)
            y1 = cy + math.sin(ang) * (r - 12)
            x2 = cx + math.cos(ang) * (r + 4)
            y2 = cy + math.sin(ang) * (r + 4)
            p.setPen(QPen(_q(self.acc, 90 if i % 3 == 0 else 40), 2 if i % 3 == 0 else 1))
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # Power Fill Arc (from 225° around to -45°, a 270° sweep span)
        span_angle = 270.0 * frac
        start_angle = 225.0

        # Background Track
        p.setPen(QPen(_q(self.acc, 30), 12, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(QRectF(cx - r * 0.82, cy - r * 0.82, 2 * r * 0.82, 2 * r * 0.82), int(-45 * 16), int(-270 * 16))

        # Active Energy Arc
        arc_col = OK if frac < 0.45 else (WARN if frac < 0.80 else HOT)
        p.setPen(QPen(_q(arc_col, 220), 14, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(QRectF(cx - r * 0.82, cy - r * 0.82, 2 * r * 0.82, 2 * r * 0.82), int(start_angle * 16), int(-span_angle * 16))

        # Center Arc Reactor Core Glow
        pulse = 0.5 + 0.5 * math.sin(self.pulse_phase)
        core_r = r * 0.50
        p.setPen(QPen(_q(self.acc, int(100 + 100 * pulse)), 2))
        p.setBrush(_q(BG, 240))
        p.drawEllipse(QPointF(cx, cy), core_r, core_r)

        # Inner Pulsing Plasma Glow
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_q(arc_col, int(25 + 30 * pulse)))
        p.drawEllipse(QPointF(cx, cy), core_r * 0.85, core_r * 0.85)

        # Center Numeric Wattage Readout
        self.text(p, cx, cy - 36, f"{w:.1f}", self.font("display", 56), TEXT, glow=True, align="c")
        self.text(p, cx, cy + 22, "TOTAL WALL WATTS", self.font("display", 16), arc_col, align="c")
        self.text(p, cx, cy + 52, f"CAPACITY: {int(frac * 100)}% OF 250W", self.font("mono", 14), DIM, align="c")

        # Bottom Sub-Indicator
        self.text(p, cx, cy + r + 38, "REAL-TIME HARDWARE AC POWER SENSORS", self.font("mono", 15), DIM, align="c")

    # ── 2. Top Right: Cost Telemetry & Live Sparkline ────────────────────────
    def _draw_cost_cards(self, p: QPainter, x: float, y: float, w: float, data: dict) -> None:
        card_w = (w - 20) / 2
        card_h = 135.0
        curr = data["currency"]
        c_hr = data["cost_per_hour"]
        rate = data["rate_per_kwh"]

        # Card 1: Running Cost per Hour
        r1 = QRectF(x, y, card_w, card_h)
        self.panel(p, r1, 0.14)

        self.text(p, x + 20, y + 28, "RUNNING COST (HOURLY)", self.font("display", 15), DIM)
        self.text(p, x + 20, y + 78, f"{curr}{c_hr:.2f}", self.font("display", 48), OK, glow=True)
        self.text(p, x + 20, y + 112, f"AT TARIFF: {curr}{rate:.2f} / kWh (UNIT)", self.font("mono", 14), DIM)

        # Card 2: Live Wattage History Sparkline
        r2 = QRectF(x + card_w + 20, y, card_w, card_h)
        self.panel(p, r2, 0.14)

        self.text(p, x + card_w + 40, y + 28, "LOAD TREND (LAST 40s)", self.font("display", 15), DIM)
        self.text(p, x + card_w + 40, y + 68, f"{data['wall_watts']:.1f} W", self.font("display", 24), TEXT)
        self.text(p, x + card_w + 40, y + 112, "80-PLUS BRONZE PSU EFFICIENCY", self.font("mono", 13), self.acc)

        hist = data.get("history", [])
        if len(hist) > 2:
            self._draw_sparkline(p, x + card_w + 160, y + 36, card_w - 180, 75, hist, self.acc)

        # Card 3: Monthly Projection Banner
        r3 = QRectF(x, y + card_h + 14, w, 115.0)
        self.panel(p, r3, 0.14)

        hrs = data["active_hours"]
        c_mo = data["cost_month_active"]
        c_day = data["cost_day_active"]

        self.text(p, x + 24, y + card_h + 40, f"ESTIMATED MONTHLY BILL ({hrs:.0f} HRS/DAY)", self.font("mono", 14), DIM)
        self.text(p, x + 24, y + card_h + 84, f"{curr}{c_mo:.0f}", self.font("display", 38), self.acc, glow=True)
        self.text(p, x + 155, y + card_h + 80, f"/ month ({curr}{c_day:.1f}/day)", self.font("mono", 17), TEXT)

        # 24/7 Always-On Comparison
        c_24 = data["cost_month_24h"]
        self.text(p, x + 440, y + card_h + 40, "IF RUN 24/7 (ALWAYS-ON)", self.font("mono", 14), DIM)
        self.text(p, x + 440, y + card_h + 84, f"{curr}{c_24:.0f}", self.font("display", 32), WARN)
        self.text(p, x + 560, y + card_h + 80, "/ month", self.font("mono", 17), DIM)

    def _draw_sparkline(self, p: QPainter, sx: float, sy: float, sw: float, sh: float, values: list, col) -> None:
        if not values:
            return
        v_min, v_max = min(values), max(values)
        rng = max(15.0, v_max - v_min)
        n = len(values)
        poly = QPolygonF()
        for i, v in enumerate(values):
            vx = sx + (i / max(1, n - 1)) * sw
            vy = sy + sh - ((v - v_min) / rng) * (sh - 10)
            poly.append(QPointF(vx, vy))

        p.setPen(QPen(_q(col, 190), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolyline(poly)

    # ── 3. Right Mid: Component Power Breakdown ──────────────────────────────
    def _draw_hardware_bars(self, p: QPainter, x: float, y: float, w: float, data: dict) -> None:
        panel_h = 220.0
        r = QRectF(x, y, w, panel_h)
        self.panel(p, r, 0.12)

        self.text(p, x + 24, y + 20, "HARDWARE POWER DRAW BREAKDOWN", self.font("display", 15), DIM)

        # Row 1: CPU
        cpu_w = data["cpu_watts"]
        cpu_name = data["cpu_name"][:32]
        cpu_pct = data["cpu_pct"]
        self._draw_bar_row(p, x + 24, y + 48, w - 48, f"CPU: {cpu_name}", f"{cpu_w:.1f} W ({cpu_pct:.0f}% load)", cpu_w / 75.0, self.acc)

        # Row 2: GPU
        gpu_w = data["gpu_watts"]
        gpu_name = data["gpu_name"][:32]
        gpu_pct = data["gpu_pct"]
        self._draw_bar_row(p, x + 24, y + 104, w - 48, f"GPU: {gpu_name}", f"{gpu_w:.1f} W ({gpu_pct:.0f}% load)", gpu_w / 40.0, OK)

        # Row 3: Motherboard, RAM & SSDs Baseline
        base_w = data["base_watts"]
        self._draw_bar_row(p, x + 24, y + 160, w - 48, "BOARD, RAM, NVMe & FANS", f"{base_w:.1f} W (Fixed baseline)", base_w / 50.0, DIM)

    def _draw_bar_row(self, p: QPainter, bx: float, by: float, bw: float, label: str, value_str: str, frac: float, col) -> None:
        self.text(p, bx, by + 4, label, self.font("mono", 14), TEXT)
        self.text(p, bx + bw, by + 4, value_str, self.font("mono", 14), col, align="r")

        # Background track
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_q(self.acc, 25))
        p.drawRoundedRect(QRectF(bx, by + 30, bw, 8), 4, 4)

        # Active progress bar
        bar_w = max(6.0, min(bw, bw * min(1.0, max(0.02, frac))))
        p.setBrush(_q(col, 200))
        p.drawRoundedRect(QRectF(bx, by + 30, bar_w, 8), 4, 4)

    # ── 4. Right Bottom: Session Energy & Interactive Projections ────────────
    def _draw_session_projections(self, p: QPainter, x: float, y: float, w: float, h: float, data: dict) -> None:
        # Mode selector buttons
        btn_w = 120.0
        btn_h = 36.0
        for i, (m_key, m_label) in enumerate([("8h", "8H USAGE"), ("12h", "12H USAGE"), ("24h", "24H SERVER")]):
            bx = x + i * (btn_w + 14)
            r_btn = QRectF(bx, y, btn_w, btn_h)
            is_active = self.mode == m_key
            over = self.hit(r_btn, f"mode:{m_key}")
            self.panel(p, r_btn, 0.35 if is_active else (0.18 if over else 0.08), active=is_active)
            self.text(p, bx + btn_w / 2, y + 23, m_label, self.font("display", 14), self.acc if is_active else DIM, align="c")

        # Container box
        body_r = QRectF(x, y + 46, w, h - 46)
        self.panel(p, body_r, 0.12)

        curr = data["currency"]
        kwh = data["session_kwh"]
        s_cost = data["session_cost"]

        # Left Column: Session Telemetry
        self.text(p, x + 24, y + 76, "THIS RUN SESSION CONSUMPTION", self.font("mono", 14), DIM)
        self.text(p, x + 24, y + 115, f"{kwh:.3f}", self.font("display", 36), TEXT)
        self.text(p, x + 155, y + 112, "kWh (Units)", self.font("mono", 16), DIM)
        self.text(p, x + 24, y + 148, f"Session Cost: {curr}{s_cost:.2f}", self.font("mono", 17), OK)

        # Right Column: Environmental & Efficiency Score
        self.text(p, x + 380, y + 76, "POWER EFFICIENCY & CARBON METRICS", self.font("mono", 14), DIM)
        self.text(p, x + 380, y + 104, "▸ PSU Standard: 80-Plus Certified", self.font("mono", 15), TEXT)
        # CO2 emissions ~0.82 kg/kWh average grid emission factor
        co2_kg = kwh * 0.82
        self.text(p, x + 380, y + 128, f"▸ Est. CO₂ Output: {co2_kg:.2f} kg", self.font("mono", 15), DIM)
        self.text(p, x + 380, y + 152, "▸ Energy Grade: High Desktop Efficiency", self.font("mono", 15), self.acc)


def show_power_panel(player) -> bool:
    """Open the dedicated Power & Electricity Cost Board on the HUD. Safe from any thread."""
    return bool(lb._gui(lambda: lb._open(player, "power_cost", PowerCostBoard)))
