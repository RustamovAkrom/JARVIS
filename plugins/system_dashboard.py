"""
JARVIS v2 — System Dashboard (UI/UX overhaul)
=============================================

A responsive, plugin-only system dashboard for the existing JARVIS HUD.

Design:
    • FULL   : complete system dashboard with core, KPIs, network, disk,
               history, processes and system information.
    • MEDIUM : compact two-column dashboard.
    • COMPACT: AI core + micro-strip (RAM / NET / TEMP).

The widget always fits inside the actual _hud_cam_stack rectangle.
All telemetry collection stays off the Qt paint thread.

UI/UX v2:
    • design tokens (uniform rhythm/typography)
    • hover / pressed / selected / keyboard-focus states everywhere
    • animated detail overlay with Esc/Back
    • adaptive frame rate (~30 fps active, ~12 fps idle)
    • interactive history chart with axes and scrubbing
    • full keyboard support: Tab / Enter / Esc / 1-7
"""

from __future__ import annotations

import json
import math
import os
import platform
import threading
import time
import warnings
from collections import deque
from pathlib import Path
from typing import Any, Optional

import psutil

from PyQt6.QtCore import (
    QEvent,
    QPointF,
    QRectF,
    Qt,
    QTimer,
    QObject,
    QThread,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QWidget

PLUGIN = {
    "name": "system_dashboard",
    "description": (
        "Open a responsive live system dashboard on the JARVIS HUD. "
        "Shows CPU, RAM, GPU, VRAM, network, temperature, disk, battery, "
        "uptime, process count and top processes. Clicking metrics or a "
        "process opens detailed telemetry."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["open", "close", "toggle", "status", "process"],
                "description": "Open/close the dashboard or operate on a selected process.",
            },
            "operation": {
                "type": "STRING",
                "enum": [
                    "details",
                    "copy",
                    "analyze",
                    "terminate",
                    "suspend",
                    "resume",
                    "open_location",
                ],
                "description": "Process operation when action='process'.",
            },
            "pid": {
                "type": "INTEGER",
                "description": "Process ID for action='process'.",
            },
        },
    },
    "behavior": "NON_BLOCKING",
    "scheduling": "SILENT",
}


# ─────────────────────────────────────────────────────────────────────────────
# Theme
# ─────────────────────────────────────────────────────────────────────────────

BG = (3, 8, 13)
SURFACE = (7, 15, 24)
SURFACE_2 = (9, 20, 31)
GRID = (14, 36, 46)
TEXT = (228, 242, 248)
TEXT_DIM = (148, 170, 182)
MUTED = (106, 136, 150)
GOOD = (0, 234, 146)
WARN = (255, 188, 64)
BAD = (255, 72, 92)
WHITE = (241, 250, 253)


class DT:
    """Design tokens — single source of truth for rhythm and type."""

    PAD_X = 14.0
    PAD_Y = 10.0
    RADIUS = 11.0
    ROW_H = 26.0
    BTN = 28.0
    T_VALUE = 17.0


def _rgb(value: Any) -> tuple[int, int, int]:
    if isinstance(value, tuple) and len(value) == 3:
        return tuple(max(0, min(255, int(x))) for x in value)

    value = str(value or "").strip()
    if value.startswith("#") and len(value) == 7:
        try:
            return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))
        except ValueError:
            pass

    return (0, 212, 255)


def _accent() -> tuple[int, int, int]:
    try:
        path = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        return _rgb(cfg.get("ui_color"))
    except Exception:
        return (0, 212, 255)


def _live_accent() -> tuple[int, int, int]:
    """
    Read the SAME runtime accent object that JARVIS paints with.

    Customize Assistant updates ui.C.PRI immediately, including during the
    live colour preview. Reading C.PRI directly is more reliable than keeping
    a plugin-local colour snapshot and guarantees the dashboard follows the
    current JARVIS theme while it is already open.
    """
    try:
        import ui

        value = getattr(getattr(ui, "C", None), "PRI", None)
        if value:
            return _rgb(value)
    except Exception:
        pass

    # Secondary path for compatibility with ui.py variants.
    try:
        from ui import current_palette

        palette = current_palette()
        if isinstance(palette, dict):
            value = palette.get("PRI") or palette.get("primary")
            if value:
                return _rgb(value)
    except Exception:
        pass

    return _accent()


def _q(rgb: tuple[int, int, int], alpha: int = 255) -> QColor:
    return QColor(
        int(rgb[0]),
        int(rgb[1]),
        int(rgb[2]),
        max(0, min(255, int(alpha))),
    )


def _mix(a, b, amount: float) -> tuple[int, int, int]:
    amount = max(0.0, min(1.0, float(amount)))
    return tuple(int(a[i] + (b[i] - a[i]) * amount) for i in range(3))


def _level_color(
    value: Optional[float], accent: tuple[int, int, int]
) -> tuple[int, int, int]:
    if value is None:
        return MUTED
    if value >= 90:
        return BAD
    if value >= 75:
        return WARN
    return accent


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


# ─────────────────────────────────────────────────────────────────────────────
# Telemetry sampler
# ─────────────────────────────────────────────────────────────────────────────


