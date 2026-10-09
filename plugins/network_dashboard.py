"""A visual, live network diagnostics dashboard for the JARVIS HUD.

Shows real-time download/upload speeds, 60-second history, latency to
common DNS servers, connection count and interface summary.
All Qt work stays inside the shared live-panel module.
"""

from __future__ import annotations

import math
import re
import socket
import subprocess
import threading
import time
from collections import deque
from typing import Optional

import psutil
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QWidget

try:
    from plugins import _live_panels_core as _live_panels
except ImportError:
    import _live_panels_core as _live_panels  # type: ignore


PLUGIN = {
    "name": "network_dashboard",
    "description": (
        "Open a live network diagnostics dashboard in the JARVIS HUD. "
        "Use this when the user asks to see, show, open or visualize "
        "network speed, download, upload, ping, latency, connections, "
        "Wi-Fi, internet status or network diagnostics."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["open", "close", "status"],
                "description": (
                    "open (default) — show the network board, "
                    "close — hide it, "
                    "status — report whether it is currently open."
                ),
            },
        },
        "required": [],
    },
    "behavior": "NON_BLOCKING",
    "scheduling": "SILENT",
}


# ─────────────────────────────────────────────────────────────────────────────
# Network sampler
# ─────────────────────────────────────────────────────────────────────────────


class _NetSampler:
    def __init__(self):
        self.lock = threading.Lock()
        self.data: dict = {
            "down": 0.0,
            "up": 0.0,
            "ping": {},
            "established": 0,
            "listening": 0,
            "ifaces": [],
            "total_recv_gb": 0.0,
            "total_sent_gb": 0.0,
        }
        self.hist = {
            "down": deque([0.0] * 60, maxlen=60),
            "up": deque([0.0] * 60, maxlen=60),
        }
        self.stop = threading.Event()
        self.threads: list = []

    def start(self) -> None:
        if any(t.is_alive() for t in self.threads):
            return
        self.stop = threading.Event()
        self.threads = [
            threading.Thread(target=self._run_vitals, name="net-vitals", daemon=True),
            threading.Thread(target=self._run_ping, name="net-ping", daemon=True),
        ]
        for t in self.threads:
            t.start()

    def _run_vitals(self) -> None:
        try:
            net0 = psutil.net_io_counters()
        except Exception:
            net0 = None
        t0 = time.time()
        self.stop.wait(0.35)
        tick = 0

        while not self.stop.is_set():
            try:
                net1 = psutil.net_io_counters()
                t1 = time.time()
                dt = max(0.25, t1 - t0)

                if net0 is not None:
                    down = max(
                        0.0, (net1.bytes_recv - net0.bytes_recv) * 8 / dt / 1_000_000
                    )
                    up = max(
                        0.0, (net1.bytes_sent - net0.bytes_sent) * 8 / dt / 1_000_000
                    )
                else:
                    down = up = 0.0

                net0, t0 = net1, t1

                # connections
                try:
                    conns = psutil.net_connections(kind="inet")
                    established = sum(
                        1 for c in conns if getattr(c, "status", "") == "ESTABLISHED"
                    )
                    listening = sum(
                        1 for c in conns if getattr(c, "status", "") == "LISTEN"
                    )
                except Exception:
                    established = listening = 0

                # interfaces
                ifaces = []
                try:
                    stats = psutil.net_if_stats()
                    addrs = psutil.net_if_addrs()
                    for name, st in stats.items():
                        if not st.isup:
                            continue
                        ip = ""
                        for a in addrs.get(name, []):
                            if a.family == socket.AF_INET and not a.address.startswith(
                                "127."
                            ):
                                ip = a.address
                                break
                        ifaces.append(
                            {
                                "name": name,
                                "ip": ip or "–",
                                "speed": st.speed,
                            }
                        )
                except Exception:
                    pass

                d = {
                    "down": down,
                    "up": up,
                    "established": established,
                    "listening": listening,
                    "ifaces": ifaces[:4],
                    "total_recv_gb": net1.bytes_recv / 1e9,
                    "total_sent_gb": net1.bytes_sent / 1e9,
                }

                with self.lock:
                    # preserve existing ping results
                    d["ping"] = self.data.get("ping", {})
                    self.data = d
                    if tick == 0:
                        self.hist["down"].extend([down] * 59)
                        self.hist["up"].extend([up] * 59)
                    self.hist["down"].append(down)
                    self.hist["up"].append(up)

            except Exception as exc:
                _live_panels._once("net sampler", exc)

            tick += 1
            self.stop.wait(1.0)

    def _run_ping(self) -> None:
        targets = [("Cloudflare", "1.1.1.1"), ("Google", "8.8.8.8")]

        while not self.stop.is_set():
            results = {}
            for label, host in targets:
                ms = self._ping_once(host)
                results[label] = ms

            with self.lock:
                self.data["ping"] = results

            self.stop.wait(2.8)

    def _ping_once(self, host: str) -> Optional[float]:
        """Reliable ping that works on Windows (any locale) and Linux."""
        try:
            if psutil.WINDOWS:
                # -n 1 = one packet, -w 1500 = 1.5s timeout
                cmd = ["ping", "-n", "1", "-w", "1500", host]
                flags = 0x08000000  # CREATE_NO_WINDOW
            else:
                cmd = ["ping", "-c", "1", "-W", "1", host]
                flags = 0

            out = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=2.8,
                creationflags=flags,
                encoding="utf-8",
                errors="replace",
            )
            text = (out.stdout or "") + (out.stderr or "")

            # Works for English "time=12ms" / "time<1ms"
            # and Russian "время=12мс" / "время<1мс"
            m = re.search(
                r"(?:time|время)\s*[=<]\s*([\d.,]+)\s*(?:ms|мс)",
                text,
                re.IGNORECASE,
            )
            if m:
                return float(m.group(1).replace(",", "."))
        except Exception:
            pass
        return None

    def snapshot(self) -> tuple[dict, dict]:
        with self.lock:
            return dict(self.data), {k: list(v) for k, v in self.hist.items()}


