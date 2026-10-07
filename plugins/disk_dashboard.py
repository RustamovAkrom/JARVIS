"""A visual, live disk dashboard for the JARVIS HUD.

Shows partition usage, free space, live read/write speeds and a short
history of disk activity. Includes a safe cache-cleanup button.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from collections import deque
from pathlib import Path
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
    "name": "disk_dashboard",
    "description": (
        "Open a live disk dashboard in the JARVIS HUD. "
        "Use this when the user asks to see, show, open or visualize "
        "disk space, free space, storage, partitions, drive usage, "
        "read/write speed, disk diagnostics or clean cache."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["open", "close", "status", "clean_cache"],
                "description": "open (default), close, status, or clean_cache.",
            },
        },
        "required": [],
    },
    "behavior": "NON_BLOCKING",
    "scheduling": "SILENT",
}


# ─────────────────────────────────────────────────────────────────────────────
# Safe cache cleaner
# ─────────────────────────────────────────────────────────────────────────────

def _safe_clear_dir(path: Path, max_depth: int = 2) -> int:
    """Delete files inside a directory. Returns bytes freed. Never deletes the dir itself."""
    freed = 0
    if not path.is_dir():
        return 0
    try:
        for root, dirs, files in os.walk(path, topdown=True):
            depth = root.replace(str(path), "").count(os.sep)
            if depth > max_depth:
                dirs.clear()
                continue
            for name in files:
                fp = Path(root) / name
                try:
                    size = fp.stat().st_size
                    fp.unlink(missing_ok=True)
                    freed += size
                except Exception:
                    continue
            # remove empty subdirs
            for name in dirs:
                dp = Path(root) / name
                try:
                    if not any(dp.iterdir()):
                        dp.rmdir()
                except Exception:
                    pass
    except Exception:
        pass
    return freed


def clean_system_cache() -> dict:
    """Clean well-known safe cache locations. Returns summary."""
    targets = []

    # User Temp
    temp = os.environ.get("TEMP") or os.environ.get("TMP")
    if temp:
        targets.append(Path(temp))

    # Windows Temp
    windir = os.environ.get("WINDIR", r"C:\Windows")
    targets.append(Path(windir) / "Temp")

    # Prefetch (safe)
    targets.append(Path(windir) / "Prefetch")

    # Thumbnail cache
    local = os.environ.get("LOCALAPPDATA")
    if local:
        targets.append(Path(local) / "Microsoft" / "Windows" / "Explorer")
        # Chrome / Edge caches (only Cache folders)
        for browser in ("Google/Chrome", "Microsoft/Edge"):
            targets.append(Path(local) / browser / "User Data" / "Default" / "Cache")
            targets.append(Path(local) / browser / "User Data" / "Default" / "Code Cache")

    total_freed = 0
    cleaned = []

    for t in targets:
        if t.exists():
            before = total_freed
            freed = _safe_clear_dir(t)
            total_freed += freed
            if freed > 0:
                cleaned.append(f"{t.name}: {_gb(freed)}")

    return {
        "freed_bytes": total_freed,
        "freed_human": _gb(total_freed),
        "targets": cleaned,
    }


def _gb(n: float) -> str:
    if n >= 1024**3:
        return f"{n / 1024**3:.2f} TB"
    if n >= 1024**2:
        return f"{n / 1024**2:.1f} GB"
    if n >= 1024:
        return f"{n / 1024:.0f} MB"
    return f"{n:.0f} B"


# ─────────────────────────────────────────────────────────────────────────────
# Sampler
# ─────────────────────────────────────────────────────────────────────────────

class _DiskSampler:
    def __init__(self):
        self.lock = threading.Lock()
        self.data: dict = {
            "partitions": [],
            "read_mb": 0.0,
            "write_mb": 0.0,
        }
        self.hist = {
            "read": deque([0.0] * 60, maxlen=60),
            "write": deque([0.0] * 60, maxlen=60),
        }
        self.stop = threading.Event()
        self.threads: list = []

    def start(self) -> None:
        if any(t.is_alive() for t in self.threads):
            return
        self.stop = threading.Event()
        self.threads = [
            threading.Thread(target=self._run_usage, name="disk-usage", daemon=True),
            threading.Thread(target=self._run_io, name="disk-io", daemon=True),
        ]
        for t in self.threads:
            t.start()

    def _run_usage(self) -> None:
        while not self.stop.is_set():
            try:
                parts = []
                for p in psutil.disk_partitions(all=False):
                    if "cdrom" in p.opts or p.fstype == "":
                        continue
                    try:
                        u = psutil.disk_usage(p.mountpoint)
                        parts.append({
                            "device": p.device,
                            "mount": p.mountpoint,
                            "fstype": p.fstype,
                            "total": u.total,
                            "used": u.used,
                            "free": u.free,
                            "percent": u.percent,
                        })
                    except Exception:
                        continue
                parts.sort(key=lambda x: x["total"], reverse=True)
                with self.lock:
                    self.data["partitions"] = parts[:6]
            except Exception as exc:
                _live_panels._once("disk usage", exc)
            self.stop.wait(8.0)

    def _run_io(self) -> None:
        try:
            io0 = psutil.disk_io_counters()
        except Exception:
            io0 = None
        t0 = time.time()
        self.stop.wait(0.4)
        tick = 0

        while not self.stop.is_set():
            try:
                io1 = psutil.disk_io_counters()
                t1 = time.time()
                dt = max(0.25, t1 - t0)

                if io0 is not None and io1 is not None:
                    read_mb = max(0.0, (io1.read_bytes - io0.read_bytes) / dt / 1_000_000)
                    write_mb = max(0.0, (io1.write_bytes - io0.write_bytes) / dt / 1_000_000)
                else:
                    read_mb = write_mb = 0.0

                io0, t0 = io1, t1

                with self.lock:
                    self.data["read_mb"] = read_mb
                    self.data["write_mb"] = write_mb
                    if tick == 0:
                        self.hist["read"].extend([read_mb] * 59)
                        self.hist["write"].extend([write_mb] * 59)
                    self.hist["read"].append(read_mb)
                    self.hist["write"].append(write_mb)
            except Exception as exc:
                _live_panels._once("disk io", exc)

            tick += 1
            self.stop.wait(1.0)

    def snapshot(self) -> tuple[dict, dict]:
        with self.lock:
            return dict(self.data), {k: list(v) for k, v in self.hist.items()}


def _disk_color(pct: float, acc):
    if pct >= 92:
        return _live_panels.HOT
    if pct >= 80:
        return _live_panels.WARN
    return acc


class DiskBoard(_live_panels.Board):
    def __init__(self, acc, parent: Optional[QWidget] = None):
        super().__init__(acc, parent)
        self.sampler = _DiskSampler()
        self.shown_read = 0.0
        self.shown_write = 0.0
        self.selected = "read"
        self._moving = True
        self.clean_msg = ""          # result message after cleanup
        self.clean_msg_until = 0.0
        self.cleaning = False

    def on_open(self) -> None:
        self.sampler.start()

    def on_close(self) -> None:
        self.sampler.stop.set()

    def animating(self) -> bool:
        return getattr(self, "_moving", True) or time.time() < self.clean_msg_until

    def title(self):
        d, _ = self.sampler.snapshot()
        parts = d.get("partitions") or []
        if parts:
            free = _gb(parts[0]["free"])
            return ("Disk Diagnostics", f"Main free: {free}  ·  {time.strftime('%H:%M:%S')}")
        return ("Disk Diagnostics", time.strftime("%H:%M:%S"))

    def clicked(self, key: str) -> None:
        if key.startswith("io:"):
            self.selected = key[3:]
        elif key == "clean_cache":
            if self.cleaning:
                return
            self.cleaning = True
            self.clean_msg = "Cleaning cache…"
            self.clean_msg_until = time.time() + 30

            def job():
                try:
                    result = clean_system_cache()
                    msg = f"Freed {result['freed_human']}"
                    if result["targets"]:
                        msg += "  ·  " + ", ".join(result["targets"][:3])
                except Exception as exc:
                    msg = f"Cleanup failed: {exc}"
                # update UI from any thread via the board itself
                self.clean_msg = msg
                self.clean_msg_until = time.time() + 8
                self.cleaning = False
                # force a refresh of partition data soon
                self.sampler.stop.wait(0.1)

            threading.Thread(target=job, daemon=True, name="disk-clean").start()

    def draw(self, p: QPainter, dt: float, now: float) -> None:
        d, hist = self.sampler.snapshot()
        parts = d.get("partitions") or []

        self.shown_read = _live_panels._approach(self.shown_read, float(d.get("read_mb") or 0), dt, 8)
        self.shown_write = _live_panels._approach(self.shown_write, float(d.get("write_mb") or 0), dt, 8)
        self._moving = (
            abs(self.shown_read - float(d.get("read_mb") or 0)) > 0.05
            or abs(self.shown_write - float(d.get("write_mb") or 0)) > 0.05
        )

        # ── IO rings ───────────────────────────────────────────────────────
        for i, (key, label, val) in enumerate([
            ("read", "READ", self.shown_read),
            ("write", "WRITE", self.shown_write),
        ]):
            cx = 260 + i * 420
            r = 120
            over = self.hit(QRectF(cx - r - 15, 280 - r - 15, 2 * r + 30, 2 * r + 60), f"io:{key}")
            pct = min(100.0, val / 200.0 * 100)
            self.ring(p, cx, 280, r, pct, label, f"{val:.1f} MB/s", "",
                      selected=(self.selected == key), hover=over)

        # ── main drive card + Clean Cache button ───────────────────────────
        if parts:
            main = parts[0]
            box = QRectF(1050, 160, 470, 260)
            self.panel(p, box, 0.12)
            self.text(p, 1075, 185, "MAIN DRIVE", self.font("mono", 18), _live_panels.DIM)
            self.text(p, 1075, 230, main["mount"], self.font("display", 34), self.acc, glow=True)

            used_pct = main["percent"]
            col = _disk_color(used_pct, self.acc)
            self.text(p, 1075, 285, f"{used_pct:.0f}% used", self.font("mono", 22), col)
            self.text(p, 1075, 320, f"{_gb(main['free'])} free of {_gb(main['total'])}",
                      self.font("mono", 18), _live_panels.TEXT)

            # ── Clean Cache button ─────────────────────────────────────────
            btn = QRectF(1075, 360, 420, 42)
            over_btn = self.hit(btn, "clean_cache")
            active = over_btn or self.cleaning
            self.panel(p, btn, 0.25 if active else 0.12, active=active)
            label = "CLEANING…" if self.cleaning else "CLEAR CACHE"
            self.text(p, btn.center().x(), btn.center().y(),
                      label, self.font("mono", 18),
                      self.acc if active else _live_panels.TEXT,
                      align="c", valign="mid", glow=active)

        # ── result toast ───────────────────────────────────────────────────
        if now < self.clean_msg_until and self.clean_msg:
            self.text(p, 800, 450, self.clean_msg,
                      self.font("mono", 20), self.acc, align="c", glow=True)

        # ── partitions ─────────────────────────────────────────────────────
        L = QRectF(70, 500, 900, 420)
        self.panel(p, L)
        self.text(p, 100, 525, "PARTITIONS", self.font("mono", 20), self.acc)

        if not parts:
            self.text(p, 100, 580, "No partitions found", self.font("mono", 22), _live_panels.DIM)
        else:
            for i, part in enumerate(parts[:5]):
                y = 560 + i * 68
                pct = part["percent"]
                col = _disk_color(pct, self.acc)

                name = part["mount"]
                if len(name) > 18:
                    name = name[:16] + "…"
                self.text(p, 100, y + 6, name, self.font("mono", 20), _live_panels.TEXT)
                self.text(p, 100, y + 32, f"{_gb(part['used'])} / {_gb(part['total'])}",
                          self.font("mono", 15), _live_panels.DIM)

                bar = QRectF(380, y + 14, 520, 20)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_live_panels._q(_live_panels._mix(_live_panels.BG, self.acc, 0.15)))
                p.drawRoundedRect(bar, 4, 4)
                fill_w = bar.width() * min(pct, 100) / 100
                p.setBrush(_live_panels._q(col, 200))
                p.drawRoundedRect(QRectF(bar.left(), bar.top(), fill_w, bar.height()), 4, 4)
                self.text(p, 920, y + 24, f"{pct:.0f}%", self.font("mono", 18),
                          col, align="r", valign="mid")

        # ── IO history ─────────────────────────────────────────────────────
        R = QRectF(1000, 500, 520, 420)
        self.panel(p, R)
        self.text(p, 1030, 525, f"HISTORY · {self.selected.upper()} (60 s)",
                  self.font("mono", 18), self.acc)

        vals = hist.get(self.selected, [])
        if vals:
            hi = max(max(vals) * 1.3, 1.0)
            self.graph(p, QRectF(1030, 570, 460, 290), vals, 0, hi, self.acc,
                       lambda v: f"{v:.0f}")
            self.text(p, 1030, 885, "60 s ago", self.font("mono", 14), _live_panels.DIM)
            self.text(p, 1490, 885, "now", self.font("mono", 14), _live_panels.DIM, align="r")


def run(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "open").strip().lower()

    if action == "clean_cache":
        try:
            result = clean_system_cache()
            return f"Cache cleaned. Freed {result['freed_human']}."
        except Exception as exc:
            return f"Cache cleanup failed: {exc}"

    if action == "status":
        try:
            open_now = _live_panels.panel_status("disk")
        except Exception as exc:
            return f"I couldn't check the disk dashboard: {exc}"
        return ("Disk dashboard is currently open." if open_now
                else "Disk dashboard is closed.")

    if action == "close":
        try:
            closed = _live_panels.close_panel("disk")
        except Exception as exc:
            return f"I couldn't close the disk dashboard: {exc}"
        return ("Disk dashboard closed." if closed
                else "The disk dashboard is not open.")

    if action not in {"open", "refresh"}:
        return "Disk dashboard action must be open, close, status or clean_cache."

    if player is None:
        return "The disk dashboard needs the JARVIS HUD to be running."

    compact_state = getattr(player, "is_compact_mode", getattr(player, "is_mini", False))
    is_compact = compact_state() if callable(compact_state) else bool(compact_state)
    if is_compact:
        return ("Disk dashboard is unavailable in Mini Orb or Top Dock mode. "
                "Expand JARVIS with F9 or the FULL button first.")

    try:
        ok = _live_panels.open_panel(player, "disk", DiskBoard)
        if not ok:
            return "I couldn't open the disk dashboard."
        return "Disk dashboard opened."
    except Exception as exc:
        return f"I couldn't open the disk dashboard: {exc}"