class _Sampler:
    """
    Hardware collection is deliberately isolated from Qt.

    Fast:
        CPU / RAM / network / uptime

    Medium:
        battery / disk / interfaces

    Slow:
        process walk / NVIDIA telemetry / temperatures
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread: Optional[threading.Thread] = None

        self.data: dict[str, Any] = {
            "cpu": 0.0,
            "cores": [],
            "cpu_freq": None,
            "ram": 0.0,
            "ram_used": 0.0,
            "ram_total": 0.0,
            "ram_available": 0.0,
            "swap": 0.0,
            "gpu": None,
            "gpu_mem": None,
            "gpu_name": "N/A",
            "temp": None,
            "temp_source": "N/A",
            "down": 0.0,
            "up": 0.0,
            "interfaces": [],
            "disk": 0.0,
            "disk_free": 0.0,
            "disk_total": 0.0,
            "disk_read": 0.0,
            "disk_write": 0.0,
            "nproc": 0,
            "threads": 0,
            "top": [],
            "battery": None,
            "plugged": False,
            "uptime": 0.0,
        }

        self.history: dict[str, deque[float]] = {
            "cpu": deque(maxlen=90),
            "ram": deque(maxlen=90),
            "gpu": deque(maxlen=90),
            "temp": deque(maxlen=90),
            "down": deque(maxlen=90),
            "up": deque(maxlen=90),
            "disk": deque(maxlen=90),
        }

        self._last_net: Any = None
        self._last_disk: Any = None
        self._last_io_t = time.monotonic()

        self._gpu_backend = None
        self._gpu_handle = None
        self._gpu_ready = False
        self._gpu_failed = False

        self._wmi = None
        self._wmi_failed = False

        self._process_cache: dict[int, psutil.Process] = {}
        self._process_prev: dict[int, tuple[float, float]] = {}
        self._cached_top: list[dict[str, Any]] = []
        self._last_process_scan = 0.0

    def start(self) -> None:
        # Re-join a shutting-down thread so repeated open/close cycles work.
        if self.thread and self.thread.is_alive():
            if not self.stop.is_set():
                return
            self.thread.join(timeout=2.0)

        self.stop.clear()
        self.thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="jarvis-system-dashboard-sampler",
        )
        self.thread.start()

    def shutdown(self) -> None:
        self.stop.set()

    def snapshot(self):
        with self.lock:
            return (
                dict(self.data),
                {key: list(values) for key, values in self.history.items()},
            )

    def _loop(self) -> None:
        try:
            psutil.cpu_percent(interval=None)
        except Exception:
            pass

        while not self.stop.is_set():
            start = time.monotonic()
            try:
                self._sample()
            except Exception:
                pass

            elapsed = time.monotonic() - start
            self.stop.wait(max(0.25, 1.0 - elapsed))

    def _sample(self) -> None:
        now = time.monotonic()

        cpu = _num(psutil.cpu_percent(interval=None))
        try:
            cores = [float(v) for v in psutil.cpu_percent(interval=None, percpu=True)]
        except Exception:
            cores = []

        try:
            freq = psutil.cpu_freq()
            cpu_freq = (freq.current / 1000.0) if freq else None
        except Exception:
            cpu_freq = None

        vm = psutil.virtual_memory()
        ram = _num(vm.percent)
        ram_used = vm.used / (1024**3)
        ram_total = vm.total / (1024**3)
        ram_available = vm.available / (1024**3)

        try:
            swap = _num(psutil.swap_memory().percent)
        except Exception:
            swap = 0.0

        net_now = psutil.net_io_counters()
        dt = max(0.25, now - self._last_io_t)
        down = 0.0
        up = 0.0
        if net_now is not None and self._last_net is not None:
            down = max(
                0.0, (net_now.bytes_recv - self._last_net.bytes_recv) / dt / (1024**2)
            )
            up = max(
                0.0, (net_now.bytes_sent - self._last_net.bytes_sent) / dt / (1024**2)
            )
        if net_now is not None:
            self._last_net = net_now

        disk_read = 0.0
        disk_write = 0.0
        try:
            dio = psutil.disk_io_counters()
            if dio is not None and self._last_disk is not None:
                disk_read = max(
                    0.0, (dio.read_bytes - self._last_disk.read_bytes) / dt / (1024**2)
                )
                disk_write = max(
                    0.0,
                    (dio.write_bytes - self._last_disk.write_bytes) / dt / (1024**2),
                )
            if dio is not None:
                self._last_disk = dio
        except Exception:
            pass

        self._last_io_t = now

        try:
            root = Path.home().anchor or "/"
            du = psutil.disk_usage(root)
            disk_pct = _num(du.percent)
            disk_free = du.free / (1024**3)
            disk_total = du.total / (1024**3)
        except Exception:
            disk_pct = 0.0
            disk_free = 0.0
            disk_total = 0.0

        gpu, gpu_mem, gpu_name = self._gpu()
        temp, temp_source = self._temperature()
        interfaces = self._interfaces()

        battery = None
        plugged = False
        try:
            battery_info = psutil.sensors_battery()
            if battery_info is not None:
                battery = _num(battery_info.percent)
                plugged = bool(battery_info.power_plugged)
        except Exception:
            pass

        if now - self._last_process_scan >= 2.0:
            self._last_process_scan = now
            self._cached_top = self._top_processes()

        try:
            nproc = len(psutil.pids())
        except Exception:
            nproc = 0

        threads = 0
        try:
            current = psutil.Process()
            threads = current.num_threads()
        except Exception:
            pass

        try:
            uptime = max(0.0, time.time() - psutil.boot_time())
        except Exception:
            uptime = 0.0

        data = {
            "cpu": cpu,
            "cores": cores,
            "cpu_freq": cpu_freq,
            "ram": ram,
            "ram_used": ram_used,
            "ram_total": ram_total,
            "ram_available": ram_available,
            "swap": swap,
            "gpu": gpu,
            "gpu_mem": gpu_mem,
            "gpu_name": gpu_name,
            "temp": temp,
            "temp_source": temp_source,
            "down": down,
            "up": up,
            "interfaces": interfaces,
            "disk": disk_pct,
            "disk_free": disk_free,
            "disk_total": disk_total,
            "disk_read": disk_read,
            "disk_write": disk_write,
            "nproc": nproc,
            "threads": threads,
            "top": list(self._cached_top),
            "battery": battery,
            "plugged": plugged,
            "uptime": uptime,
        }

        with self.lock:
            self.data = data

            for key, value in (
                ("cpu", cpu),
                ("ram", ram),
                ("down", down),
                ("up", up),
                ("disk", disk_pct),
            ):
                self.history[key].append(float(value))

            if gpu is not None:
                self.history["gpu"].append(float(gpu))

            if temp is not None:
                self.history["temp"].append(float(temp))

    def _gpu(self) -> tuple[Optional[float], Optional[float], str]:
        if self._gpu_failed:
            return None, None, "N/A"

        try:
            if not self._gpu_ready:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    import pynvml  # type: ignore

                pynvml.nvmlInit()
                self._gpu_backend = pynvml
                self._gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                self._gpu_ready = True

            util = self._gpu_backend.nvmlDeviceGetUtilizationRates(self._gpu_handle)
            memory = self._gpu_backend.nvmlDeviceGetMemoryInfo(self._gpu_handle)
            name = self._gpu_backend.nvmlDeviceGetName(self._gpu_handle)

            if isinstance(name, bytes):
                name = name.decode(errors="replace")

            mem_pct = 0.0
            if getattr(memory, "total", 0):
                mem_pct = float(memory.used) / float(memory.total) * 100.0

            return float(util.gpu), mem_pct, str(name)
        except Exception:
            self._gpu_failed = True
            return None, None, "N/A"

    def _temperature(self) -> tuple[Optional[float], str]:
        try:
            sensors = psutil.sensors_temperatures()
            preferred = (
                "coretemp",
                "k10temp",
                "cpu_thermal",
                "cpu-thermal",
                "zenpower",
                "it8688",
                "acpitz",
            )

            for name in preferred:
                rows = sensors.get(name)
                if rows:
                    return _num(rows[0].current), name

            for name, rows in sensors.items():
                if rows:
                    return _num(rows[0].current), name
        except Exception:
            pass

        if platform.system() == "Windows" and not self._wmi_failed:
            try:
                if self._wmi is None:
                    import wmi  # type: ignore

                    self._wmi = wmi.WMI(namespace="root/wmi")

                rows = self._wmi.MSAcpi_ThermalZoneTemperature()
                if rows:
                    value = _num(rows[0].CurrentTemperature) / 10.0 - 273.15
                    return value, "WMI thermal zone"
            except Exception:
                self._wmi_failed = True

        return None, "N/A"

    def _interfaces(self) -> list[dict[str, Any]]:
        try:
            counters = psutil.net_io_counters(pernic=True)
            rows = []
            for name, value in counters.items():
                rows.append(
                    {
                        "name": str(name),
                        "recv": int(value.bytes_recv),
                        "sent": int(value.bytes_sent),
                        "total": int(value.bytes_recv + value.bytes_sent),
                    }
                )
            rows.sort(key=lambda item: item["total"], reverse=True)
            return rows[:8]
        except Exception:
            return []

    def _top_processes(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        now = time.monotonic()
        ncpu = max(1, psutil.cpu_count() or 1)

        try:
            pids = psutil.pids()
        except Exception:
            return list(self._cached_top)

        seen: set[int] = set()

        for index, pid in enumerate(pids):
            if index and index % 14 == 0:
                if self.stop.is_set():
                    break
                time.sleep(0.001)

            try:
                proc = self._process_cache.get(pid)
                if proc is None:
                    proc = self._process_cache[pid] = psutil.Process(pid)

                with proc.oneshot():
                    name = proc.name()
                    cpu_times = proc.cpu_times()
                    rss = proc.memory_info().rss

                total = _num(cpu_times.user) + _num(cpu_times.system)
                old = self._process_prev.get(pid)
                cpu = None

                if old:
                    old_total, old_at = old
                    span = now - old_at
                    if span > 0:
                        cpu = max(0.0, (total - old_total) / span * 100.0 / ncpu)

                self._process_prev[pid] = (total, now)
                seen.add(pid)

                if not name:
                    continue

                rows.append(
                    {
                        "pid": int(pid),
                        "name": str(name),
                        "cpu": cpu,
                        "mb": rss / (1024**2),
                    }
                )
            except Exception:
                self._process_cache.pop(pid, None)

        for pid in list(self._process_cache):
            if pid not in seen:
                self._process_cache.pop(pid, None)
                self._process_prev.pop(pid, None)

        rows.sort(
            key=lambda item: (
                item["cpu"] if item["cpu"] is not None else -1.0,
                item["mb"],
            ),
            reverse=True,
        )

        rows = [
            row
            for row in rows
            if row["name"].strip().casefold() not in {"system idle process", "idle"}
        ]

        return rows[:8]


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard widget
# ─────────────────────────────────────────────────────────────────────────────


class _Dashboard(QWidget):
    METRICS = ("cpu", "ram", "gpu", "temp", "disk")
    DETAIL_TITLES = {
        "process": "PROCESS DETAILS",
        "network": "NETWORK",
        "ram": "MEMORY",
        "gpu": "GPU",
        "temp": "TEMPERATURE",
        "disk": "DISK",
        "system": "SYSTEM",
    }

    def __init__(self, host: QWidget, parent=None) -> None:
        super().__init__(parent or host)
        self.host = host
        self.player = None
        self.acc = _live_accent()
        self._last_acc = self.acc
        self.sampler = _Sampler()

        # interaction state
        self._hit: dict[str, QRectF] = {}
        self._proc_hits: dict[int, QRectF] = {}
        self._hover = ""
        self._press = ""
        self._focus = ""
        self._selected_metric = "cpu"
        self._selected_process: Optional[int] = None
        self._detail = ""  # "", "cpu", "process", "network", ...
        self._detail_t = 0.0  # overlay animation 0..1
        self._closing = False
        self._mouse = QPointF(-1, -1)
        self._last_input = 0.0

        # render state
        self._phase = 0.0
        self._last_t = time.monotonic()

        rng = __import__("random").Random(14821)
        self._nodes = []
        for _ in range(70):
            z = rng.uniform(-1.0, 1.0)
            th = rng.uniform(0.0, math.tau)
            r = math.sqrt(max(0.0, 1.0 - z * z))
            self._nodes.append((r * math.cos(th), r * math.sin(th), z))

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    # ── lifecycle ────────────────────────────────────────────────────────────

    def open(self) -> None:
        self.setGeometry(self.host.rect())
        self._detail = ""
        self._detail_t = 0.0
        self._closing = False
        self._hover = ""
        self._press = ""
        self._selected_process = None
        self._last_t = time.monotonic()
        self.sampler.start()
        self._timer.start()
        try:  # re-install (idempotent): fixes stale filter after close/open
            self.host.removeEventFilter(self)
            self.host.installEventFilter(self)
        except Exception:
            pass
        self.show()
        self.raise_()
        self.setFocus()
        self.update()

    def close_board(self) -> None:
        self._timer.stop()
        self.sampler.shutdown()
        try:
            self.host.removeEventFilter(self)
        except Exception:
            pass
        self.hide()

    def _host_page_changed(self, index) -> None:
        try:
            if int(index) != 0 and self.isVisible():
                self.hide()
        except Exception:
            pass

    def eventFilter(self, watched, event) -> bool:
        try:
            if watched is self.host:
                if event.type() == QEvent.Type.Resize:
                    self.setGeometry(self.host.rect())
                    self.update()
                elif event.type() == QEvent.Type.Hide:
                    self.hide()
        except Exception:
            pass
        return super().eventFilter(watched, event)

    # ── adaptive frame loop ─────────────────────────────────────────────────

    def _sync_theme(self) -> None:
        """
        Synchronize the dashboard with JARVIS's live UI palette.
        current_palette() is in-memory, so this adds no config-file I/O.
        """
        try:
            accent = _live_accent()
            if accent != self._last_acc:
                self.acc = accent
                self._last_acc = accent
                self.update()
        except Exception:
            pass

    def _tick(self) -> None:
        if not self.isVisible():
            return
        self._sync_theme()
        now = time.monotonic()
        dt = max(0.0, min(0.12, now - self._last_t))
        self._last_t = now
        self._phase += dt  # time-based, rotation speed is fps-independent

        if self._detail and not self._closing and self._detail_t < 1.0:
            self._detail_t = min(1.0, self._detail_t + dt / 0.20)
        if self._closing:
            self._detail_t = max(0.0, self._detail_t - dt / 0.15)
            if self._detail_t <= 0.0:
                self._closing = False
                self._detail = ""
                self._selected_process = None

        animating = self._detail_t < 1.0
        active = bool(self._hover) or animating or (now - self._last_input) < 6.0
        self._timer.setInterval(33 if active else 80)  # ~30fps busy / ~12fps idle
        self.update()

    # ── layout as data (paint + hit-testing share this) ─────────────────────

    def _mode(self) -> str:
        w, h = self.width(), self.height()
        if w < 640 or h < 420:
            return "compact"
        if w < 960 or h < 580:
            return "medium"
        return "full"

    def _layout(self, mode: str, _data: dict) -> dict[str, QRectF]:
        w, h = float(self.width()), float(self.height())
        m = max(10.0, min(22.0, min(w, h) * 0.032))
        g = max(8.0, min(14.0, min(w, h) * 0.018))
        head = max(34.0, min(46.0, h * 0.065))

        L: dict[str, QRectF] = {}
        L["header"] = QRectF(m, m, w - 2 * m, head)
        y = m + head + g
        content = QRectF(m, y, w - 2 * m, h - m - y)
        L["content"] = content

        if mode == "compact":
            strip_h = 32.0 if content.height() > 250 else 0.0
            side = content.width()
            if strip_h:
                side = min(side, content.height() - strip_h - g * 0.6)
            side = max(110.0, min(side, content.height() - 2))
            core = QRectF(content.center().x() - side / 2, content.top(), side, side)
            L["core"] = core
            if strip_h:
                L["micro"] = QRectF(
                    core.left(), core.bottom() + g * 0.6, core.width(), strip_h
                )
            return L

        left_w = content.width() * (0.38 if mode == "medium" else 0.34)
        left = QRectF(content.left(), content.top(), left_w, content.height())
        right = QRectF(
            content.left() + left_w + g,
            content.top(),
            content.width() - left_w - g,
            content.height(),
        )

        core_h = left.height() * 0.57
        L["core"] = QRectF(left.left(), left.top(), left.width(), core_h)
        L["system"] = QRectF(
            left.left(),
            left.top() + core_h + g,
            left.width(),
            left.height() - core_h - g,
        )

        if mode == "medium":
            kpi_h = max(84.0, min(104.0, right.height() * 0.22))
            kw = (right.width() - 2 * g) / 3
            for i, k in enumerate(("cpu", "ram", "disk")):
                L[f"kpi:{k}"] = QRectF(
                    right.left() + i * (kw + g), right.top(), kw, kpi_h
                )
            row2_y = right.top() + kpi_h + g
            net_h = max(96.0, min(120.0, right.height() * 0.24))
            L["network"] = QRectF(right.left(), row2_y, right.width(), net_h)
            L["processes"] = QRectF(
                right.left(),
                row2_y + net_h + g,
                right.width(),
                right.bottom() - row2_y - net_h - g,
            )
            return L

        kpi_h = max(92.0, min(116.0, right.height() * 0.20))
        kw = (right.width() - 3 * g) / 4
        for i, k in enumerate(("cpu", "ram", "gpu", "temp")):
            L[f"kpi:{k}"] = QRectF(right.left() + i * (kw + g), right.top(), kw, kpi_h)
        row2_y = right.top() + kpi_h + g
        net_h = max(100.0, min(124.0, right.height() * 0.22))
        half = (right.width() - g) / 2
        L["network"] = QRectF(right.left(), row2_y, half, net_h)
        L["diskcard"] = QRectF(right.left() + half + g, row2_y, half, net_h)
        row3_y = row2_y + net_h + g
        hist_h = max(112.0, min(148.0, right.height() * 0.24))
        L["history"] = QRectF(right.left(), row3_y, right.width(), hist_h)
        L["processes"] = QRectF(
            right.left(),
            row3_y + hist_h + g,
            right.width(),
            right.bottom() - row3_y - hist_h - g,
        )
        return L

    # ── text primitives ──────────────────────────────────────────────────────

    def _font(self, size: float, bold: bool = False) -> QFont:
        return QFont(
            "Segoe UI",
            max(7, int(round(size))),
            QFont.Weight.Bold if bold else QFont.Weight.Normal,
        )

    def _fit(self, text: Any, font: QFont, width: float) -> str:
        text = str(text)
        if width <= 4:
            return ""
        fm = QFontMetrics(font)
        if fm.horizontalAdvance(text) <= width:
            return text
        cur = text
        while cur and fm.horizontalAdvance(cur + "…") > width:
            cur = cur[:-1]
        return (cur.rstrip() + "…") if cur else "…"

    def _text(
        self,
        p,
        text,
        rect,
        size,
        color,
        bold=False,
        align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
    ) -> None:
        if rect.width() <= 3 or rect.height() <= 3:
            return
        f = self._font(size, bold)
        p.setFont(f)
        p.setPen(QPen(_q(color), 1))
        p.drawText(rect, align, self._fit(text, f, rect.width()))

    def _label(
        self, p, text, rect, color, size: float = 8.5, spacing: float = 0.8
    ) -> None:
        f = self._font(size, True)
        if spacing:
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
        p.setFont(f)
        p.setPen(QPen(_q(color), 1))
        p.drawText(
            rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._fit(str(text).upper(), f, rect.width()),
        )

    def _panel(self, p, rect, active=False, pressed=False, focus=False) -> None:
        if rect.width() < 8 or rect.height() < 8:
            return
        fill = _mix(
            SURFACE_2, self.acc, 0.06 if pressed else (0.10 if active else 0.025)
        )
        border = _mix(MUTED, self.acc, 0.60 if pressed else (0.75 if active else 0.40))
        p.setPen(
            QPen(_q(border, 245 if pressed or active else 200), 1.35 if active else 1.0)
        )
        p.setBrush(QBrush(_q(fill, 250)))
        p.drawRoundedRect(rect, DT.RADIUS, DT.RADIUS)
        p.setPen(QPen(_q(self.acc, 190 if active else 85), 2))
        p.drawLine(
            QPointF(rect.left() + 12, rect.top() + 1),
            QPointF(min(rect.left() + 60, rect.right() - 12), rect.top() + 1),
        )
        if focus:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_q(WHITE, 170), 1.3, Qt.PenStyle.DashLine))
            p.drawRoundedRect(rect.adjusted(-3, -3, 3, 3), DT.RADIUS + 2, DT.RADIUS + 2)

    def _spark(self, p, rect, values, color) -> None:
        if len(values) < 2 or rect.width() < 8 or rect.height() < 4:
            return
        high = max(1e-3, max(values))
        n = len(values)
        path = QPainterPath()
        for i, v in enumerate(values):
            x = rect.left() + rect.width() * i / (n - 1)
            y = rect.bottom() - rect.height() * max(0.0, float(v)) / high
            path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
        fill = QPainterPath(path)
        fill.lineTo(rect.right(), rect.bottom())
        fill.lineTo(rect.left(), rect.bottom())
        fill.closeSubpath()
        grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
        grad.setColorAt(0.0, _q(color, 42))
        grad.setColorAt(1.0, _q(color, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        p.drawPath(fill)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_q(color, 200), 1.2))
        p.drawPath(path)

    # ── formatters ───────────────────────────────────────────────────────────

    def _rate(self, v) -> str:
        v = _num(v)
        return f"{v / 1024:.2f} GB/s" if v >= 1024 else f"{v:.2f} MB/s"

    def _bytes(self, v) -> str:
        v = float(v or 0.0)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if v < 1024 or unit == "TB":
                return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
            v /= 1024.0
        return "0 B"

    def _uptime(self, s) -> str:
        s = int(_num(s))
        d, r = divmod(s, 86400)
        h, r = divmod(r, 3600)
        m, _ = divmod(r, 60)
        return f"{d}d {h}h" if d else f"{h}h {m}m"

    def _suffix(self, metric: str) -> str:
        return (
            "°C" if metric == "temp" else (" MB/s" if metric in ("down", "up") else "%")
        )

    # ── header ───────────────────────────────────────────────────────────────

    def _draw_header(self, p, rect, data) -> None:
        title = "JARVIS // SYSTEM"
        f = self._font(12, True)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.1)
        p.setFont(f)
        p.setPen(QPen(_q(TEXT), 1))
        p.drawText(
            QRectF(rect.left(), rect.top() + 2, 260, rect.height() - 4),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            title,
        )
        tw = QFontMetrics(f).horizontalAdvance(title)

        pulse = 0.5 + 0.5 * math.sin(self._phase * 2.2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_q(GOOD, int(120 + 100 * pulse)))
        p.drawEllipse(QPointF(rect.left() + tw + 14, rect.center().y()), 3.0, 3.0)

        if rect.width() > 430:
            self._label(
                p,
                self._mode(),
                QRectF(rect.left() + tw + 24, rect.top(), 100, rect.height()),
                MUTED,
                7.5,
            )

        # close button
        btn = QRectF(
            rect.right() - DT.BTN, rect.center().y() - DT.BTN / 2, DT.BTN, DT.BTN
        )
        self._hit["header:close"] = btn
        hov = self._hover == "header:close"
        p.setPen(QPen(_q(BAD if hov else MUTED, 220), 1))
        p.setBrush(_q(BAD, 45) if hov else _q(WHITE, 8))
        p.drawRoundedRect(btn, 8, 8)
        if self._focus == "header:close":
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_q(WHITE, 170), 1.2, Qt.PenStyle.DashLine))
            p.drawRoundedRect(btn.adjusted(-3, -3, 3, 3), 10, 10)
        pen = QPen(_q(BAD if hov else TEXT_DIM, 235), 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        c, s = btn.center(), 5.0
        p.drawLine(QPointF(c.x() - s, c.y() - s), QPointF(c.x() + s, c.y() + s))
        p.drawLine(QPointF(c.x() - s, c.y() + s), QPointF(c.x() + s, c.y() - s))

        # battery pill
        if data.get("battery") is not None and rect.width() > 360:
            b = _num(data.get("battery"))
            plugged = bool(data.get("plugged"))
            col = GOOD if plugged else _level_color(b, self.acc)
            pill = QRectF(btn.left() - 10 - 78, rect.center().y() - 12, 78, 24)
            p.setPen(QPen(_q(col, 160), 1))
            p.setBrush(_q(col, 22))
            p.drawRoundedRect(pill, 12, 12)
            bar = pill.adjusted(9, 8, -34, -8)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_q(col, 60))
            p.drawRoundedRect(bar, 3, 3)
            p.setBrush(_q(col, 235))
            p.drawRoundedRect(
                QRectF(
                    bar.left(),
                    bar.top(),
                    bar.width() * max(0.05, min(1.0, b / 100.0)),
                    bar.height(),
                ),
                3,
                3,
            )
            self._text(
                p,
                f"{b:.0f}%",
                QRectF(pill.right() - 32, pill.top(), 28, pill.height()),
                8,
                TEXT,
                True,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )

    # ── AI core visualizer ───────────────────────────────────────────────────

    def _project(self, point, cx: float, cy: float, radius: float):
        nx, ny, nz = point
        angle = self._phase * 0.46
        ca, sa = math.cos(angle), math.sin(angle)
        x = nx * ca + nz * sa
        z = -nx * sa + nz * ca
        tilt = 0.15 + 0.05 * math.sin(self._phase * 0.35)
        ct, st = math.cos(tilt), math.sin(tilt)
        y = ny * ct - z * st
        z = ny * st + z * ct
        depth = 0.32 + 0.68 * ((z + 1.0) * 0.5)
        return cx + x * radius, cy + y * radius, z, depth

    def _draw_core(self, p, rect, cpu: float, compact: bool) -> None:
        if rect.width() < 60 or rect.height() < 60:
            return
        cx, cy = rect.center().x(), rect.center().y()
        radius = min(rect.width(), rect.height()) * 0.29
        radius = max(52.0, min(radius, rect.height() * 0.36))
        hover = self._hover == "core"

        glow = QRadialGradient(QPointF(cx, cy), radius * 1.25)
        glow.setColorAt(0.0, _q(self.acc, 24 if hover else 20))
        glow.setColorAt(0.55, _q(self.acc, 9))
        glow.setColorAt(1.0, _q(BG, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(
            QRectF(cx - radius * 1.25, cy - radius * 1.25, radius * 2.5, radius * 2.5)
        )

        body = QRadialGradient(
            QPointF(cx - radius * 0.22, cy - radius * 0.27), radius * 1.02
        )
        body.setColorAt(0.0, _q(_mix(BG, self.acc, 0.25), 250))
        body.setColorAt(0.52, _q(_mix(BG, self.acc, 0.08), 252))
        body.setColorAt(1.0, _q(BG, 255))
        p.setPen(QPen(_q(self.acc, 150 if hover else 130), 1.1))
        p.setBrush(QBrush(body))
        p.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))

        p.setBrush(Qt.BrushStyle.NoBrush)
        for k in (-0.72, -0.45, -0.18, 0.18, 0.45, 0.72):
            yy = cy + k * radius
            half = math.sqrt(max(0.0, 1.0 - k * k)) * radius
            p.setPen(QPen(_q(self.acc, 42), 0.7))
            p.drawEllipse(
                QRectF(
                    cx - half,
                    yy - radius * (0.045 + 0.035 * (1.0 - abs(k))),
                    half * 2,
                    radius * (0.09 + 0.07 * (1.0 - abs(k))),
                )
            )
        for k in (-0.72, -0.48, -0.24, 0.0, 0.24, 0.48, 0.72):
            wq = radius * (0.20 + 0.80 * (1.0 - abs(k)))
            p.setPen(QPen(_q(self.acc, 40 if k else 64), 0.7))
            p.drawEllipse(QRectF(cx - wq * 0.35, cy - radius, wq * 0.70, radius * 2))

        projected = []
        for index, point in enumerate(self._nodes):
            x, y, z, depth = self._project(point, cx, cy, radius * 0.95)
            projected.append((x, y, z, depth, index))
        visible = [it for it in projected if it[2] > -0.22]
        max_d2 = (radius * 0.42) ** 2
        for i in range(len(visible)):
            x1, y1, _z1, d1, _ = visible[i]
            for j in range(i + 1, min(i + 7, len(visible))):
                x2, y2, _z2, d2, _ = visible[j]
                dx, dy = x2 - x1, y2 - y1
                if dx * dx + dy * dy <= max_d2:
                    p.setPen(QPen(_q(self.acc, int(24 + 44 * min(d1, d2))), 0.65))
                    p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        pulse_idx = int(self._phase * 8.0) % max(1, len(projected))
        for x, y, z, depth, index in projected:
            if z < -0.20:
                continue
            pulse = 1.0 if index == pulse_idx else 0.0
            size = 1.25 + depth * 1.65 + pulse * 1.5
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_q(self.acc, int(80 + depth * 125 + pulse * 50)))
            p.drawEllipse(QRectF(x - size, y - size, size * 2, size * 2))

        ring = QRectF(
            cx - radius * 1.10, cy - radius * 1.10, radius * 2.20, radius * 2.20
        )
        p.setPen(QPen(_q(_mix(BG, self.acc, 0.38), 235), 3))
        p.drawArc(ring, 90 * 16, -360 * 16)
        load_angle = max(0.0, min(100.0, cpu)) * 3.6
        p.setPen(QPen(_q(_level_color(cpu, self.acc), 240), 3))
        sweep = int((self._phase * 58.0) % 360.0)
        p.drawArc(ring, sweep * 16, int(-load_angle * 16))

        core_r = radius * 0.22
        sensor = QRadialGradient(
            QPointF(cx - core_r * 0.25, cy - core_r * 0.35), core_r * 1.35
        )
        sensor.setColorAt(0.0, _q(_mix(BG, self.acc, 0.42), 245))
        sensor.setColorAt(0.55, _q(_mix(BG, self.acc, 0.12), 245))
        sensor.setColorAt(1.0, _q(BG, 252))
        p.setPen(QPen(_q(self.acc, 150), 1))
        p.setBrush(QBrush(sensor))
        p.drawEllipse(QRectF(cx - core_r, cy - core_r, core_r * 2, core_r * 2))

        self._text(
            p,
            f"{cpu:.0f}%",
            QRectF(cx - core_r - 16, cy - 14, core_r * 2 + 32, 28),
            max(14, int(radius * 0.155)),
            TEXT,
            True,
            Qt.AlignmentFlag.AlignCenter,
        )
        self._text(
            p,
            "SYSTEM LOAD",
            QRectF(cx - core_r - 16, cy + 13, core_r * 2 + 32, 16),
            7,
            self.acc,
            True,
            Qt.AlignmentFlag.AlignCenter,
        )

        if self._focus == "core":
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_q(WHITE, 170), 1.3, Qt.PenStyle.DashLine))
            p.drawRoundedRect(rect.adjusted(-3, -3, 3, 3), 14, 14)

    # ── compact micro-strip ─────────────────────────────────────────────────

    def _draw_micro(self, p, rect, data) -> None:
        if rect.width() < 60 or rect.height() < 16:
            return
        temp = data.get("temp")
        cells = [
            ("RAM", f"{_num(data.get('ram')):.0f}%", _num(data.get("ram"))),
            ("NET ↓", self._rate(data.get("down")), None),
            (
                "TEMP",
                "N/A" if temp is None else f"{_num(temp):.0f}°C",
                None if temp is None else _num(temp) * 1.15,
            ),
        ]
        cw = rect.width() / 3.0
        for i, (lab, val, pct) in enumerate(cells):
            key = f"micro:{i}"
            r = QRectF(rect.left() + i * cw + 2, rect.top(), cw - 4, rect.height())
            self._hit[key] = r
            hov = self._hover == key or self._press == key
            self._panel(p, r, hov, self._press == key, self._focus == key)
            self._label(
                p,
                lab,
                QRectF(r.left() + 9, r.top() + 3, r.width() - 18, 12),
                MUTED,
                7.0,
            )
            self._text(
                p,
                val,
                QRectF(r.left() + 9, r.top() + 14, r.width() - 18, r.height() - 16),
                10.5,
                TEXT if pct is None else _level_color(pct, self.acc),
                True,
            )

    # ── KPI card ─────────────────────────────────────────────────────────────

    def _card(self, p, rect, key, label, value, pct, history, sub="") -> None:
        hkey = f"kpi:{key}"
        active = (
            self._hover == hkey or self._press == hkey or self._selected_metric == key
        )
        pressed = self._press == hkey
        self._panel(p, rect, active and not pressed, pressed, self._focus == hkey)
        self._label(
            p,
            label,
            QRectF(
                rect.left() + DT.PAD_X,
                rect.top() + DT.PAD_Y - 3,
                rect.width() * 0.62,
                14,
            ),
            self.acc if active else TEXT_DIM,
            8.0,
        )
        if sub:
            self._text(
                p,
                sub,
                QRectF(
                    rect.right() - DT.PAD_X - rect.width() * 0.42,
                    rect.top() + DT.PAD_Y - 3,
                    rect.width() * 0.42,
                    14,
                ),
                7,
                MUTED,
                False,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )
        self._text(
            p,
            value,
            QRectF(
                rect.left() + DT.PAD_X,
                rect.top() + DT.PAD_Y + 11,
                rect.width() - 2 * DT.PAD_X,
                26,
            ),
            min(DT.T_VALUE, rect.height() * 0.26),
            _level_color(pct, self.acc),
            True,
        )
        self._spark(
            p,
            QRectF(
                rect.left() + DT.PAD_X,
                rect.bottom() - 21,
                rect.width() - 2 * DT.PAD_X,
                15,
            ),
            history,
            self.acc,
        )

    # ── network / disk cards ────────────────────────────────────────────────

    def _network_card(self, p, rect, data, hist) -> None:
        active = self._hover == "network" or self._press == "network"
        self._panel(p, rect, active, self._press == "network", self._focus == "network")
        self._label(
            p,
            "NETWORK",
            QRectF(
                rect.left() + DT.PAD_X,
                rect.top() + DT.PAD_Y - 3,
                rect.width() * 0.6,
                14,
            ),
            self.acc if active else TEXT_DIM,
            8.0,
        )
        self._text(
            p,
            f"↓  {self._rate(data.get('down'))}",
            QRectF(rect.left() + DT.PAD_X, rect.top() + 26, 130, 18),
            10.5,
            GOOD,
            True,
        )
        self._text(
            p,
            f"↑  {self._rate(data.get('up'))}",
            QRectF(rect.left() + DT.PAD_X, rect.top() + 46, 130, 18),
            10.5,
            self.acc,
            True,
        )
        self._text(
            p,
            f"{len(data.get('interfaces') or [])} INTERFACES",
            QRectF(rect.left() + DT.PAD_X, rect.bottom() - 18, 130, 13),
            6.5,
            MUTED,
        )
        chart = QRectF(
            rect.left() + 128, rect.top() + 10, rect.width() - 140, rect.height() - 20
        )
        if chart.width() > 50:
            self._spark(p, chart, hist.get("down", []), GOOD)
            self._spark(
                p,
                chart.adjusted(0, chart.height() * 0.45, 0, -chart.height() * 0.05),
                hist.get("up", []),
                self.acc,
            )

    def _disk_card(self, p, rect, data) -> None:
        active = self._hover == "diskcard" or self._press == "diskcard"
        self._panel(
            p, rect, active, self._press == "diskcard", self._focus == "diskcard"
        )
        pct = _num(data.get("disk"))
        self._label(
            p,
            "DISK",
            QRectF(
                rect.left() + DT.PAD_X,
                rect.top() + DT.PAD_Y - 3,
                rect.width() * 0.5,
                14,
            ),
            self.acc if active else TEXT_DIM,
            8.0,
        )
        self._text(
            p,
            f"{_num(data.get('disk_free')):.0f} GB FREE",
            QRectF(rect.right() - DT.PAD_X - 120, rect.top() + DT.PAD_Y - 3, 120, 14),
            7,
            MUTED,
            False,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self._text(
            p,
            f"{pct:.0f}%",
            QRectF(
                rect.left() + DT.PAD_X,
                rect.top() + DT.PAD_Y + 11,
                rect.width() - 2 * DT.PAD_X,
                26,
            ),
            DT.T_VALUE,
            _level_color(pct, self.acc),
            True,
        )
        self._text(
            p,
            f"R {self._rate(data.get('disk_read'))}    "
            f"W {self._rate(data.get('disk_write'))}",
            QRectF(
                rect.left() + DT.PAD_X,
                rect.bottom() - 32,
                rect.width() - 2 * DT.PAD_X,
                13,
            ),
            7,
            MUTED,
        )
        bar = QRectF(
            rect.left() + DT.PAD_X, rect.bottom() - 16, rect.width() - 2 * DT.PAD_X, 5
        )
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_q(_mix(BG, self.acc, 0.14)))
        p.drawRoundedRect(bar, 2, 2)
        p.setBrush(_q(_level_color(pct, self.acc), 230))
        p.drawRoundedRect(
            QRectF(
                bar.left(),
                bar.top(),
                bar.width() * max(0.0, min(1.0, pct / 100.0)),
                bar.height(),
            ),
            2,
            2,
        )

    # ── processes ────────────────────────────────────────────────────────────

    def _proc_cols(self, rr: QRectF):
        pad = 9.0
        w = rr.width() - 2 * pad
        name = QRectF(rr.left() + pad, rr.top(), w * 0.40, rr.height())
        pid = QRectF(name.right(), rr.top(), w * 0.16, rr.height())
        cpu = QRectF(pid.right(), rr.top(), w * 0.14, rr.height())
        mem = QRectF(cpu.right(), rr.top(), w * 0.30, rr.height())
        return name, pid, cpu, mem

    def _process_card(self, p, rect, data) -> None:
        active = self._hover == "processes"
        self._panel(p, rect, active, False, self._focus == "processes")
        self._label(
            p,
            "TOP PROCESSES",
            QRectF(
                rect.left() + DT.PAD_X,
                rect.top() + DT.PAD_Y - 3,
                rect.width() * 0.5,
                14,
            ),
            self.acc if active else TEXT_DIM,
            8.0,
        )
        self._text(
            p,
            f"{data.get('nproc', 0)} RUNNING",
            QRectF(rect.right() - DT.PAD_X - 120, rect.top() + DT.PAD_Y - 3, 120, 14),
            7,
            MUTED,
            False,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

        name, pid, cpu, mem = self._proc_cols(
            QRectF(rect.left() + 4, rect.top() + 24, rect.width() - 8, 14)
        )
        self._label(p, "NAME", name, MUTED, 6.5)
        self._label(p, "PID", pid, MUTED, 6.5)
        self._text(p, "CPU", cpu, 6.5, MUTED, True, Qt.AlignmentFlag.AlignCenter)
        self._text(
            p,
            "MEM",
            mem,
            6.5,
            MUTED,
            True,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

        self._proc_hits.clear()
        rows = list(data.get("top") or [])
        max_rows = int(max(3, (rect.height() - 46) // DT.ROW_H))
        y = rect.top() + 40
        for idx, row in enumerate(rows[:max_rows]):
            pid_v = int(row.get("pid") or 0)
            rr = QRectF(rect.left() + 8, y, rect.width() - 16, DT.ROW_H - 3)
            self._proc_hits[pid_v] = rr
            hkey = f"proc:{pid_v}"
            hov = self._hover == hkey or self._press == hkey
            sel = self._selected_process == pid_v and self._detail == "process"
            p.setPen(Qt.PenStyle.NoPen)
            if sel:
                p.setBrush(_q(self.acc, 46))
            elif hov:
                p.setBrush(_q(WHITE, 12))
            elif idx % 2:
                p.setBrush(_q(WHITE, 5))
            else:
                p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rr, 6, 6)
            if sel or self._focus == hkey:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(
                    QPen(
                        _q(self.acc if sel else WHITE, 170),
                        1,
                        Qt.PenStyle.DashLine if not sel else Qt.PenStyle.SolidLine,
                    )
                )
                p.drawRoundedRect(rr, 6, 6)

            name_r, pid_r, cpu_r, mem_r = self._proc_cols(rr)
            self._text(
                p,
                row.get("name") or "unknown",
                name_r,
                8,
                self.acc if sel else TEXT,
                sel or hov,
            )
            self._text(p, str(pid_v), pid_r, 7, MUTED)
            cpu_val = row.get("cpu")
            self._text(
                p,
                "—" if cpu_val is None else f"{_num(cpu_val):.1f}%",
                cpu_r,
                8,
                self.acc,
                True,
                Qt.AlignmentFlag.AlignCenter,
            )
            self._text(
                p,
                f"{_num(row.get('mb')):.0f} MB",
                mem_r,
                7.5,
                TEXT_DIM,
                False,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )
            y += DT.ROW_H

        if not rows:
            self._text(
                p,
                "Waiting for process telemetry…",
                QRectF(
                    rect.left() + DT.PAD_X,
                    rect.top() + 44,
                    rect.width() - 2 * DT.PAD_X,
                    20,
                ),
                8,
                MUTED,
                False,
                Qt.AlignmentFlag.AlignCenter,
            )

    # ── system card + generic rows ───────────────────────────────────────────

    def _rows_block(self, p, rect, rows) -> None:
        if rect.height() < 14 or not rows:
            return
        n = len(rows)
        rh = max(16.0, min(38.0, rect.height() / n))
        y = rect.top()
        for k, v in rows:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_q(WHITE, 5))
            p.drawRoundedRect(QRectF(rect.left(), y + 1, rect.width(), rh - 3), 4, 4)
            self._label(
                p,
                str(k),
                QRectF(rect.left() + 8, y, rect.width() * 0.34, rh),
                MUTED,
                7.0,
            )
            self._text(
                p,
                str(v),
                QRectF(rect.left() + rect.width() * 0.36, y, rect.width() * 0.62, rh),
                8.5,
                TEXT,
                True,
            )
            y += rh

    def _system_rows(self, data) -> list:
        rows = [
            ("HOST", platform.node() or "LOCAL"),
            ("OS", f"{platform.system()} {platform.release()}"),
            ("UPTIME", self._uptime(data.get("uptime"))),
            (
                "CPU",
                (
                    "N/A"
                    if data.get("cpu_freq") is None
                    else f"{_num(data.get('cpu_freq')):.2f} GHz"
                ),
            ),
        ]
        if data.get("gpu") is not None:
            rows.append(
                ("GPU", f"{data.get('gpu_name', 'N/A')} · {_num(data.get('gpu')):.0f}%")
            )
        if data.get("temp") is not None:
            rows.append(("TEMP", f"{_num(data.get('temp')):.0f}°C"))
        if data.get("battery") is not None:
            rows.append(
                (
                    "BATTERY",
                    f"{_num(data.get('battery')):.0f}%"
                    + (" · AC" if data.get("plugged") else ""),
                )
            )
        return rows

    def _system_card(self, p, rect, data) -> None:
        active = self._hover == "system" or self._press == "system"
        self._panel(p, rect, active, self._press == "system", self._focus == "system")
        self._label(
            p,
            "SYSTEM",
            QRectF(
                rect.left() + DT.PAD_X,
                rect.top() + DT.PAD_Y - 3,
                rect.width() * 0.5,
                14,
            ),
            self.acc if active else TEXT_DIM,
            8.0,
        )
        self._rows_block(
            p,
            rect.adjusted(DT.PAD_X, 26, -DT.PAD_X, -DT.PAD_Y),
            self._system_rows(data),
        )

    # ── history panel + chart with axes and scrubbing ───────────────────────

    def _history_panel(self, p, rect, hist) -> None:
        self._panel(p, rect)
        self._label(
            p,
            f"{self._selected_metric} · last 90s",
            QRectF(rect.left() + DT.PAD_X, rect.top() + 7, rect.width() * 0.5, 16),
            self.acc,
            8.5,
        )
        x = rect.right() - DT.PAD_X
        for name in reversed(("cpu", "ram", "gpu", "temp", "down", "up", "disk")):
            r = QRectF(x - 38, rect.top() + 6, 38, 16)
            x = r.left() - 4
            self._hit[f"chip:{name}"] = r
            on = self._selected_metric == name
            hov = self._hover == f"chip:{name}"
            p.setPen(QPen(_q(self.acc if on else MUTED, 210 if on or hov else 140), 1))
            p.setBrush(_q(self.acc, 55 if on else (28 if hov else 12)))
            p.drawRoundedRect(r, 8, 8)
            if self._focus == f"chip:{name}":
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(_q(WHITE, 170), 1.2, Qt.PenStyle.DashLine))
                p.drawRoundedRect(r.adjusted(-2, -2, 2, 2), 10, 10)
            self._text(
                p,
                name.upper(),
                r,
                6.5,
                self.acc if on else MUTED,
                on,
                Qt.AlignmentFlag.AlignCenter,
            )

        values = list(hist.get(self._selected_metric, []))
        self._history_chart(
            p,
            rect.adjusted(DT.PAD_X, 28, -DT.PAD_X, -DT.PAD_Y - 14),
            values,
            self._suffix(self._selected_metric),
        )
        if values:
            avg = sum(values) / len(values)
            self._text(
                p,
                f"MIN {min(values):.1f}   AVG {avg:.1f}   MAX {max(values):.1f}",
                QRectF(
                    rect.left() + DT.PAD_X,
                    rect.bottom() - 16,
                    rect.width() - 2 * DT.PAD_X,
                    12,
                ),
                7,
                MUTED,
                False,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )

    def _history_chart(self, p, rect, values, suffix="%") -> None:
        if rect.width() < 40 or rect.height() < 30:
            return
        p.setPen(QPen(_q(GRID, 210), 1))
        p.setBrush(_q(SURFACE, 110))
        p.drawRoundedRect(rect, 8, 8)

        if len(values) < 2:
            self._text(
                p,
                "Collecting telemetry…",
                rect,
                8,
                MUTED,
                False,
                Qt.AlignmentFlag.AlignCenter,
            )
            self._hit["history_chart"] = rect
            return

        maximum = max(1e-3, max(values) * 1.12)
        plot = rect.adjusted(10, 8, -38, -8)
        self._hit["history_chart"] = plot

        def lab(v):
            if suffix == " MB/s":
                return f"{v:.1f}"
            if suffix == "°C":
                return f"{v:.0f}°"
            return f"{v:.0f}%"

        p.setFont(self._font(6.5))
        for i in range(5):
            frac = i / 4.0
            yy = plot.top() + plot.height() * (1.0 - frac)
            p.setPen(QPen(_q(GRID, 150 if i else 210), 1))
            p.drawLine(QPointF(plot.left(), yy), QPointF(plot.right(), yy))
            p.setPen(QPen(_q(MUTED, 210), 1))
            p.drawText(
                QRectF(plot.right() + 4, yy - 7, 34, 14),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                lab(maximum * frac),
            )

        n = len(values)
        path = QPainterPath()
        for i, v in enumerate(values):
            x = plot.left() + plot.width() * i / (n - 1)
            y = (
                plot.bottom()
                - plot.height() * max(0.0, min(float(v), maximum)) / maximum
            )
            path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
        fill = QPainterPath(path)
        fill.lineTo(plot.right(), plot.bottom())
        fill.lineTo(plot.left(), plot.bottom())
        fill.closeSubpath()
        grad = QLinearGradient(0, plot.top(), 0, plot.bottom())
        grad.setColorAt(0.0, _q(self.acc, 52))
        grad.setColorAt(1.0, _q(self.acc, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        p.drawPath(fill)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_q(self.acc, 235), 1.6))
        p.drawPath(path)

        last_y = (
            plot.bottom()
            - plot.height() * max(0.0, min(float(values[-1]), maximum)) / maximum
        )
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_q(TEXT, 240))
        p.drawEllipse(QPointF(plot.right(), last_y), 2.4, 2.4)

        if self._hover == "history_chart" and plot.contains(self._mouse):
            frac = (self._mouse.x() - plot.left()) / max(1.0, plot.width())
            idx = max(0, min(n - 1, int(round(frac * (n - 1)))))
            sx = plot.left() + plot.width() * idx / (n - 1)
            sy = (
                plot.bottom()
                - plot.height() * max(0.0, min(float(values[idx]), maximum)) / maximum
            )
            p.setPen(QPen(_q(TEXT_DIM, 110), 1, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(sx, plot.top()), QPointF(sx, plot.bottom()))
            p.setBrush(_q(self.acc, 240))
            p.drawEllipse(QPointF(sx, sy), 3.2, 3.2)
            pill = QRectF(sx - 32, plot.top() + 2, 64, 16)
            pill.moveLeft(max(plot.left() + 2, min(pill.left(), plot.right() - 66)))
            p.setPen(QPen(_q(self.acc, 220), 1))
            p.setBrush(_q(BG, 235))
            p.drawRoundedRect(pill, 8, 8)
            self._text(
                p,
                f"{values[idx]:.1f}{suffix}",
                pill,
                7.5,
                TEXT,
                True,
                Qt.AlignmentFlag.AlignCenter,
            )

    # ── detail overlay (unified, animated) ──────────────────────────────────

    def _open_detail(self, name: str) -> None:
        if self._detail == name and not self._closing:
            return
        self._detail = name
        self._closing = False
        self._detail_t = 0.0
        self._focus = "detail:back"

    def _close_detail(self) -> None:
        if self._detail:
            self._closing = True
            self._focus = ""

    def _metric_subtitle(self, d: str, data: dict) -> str:
        if d == "ram":
            return (
                f"{_num(data.get('ram_used')):.1f} / {_num(data.get('ram_total')):.1f} GB"
                f"  ·  SWAP {_num(data.get('swap')):.1f}%"
            )
        if d == "gpu":
            return f"{data.get('gpu_name', 'N/A')}  ·  VRAM {_num(data.get('gpu_mem')):.0f}%"
        if d == "temp":
            return f"SOURCE  {data.get('temp_source', 'N/A')}"
        if d == "disk":
            return (
                f"{_num(data.get('disk_free')):.1f} GB FREE OF "
                f"{_num(data.get('disk_total')):.1f} GB"
            )
        if d in ("down", "up"):
            return f"NOW  ↓ {self._rate(data.get('down'))}   ↑ {self._rate(data.get('up'))}"
        return f"CURRENT  {_num(data.get(d, 0)):.1f}%"

    def _draw_detail_overlay(self, p, content: QRectF, data, hist) -> None:
        if not self._detail:
            return
        t = _ease_out(self._detail_t)
        if t <= 0.01:
            return
        r = QRectF(
            content.left(),
            content.top() + (1.0 - t) * 16.0,
            content.width(),
            content.height(),
        )
        p.setOpacity(0.2 + 0.8 * t)
        self._panel(p, r, True)

        if self._detail in ("process", "network", "system"):
            title = self.DETAIL_TITLES[self._detail]
        else:
            title = f"{self.DETAIL_TITLES.get(self._detail, self._detail.upper())} · HISTORY"
        self._label(
            p,
            title,
            QRectF(r.left() + DT.PAD_X, r.top() + 9, r.width() - 110, 18),
            self.acc,
            9.5,
            1.0,
        )
        if self._detail not in ("process", "network", "system"):
            self._text(
                p,
                self._metric_subtitle(self._detail, data),
                QRectF(r.left() + DT.PAD_X, r.top() + 27, r.width() - 110, 14),
                7.5,
                MUTED,
            )

        back = QRectF(r.right() - DT.PAD_X - 76, r.top() + 9, 76, 24)
        self._hit["detail:back"] = back
        hov = self._hover == "detail:back"
        p.setPen(QPen(_q(self.acc if hov else MUTED, 215), 1))
        p.setBrush(_q(self.acc, 40 if hov else 14))
        p.drawRoundedRect(back, 7, 7)
        self._text(
            p,
            "ESC · BACK",
            back,
            7,
            self.acc if hov else TEXT_DIM,
            bool(hov),
            Qt.AlignmentFlag.AlignCenter,
        )

        body = r.adjusted(DT.PAD_X, 44, -DT.PAD_X, -DT.PAD_Y)
        d = self._detail
        if d == "process":
            self._body_process(p, body, data)
        elif d == "network":
            self._body_network(p, body, data)
        elif d == "system":
            self._rows_block(p, body, self._system_rows(data))
        elif d == "temp":
            self._body_temp(p, body, data)
        else:
            values = list(hist.get(d, []))
            self._history_chart(p, body, values, self._suffix(d))
            if values:
                avg = sum(values) / len(values)
                self._text(
                    p,
                    f"MIN {min(values):.1f}   AVG {avg:.1f}   "
                    f"MAX {max(values):.1f}   NOW {values[-1]:.1f}",
                    QRectF(body.left(), body.bottom() + 4, body.width(), 14),
                    8,
                    TEXT_DIM,
                    False,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                )
        p.setOpacity(1.0)

    def _body_process(self, p, rect, data) -> None:
        pid = self._selected_process
        row = next(
            (it for it in (data.get("top") or []) if int(it.get("pid") or 0) == pid),
            None,
        )
        if row is None:
            self._text(
                p,
                "Process ended or telemetry is unavailable.",
                rect,
                9,
                MUTED,
                False,
                Qt.AlignmentFlag.AlignCenter,
            )
            return
        try:
            proc = psutil.Process(pid)
            with proc.oneshot():
                name = proc.name()
                status = proc.status()
                threads = proc.num_threads()
                user = proc.username()
                exe = proc.exe() or "N/A"
                created = time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(proc.create_time())
                )
                mem = proc.memory_info().rss / (1024**2)
                cpu = proc.cpu_percent(interval=None)
        except Exception:
            name = row.get("name", "unknown")
            status = "inaccessible"
            threads = "N/A"
            user = "N/A"
            exe = "N/A"
            created = "N/A"
            mem = _num(row.get("mb"))
            cpu = row.get("cpu")
        self._rows_block(
            p,
            rect,
            [
                ("NAME", name),
                ("PID", pid),
                ("CPU", "N/A" if cpu is None else f"{_num(cpu):.1f}%"),
                ("MEMORY", f"{_num(mem):.1f} MB"),
                ("THREADS", threads),
                ("STATUS", status),
                ("USER", user),
                ("STARTED", created),
                ("EXECUTABLE", exe),
            ],
        )

    def _body_network(self, p, rect, data) -> None:
        self._text(
            p,
            f"↓ {self._rate(data.get('down'))}",
            QRectF(rect.left(), rect.top(), rect.width() * 0.5, 26),
            11,
            GOOD,
            True,
        )
        self._text(
            p,
            f"↑ {self._rate(data.get('up'))}",
            QRectF(
                rect.left() + rect.width() * 0.5, rect.top(), rect.width() * 0.5, 26
            ),
            11,
            self.acc,
            True,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        rows = [
            (
                item.get("name", "?"),
                f"↓ {self._bytes(item.get('recv'))}   ↑ {self._bytes(item.get('sent'))}",
            )
            for item in (data.get("interfaces") or [])[:10]
        ]
        if rows:
            self._rows_block(p, rect.adjusted(0, 32, 0, 0), rows)
        else:
            self._text(
                p,
                "No interface telemetry yet.",
                rect.adjusted(0, 32, 0, 0),
                8,
                MUTED,
                False,
                Qt.AlignmentFlag.AlignCenter,
            )

    def _body_temp(self, p, rect, data) -> None:
        value = data.get("temp")
        self._label(
            p, "CURRENT", QRectF(rect.left(), rect.top(), rect.width(), 14), MUTED, 7.5
        )
        self._text(
            p,
            "N/A" if value is None else f"{_num(value):.1f}°C",
            QRectF(rect.left(), rect.top() + 16, rect.width(), rect.height() - 52),
            max(24.0, rect.height() * 0.28),
            MUTED if value is None else _level_color(_num(value) * 1.15, self.acc),
            True,
        )
        self._text(
            p,
            "Comfortable range ≈ 40–85°C",
            QRectF(rect.left(), rect.bottom() - 22, rect.width(), 16),
            7.5,
            MUTED,
        )

    # ── process operations ───────────────────────────────────────────────────

    def _selected_process_row(self):
        pid = self._selected_process
        if not pid:
            return None
        try:
            proc = psutil.Process(int(pid))
            with proc.oneshot():
                name = proc.name() or "unknown"
                exe = proc.exe() or "N/A"
                status = proc.status()
                cpu = proc.cpu_percent(interval=None)
                mem = proc.memory_info().rss / (1024**2)
                threads = proc.num_threads()
                user = proc.username()
                created = time.strftime(
                    "%Y-%m-%d %H:%M:%S",
                    time.localtime(proc.create_time()),
                )
            return {
                "pid": int(pid),
                "name": name,
                "exe": exe,
                "status": status,
                "cpu": cpu,
                "mb": mem,
                "threads": threads,
                "user": user,
                "created": created,
            }
        except Exception:
            return None

    def _process_payload(self, info: dict) -> str:
        return (
            f"Process: {info['name']}\n"
            f"PID: {info['pid']}\n"
            f"CPU: {info['cpu'] if info['cpu'] is not None else 'N/A'}%\n"
            f"Memory: {info['mb']:.1f} MB\n"
            f"Threads: {info['threads']}\n"
            f"Status: {info['status']}\n"
            f"User: {info['user']}\n"
            f"Started: {info['created']}\n"
            f"Executable: {info['exe']}"
        )

    def _copy_process(self, kind: str = "details") -> bool:
        info = self._selected_process_row()
        if info is None:
            return False

        text = info["exe"] if kind == "path" else self._process_payload(info)
        try:
            QApplication.clipboard().setText(str(text))
            return True
        except Exception:
            return False

    def _analyze_process(self) -> bool:
        info = self._selected_process_row()
        if info is None or self.player is None:
            return False

        callback = getattr(self.player, "on_text_command", None)
        if not callable(callback):
            return False

        prompt = (
            "Analyze this running Windows process using the information below. "
            "Explain what it likely is, whether its CPU/RAM usage is expected, "
            "what could cause abnormal usage, and what safe next steps I can "
            "take. Do not terminate or modify it.\n\n" + self._process_payload(info)
        )
        threading.Thread(
            target=callback,
            args=(prompt,),
            daemon=True,
            name="jarvis-process-analysis",
        ).start()
        return True

    def _terminate_process(self, method: str = "terminate") -> bool:
        info = self._selected_process_row()
        if info is None:
            return False

        pid = int(info["pid"])
        name = str(info["name"])

        def do_it() -> None:
            try:
                proc = psutil.Process(pid)
                if method == "kill":
                    proc.kill()
                else:
                    proc.terminate()
            except Exception as exc:
                try:
                    QApplication.clipboard().setText(
                        f"Could not {method} {name} (PID {pid}): {exc}"
                    )
                except Exception:
                    pass

        threading.Thread(
            target=do_it,
            daemon=True,
            name="jarvis-process-operation",
        ).start()
        return True

    def _confirm_process_operation(self, operation: str) -> None:
        info = self._selected_process_row()
        if info is None:
            return

        if operation in {"terminate", "kill"}:
            text = (
                f"Terminate process '{info['name']}' (PID {info['pid']})?\n\n"
                "The application may lose unsaved work."
            )
            answer = QMessageBox.question(
                self,
                "Confirm process operation",
                text,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._terminate_process(operation)
                self.update()
            return

        try:
            proc = psutil.Process(int(info["pid"]))
            if operation == "suspend":
                proc.suspend()
            elif operation == "resume":
                proc.resume()
            elif operation == "open_location":
                exe = info.get("exe") or ""
                if exe and os.path.exists(exe):
                    if platform.system() == "Windows":
                        os.startfile(os.path.dirname(exe))
                    else:
                        import subprocess

                        subprocess.Popen(["xdg-open", os.path.dirname(exe)])
        except Exception as exc:
            try:
                print(f"[SystemDashboard] {operation} PID {info['pid']} failed: {exc}")
            except Exception:
                pass

    def _show_process_menu(self, pid: Optional[int] = None, global_pos=None) -> None:
        if pid is not None:
            self._selected_process = int(pid)
            self._detail = "process"
            self._detail_t = 1.0

        info = self._selected_process_row()
        if info is None:
            return

        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background: {self._css_rgb(SURFACE_2)};
                color: {self._css_rgb(TEXT)};
                border: 1px solid {self._css_rgb(self.acc)};
                padding: 5px;
            }}
            QMenu::item {{ padding: 7px 24px; }}
            QMenu::item:selected {{
                background: {self._css_rgb(self.acc)};
                color: {self._css_rgb(BG)};
            }}
        """)

        a_details = menu.addAction("View details")
        a_copy = menu.addAction("Copy process details")
        a_path = menu.addAction("Copy executable path")
        a_analyze = menu.addAction("Analyze with JARVIS")
        menu.addSeparator()
        a_location = menu.addAction("Open executable location")
        a_suspend = menu.addAction("Suspend process")
        a_resume = menu.addAction("Resume process")
        menu.addSeparator()
        a_term = menu.addAction("Terminate process")

        chosen = menu.exec(global_pos or self.mapToGlobal(self._mouse.toPoint()))
        if chosen == a_details:
            self._open_detail("process")
        elif chosen == a_copy:
            self._copy_process("details")
        elif chosen == a_path:
            self._copy_process("path")
        elif chosen == a_analyze:
            self._analyze_process()
        elif chosen == a_location:
            self._confirm_process_operation("open_location")
        elif chosen == a_suspend:
            self._confirm_process_operation("suspend")
        elif chosen == a_resume:
            self._confirm_process_operation("resume")
        elif chosen == a_term:
            self._confirm_process_operation("terminate")

    def _css_rgb(self, rgb) -> str:
        return "#%02x%02x%02x" % (int(rgb[0]), int(rgb[1]), int(rgb[2]))

    def contextMenuEvent(self, event) -> None:
        key = self._hit_at(event.position())
        if key.startswith("proc:"):
            try:
                pid = int(key.split(":", 1)[1])
            except Exception:
                pid = None
            if pid is not None:
                self._show_process_menu(pid, event.globalPos())
                event.accept()
                return
        event.ignore()

    # ── interaction ──────────────────────────────────────────────────────────

    def _is_interactive(self, key: str) -> bool:
        return key.startswith(
            ("kpi:", "proc:", "micro:", "chip:", "detail:")
        ) or key in ("core", "network", "diskcard", "system", "header:close")

    def _hit_at(self, pos: QPointF) -> str:
        for key in ("detail:back", "header:close"):
            r = self._hit.get(key)
            if r is not None and r.contains(pos):
                return key
        if self._detail:
            if self._detail in ("cpu", "ram", "gpu", "disk", "down", "up"):
                r = self._hit.get("history_chart")
                if r is not None and r.contains(pos):
                    return "history_chart"
            return ""
        for key, r in self._hit.items():
            if self._is_interactive(key) and r.contains(pos):
                return key
        for pid, r in self._proc_hits.items():
            if r.contains(pos):
                return f"proc:{pid}"
        r = self._hit.get("history_chart")
        if r is not None and r.contains(pos):
            return "history_chart"
        return ""

    def _update_cursor(self, key: str) -> None:
        if key == "history_chart":
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(
                Qt.CursorShape.PointingHandCursor
                if self._is_interactive(key)
                else Qt.CursorShape.ArrowCursor
            )

    def _activate(self, key: str) -> None:
        if key.startswith("kpi:"):
            name = key[4:]
            if name in self.METRICS:
                self._selected_metric = name
            if self._mode() != "full":
                self._open_detail(name)
        elif key.startswith("chip:"):
            self._selected_metric = key[5:]
        elif key.startswith("micro:"):
            self._open_detail(
                {"0": "ram", "1": "network", "2": "temp"}.get(key[6:], "cpu")
            )
        elif key.startswith("proc:"):
            try:
                self._selected_process = int(key[5:])
            except ValueError:
                return
            self._open_detail("process")
        elif key == "network":
            self._open_detail("network")
        elif key == "system":
            self._open_detail("system")
        elif key == "diskcard":
            self._selected_metric = "disk"
            if self._mode() != "full":
                self._open_detail("disk")
        elif key == "core":
            self._open_detail(self._selected_metric or "cpu")
        elif key == "detail:back":
            self._close_detail()
        elif key == "header:close":
            self.close_board()

    def _focusables(self) -> list[str]:
        if self._detail:
            return ["detail:back"]
        items = []
        for key, r in self._hit.items():
            if self._is_interactive(key):
                items.append((r.top(), r.left(), key))
        for pid, r in self._proc_hits.items():
            items.append((r.top(), r.left(), f"proc:{pid}"))
        items.sort()
        return [k for _, _, k in items]

    def _cycle_focus(self, step: int) -> None:
        keys = self._focusables()
        if not keys:
            return
        try:
            i = keys.index(self._focus)
        except ValueError:
            i = -1 if step > 0 else 0
        self._focus = keys[(i + step) % len(keys)]
        self.update()

    def mouseMoveEvent(self, e) -> None:
        self._mouse = e.position()
        self._last_input = time.monotonic()
        key = self._hit_at(self._mouse)
        if key != self._hover:
            self._hover = key
            self._update_cursor(key)
        self.update()

    def mousePressEvent(self, e) -> None:
        self._last_input = time.monotonic()
        self._press = self._hit_at(e.position())
        self.setFocus()
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        self._last_input = time.monotonic()
        key = self._hit_at(e.position())
        if key and key == self._press:
            self._activate(key)
        self._press = ""
        self.update()

    def leaveEvent(self, _e) -> None:
        self._hover = ""
        self._mouse = QPointF(-1, -1)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def keyPressEvent(self, e) -> None:
        self._last_input = time.monotonic()
        k = e.key()
        if k == Qt.Key.Key_Escape:
            # ESC belongs to JARVIS's global interrupt. Only consume it when
            # we are inside a dashboard detail view; otherwise let the event
            # bubble to MainWindow so a speaking JARVIS can still be stopped.
            if self._detail:
                self._close_detail()
                e.accept()
                self.update()
                return
            e.ignore()
            return
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            if self._focus:
                self._activate(self._focus)
        elif k in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self._cycle_focus(1 if k == Qt.Key.Key_Tab else -1)
        elif Qt.Key.Key_1 <= k <= Qt.Key.Key_7:
            order = ("cpu", "ram", "gpu", "temp", "disk", "network", "system")
            name = order[k - Qt.Key.Key_1]
            self._activate(name if name in ("network", "system") else f"kpi:{name}")
        self.update()

    # ── paint ────────────────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        if not p.isActive():
            return
        try:
            live = _live_accent()
            if live != self._last_acc:
                self.acc = live
                self._last_acc = live
        except Exception:
            pass

        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.fillRect(self.rect(), _q(BG))

        mode = self._mode()
        data, hist = self.sampler.snapshot()
        L = self._layout(mode, data)
        self._hit = {
            k: v for k, v in L.items() if isinstance(v, QRectF) and k != "content"
        }
        self._proc_hits.clear()

        self._draw_header(p, L["header"], data)
        cpu = _num(data.get("cpu"))

        if mode == "compact":
            self._draw_core(p, L["core"], cpu, True)
            self._draw_micro(p, L.get("micro", QRectF()), data)
        elif mode == "medium":
            self._draw_core(p, L["core"], cpu, False)
            self._system_card(p, L["system"], data)
            self._card(
                p,
                L["kpi:cpu"],
                "cpu",
                "CPU",
                f"{cpu:.0f}%",
                cpu,
                hist.get("cpu", []),
                (
                    ""
                    if data.get("cpu_freq") is None
                    else f"{_num(data.get('cpu_freq')):.1f} GHz"
                ),
            )
            self._card(
                p,
                L["kpi:ram"],
                "ram",
                "MEMORY",
                f"{_num(data.get('ram')):.0f}%",
                _num(data.get("ram")),
                hist.get("ram", []),
                f"{_num(data.get('ram_used')):.0f}/{_num(data.get('ram_total')):.0f} GB",
            )
            self._card(
                p,
                L["kpi:disk"],
                "disk",
                "DISK",
                f"{_num(data.get('disk')):.0f}%",
                _num(data.get("disk")),
                hist.get("disk", []),
                f"{_num(data.get('disk_free')):.0f} GB FREE",
            )
            self._network_card(p, L["network"], data, hist)
            self._process_card(p, L["processes"], data)
        else:
            self._draw_core(p, L["core"], cpu, False)
            self._system_card(p, L["system"], data)
            gpu, temp = data.get("gpu"), data.get("temp")
            self._card(
                p, L["kpi:cpu"], "cpu", "CPU", f"{cpu:.0f}%", cpu, hist.get("cpu", [])
            )
            self._card(
                p,
                L["kpi:ram"],
                "ram",
                "MEMORY",
                f"{_num(data.get('ram')):.0f}%",
                _num(data.get("ram")),
                hist.get("ram", []),
                f"{_num(data.get('ram_used')):.1f}/{_num(data.get('ram_total')):.1f} GB",
            )
            self._card(
                p,
                L["kpi:gpu"],
                "gpu",
                "GPU",
                "N/A" if gpu is None else f"{_num(gpu):.0f}%",
                None if gpu is None else _num(gpu),
                hist.get("gpu", []),
                (
                    "N/A"
                    if data.get("gpu_mem") is None
                    else f"VRAM {_num(data.get('gpu_mem')):.0f}%"
                ),
            )
            self._card(
                p,
                L["kpi:temp"],
                "temp",
                "TEMP",
                "N/A" if temp is None else f"{_num(temp):.0f}°C",
                None if temp is None else _num(temp) * 1.15,
                hist.get("temp", []),
            )
            self._network_card(p, L["network"], data, hist)
            self._disk_card(p, L["diskcard"], data)
            self._history_panel(p, L["history"], hist)
            self._process_card(p, L["processes"], data)

        self._draw_detail_overlay(p, L["content"], data, hist)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