# ─────────────────────────────────────────────────────────────────────────────
# NetworkBoard
# ─────────────────────────────────────────────────────────────────────────────


class NetworkBoard(_live_panels.Board):
    def __init__(self, acc, parent: Optional[QWidget] = None):
        super().__init__(acc, parent)
        self.sampler = _NetSampler()
        self.shown = {"down": 0.0, "up": 0.0}
        self.selected = "down"
        self._moving = True

    def on_open(self) -> None:
        self.sampler.start()

    def on_close(self) -> None:
        self.sampler.stop.set()

    def animating(self) -> bool:
        return getattr(self, "_moving", True)

    def title(self):
        d, _ = self.sampler.snapshot()
        ping = d.get("ping") or {}
        cf = ping.get("Cloudflare")
        if cf is not None:
            ping_str = f"Ping Cloudflare {cf:.0f} ms"
        else:
            ping_str = "Ping …"
        return (
            "Network Diagnostics",
            f"{ping_str}  ·  {time.strftime('%H:%M:%S')}",
        )

    def clicked(self, key: str) -> None:
        if key.startswith("ring:"):
            self.selected = key[5:]

    def draw(self, p: QPainter, dt: float, now: float) -> None:
        d, hist = self.sampler.snapshot()
        moving = False

        for k in ("down", "up"):
            v = float(d.get(k) or 0.0)
            # faster approach so rings react quicker
            self.shown[k] = _live_panels._approach(self.shown[k], v, dt, rate=9.0)
            moving = moving or abs(self.shown[k] - v) > 0.03
        self._moving = moving

        # ── speed rings ────────────────────────────────────────────────────
        rings = [
            ("down", "DOWNLOAD", f"{self.shown['down']:.2f} Mbps"),
            ("up", "UPLOAD", f"{self.shown['up']:.2f} Mbps"),
        ]
        for i, (k, label, value) in enumerate(rings):
            cx = 280 + i * 520
            r = 145
            over = self.hit(
                QRectF(cx - r - 20, 300 - r - 20, 2 * r + 40, 2 * r + 80),
                f"ring:{k}",
            )
            # Scale: 0–50 Mbps fills the ring nicely for most home connections
            pct = min(100.0, self.shown[k] / 50.0 * 100.0)
            self.ring(
                p,
                cx,
                300,
                r,
                pct,
                label,
                value,
                "",
                selected=(k == self.selected),
                hover=over,
            )

        # ── latency cards ──────────────────────────────────────────────────
        ping = d.get("ping") or {}
        cards = [
            ("Cloudflare", ping.get("Cloudflare"), "1.1.1.1"),
            ("Google DNS", ping.get("Google"), "8.8.8.8"),
        ]
        for i, (name, ms, host) in enumerate(cards):
            x = 1100 + i * 230
            box = QRectF(x, 200, 210, 160)
            self.panel(p, box, 0.12)
            self.text(
                p, x + 20, 220, name.upper(), self.font("mono", 16), _live_panels.DIM
            )

            if ms is not None:
                if ms > 100:
                    col = _live_panels.HOT
                elif ms > 50:
                    col = _live_panels.WARN
                else:
                    col = self.acc
                self.text(
                    p,
                    x + 20,
                    270,
                    f"{ms:.0f}",
                    self.font("display", 52),
                    col,
                    glow=True,
                )
                self.text(p, x + 20, 325, "ms", self.font("mono", 20), _live_panels.DIM)
            else:
                self.text(
                    p, x + 20, 280, "–", self.font("display", 48), _live_panels.DIM
                )

            self.text(p, x + 20, 345, host, self.font("mono", 15), _live_panels.DIM)

        # ── history graph ──────────────────────────────────────────────────
        L = QRectF(70, 520, 900, 400)
        self.panel(p, L)
        self.text(
            p,
            100,
            545,
            f"HISTORY · {self.selected.upper()} (60 s)",
            self.font("mono", 20),
            self.acc,
        )

        vals = hist.get(self.selected, [])
        if vals:
            hi = max(max(vals) * 1.25, 1.0)
            self.graph(
                p,
                QRectF(100, 590, 840, 280),
                vals,
                0,
                hi,
                self.acc,
                lambda v: f"{v:.1f}",
            )
            self.text(p, 100, 890, "60 s ago", self.font("mono", 15), _live_panels.DIM)
            self.text(
                p, 940, 890, "now", self.font("mono", 15), _live_panels.DIM, align="r"
            )

        # ── right panel ────────────────────────────────────────────────────
        R = QRectF(1000, 520, 530, 400)
        self.panel(p, R)

        self.text(p, 1030, 545, "LIVE STATS", self.font("mono", 20), self.acc)

        stats = [
            ("ESTABLISHED", str(d.get("established", "–"))),
            ("LISTENING", str(d.get("listening", "–"))),
            ("TOTAL ↓", f"{d.get('total_recv_gb', 0):.2f} GB"),
            ("TOTAL ↑", f"{d.get('total_sent_gb', 0):.2f} GB"),
        ]
        for i, (k, v) in enumerate(stats):
            y = 590 + i * 48
            self.text(p, 1030, y, k, self.font("mono", 17), _live_panels.DIM)
            self.text(
                p,
                1480,
                y,
                v,
                self.font("display", 26),
                _live_panels.TEXT,
                align="r",
                glow=True,
            )

        self.text(p, 1030, 800, "INTERFACES", self.font("mono", 16), _live_panels.DIM)
        ifaces = d.get("ifaces") or []
        for i, iface in enumerate(ifaces[:3]):
            y = 830 + i * 28
            name = (iface.get("name") or "")[:18]
            ip = iface.get("ip") or "–"
            self.text(p, 1030, y, name, self.font("mono", 16), _live_panels.TEXT)
            self.text(
                p, 1480, y, ip, self.font("mono", 15), _live_panels.DIM, align="r"
            )