_DASH: Optional[_Dashboard] = None
_GUI: Optional["_GuiBridge"] = None
_GUI_LOCK = threading.Lock()


class _GuiBridge(QObject):
    """Persistent dispatcher whose QObject affinity is guaranteed to be Qt GUI."""

    fired = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self.fired.connect(self._execute, Qt.ConnectionType.QueuedConnection)

    @pyqtSlot(object)
    def _execute(self, job) -> None:
        try:
            job()
        except Exception as exc:
            try:
                print(f"[SystemDashboard] GUI operation failed: {exc}")
            except Exception:
                pass


def _ensure_gui_bridge(app: QApplication) -> Optional[_GuiBridge]:
    """
    Lazily create the bridge at the first real tool call.

    Plugin modules are imported during discovery and that can happen before the
    final Qt application object/event loop is established. Creating the bridge
    at module import time therefore gives it an unreliable thread affinity.

    Here the bridge is created only after QApplication exists. If the plugin
    call is coming from JARVIS's worker thread, the QObject is created there
    first and immediately moved to the real QApplication thread. This makes
    the subsequent queued signal execute in the GUI event loop.
    """
    global _GUI

    gui_thread = app.thread()
    if gui_thread is None:
        return None

    with _GUI_LOCK:
        if _GUI is not None:
            try:
                if _GUI.thread() is gui_thread:
                    return _GUI
            except Exception:
                _GUI = None

        try:
            bridge = _GuiBridge()

            # The function is normally called from JARVIS's plugin worker
            # thread, so moveToThread() is legal here: it is being called from
            # the object's current thread before we hand it to Qt's GUI thread.
            if bridge.thread() is not gui_thread:
                bridge.moveToThread(gui_thread)

            _GUI = bridge
            return _GUI
        except Exception as exc:
            try:
                print(f"[SystemDashboard] Failed to initialize GUI bridge: {exc}")
            except Exception:
                pass
            return None


def _queue_gui(job) -> bool:
    app = QApplication.instance()
    if app is None:
        return False

    if app.thread() is QThread.currentThread():
        try:
            job()
            return True
        except Exception as exc:
            try:
                print(f"[SystemDashboard] GUI operation failed: {exc}")
            except Exception:
                pass
            return False

    bridge = _ensure_gui_bridge(app)
    if bridge is None:
        return False

    try:
        bridge.fired.emit(job)
        return True
    except Exception as exc:
        try:
            print(f"[SystemDashboard] Failed to queue GUI operation: {exc}")
        except Exception:
            pass
        return False


def _resolve_host(player=None) -> Optional[QWidget]:
    """Resolve JARVIS's real HUD stack. This function is called on GUI thread."""
    try:
        win = getattr(player, "_win", None) if player is not None else None
        host = getattr(win, "_hud_cam_stack", None)
        if isinstance(host, QWidget):
            return host
    except Exception:
        pass

    app = QApplication.instance()
    if app is None:
        return None

    for widget in app.allWidgets():
        try:
            if widget.objectName() == "_hud_cam_stack":
                return widget
        except Exception:
            pass

    # The current JARVIS UI keeps the central stack as an attribute, but its
    # objectName is not guaranteed. Search MainWindow instances as a second
    # path without changing ui.py.
    for widget in app.topLevelWidgets():
        try:
            stack = getattr(widget, "_hud_cam_stack", None)
            if isinstance(stack, QWidget):
                return stack
        except Exception:
            pass

    return None