def run(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "open").strip().lower()

    if action == "status":
        try:
            open_now = _live_panels.panel_status("network")
        except Exception as exc:
            return f"I couldn't check the network dashboard: {exc}"
        return (
            "Network diagnostics dashboard is currently open."
            if open_now
            else "Network diagnostics dashboard is closed."
        )

    if action == "close":
        try:
            closed = _live_panels.close_panel("network")
        except Exception as exc:
            return f"I couldn't close the network dashboard: {exc}"
        return (
            "Network diagnostics dashboard closed."
            if closed
            else "The network diagnostics dashboard is not open."
        )

    if action not in {"open", "refresh"}:
        return "Network dashboard action must be open, close, or status."

    if player is None:
        return "The network dashboard needs the JARVIS HUD to be running."

    compact_state = getattr(
        player, "is_compact_mode", getattr(player, "is_mini", False)
    )
    is_compact = compact_state() if callable(compact_state) else bool(compact_state)
    if is_compact:
        return (
            "Network dashboard is unavailable in Mini Orb or Top Dock mode. "
            "Expand JARVIS with F9 or the FULL button first."
        )

    try:
        ok = _live_panels.open_panel(player, "network", NetworkBoard)
        if not ok:
            return "I couldn't open the network diagnostics dashboard."
        return "Network diagnostics dashboard opened."
    except Exception as exc:
        return f"I couldn't open the network dashboard: {exc}"