def _gui_open(action: str, player=None) -> dict:
    global _DASH

    action = str(action or "open").strip().lower()
    if action not in {"open", "close", "toggle", "status"}:
        return {"ok": False, "error": f"unknown action: {action}"}

    result = {"ok": True, "action": action, "open": False, "mode": None}

    if action == "status":
        result["open"] = bool(_DASH is not None and _DASH.isVisible())
        result["mode"] = _DASH._mode() if result["open"] else None
        return result

    if action == "close":
        if _DASH is not None:
            _DASH.close_board()
        return result

    host = _resolve_host(player)
    if host is None:
        return {"ok": False, "error": "JARVIS HUD host widget not found"}

    # Camera/video occupy the same central surface. Give it back before the
    # dashboard is shown.
    try:
        if hasattr(host, "currentIndex") and host.currentIndex() != 0:
            stop_video = getattr(player, "stop_video", None)
            if callable(stop_video):
                stop_video()
            host.setCurrentIndex(0)
    except Exception:
        pass

    if _DASH is None or _DASH.host is not host:
        if _DASH is not None:
            try:
                _DASH.close_board()
                _DASH.deleteLater()
            except Exception:
                pass
        _DASH = _Dashboard(host, host)

    _DASH.player = player

    if action == "toggle" and _DASH.isVisible():
        _DASH.close_board()
    else:
        # Parent, geometry and show are all executed on the GUI thread because
        # _gui_open() itself is only called through _queue_gui().
        _DASH.setParent(host)
        _DASH.setGeometry(host.rect())
        _DASH.open()
        _DASH.raise_()
        _DASH.update()
        try:
            host.update()
        except Exception:
            pass

    result["open"] = bool(_DASH.isVisible())
    result["mode"] = _DASH._mode() if result["open"] else None
    return result


def _process_from_pid(pid: int) -> Optional[dict]:
    try:
        proc = psutil.Process(int(pid))
        with proc.oneshot():
            name = proc.name() or "unknown"
            exe = proc.exe() or "N/A"
            status = proc.status()
            threads = proc.num_threads()
            user = proc.username()
            memory = proc.memory_info().rss / (1024**2)
            cpu = proc.cpu_percent(interval=None)
            created = time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(proc.create_time())
            )
        return {
            "pid": int(pid),
            "name": name,
            "exe": exe,
            "status": status,
            "threads": threads,
            "user": user,
            "memory": memory,
            "cpu": cpu,
            "created": created,
        }
    except Exception:
        return None


def _process_tool_action(action: str, operation: str, pid: int, player=None) -> dict:
    info = _process_from_pid(pid)
    if info is None:
        return {"ok": False, "error": f"Process PID {pid} is no longer available."}

    if operation == "details":
        return {"ok": True, "process": info}

    if operation == "copy":
        text = (
            f"Process: {info['name']}\nPID: {info['pid']}\n"
            f"CPU: {info['cpu']}%\nMemory: {info['memory']:.1f} MB\n"
            f"Threads: {info['threads']}\nStatus: {info['status']}\n"
            f"User: {info['user']}\nStarted: {info['created']}\n"
            f"Executable: {info['exe']}"
        )
        ok = _queue_gui(lambda: QApplication.clipboard().setText(text))
        return {"ok": ok, "operation": "copy", "pid": pid}

    if operation == "analyze":
        callback = getattr(player, "on_text_command", None)
        if not callable(callback):
            return {"ok": False, "error": "JARVIS text-command channel is unavailable."}
        prompt = (
            "Analyze this running process. Explain what it likely is, whether its "
            "resource usage is expected, possible reasons for unusual usage, and "
            "safe next steps. Do not modify or terminate it.\n\n"
            f"Process: {info['name']} | PID {info['pid']} | CPU {info['cpu']}% | "
            f"RAM {info['memory']:.1f} MB | Status {info['status']} | "
            f"Executable {info['exe']}"
        )
        threading.Thread(
            target=callback,
            args=(prompt,),
            daemon=True,
            name="jarvis-process-analysis-tool",
        ).start()
        return {"ok": True, "operation": "analyze", "pid": pid}

    if operation == "open_location":
        exe = str(info.get("exe") or "")
        if not exe or not os.path.exists(exe):
            return {"ok": False, "error": "Executable path is unavailable."}

        def open_location() -> None:
            folder = os.path.dirname(exe)
            if platform.system() == "Windows":
                os.startfile(folder)
            elif platform.system() == "Darwin":
                import subprocess

                subprocess.Popen(["open", folder])
            else:
                import subprocess

                subprocess.Popen(["xdg-open", folder])

        ok = _queue_gui(open_location)
        return {"ok": ok, "operation": operation, "pid": pid}

    if operation in {"terminate", "suspend", "resume"}:

        def ask_and_run() -> None:
            try:
                proc = psutil.Process(pid)
                if operation == "terminate":
                    answer = QMessageBox.question(
                        _DASH,
                        "Confirm process operation",
                        f"Terminate '{info['name']}' (PID {pid})?\n\n"
                        "Unsaved application data may be lost.",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if answer != QMessageBox.StandardButton.Yes:
                        return
                    proc.terminate()
                elif operation == "suspend":
                    proc.suspend()
                else:
                    proc.resume()
            except Exception as exc:
                try:
                    print(f"[SystemDashboard] {operation} PID {pid} failed: {exc}")
                except Exception:
                    pass

        ok = _queue_gui(ask_and_run)
        return {"ok": ok, "operation": operation, "pid": pid}

    return {"ok": False, "error": f"Unknown process operation: {operation}"}


def run(parameters, player=None, session_memory=None) -> dict:
    """Standard JARVIS plugin contract.

    `parameters` is the complete tool-argument dictionary. GUI work is queued
    to a persistent Qt-thread bridge, so this plugin never blocks the worker
    waiting for the event loop.
    """
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "open").strip().lower()

    if action == "process":
        operation = str(params.get("operation") or "details").strip().lower()
        try:
            pid = int(params.get("pid"))
        except Exception:
            return {"ok": False, "error": "A valid process PID is required."}
        return _process_tool_action(
            operation=operation, action=action, pid=pid, player=player
        )

    # Open/close/toggle/status are intentionally asynchronous for non-status
    # calls. This removes the timeout failure mode completely.
    if action == "status":
        result_box = {"result": None}
        event = threading.Event()

        def status_job():
            try:
                result_box["result"] = _gui_open("status", player)
            finally:
                event.set()

        if not _queue_gui(status_job):
            return {"ok": False, "error": "Qt application is not running"}

        if event.wait(1.0):
            return result_box["result"]
        return {"ok": True, "action": "status", "open": None, "pending": True}

    queued = _queue_gui(lambda: _gui_open(action, player))
    if not queued:
        return {"ok": False, "error": "Qt application is not running"}

    return {
        "ok": True,
        "action": action,
        "queued": True,
        "message": f"System dashboard {action} request queued.",
    }
