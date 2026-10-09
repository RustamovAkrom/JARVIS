from __future__ import annotations

import html as _html
import json
import math
import os
import platform
import random
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
import psutil

# Qt's video backend prints the ffmpeg stream banner — codec, bitrate, the
# whole signed googlevideo URL — to the console for every stream it opens. That
# is two screenfuls per video, it looks like an error to anyone reading the log,
# and it puts a URL carrying the viewer's IP address into a terminal people
# paste into bug reports. Silenced here, before Qt initialises its logging.
# An explicit setting from the user is left alone.
os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")

from PyQt6.QtCore import (
    QEvent,
    QLineF,
    QPointF,
    QPoint,
    QRect,
    QRectF,
    QSize,
    QSizeF,
    Qt,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QCursor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QFontDatabase,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
    QShortcut,
)

# Video playback for the HUD. Part of PyQt6, so it costs no new dependency —
# but the multimedia plugins are a separate piece of the Qt install and can be
# absent on a stripped-down system, so a failure here disables one feature
# rather than stopping JARVIS from starting.
try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem

    HAVE_VIDEO = True
except Exception as _e:  # noqa: BLE001 - reported, never fatal
    QAudioOutput = QMediaPlayer = QGraphicsVideoItem = None
    HAVE_VIDEO = False
    print(f"[Video] playback unavailable ({_e}) — the HUD will not show video.")

from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QSplitter,
    QGraphicsScene,
    QGraphicsView,
    QStackedWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

try:
    from core.avatar import HoloAvatar
except Exception:  # pragma: no cover — HUD must never die over cosmetics
    HoloAvatar = None


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE = CONFIG_DIR / "api_keys.json"


def _read_full_config() -> dict:
    """Read api_keys.json config dict. Returns {} on any error."""
    try:
        return json.loads(API_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cfg(**kv) -> None:
    """Merge a few keys into api_keys.json without touching the rest.

    If the file exists but could not be parsed we do nothing at all — writing
    only our keys back would wipe the user's API key."""
    try:
        d = _read_full_config()
        if API_FILE.exists() and not d:
            return
        d.update(kv)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        API_FILE.write_text(json.dumps(d, indent=4), encoding="utf-8")
    except Exception:
        pass


def _tasks_file() -> Path:
    return CONFIG_DIR / "ui_tasks.json"


# Single source of truth for the release name — the window title, the header
# badge and the readme must never disagree again.
APP_VERSION = "JARVIS v2"
APP_PROTOCOL = APP_VERSION.split()[-1]

_DEFAULT_W, _DEFAULT_H = 1360, 820
_MIN_W, _MIN_H = 700, 520
_LEFT_W = 244
_RIGHT_W = 350
_RAIL_W = 36

# Mini (orb-only, picture-in-picture style) window.
_MINI_W, _MINI_H = 240, 290
_MINI_MIN_W, _MINI_MIN_H = 120, 140
_MINI_DEFAULT_OPACITY = 0.80

# One-click prompts: (short label, palette title, message). A message starting
# with "clip:" is a template that needs the clipboard text.
_QUICK_ACTIONS = (
    (
        "👁  SCREEN",
        "Look at my screen",
        "Take a look at my screen and tell me what you see.",
    ),
    ("📋  SUMMARISE", "Summarise the clipboard", "clip:Summarise this: {text}"),
    (
        "💻  CODE",
        "Explain the copied code",
        "clip:Explain this code step by step: {text}",
    ),
    (
        "🌐  TRANSLATE",
        "Translate the clipboard",
        "clip:Translate this text to English: {text}",
    ),
    (
        "📰  NEWS",
        "Brief me on today's news",
        "Give me a short briefing of today's top news.",
    ),
    (
        "🗓  PLAN",
        "Help me plan my day",
        "Help me plan my day — ask me what is on my list.",
    ),
)

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


class C:
    BG = "#00060a"
    PANEL = "#010d14"
    PANEL2 = "#010f18"
    BORDER = "#0d3347"
    BORDER_B = "#1a5c7a"
    BORDER_A = "#0f4060"
    PRI = "#00d4ff"
    PRI_DIM = "#007a99"
    PRI_GHO = "#001f2e"
    ACC = "#ff6b00"
    ACC2 = "#ffcc00"
    GREEN = "#00ff88"
    GREEN_D = "#00aa55"
    RED = "#ff3355"
    MUTED_C = "#ff3366"
    TEXT = "#8ffcff"
    TEXT_DIM = "#3a8a9a"
    TEXT_MED = "#5ab8cc"
    WHITE = "#d8f8ff"
    DARK = "#000d14"
    BAR_BG = "#011520"


# Keys tied to the accent colour — status colours (ACC, GREEN, RED…) stay fixed
_HUE_LINKED = (
    "BG",
    "PANEL",
    "PANEL2",
    "BORDER",
    "BORDER_B",
    "BORDER_A",
    "PRI",
    "PRI_DIM",
    "PRI_GHO",
    "TEXT",
    "TEXT_DIM",
    "TEXT_MED",
    "WHITE",
    "DARK",
    "BAR_BG",
)
_PALETTE_DEFAULTS: dict[str, str] = {k: getattr(C, k) for k in _HUE_LINKED}

DEFAULT_UI_COLOR = _PALETTE_DEFAULTS["PRI"]


def apply_ui_accent(accent_hex: str) -> bool:
    """
    Re-derives the whole teal-family palette from the chosen accent colour
    (hue shift — brightness/saturation ratios are preserved, design stays intact).
    Painted elements (HUD, waveform, metrics) pick up the new colour on the next
    frame; stylesheet-based panels pick it up when they are rebuilt.
    """
    import colorsys

    accent_hex = (accent_hex or "").strip().lower()
    if not (accent_hex.startswith("#") and len(accent_hex) == 7):
        return False
    try:
        int(accent_hex[1:], 16)
    except ValueError:
        return False

    def _hsv(h: str) -> tuple[float, float, float]:
        r = int(h[1:3], 16) / 255
        g = int(h[3:5], 16) / 255
        b = int(h[5:7], 16) / 255
        return colorsys.rgb_to_hsv(r, g, b)

    base_h = _hsv(_PALETTE_DEFAULTS["PRI"])[0]
    acc_h, acc_s, _av = _hsv(accent_hex)
    dh = acc_h - base_h
    grey = acc_s < 0.08  # near-grey accent → the whole theme is desaturated

    for key, hex0 in _PALETTE_DEFAULTS.items():
        h, s, v = _hsv(hex0)
        if grey:
            s *= 0.15
        r, g, b = colorsys.hsv_to_rgb((h + dh) % 1.0, s, v)
        setattr(
            C,
            key,
            "#{:02x}{:02x}{:02x}".format(
                int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5)
            ),
        )
    return True


def current_palette() -> dict[str, str]:
    """A snapshot of the accent-linked colours currently on class C."""
    return {k: getattr(C, k) for k in _HUE_LINKED}


# ── THEME ENGINE ─────────────────────────────────────────────────────────────
# The interface has one appearance model: a user-selected colour.  Older
# releases exposed dark/light/custom as competing modes; retained compatibility
# below only lets existing config files open safely.
# Stylesheets are authored for the old dark-navy look. Every setStyleSheet() is
# intercepted: the authored text is kept (_ss_src) and a themed copy is applied,
# so a theme switch can re-skin every widget losslessly, in either direction.
C.MODE = "custom"
C.IS_LIGHT = False
_STATUS_KEYS = ("ACC", "ACC2", "GREEN", "GREEN_D", "RED", "MUTED_C")
_ALL_KEYS = _HUE_LINKED + _STATUS_KEYS
_ALL_DEFAULTS: dict[str, str] = {k: getattr(C, k) for k in _ALL_KEYS}

_THEME_PAL = {
    "dark": dict(
        BG="#030407",
        PANEL="#07090d",
        PANEL2="#0a0d12",
        BORDER="#3a424d",
        BORDER_B="#aeb8c4",
        BORDER_A="#6f7a87",
        PRI="#eaf1f8",
        PRI_DIM="#8b97a6",
        PRI_GHO="#141920",
        TEXT="#d5dde6",
        TEXT_DIM="#6b7683",
        TEXT_MED="#9aa6b4",
        WHITE="#ffffff",
        DARK="#020305",
        BAR_BG="#0b0e13",
        ACC="#ff7a1a",
        ACC2="#ffd23f",
        GREEN="#38f2a0",
        GREEN_D="#1fb57a",
        RED="#ff4d6a",
        MUTED_C="#ff4d8a",
    ),
    "light": dict(
        BG="#f1f3f6",
        PANEL="#ffffff",
        PANEL2="#f7f8fa",
        BORDER="#9aa4b0",
        BORDER_B="#14181d",
        BORDER_A="#59626e",
        PRI="#0a0d11",
        PRI_DIM="#4b5562",
        PRI_GHO="#e4e8ee",
        TEXT="#14181e",
        TEXT_DIM="#6b7480",
        TEXT_MED="#39424d",
        WHITE="#05070a",
        DARK="#e8ebf0",
        BAR_BG="#e9ecf1",
        ACC="#d95000",
        ACC2="#a87b00",
        GREEN="#008f4c",
        GREEN_D="#00693a",
        RED="#cf1f3d",
        MUTED_C="#c81e5a",
    ),
}


def apply_ui_color(color: str = "") -> None:
    """Apply one chosen UI colour, including neutral black and white presets."""
    color = (color or DEFAULT_UI_COLOR).strip().lower()
    # Neutral endpoints need usable contrast, rather than a hue-shifted grey.
    if color == "#000000":
        palette, C.IS_LIGHT = _THEME_PAL["dark"], False
        for k, v in palette.items():
            setattr(C, k, v)
    elif color == "#ffffff":
        palette, C.IS_LIGHT = _THEME_PAL["light"], True
        for k, v in palette.items():
            setattr(C, k, v)
    else:
        C.IS_LIGHT = False
        for k in _STATUS_KEYS:
            setattr(C, k, _ALL_DEFAULTS[k])
        if not apply_ui_accent(color):
            apply_ui_accent(DEFAULT_UI_COLOR)
    C.MODE = "custom"


def apply_theme(mode: str, accent: str = "") -> None:
    """Compatibility shim for saved pre-v2 appearance preferences."""
    if not accent:
        accent = "#000000" if mode == "dark" else "#ffffff" if mode == "light" else DEFAULT_UI_COLOR
    apply_ui_color(accent)


def current_palette() -> dict[str, str]:
    """A snapshot of every theme-linked colour currently on class C."""
    return {k: getattr(C, k) for k in _ALL_KEYS}


def _rgb(h: str):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _ink(a) -> QColor:
    """The 'glass' colour: white on dark/custom, near-black on light."""
    light = C.IS_LIGHT
    c = QColor(14, 18, 24) if light else QColor(255, 255, 255)
    c.setAlpha(max(0, min(255, int(a * (2.0 if light else 1.0)))))
    return c


def _surf(a) -> QColor:
    """Translucent panel surface in the current theme."""
    r, g, b = _rgb(C.PANEL)
    return QColor(r, g, b, max(0, min(255, int(a))))


_LIT = re.compile(
    r"rgba\(255, 255, 255, (?P<ia>\d+)\)"
    r"|rgba\((?:0, 6, 10|4, 14, 22|3, 12, 20|3, 10, 16|2, 12, 20|0, 8, 14|0, 4, 12), (?P<sa>[\d.]+)\)"
    r"|rgba\(14, 3, 0, (?P<wa>\d+)\)"
    r"|rgba\(0, 212, 255, (?P<aa>\d+)\)"
    r"|#(?P<hx>000d12|00091a|001a08|002010|000308|001a22|1a1400|001a0d)(?![0-9a-fA-F])"
)


def _lit_repl(m):
    mode = "light" if C.IS_LIGHT else "custom"
    if m.group("ia") is not None:
        a = int(m.group("ia"))
        return f"rgba(14, 18, 24, {min(255, a * 2)})" if mode == "light" else m.group(0)
    if m.group("sa") is not None:
        r, g, b = _rgb(C.PANEL)
        return f"rgba({r}, {g}, {b}, {m.group('sa')})"
    if m.group("wa") is not None:
        if mode == "light":
            return f"rgba(255, 238, 226, {m.group('wa')})"
        return m.group(0)
    if m.group("aa") is not None:
        r, g, b = _rgb(C.PRI)
        return f"rgba({r}, {g}, {b}, {m.group('aa')})"
    h = m.group("hx")
    if h in ("000d12", "00091a"):
        return C.PANEL2
    if h in ("000308", "001a22", "1a1400", "001a0d"):
        return C.BG
    on = {"001a08": ("#0b1a12", "#dff3e8"), "002010": ("#10281a", "#cfeadb")}[h]
    if mode == "light":
        return on[1]
    return on[0] if mode == "dark" else "#" + h


def _skin(ss: str) -> str:
    return _LIT.sub(_lit_repl, ss) if ss else ss


_QW_SET, _QW_GET = QWidget.setStyleSheet, QWidget.styleSheet


def _set_ss(self, ss):
    ss = "" if ss is None else str(ss)
    try:
        self._ss_src = ss
    except Exception:
        pass
    _QW_SET(self, _skin(ss))


def _get_ss(self):
    s = getattr(self, "__dict__", {}).get("_ss_src")
    return s if s is not None else _QW_GET(self)


try:
    QWidget.setStyleSheet = _set_ss
    QWidget.styleSheet = _get_ss
except Exception:  # pragma: no cover - theming degrades, the app still runs
    pass


def retheme_all_widgets(old: dict[str, str], new: dict[str, str]) -> None:
    """
    LIVE full theme change. Remaps palette hexes inside every widget's source
    stylesheet (single pass, so replacements never chain), re-skins it for the
    current mode and repaints. No restart needed.
    """
    mapping = {
        old[k].lower(): new[k].lower()
        for k in old
        if old[k].lower() != new.get(k, old[k]).lower()
    }
    app = QApplication.instance()
    if app is None:
        return
    pat = (
        re.compile(
            "(" + "|".join(re.escape(k) for k in mapping) + ")(?![0-9a-fA-F])", re.I
        )
        if mapping
        else None
    )
    for w in app.allWidgets():
        try:
            src = getattr(w, "__dict__", {}).get("_ss_src")
            if src is None:
                continue
            if pat is not None:
                src = pat.sub(lambda m: mapping[m.group(1).lower()], src)
                w._ss_src = src
            _QW_SET(w, _skin(src))
            w.update()
        except Exception:
            pass


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h)
    c.setAlpha(max(0, min(255, int(a))))
    return c


# ── Typography + glass helpers (added by the UI refresh) ─────────────────────
from PyQt6.QtGui import QFontMetrics

_FONT = {"Windows": "Segoe UI", "Darwin": "Helvetica Neue"}.get(_OS, "Noto Sans")
_MONO = {"Windows": "Consolas", "Darwin": "Menlo"}.get(_OS, "DejaVu Sans Mono")
_DISPLAY = _FONT  # headings, the name under the orb, the clock


def _init_fonts() -> None:
    """Pick a technical, machine-like type stack.

    Has to run after QApplication exists (Qt cannot enumerate families before
    that), so it is called from JarvisUI.__init__ and the module globals are
    rebound; every QFont(_FONT, ...) in the file reads them at call time.

    Drop .ttf/.otf files into  assets/fonts/  and they are loaded too — the
    intended look is Orbitron (display), Rajdhani (UI) and Share Tech Mono
    (numbers), all free on Google Fonts. Without them it falls back to
    Bahnschrift, which ships with Windows 10/11 and is a DIN-style technical
    face, so the UI is never left on a plain system font."""
    global _FONT, _MONO, _DISPLAY
    have: set = set()
    try:
        fdir = BASE_DIR / "assets" / "fonts"
        if fdir.is_dir():
            for f in sorted(fdir.glob("*.[to]tf")):
                QFontDatabase.addApplicationFont(str(f))
        have = set(QFontDatabase.families())
    except Exception:
        pass

    def pick(cands, default):
        for c in cands:
            if c in have:
                return c
        return default

    _FONT = pick(("Inter", "Segoe UI Variable Text", "Bahnschrift", "Rajdhani"), _FONT)
    _DISPLAY = pick(("Rajdhani", "Bahnschrift", "Inter"), _FONT)
    _MONO = pick(
        ("JetBrains Mono", "Cascadia Mono", "Share Tech Mono", "Consolas"), _MONO
    )


def _bump(n) -> int:
    """Courier New at 6-8 pt was tiny; proportional fonts want a size more."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return 9
    return 8 if n <= 7 else (n + 1 if n <= 9 else n)


def _glass_css(obj: str, radius: int = 14, fill: int = 12, edge: int = 26) -> str:
    """Frosted card. Neutral white alpha so it works with any accent colour."""
    return (
        f"QFrame#{obj} {{ background: rgba(255, 255, 255, {fill}); "
        f"border: 1px solid rgba(255, 255, 255, {edge}); border-radius: {radius}px; }} "
        f"QFrame#{obj} QLabel {{ background: transparent; border: none; }}"
    )


class _AuroraRoot(QWidget):
    """Central widget: themed base + nebula + a crisp spiral galaxy and stars.

    Rendered once per (size, theme) into a device-pixel-ratio-aware pixmap, so
    painting stays a single blit. Panels sit on top as translucent glass.

    In mini mode `transparent` is set and nothing is painted at all, so the
    desktop shows through around the orb (the window is translucent then)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pm = None
        self._key = None
        self.transparent = False

    def _render(self, W: int, H: int, dpr: float) -> QPixmap:
        W, H = max(1, W), max(1, H)
        mode = "light" if C.IS_LIGHT else "custom"
        pm = QPixmap(int(W * dpr), int(H * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(qcol(C.BG))

        # nebula, half resolution (smooth gradients scale well)
        w, h = max(1, W // 2), max(1, H // 2)
        neb = QPixmap(w, h)
        neb.fill(Qt.GlobalColor.transparent)
        n = QPainter(neb)
        n.setRenderHint(QPainter.RenderHint.Antialiasing)

        def blob(bx, by, rad, col, alpha):
            g = QRadialGradient(bx, by, rad)
            c0 = QColor(col)
            c0.setAlpha(alpha)
            c1 = QColor(col)
            c1.setAlpha(0)
            g.setColorAt(0.0, c0)
            g.setColorAt(1.0, c1)
            n.setPen(Qt.PenStyle.NoPen)
            n.setBrush(QBrush(g))
            n.drawEllipse(QRectF(bx - rad, by - rad, rad * 2, rad * 2))

        m = max(w, h)
        if mode == "light":
            blob(w * 0.25, h * 0.05, m * 0.60, QColor(255, 255, 255), 210)
            blob(w * 0.85, h * 0.85, m * 0.65, QColor(196, 206, 224), 120)
            blob(w * 0.05, h * 0.95, m * 0.45, QColor(222, 214, 236), 90)
            blob(w * 0.50, h * 0.46, m * 0.30, QColor(255, 255, 255), 150)
        elif mode == "dark":
            blob(w * 0.30, h * 0.10, m * 0.55, QColor(70, 90, 150), 34)
            blob(w * 0.84, h * 0.80, m * 0.62, QColor(110, 70, 150), 30)
            blob(w * 0.08, h * 0.96, m * 0.40, QColor(255, 255, 255), 14)
            blob(w * 0.50, h * 0.46, m * 0.30, QColor(255, 255, 255), 16)
        else:
            base = QColor(C.PRI)
            hh = max(0.0, base.hsvHueF())
            ss, vv = base.hsvSaturationF(), base.valueF()
            blob(w * 0.30, h * 0.10, m * 0.55, base, 52)
            blob(
                w * 0.84,
                h * 0.80,
                m * 0.62,
                QColor.fromHsvF((hh + 0.14) % 1.0, ss, vv),
                46,
            )
            blob(
                w * 0.08,
                h * 0.96,
                m * 0.40,
                QColor.fromHsvF((hh - 0.10) % 1.0, ss, vv),
                30,
            )
            blob(w * 0.50, h * 0.46, m * 0.30, base, 26)
        n.end()

        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(QRect(0, 0, W, H), neb)

        if mode == "light":
            ink = QColor(14, 18, 24)
        elif mode == "custom":
            ink = QColor(C.PRI).lighter(170)
        else:
            ink = QColor(255, 255, 255)

        def dots(pts, alpha, wd):
            c = QColor(ink)
            c.setAlpha(alpha)
            pn = QPen(c, wd)
            pn.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pn)
            _draw_points(p, pts)

        rnd = random.Random(11)

        # spiral galaxy: two log-spiral arms, tilted, crisp 1-2 px dots
        cxg, cyg = W * 0.5, H * 0.46
        R0 = max(W, H) * 0.55
        tilt = -0.42
        ct, st = math.cos(tilt), math.sin(tilt)
        gb = ([], [], [])
        for i in range(1200):
            t = rnd.random() ** 0.7
            a = (i % 2) * math.pi + 5.4 * t + rnd.gauss(0.0, 0.30 * (1.15 - t))
            r = R0 * (0.04 + 0.96 * t)
            gx, gy = r * math.cos(a), r * math.sin(a) * 0.38
            lvl = 0 if rnd.random() < 0.6 else (1 if rnd.random() < 0.7 else 2)
            gb[lvl].append(QPointF(cxg + gx * ct - gy * st, cyg + gx * st + gy * ct))
        ga = (24, 50, 90) if mode != "light" else (24, 44, 78)
        for lvl in range(3):
            dots(gb[lvl], ga[lvl], 1.1 + 0.4 * lvl)

        # stars
        sb = {}
        for _ in range(170):
            x, y, r = rnd.random() * W, rnd.random() * H, rnd.random()
            lvl = 0 if r < 0.5 else (1 if r < 0.85 else 2)
            sb.setdefault((r > 0.93, lvl), []).append(QPointF(x, y))
        sa = (70, 140, 230) if mode != "light" else (60, 110, 170)
        for (big, lvl), pts in sb.items():
            dots(pts, sa[lvl], 2.4 if big else 1.3)
        fc = QColor(ink)
        fc.setAlpha(110)
        p.setPen(QPen(fc, 1))
        for pt in sb.get((True, 2), [])[:7]:
            p.drawLine(QLineF(pt.x() - 6, pt.y(), pt.x() + 6, pt.y()))
            p.drawLine(QLineF(pt.x(), pt.y() - 6, pt.x(), pt.y() + 6))
        p.end()
        return pm

    def paintEvent(self, _):
        if self.transparent:
            return
        W, H = self.width(), self.height()
        dpr = self.devicePixelRatioF()
        key = (W, H, dpr, C.IS_LIGHT, C.PRI, C.BG)
        if self._pm is None or key != self._key:
            self._pm = self._render(W, H, dpr)
            self._key = key
        p = QPainter(self)
        if not p.isActive():
            return
        p.drawPixmap(0, 0, self._pm)
        p.end()


class _GlassHeader(QWidget):
    """Header bar: frosted strip with a fading accent line along the bottom."""

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        W, H = self.width(), self.height()
        g = QLinearGradient(0, 0, 0, H)
        g.setColorAt(0.0, _ink(20))
        g.setColorAt(1.0, _ink(6))
        p.fillRect(self.rect(), QBrush(g))
        line = QLinearGradient(0, 0, W, 0)
        line.setColorAt(0.0, qcol(C.PRI, 0))
        line.setColorAt(0.5, qcol(C.PRI, 210))
        line.setColorAt(1.0, qcol(C.PRI, 0))
        p.fillRect(QRectF(0, H - 1, W, 1), QBrush(line))
        p.end()


class _OrbIcon(QWidget):
    """Small glowing ring used as the brand mark in the header."""

    def __init__(self, size: int = 38, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.width()
        c = s / 2.0
        g = QRadialGradient(c, c, c)
        g.setColorAt(0.0, qcol(C.PRI, 110))
        g.setColorAt(0.6, qcol(C.PRI, 28))
        g.setColorAt(1.0, qcol(C.PRI, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QRectF(0, 0, s, s))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qcol(C.PRI, 230), 1.8))
        p.drawEllipse(QRectF(c - s * 0.27, c - s * 0.27, s * 0.54, s * 0.54))
        p.setPen(QPen(qcol(C.PRI, 90), 1.0))
        p.drawEllipse(QRectF(c - s * 0.40, c - s * 0.40, s * 0.80, s * 0.80))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(qcol(C.WHITE)))
        p.drawEllipse(QPointF(c, c), s * 0.07, s * 0.07)
        p.end()


# ── Windows GPU via NVML DLL (no subprocess, no console window) ──────────────
_nvml_lib: object = None  # cached ctypes DLL
_nvml_ok: object = None  # None=untested, True=works, False=unavailable


def _nvml_gpu_windows() -> float:
    """Return NVIDIA GPU utilisation % using nvml.dll directly — zero subprocess."""
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        import ctypes

        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            for dll_name in ("nvml", r"C:\Windows\System32\nvml.dll"):
                try:
                    lib = ctypes.WinDLL(dll_name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            import pynvml  # type: ignore

            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            _nvml_ok = True
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        util = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(util))
        _nvml_ok = True
        return float(util.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


class _SysMetrics:
    def __init__(self):
        self.cpu = 0.0
        self.mem = 0.0
        self.net = 0.0
        self.gpu = -1.0
        self.tmp = -1.0
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        # Probe caches — GPU (NVML) and temperature (WMI) are the expensive
        # queries; initialise their handles once and reuse them instead of
        # rebuilding a connection on every poll.
        self._slow_tick = 0  # gpu/temp refreshed every 3rd cycle
        self._pynvml = None  # cached pynvml module + device handle
        self._pynvml_h = None
        self._pynvml_ok = None  # None=untested, False=unavailable here
        self._nv_unix = None  # cached (lib, dev) for Linux/macOS NVML
        self._wmi_conn = None  # cached WMI connection (creating one is slow)
        self._wmi_ok = None  # None=untested, False=unavailable here
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(2.0)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc = psutil.net_io_counters()
        now = time.time()
        dt = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net = nc
        self._last_net_t = now

        # GPU and temperature change slowly and are the most expensive probes
        # (NVML / WMI) — refresh them every 3rd cycle (~6 s) instead of every
        # cycle, reusing the previous reading in between.
        self._slow_tick = (self._slow_tick + 1) % 3
        if self._slow_tick == 1:
            gpu = self._get_gpu()
            tmp = self._get_temp()
        else:
            gpu = self.gpu
            tmp = self.tmp

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # pynvml — subprocess-free; initialise once and reuse the handle.
        # Re-initialising NVML on every poll is slow, so cache it and stop
        # retrying pynvml entirely once it proves unavailable here.
        if self._pynvml_ok is not False:
            try:
                if self._pynvml_h is None:
                    import pynvml  # type: ignore

                    pynvml.nvmlInit()
                    self._pynvml = pynvml
                    self._pynvml_h = pynvml.nvmlDeviceGetHandleByIndex(0)
                    self._pynvml_ok = True
                return float(
                    self._pynvml.nvmlDeviceGetUtilizationRates(self._pynvml_h).gpu
                )
            except Exception:
                self._pynvml_ok = False

        # Windows: nvml.dll via ctypes (already cached in _nvml_gpu_windows)
        if _OS == "Windows":
            return _nvml_gpu_windows()

        # Linux / macOS: libnvidia-ml shared lib via ctypes — init once, reuse
        try:
            import ctypes

            class _Util(ctypes.Structure):
                _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

            if self._nv_unix is None:
                _lib = "libnvidia-ml.so.1" if _OS == "Linux" else "libnvidia-ml.dylib"
                nv = ctypes.CDLL(_lib)
                nv.nvmlInit_v2()
                dev = ctypes.c_void_p()
                nv.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
                self._nv_unix = (nv, dev)

            nv, dev = self._nv_unix
            u = _Util()
            nv.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
            return float(u.gpu)
        except Exception:
            pass

        return -1.0  # N/A — zero subprocess on all platforms

    def _get_temp(self) -> float:
        # psutil — works on Linux; occasionally Windows with driver support
        try:
            temps = psutil.sensors_temperatures()
            for name in [
                "coretemp",
                "k10temp",
                "cpu_thermal",
                "acpitz",
                "cpu-thermal",
                "zenpower",
                "it8688",
            ]:
                if name in temps and temps[name]:
                    return temps[name][0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass

        # Windows: wmi module (pure Python COM, zero subprocess). Reuse a single
        # connection — building a fresh wmi.WMI() on every poll spins up a COM
        # connection each time and is very slow. Give up after one failure.
        if _OS == "Windows" and self._wmi_ok is not False:
            try:
                if self._wmi_conn is None:
                    import wmi  # type: ignore

                    self._wmi_conn = wmi.WMI(namespace="root/wmi")
                tz = self._wmi_conn.MSAcpi_ThermalZoneTemperature()
                if tz:
                    return (tz[0].CurrentTemperature / 10.0) - 273.15
            except Exception:
                self._wmi_ok = False
                self._wmi_conn = None

        return -1.0  # N/A — zero subprocess on all platforms

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()


# ── Particle-mesh orb data ───────────────────────────────────────────────────
# The orb is a Fibonacci-lattice sphere of dots. Which dot is joined to which is
# decided ONCE, here, on the unit sphere — so a frame costs a rotation and a
# batch of line draws, never an O(n²) neighbour search. Stars are generated the
# same way: fixed seed, so the sky does not reshuffle between launches.
_ORB_N = 340
_ORB_CACHE = None
_ORB_EDGE_Y: list = []  # object-space height of every edge (scan highlight)

# Satellites: (orbit radius in sphere radii, inclination, node angle, angular
# speed, start phase, head size). Each leaves a short fading trail of dots —
# there is deliberately no orbit *line*.
_SATS = (
    (1.30, 0.55, 0.00, 0.75, 0.0, 3.4),
    (1.18, -0.80, 2.10, -0.95, 2.0, 3.0),
    (1.42, 1.25, 4.00, 0.52, 4.1, 2.8),
    (1.56, -0.35, 5.20, 0.66, 1.3, 2.6),
)


def _build_orb():
    global _ORB_EDGE_Y
    rnd = random.Random(7)
    ga = math.pi * (3.0 - math.sqrt(5.0))
    pts, phs = [], []
    for i in range(_ORB_N):
        y = 1.0 - 2.0 * (i + 0.5) / _ORB_N
        r = math.sqrt(max(0.0, 1.0 - y * y))
        th = ga * i
        pts.append((math.cos(th) * r, y, math.sin(th) * r))
        phs.append(rnd.uniform(0.0, 6.2832))
    edges = []
    lim = 0.28 * 0.28
    for i in range(_ORB_N):
        xi, yi, zi = pts[i]
        for j in range(i + 1, _ORB_N):
            xj, yj, zj = pts[j]
            dx, dy, dz = xi - xj, yi - yj, zi - zj
            if dx * dx + dy * dy + dz * dz < lim:
                edges.append((i, j))
    _ORB_EDGE_Y = [(pts[a][1] + pts[b][1]) * 0.5 for a, b in edges]
    stars = []
    for _ in range(80):
        stars.append(
            (
                rnd.uniform(-1.0, 1.0),
                rnd.uniform(-1.0, 1.0),
                rnd.random() < 0.16,
                rnd.uniform(0.0, 6.2832),
                rnd.uniform(0.6, 2.2),
                rnd.uniform(0.3, 1.0),
            )
        )
    return pts, phs, edges, stars


def _get_orb():
    global _ORB_CACHE
    if _ORB_CACHE is None:
        _ORB_CACHE = _build_orb()
    return _ORB_CACHE


def _draw_lines(p: QPainter, lines) -> None:
    if not lines:
        return
    try:
        p.drawLines(lines)
    except TypeError:
        for ln in lines:
            p.drawLine(ln)


def _draw_points(p: QPainter, pts) -> None:
    if not pts:
        return
    try:
        p.drawPoints(QPolygonF(pts))
    except TypeError:
        for pt in pts:
            p.drawPoint(pt)


class _MiniBar(QWidget):
    """Three small buttons that fade in over the mini orb while the mouse is on
    it: mute, stop speaking, expand."""

    expand_clicked = pyqtSignal()
    mute_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        def mk(text: str, tip: str, sig) -> QPushButton:
            b = QPushButton(text)
            b.setFixedSize(24, 24)
            b.setFont(QFont(_FONT, 9, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tip)
            b.setStyleSheet(f"""
                QPushButton {{
                    color: {C.TEXT_MED};
                    background: rgba(255, 255, 255, 18);
                    border: 1px solid rgba(255, 255, 255, 40);
                    border-radius: 12px;
                }}
                QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}
            """)
            b.clicked.connect(lambda _=False: sig.emit())
            lay.addWidget(b)
            return b

        self._mute = mk("🎙", "Mute microphone  [F4]", self.mute_clicked)
        mk("■", "Stop speaking  [Esc]", self.stop_clicked)
        mk("⤢", "Expand  (double-click the orb)", self.expand_clicked)
        self.adjustSize()
        self.hide()

    def set_muted(self, muted: bool) -> None:
        self._mute.setText("🔇" if muted else "🎙")


class HudCanvas(QWidget):
    def __init__(
        self, face_path: str, assistant_name: str = "J.A.R.V.I.S", parent=None
    ):
        super().__init__(parent)
        self.setAutoFillBackground(False)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted = False
        self.speaking = False
        self.state = "INITIALISING"
        self._assistant_name = assistant_name

        # The holographic head that fills the HUD. If it could not be imported
        # we fall back to the particle orb so the panel is never empty.
        self._avatar = None
        if HoloAvatar is not None:
            try:
                self._avatar = HoloAvatar()
            except Exception:
                self._avatar = None

        # Which centrepiece to draw. Read once here and changed live by the
        # settings toggle; the avatar object is kept either way so switching
        # back is instant and costs no reload.
        try:
            from memory.config_manager import get_hud_style

            self.hud_style = get_hud_style()
        except Exception:
            self.hud_style = "face"
        self._core_phase = 0.0

        # Particle orb: topology + stars are built once (module cache). Pulses
        # are the little bright packets that run along random mesh edges:
        # [edge index, progress 0..1, speed].
        self._orb = _get_orb()
        _ne = max(1, len(self._orb[2]))
        self._pulses = [
            [random.randrange(_ne), random.random(), random.uniform(0.5, 1.2)]
            for _ in range(14)
        ]
        # Spin angle is integrated frame by frame, never "time x rate": a rate
        # that changes with the state would make the sphere jump the instant
        # JARVIS starts to speak.
        self._spin = 0.0
        self._shoot = None  # the shooting star, if one is out
        self._shoot_next = time.time() + 3.0

        self._tick = 0
        self._born = time.time()
        self._tel = {"cpu": 0.0, "mem": 0.0}
        self._tel_t = 0.0
        self._step_t = time.time()
        self._blink = True
        self._blink_tick = 0

        # Static grid-dot layer, pre-rendered once per size/theme into a pixmap
        # so paintEvent blits it in one call instead of thousands of drawPoint()s.
        self._grid_cache: QPixmap | None = None
        self._grid_key = None
        # Repaint throttle counter (idle frames drop to ~20-30 Hz — see _step()).
        self._paint_tick = 0

        # Live audio reactivity: _live_amp is written from the audio threads
        # (0.0–1.0), _amp_disp is the smoothed value the paint code reads.
        self._live_amp = 0.0
        self._amp_disp = 0.0
        # (frames, start_time, hop) posted by the playback thread — see
        # push_visemes(). None means "no schedule; use the plain level".
        self._visemes = None
        self._vis_i = None  # first schedule frame not yet handed to the mouth

        # ── Compact (mini window) mode ───────────────────────────────────────
        # In compact mode the canvas is the whole window: it draws a rounded
        # dark card with just the orb, lets you drag the window by it, and shows
        # a hover bar plus a resize grip. MainWindow wires the callbacks.
        self.compact = False
        self.on_expand = None  # callable: () -> None   (double-click)
        self.on_menu = None  # callable: (QPoint) -> None  (right-click)
        self._drag_off = None
        self.mini_bar = _MiniBar(self)
        self._grip = QSizeGrip(self)
        self._grip.hide()

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    # ── compact mode plumbing ────────────────────────────────────────────────
    def set_compact(self, on: bool) -> None:
        self.compact = bool(on)
        self._drag_off = None
        if self.compact:
            self.setMinimumSize(100, 100)
        else:
            self.setMinimumSize(300, 300)
            self.mini_bar.hide()
            self._grip.hide()
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.mini_bar.move(self.width() - self.mini_bar.width() - 8, 8)
        self._grip.adjustSize()
        self._grip.move(
            self.width() - self._grip.width() - 4,
            self.height() - self._grip.height() - 4,
        )

    def enterEvent(self, e):
        if self.compact:
            self.mini_bar.show()
            self.mini_bar.raise_()
            self._grip.show()
            self._grip.raise_()
        super().enterEvent(e)

    def leaveEvent(self, e):
        # Moving onto one of our own child buttons also fires leave on the
        # parent, so only hide when the cursor is genuinely outside.
        if self.compact and not self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            self.mini_bar.hide()
            self._grip.hide()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if self.compact and e.button() == Qt.MouseButton.LeftButton:
            self._drag_off = (
                e.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            )
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if (
            self.compact
            and self._drag_off is not None
            and (e.buttons() & Qt.MouseButton.LeftButton)
        ):
            self.window().move(e.globalPosition().toPoint() - self._drag_off)
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_off = None
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if self.compact and self.on_expand:
            self.on_expand()
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def contextMenuEvent(self, e):
        if self.compact and self.on_menu:
            self.on_menu(e.globalPos())
            e.accept()
            return
        super().contextMenuEvent(e)

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Ask the avatar to look somewhere for a moment (see HoloAvatar.glance)."""
        try:
            if self._avatar is not None:
                self._avatar.glance(dx, dy, hold)
        except Exception:
            pass

    def push_visemes(self, frames, hop: float, at: float) -> None:
        """Thread-safe: hand over a schedule of (level, openness, width) frames.

        The playback thread writes up to 200 ms of audio in one go, so a single
        averaged level would only move the mouth five times a second — enough to
        flap, nowhere near enough to articulate. It instead posts the whole
        slice's worth of 20 ms frames here and `_step()` plays them out against
        the wall clock, in step with the audio going to the speakers.

        `at` is the wall-clock time this batch will *begin to sound*, which the
        caller tracks as a playback cursor. It is not the time of the call, and
        the difference is the whole point: `stream.write` returns once the buffer
        accepts the samples, so consecutive batches are handed over far faster
        than they play. Anchoring each one to "now" made every batch start while
        its predecessor was still sounding, so each schedule replaced the last
        after a couple of frames and the mouth only ever played the opening
        instant of every 200 ms — the reason it did not match the words.

        Successive batches are therefore *appended* into one continuous
        timeline, not swapped in. A paragraph is one schedule; the mouth stops
        falling into a gap at every chunk boundary and having to climb back out.
        """
        try:
            if not frames:
                return
            hop = max(1e-3, float(hop))
            at = float(at)
            new = list(frames)
            cur = self._visemes
            if cur is not None:
                old, t0, ohop = cur
                if abs(ohop - hop) < 1e-6:
                    # Where in the existing timeline does this batch land?
                    i = int(round((at - t0) / hop))
                    if 0 <= i <= len(old) + 1:
                        # Continues (or slightly overlaps) what is already
                        # queued: extend rather than restart. Drop whatever has
                        # already been played so the list cannot grow without
                        # bound over a long reply.
                        merged = old[:i] + new
                        played = int((time.time() - t0) / hop) - 2
                        if played > 60:
                            merged = merged[played:]
                            t0 += played * hop
                            if self._vis_i is not None:
                                self._vis_i = max(0, self._vis_i - played)
                        self._visemes = (merged, t0, hop)
                        return
            self._visemes = (new, at, hop)
            self._vis_i = None
        except Exception:
            pass

    def set_audio_level(self, level: float) -> None:
        """Thread-safe entry point for the audio threads. Stores the louder of
        the incoming level and the current value so brief gaps between chunks
        don't make the waveform stutter; _step() decays it back down."""
        try:
            lv = float(level)
        except (TypeError, ValueError):
            return
        if lv < 0.0:
            lv = 0.0
        elif lv > 1.0:
            lv = 1.0
        if lv > self._live_amp:
            self._live_amp = lv

    def _make_grid(self, W: int, H: int) -> QPixmap:
        """Pre-render the static grid-dot background into a transparent pixmap so
        paintEvent can blit it once per frame instead of running a nested
        drawPoint() loop across the whole widget every 16 ms."""
        pm = QPixmap(max(1, W), max(1, H))
        pm.fill(Qt.GlobalColor.transparent)
        gp = QPainter(pm)
        gp.setPen(QPen(_ink(34), 1))
        for x in range(0, W, 48):
            for y in range(0, H, 48):
                gp.drawPoint(x, y)
        gp.end()
        return pm

    def _step(self):
        self._tick += 1
        now = time.time()

        # ── Live audio reactivity ────────────────────────────────────────────
        # A viseme schedule, if one is playing, gives both the level and the
        # mouth shape for this exact instant; otherwise fall back to the peak
        # level the audio threads pushed in.
        v_open = v_wide = v_level = None
        v_seq = None
        sched = self._visemes
        if sched is not None:
            frames, t0, hop = sched
            i = int((now - t0) / hop)
            if 0 <= i < len(frames):
                # Hand over *every* frame since the last tick, not just the one
                # under the cursor. This timer runs at 60 Hz but the paint is
                # throttled and the machine may be busy, so a tick can span two
                # or three 20 ms frames — and a consonant closure is only two
                # frames long. Sampling one and discarding the rest is how the
                # closures between words went missing.
                j = self._vis_i if self._vis_i is not None else i
                v_seq = frames[max(0, j) : i + 1]
                self._vis_i = max(j, i + 1)
                v_level, v_open, v_wide = frames[i]
                if v_seq:
                    peak = max(f[0] for f in v_seq)
                    if peak > self._live_amp:
                        self._live_amp = peak
            elif i >= len(frames):
                self._visemes = None  # schedule spent
                self._vis_i = None

        # Audio threads push peaks into _live_amp; decay it toward silence so
        # gaps between chunks fade out instead of freezing, then smooth it.
        self._live_amp *= 0.86
        self._amp_disp += (self._live_amp - self._amp_disp) * 0.45
        amp = self._amp_disp

        # The avatar animates off the very same smoothed level the waveform
        # uses — one audio source, so the mouth can never drift out of sync.
        dt = now - self._step_t
        self._step_t = now
        # Integrated, not derived from absolute time: multiplying wall-clock by
        # a rate that changes with state jumps the rings the instant JARVIS
        # starts talking. Same lesson the head's sway taught.
        self._core_phase += min(0.10, max(0.0, dt))
        self._spin += min(0.10, max(0.0, dt)) * (
            0.42
            + (0.30 if self.speaking else 0.0)
            + (0.16 if self.state in ("THINKING", "PROCESSING") else 0.0)
            + 0.6 * amp
        )

        use_face = self._avatar is not None and self.hud_style == "face"
        if use_face:
            self._avatar.step(
                dt,
                amp,
                speaking=self.speaking,
                muted=self.muted,
                state=self.state,
                v_open=v_open,
                v_wide=v_wide or 0.0,
                v_level=v_level,
                v_seq=v_seq,
                v_hop=(sched[2] if sched is not None else 0.02),
            )
        else:
            # Mesh pulses: little packets running along the orb's edges, faster
            # when the voice is loud or JARVIS is busy.
            spd = (
                1.0
                + 1.6 * amp
                + (0.6 if self.speaking else 0.0)
                + (0.5 if self.state in ("THINKING", "PROCESSING") else 0.0)
            )
            step = min(0.10, max(0.0, dt)) * spd
            ne = len(self._orb[2])
            for pl in self._pulses:
                pl[1] += step * pl[2]
                if pl[1] >= 1.0:
                    pl[0] = random.randrange(ne)
                    pl[1] = 0.0
                    pl[2] = random.uniform(0.5, 1.2)
            # A shooting star now and then.
            sh = self._shoot
            if sh is None:
                if now >= self._shoot_next:
                    self._shoot = {
                        "x": random.uniform(0.05, 0.55),
                        "y": random.uniform(0.04, 0.35),
                        "a": random.uniform(0.35, 0.8),
                        "v": random.uniform(0.55, 0.9),
                        "t": 0.0,
                        "life": random.uniform(0.7, 1.1),
                    }
            else:
                sh["t"] += min(0.10, max(0.0, dt))
                if sh["t"] >= sh["life"]:
                    self._shoot = None
                    self._shoot_next = now + random.uniform(3.0, 7.0)

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0
            _blinked = True
        else:
            _blinked = False

        # Repaint throttling — advancing the animation state above is cheap at
        # 60 Hz, but the paint is heavy. Active (speaking, audio, thinking) runs
        # at ~30 Hz, which is the frame rate animation has used for talking
        # characters forever and is indistinguishable here; idle drops to ~20 Hz
        # for the head. The particle orb is nothing but slow motion, so it idles
        # at 30 Hz too — 20 Hz made the rotation visibly steppy. The visuals stay
        # smooth either way because the animation state keeps stepping at 60 Hz.
        self._paint_tick = (self._paint_tick + 1) % 6
        active = self.speaking or amp > 0.02 or self.state in ("THINKING", "PROCESSING")
        idle_mod = 3 if use_face else 2
        if _blinked or (
            self._paint_tick % 2 == 0 if active else self._paint_tick % idle_mod == 0
        ):
            # Nothing is on screen when the window is hidden or minimised, so
            # rendering the avatar into it is pure waste — and this app is meant
            # to sit running all day. The animation state above keeps stepping,
            # so it picks up mid-motion instead of snapping when you come back.
            if self._on_screen():
                self.update()

    def _on_screen(self) -> bool:
        """True only when this canvas can actually be seen by the user."""
        try:
            if not self.isVisible():
                return False
            win = self.window()
            return not (win.isMinimized() or win.isHidden())
        except Exception:
            return True  # never let a visibility check stop the HUD drawing

    # ── particle orb ─────────────────────────────────────────────────────────
    # A sphere made of dots joined into a mesh, with satellites and stars around
    # it. QPainter only — no GPU, no assets. Everything on it means something:
    # the dots swell with the voice, the mesh brightens with it, the packets
    # running along the edges speed up when JARVIS talks or thinks, and the
    # spin rate follows the state. Nothing is drawn as a ring or an orbit line.
    #
    # Cost is kept flat: the mesh topology is precomputed, lines and dots are
    # drawn in four depth buckets (one pen change per bucket, one batched call),
    # and stars in a few twinkle buckets. A frame is ~150 rotations, ~450 line
    # objects and a handful of draw calls.

    def _core_colours(self):
        if self.muted:
            return qcol(C.MUTED_C), qcol(C.MUTED_C)
        if self.speaking:
            return qcol(C.PRI), qcol(C.ACC)
        if self.state in ("THINKING", "PROCESSING"):
            return qcol(C.PRI), qcol(C.ACC2)
        if self.state == "LISTENING":
            return qcol(C.PRI), qcol(C.GREEN)
        return qcol(C.PRI), qcol(C.PRI_DIM)

    def _paint_core(
        self,
        p: QPainter,
        x: float,
        y: float,
        w: float,
        h: float,
        show_name: bool = True,
        brackets: bool = True,
    ):
        main, acc = self._core_colours()
        amp = self._amp_disp if not self.muted else 0.0
        t = self._core_phase
        thinking = self.state in ("THINKING", "PROCESSING")
        pts, phs, edges, stars = self._orb

        def tint(col: QColor, alpha: float) -> QColor:
            c = QColor(col)
            c.setAlpha(max(0, min(255, int(alpha))))
            return c

        def pen(col: QColor, wd: float, rnd: bool = False) -> QPen:
            pn = QPen(col, wd)
            if rnd:
                pn.setCapStyle(Qt.PenCapStyle.RoundCap)
            return pn

        name_h = 38.0 if show_name else 0.0
        avail = max(20.0, h - name_h)
        R = max(10.0, min(w * 0.30, avail / 2.0 / 1.42))
        cx = x + w / 2.0
        cy = y + avail / 2.0

        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)

        # HUD corner brackets
        if brackets and w > 40 and h > 40:
            m, arm = min(w, h) * 0.03, min(w, h) * 0.055
            p.setPen(QPen(tint(main, 120), 1.4))
            for px, py, sx, sy in (
                (x + m, y + m, 1, 1),
                (x + w - m, y + m, -1, 1),
                (x + m, y + h - m, 1, -1),
                (x + w - m, y + h - m, -1, -1),
            ):
                p.drawLine(QLineF(px, py, px + sx * arm, py))
                p.drawLine(QLineF(px, py, px, py + sy * arm))

        # ── stars: a fixed sky that twinkles and drifts a few pixels ─────────
        buckets: dict = {}
        for u, v, big, sph, sps, dep in stars:
            tw = 0.5 + 0.5 * math.sin(t * sps + sph)
            lvl = 0 if tw < 0.34 else (1 if tw < 0.67 else 2)
            sx_ = x + (u * 0.5 + 0.5) * w + math.sin(t * 0.08 * dep + sph) * 4.0 * dep
            sy_ = y + (v * 0.5 + 0.5) * h + math.cos(t * 0.06 * dep + sph) * 3.0 * dep
            buckets.setdefault((big, lvl), []).append(QPointF(sx_, sy_))
        star_alpha = (50, 115, 205)
        for (big, lvl), lst in buckets.items():
            p.setPen(pen(qcol(C.WHITE, star_alpha[lvl]), 2.6 if big else 1.4, True))
            _draw_points(p, lst)

        # ── shooting star ────────────────────────────────────────────────────
        sh = self._shoot
        if sh is not None:
            fr = sh["t"] / sh["life"]
            fade = math.sin(math.pi * min(1.0, max(0.0, fr)))
            ca, sa = math.cos(sh["a"]), math.sin(sh["a"])
            hx = x + sh["x"] * w + ca * sh["v"] * sh["t"] * w
            hy = y + sh["y"] * h + sa * sh["v"] * sh["t"] * w
            tl = 0.14 * w
            for i in range(10):
                f0, f1 = i / 10.0, (i + 1) / 10.0
                p.setPen(
                    pen(
                        qcol(C.WHITE, 230 * (1.0 - f0) * fade),
                        1.9 * (1.0 - f0 * 0.8),
                        True,
                    )
                )
                p.drawLine(
                    QLineF(
                        hx - ca * tl * f0,
                        hy - sa * tl * f0,
                        hx - ca * tl * f1,
                        hy - sa * tl * f1,
                    )
                )

        # ── instrument bezel: fine ticks turning slowly around the orb ───────
        Rz = min(R * 1.86, avail / 2.0 - 6.0)
        if brackets and Rz > R * 1.5:
            nt, a0 = 120, t * 0.045
            minor, major = [], []
            for i in range(nt):
                ang = a0 + i * (2.0 * math.pi / nt)
                c_, s_ = math.cos(ang), math.sin(ang)
                r0_ = Rz - (7.0 if i % 10 == 0 else 3.5)
                (major if i % 10 == 0 else minor).append(
                    QLineF(cx + c_ * r0_, cy + s_ * r0_, cx + c_ * Rz, cy + s_ * Rz)
                )
            p.setPen(QPen(tint(main, 46), 1.0))
            _draw_lines(p, minor)
            p.setPen(QPen(tint(main, 120), 1.4))
            _draw_lines(p, major)

        # ── view rotation ────────────────────────────────────────────────────
        ry = self._spin
        rx = -0.28 + math.sin(t * 0.27) * 0.16 + math.sin(t * 0.11) * 0.05
        cyr, syr = math.cos(ry), math.sin(ry)
        cxr, sxr = math.cos(rx), math.sin(rx)
        persp = 0.28

        # ── satellites (heads + dotted trails), split behind / in front ──────
        def sat_pos(ro, inc, nd, a):
            sx0 = math.cos(a) * ro
            sz0 = math.sin(a) * ro
            sy1 = -sz0 * math.sin(inc)
            sz1 = sz0 * math.cos(inc)
            x2 = sx0 * math.cos(nd) + sz1 * math.sin(nd)
            z2 = -sx0 * math.sin(nd) + sz1 * math.cos(nd)
            y3 = sy1 * cxr - z2 * sxr
            z3 = sy1 * sxr + z2 * cxr
            d = 1.0 / max(0.6, 1.0 - persp * z3)
            return QPointF(cx + x2 * R * d, cy + y3 * R * d), z3

        back, front = [], []
        for ro, inc, nd, spd, ph0, head in _SATS:
            a0 = ph0 + t * spd
            sgn = 1.0 if spd > 0 else -1.0
            for k in range(10):
                pt, z = sat_pos(ro, inc, nd, a0 - sgn * 0.08 * k)
                fade = 1.0 - k / 10.0
                behind = z < 0.0
                r = head * (1.0 - 0.07 * k) * (0.8 if behind else 1.0)
                al = 235.0 * fade * fade * (0.45 if behind else 1.0)
                (back if behind else front).append((pt, r, al, k == 0))

        def draw_sats(items):
            p.setPen(Qt.PenStyle.NoPen)
            for pt, r, al, head in items:
                if head:
                    p.setBrush(QBrush(tint(acc, al * 0.22)))
                    p.drawEllipse(pt, r * 2.8, r * 2.8)
                    p.setBrush(QBrush(tint(QColor(C.WHITE), al)))
                else:
                    p.setBrush(QBrush(tint(acc, al)))
                p.drawEllipse(pt, r, r)

        draw_sats(back)

        # ── ambient glow + glass body (light from the upper left) ────────────
        lift = (
            1.0
            + 0.9 * amp
            + (0.25 if self.speaking else 0.0)
            + 0.12 * math.sin(t * 1.15)
        )
        gr = R * 2.1
        g = QRadialGradient(cx, cy, gr)
        g.setColorAt(0.0, tint(main, 60 * lift))
        g.setColorAt(0.5, tint(main, 18 * lift))
        g.setColorAt(1.0, tint(main, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QRectF(cx - gr, cy - gr, gr * 2, gr * 2))

        Rb = R * (1.0 + 0.018 * math.sin(t * 1.15) + amp * 0.05)
        g2 = QRadialGradient(cx - Rb * 0.30, cy - Rb * 0.35, Rb * 1.3)
        g2.setColorAt(0.0, tint(main, 46))
        g2.setColorAt(0.6, tint(main, 14))
        g2.setColorAt(1.0, tint(main, 6))
        p.setBrush(QBrush(g2))
        p.drawEllipse(QRectF(cx - Rb, cy - Rb, Rb * 2, Rb * 2))

        # ── project the dots ─────────────────────────────────────────────────
        proj = []
        zs = []
        wob_t = t * 5.0
        slow_t = t * 1.3
        for i in range(len(pts)):
            px0, py0, pz0 = pts[i]
            ph = phs[i]
            k = (
                1.0
                + 0.022 * math.sin(slow_t + ph)
                + amp * 0.20 * (0.5 + 0.5 * math.sin(wob_t + ph * 2.0))
                # a ripple that travels up the sphere, stronger with the voice
                + (0.016 + 0.045 * amp) * math.sin(py0 * 6.0 - t * 2.8 + ph * 0.2)
            )
            px0 *= k
            py0 *= k
            pz0 *= k
            x1 = px0 * cyr + pz0 * syr
            z1 = -px0 * syr + pz0 * cyr
            y1 = py0 * cxr - z1 * sxr
            z2 = py0 * sxr + z1 * cxr
            d = 1.0 / max(0.6, 1.0 - persp * z2)
            proj.append(QPointF(cx + x1 * Rb * d, cy + y1 * Rb * d))
            zs.append(z2)

        # ── mesh edges, four depth buckets ───────────────────────────────────
        NB = 4
        elines = [[] for _ in range(NB)]
        hot = []  # edges the scan band is crossing
        scan = math.sin(t * 0.85) * 0.92
        ey = _ORB_EDGE_Y
        for ei, (a, b) in enumerate(edges):
            dn = ((zs[a] + zs[b]) * 0.25) + 0.5
            bi = 0 if dn < 0.0 else (NB - 1 if dn >= 1.0 else int(dn * NB))
            ln = QLineF(proj[a], proj[b])
            if bi >= 2 and abs(ey[ei] - scan) < 0.07:
                hot.append(ln)
            else:
                elines[bi].append(ln)
        e_alpha = (22, 48, 86, 134)
        e_width = (0.7, 0.8, 1.0, 1.2)
        elift = 1.0 + 0.8 * amp
        for bi in range(NB):
            p.setPen(QPen(tint(main, e_alpha[bi] * elift), e_width[bi]))
            _draw_lines(p, elines[bi])
        if hot:
            p.setPen(QPen(tint(acc, 120 + 70 * amp), 1.1))
            _draw_lines(p, hot)

        # ── the dots themselves ──────────────────────────────────────────────
        dpts = [[] for _ in range(NB)]
        for i in range(len(proj)):
            dn = (zs[i] + 1.0) * 0.5
            bi = 0 if dn < 0.0 else (NB - 1 if dn >= 1.0 else int(dn * NB))
            dpts[bi].append(proj[i])
        d_alpha = (70, 120, 190, 255)
        d_size = (1.6, 2.2, 3.0, 4.0)
        bright = QColor(main).lighter(140)
        for bi in range(NB):
            col = bright if bi == NB - 1 else main
            sz = d_size[bi] * (1.0 + 0.25 * amp)
            p.setPen(pen(tint(col, d_alpha[bi]), sz, True))
            _draw_points(p, dpts[bi])

        # ── data packets running along the mesh ──────────────────────────────
        p.setPen(Qt.PenStyle.NoPen)
        for ei, pr, _sp in self._pulses:
            a, b = edges[ei]
            z = zs[a] + (zs[b] - zs[a]) * pr
            if z < -0.25:
                continue
            pa, pb = proj[a], proj[b]
            for k in range(4):  # head plus a short fading tail
                q = pr - 0.07 * k
                if q < 0.0:
                    break
                pt = QPointF(
                    pa.x() + (pb.x() - pa.x()) * q, pa.y() + (pb.y() - pa.y()) * q
                )
                if k == 0:
                    p.setBrush(QBrush(tint(acc, 70)))
                    p.drawEllipse(pt, 5.0, 5.0)
                    p.setBrush(QBrush(tint(QColor(C.WHITE), 235)))
                    p.drawEllipse(pt, 2.1, 2.1)
                else:
                    fd = 1.0 - k * 0.25
                    p.setBrush(QBrush(tint(acc, 160 * fd)))
                    p.drawEllipse(pt, 1.2 + 0.7 * fd, 1.2 + 0.7 * fd)

        draw_sats(front)

        # ── the name, under the sphere ───────────────────────────────────────
        if show_name:
            name = (self._assistant_name or "JARVIS").upper()
            fsz = int(max(10, min(22, R * 0.16)))
            f = QFont(_DISPLAY, fsz, QFont.Weight.Bold)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, max(2.0, fsz * 0.28))
            p.setFont(f)
            ny = min(cy + R * 1.42, y + h - name_h)
            r1 = QRectF(x, ny, w, name_h)
            p.setPen(QPen(tint(main, 90), 1))
            p.drawText(r1.translated(0, 1.5), Qt.AlignmentFlag.AlignCenter, name)
            p.setPen(QPen(tint(QColor(C.WHITE), 200 + 55 * amp), 1))
            p.drawText(r1, Qt.AlignmentFlag.AlignCenter, name)

        p.restore()

    def _paint_telemetry(
        self,
        p: QPainter,
        W: int,
        H: int,
        band_top: float,
        state_txt: str,
        state_col: QColor,
    ) -> None:
        """Four small readouts in the corners: state, load, microphone, uptime.

        They are real values, not decoration — and they are the reason a user
        can hide both sidebars and still see that JARVIS is alive and how hard
        the machine is working."""
        now = time.time()
        if now - self._tel_t > 1.0:
            self._tel = _metrics.snapshot()
            self._tel_t = now
        s = self._tel
        f = QFont(_MONO, 8)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.6)
        p.setFont(f)
        fm = QFontMetrics(f)
        m = min(W, H) * 0.03
        inx = m + 12.0
        top = band_top + m + 2.0
        bot = H - 22.0
        dim = qcol(C.TEXT_DIM)
        AL = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        AR = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

        p.setPen(QPen(dim, 1))
        p.drawText(QRectF(inx, top, 60, 14), AL, "CORE")
        p.setPen(QPen(state_col, 1))
        p.drawText(
            QRectF(inx + fm.horizontalAdvance("CORE") + 8, top, 220, 14), AL, state_txt
        )

        cpu, mem = float(s.get("cpu", 0.0)), float(s.get("mem", 0.0))
        txt = f"CPU {cpu:.0f}%   MEM {mem:.0f}%"
        p.setPen(QPen(qcol(C.RED) if max(cpu, mem) > 85 else dim, 1))
        p.drawText(QRectF(W - inx - 240, top, 240, 14), AR, txt)

        p.setPen(QPen(qcol(C.MUTED_C) if self.muted else dim, 1))
        p.drawText(
            QRectF(inx, bot, 200, 14), AL, "MIC MUTED" if self.muted else "MIC LIVE"
        )

        up = int(now - self._born)
        p.setPen(QPen(dim, 1))
        p.drawText(
            QRectF(W - inx - 240, bot, 240, 14),
            AR,
            f"UP {up // 3600:02d}:{up % 3600 // 60:02d}:{up % 60:02d}",
        )

    def _status(self):
        if self.muted:
            return "MUTED", qcol(C.MUTED_C)
        if self.speaking:
            return "SPEAKING", qcol(C.ACC)
        if self.state == "THINKING":
            return "THINKING", qcol(C.ACC2)
        if self.state == "PROCESSING":
            return "PROCESSING", qcol(C.ACC2)
        if self.state == "LISTENING":
            return "LISTENING", qcol(C.GREEN)
        return str(self.state), qcol(C.PRI)

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():  # device not ready (e.g. 0-size during layout)
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # No background fill in the full window: the aurora root shows through,
        # which is what makes the HUD feel like it floats on glass.

        W, H = self.width(), self.height()
        compact = self.compact
        txt, col = self._status()

        if compact:
            # A deliberately light glass card: mini mode is an unobtrusive
            # development/status indicator, not a second full HUD.
            card = QPainterPath()
            card.addRoundedRect(QRectF(0.5, 0.5, W - 1, H - 1), 18, 18)
            p.fillPath(card, _surf(158))
            p.setPen(QPen(qcol(C.PRI, 46), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(card)
            bx, by, bw, bh = 8.0, 8.0, W - 16.0, H - 16.0
        else:
            _gkey = (W, H, C.PRI_GHO)
            if self._grid_cache is None or self._grid_key != _gkey:
                self._grid_cache = self._make_grid(W, H)
                self._grid_key = _gkey
            p.drawPixmap(0, 0, self._grid_cache)

            # Bottom 86 px belong to the status pill + waveform; the centrepiece
            # gets everything above, so nothing can overlap at any window size.
            _sy_status = H - 64.0
            _band_t = 12.0
            _band_h = max(60.0, _sy_status - 12.0 - _band_t)
            bx, by, bw, bh = 0.0, _band_t, float(W), _band_h

        if self._avatar is not None and self.hud_style == "face":
            _r_head = min(min(bw, bh) * 0.355, bh / (self._avatar.SPAN + 0.08))
            _head_cy = by + (bh - self._avatar.SPAN * _r_head) / 2.0 + _r_head
            if self.muted:
                _main = _acc = qcol(C.MUTED_C)
            else:
                _main = qcol(C.PRI)
                if self.speaking:
                    _acc = qcol(C.ACC)
                elif self.state in ("THINKING", "PROCESSING"):
                    _acc = qcol(C.ACC2)
                elif self.state == "LISTENING":
                    _acc = qcol(C.GREEN)
                else:
                    _acc = qcol(C.PRI)
            self._avatar.paint(
                p, bx + bw / 2.0, _head_cy, _r_head, _main, _acc, qcol(C.BG)
            )
        else:
            self._paint_core(
                p,
                bx,
                by,
                bw,
                bh,
                show_name=(not compact) or (H >= 200 and W >= 150),
                brackets=not compact,
            )

        if not compact and W >= 460 and H >= 360:
            self._paint_telemetry(p, W, H, by, txt, col)

        if compact:
            # One small status dot in the corner replaces the pill + waveform.
            dot = QColor(col)
            dot.setAlpha(255 if (self._blink or self.speaking or self.muted) else 90)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(dot))
            p.drawEllipse(QPointF(18, 18), 4.0, 4.0)
            p.end()
            return

        # ── status pill ──────────────────────────────────────────────────────
        sy = _sy_status
        f = QFont(_DISPLAY, 9, QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.0)
        p.setFont(f)
        tw = QFontMetrics(f).horizontalAdvance(txt)
        pw, ph = tw + 50.0, 24.0
        pr = QRectF((W - pw) / 2, sy, pw, ph)
        edge = QColor(col)
        edge.setAlpha(95)
        p.setPen(QPen(edge, 1))
        p.setBrush(QBrush(_ink(16)))
        p.drawRoundedRect(pr, ph / 2, ph / 2)
        dot = QColor(col)
        dot.setAlpha(255 if (self._blink or self.speaking or self.muted) else 90)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(dot))
        p.drawEllipse(QPointF(pr.left() + 17, pr.center().y()), 3.5, 3.5)
        p.setPen(QPen(col, 1))
        p.drawText(
            QRectF(pr.left() + 28, pr.top(), tw + 14, ph),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            txt,
        )

        # ── waveform: rounded bars mirrored around a centre line ─────────────
        wy = sy + 44
        N, bw_, gap = 36, 4.0, 3.0
        wx0 = (W - (N * (bw_ + gap) - gap)) / 2
        amp = self._amp_disp
        mid = (N - 1) / 2.0
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(N):
            if self.muted:
                hgt = 3.0
                cl = qcol(C.MUTED_C, 130)
            else:
                env = (1.0 - abs(i - mid) / mid) ** 0.7
                shimmer = 0.55 + 0.45 * math.sin(self._tick * 0.18 + i * 0.7)
                idle = 3.0 + 2.0 * math.sin(self._tick * 0.09 + i * 0.6)
                hgt = max(3.0, min(26.0, idle + amp * 24.0 * env * shimmer))
                if amp > 0.05:
                    cl = qcol(C.PRI) if hgt > 13 else qcol(C.PRI_DIM)
                else:
                    cl = qcol(C.BORDER_B, 210)
            p.setBrush(QBrush(cl))
            p.drawRoundedRect(
                QRectF(wx0 + i * (bw_ + gap), wy - hgt / 2, bw_, hgt), 2, 2
            )

        p.end()  # end deterministically so the backing store never flushes an active painter


class RingGauge(QWidget):
    """Round load gauge for the system monitor.

    A ring of ticks that light up as the value climbs (the newest ones
    brightest, so a rising load leaves a trail), a thick arc with a glowing
    head, a short scanner sweeping the outside, and the number in the middle.
    The displayed value eases toward the real one instead of jumping, so a
    reading that arrives every two seconds still moves smoothly on screen, and
    the scanner turns faster the harder the machine is working.

    Same set_value(pct, text) as the old tile; MainWindow ticks the animation
    from a single shared timer and only while the gauge can actually be seen."""

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._target = 0.0
        self._disp = 0.0
        self._text = "--"
        self._phase = random.uniform(0.0, 6.28)
        self._last = time.time()
        sp = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)
        self.setMinimumSize(64, 64)

    # keep the gauge a circle: its height follows its width
    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        return int(w)

    def sizeHint(self) -> QSize:
        return QSize(96, 96)

    def set_value(self, pct: float, text: str) -> None:
        self._target = max(0.0, min(100.0, float(pct)))
        self._text = text

    def tick(self) -> None:
        now = time.time()
        dt = min(0.1, max(0.0, now - self._last))
        self._last = now
        self._disp += (self._target - self._disp) * (1.0 - math.exp(-dt * 4.5))
        if abs(self._target - self._disp) < 0.02:
            self._disp = self._target
        self._phase += dt * (0.9 + 2.6 * self._disp / 100.0)
        self.update()

    def _accent(self) -> str:
        c0 = str(self._color)
        if c0.isalpha() and hasattr(C, c0):
            return getattr(C, c0)
        c = str(self._color).lower()
        for k, v in _PALETTE_DEFAULTS.items():
            if v.lower() == c:
                return getattr(C, k)
        return self._color

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        cx, cy = W / 2.0, H / 2.0
        R = max(10.0, min(W, H) / 2.0 - 3.0)
        v = self._disp
        if v > 85:
            col = qcol(C.RED)
        elif v > 65:
            col = qcol(C.ACC)
        else:
            col = qcol(self._accent())
        pulse = (0.5 + 0.5 * math.sin(self._phase * 3.0)) if v > 85 else 0.0

        def tint(c: QColor, a: float) -> QColor:
            c2 = QColor(c)
            c2.setAlpha(max(0, min(255, int(a))))
            return c2

        # glass disc
        g = QRadialGradient(cx, cy - R * 0.25, R)
        g.setColorAt(0.0, _ink(10))
        g.setColorAt(1.0, _ink(32))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(cx, cy), R, R)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_ink(30), 1))
        p.drawEllipse(QPointF(cx, cy), R - 0.5, R - 0.5)

        # tick ring — lit up to the value, brightest at the leading edge
        N = 48
        r0, r1 = R * 0.84, R * 0.93
        lit = v / 100.0 * N
        off = []
        for i in range(N):
            a = math.radians(-90.0 + i * 360.0 / N)
            ca, sa = math.cos(a), math.sin(a)
            ln = QLineF(cx + ca * r0, cy + sa * r0, cx + ca * r1, cy + sa * r1)
            if i < lit:
                f = (i + 1) / max(1.0, lit)
                p.setPen(QPen(tint(col, 55 + 200 * f * f), 1.6))
                p.drawLine(ln)
            else:
                off.append(ln)
        p.setPen(QPen(_ink(34), 1.1))
        _draw_lines(p, off)

        # arc with a glowing head
        ra = R * 0.68
        rect = QRectF(cx - ra, cy - ra, ra * 2, ra * 2)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_ink(30), 4))
        p.drawEllipse(rect)
        if v > 0.4:
            span = -int(v * 3.6 * 16)
            pen = QPen(tint(col, 46 + 50 * pulse), 9)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(rect, 90 * 16, span)
            pen = QPen(col, 4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(rect, 90 * 16, span)
            ang = math.radians(90.0 - v * 3.6)
            hx, hy = cx + ra * math.cos(ang), cy - ra * math.sin(ang)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(tint(col, 90 + 60 * pulse)))
            p.drawEllipse(QPointF(hx, hy), 6.0, 6.0)
            p.setBrush(QBrush(tint(QColor(C.WHITE), 245)))
            p.drawEllipse(QPointF(hx, hy), 2.4, 2.4)

        # scanner on the outside edge: a short comet turning clockwise
        srect = QRectF(cx - R + 1.5, cy - R + 1.5, (R - 1.5) * 2, (R - 1.5) * 2)
        deg = math.degrees(self._phase)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k in range(4):
            p.setPen(QPen(tint(col, 130 - k * 30), 1.8))
            p.drawArc(srect, int((-deg + k * 9.0) * 16), 9 * 16)

        # label + value in the middle; the number counts up with the arc
        r_in = ra - 5.0
        maxw = max(20.0, r_in * 1.55)
        lsz = 7
        while True:
            lf = QFont(_FONT, lsz, QFont.Weight.Bold)
            lf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.7)
            if QFontMetrics(lf).horizontalAdvance(self._label) <= maxw or lsz <= 5:
                break
            lsz -= 1
        p.setFont(lf)
        p.setPen(QPen(qcol(C.TEXT_MED), 1))
        p.drawText(
            QRectF(cx - maxw / 2, cy - r_in * 0.42 - 8, maxw, 16),
            Qt.AlignmentFlag.AlignCenter,
            self._label,
        )
        txt = self._text
        if txt.endswith("%"):
            txt = f"{self._disp:.0f}%"
        fs = 13
        while fs > 6:
            vf = QFont(_MONO, fs, QFont.Weight.Bold)
            if QFontMetrics(vf).horizontalAdvance(txt) <= maxw:
                break
            fs -= 1
        vf = QFont(_MONO, fs, QFont.Weight.Bold)
        p.setFont(vf)
        p.setPen(QPen(col if self._text not in ("--", "N/A") else qcol(C.TEXT_DIM), 1))
        p.drawText(
            QRectF(cx - maxw / 2, cy + r_in * 0.14 - 11, maxw, 22),
            Qt.AlignmentFlag.AlignCenter,
            txt,
        )
        p.end()


class _LoadGraph(QWidget):
    """Two minutes of CPU and memory load as a pair of filled lines."""

    _N = 60

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cpu = [0.0] * self._N
        self._mem = [0.0] * self._N
        self.setMinimumHeight(70)
        self.setMaximumHeight(110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def push(self, cpu: float, mem: float) -> None:
        self._cpu.append(max(0.0, min(100.0, cpu)))
        del self._cpu[0]
        self._mem.append(max(0.0, min(100.0, mem)))
        del self._mem[0]
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        card = QPainterPath()
        card.addRoundedRect(QRectF(0.5, 0.5, W - 1, H - 1), 12, 12)
        p.fillPath(card, _ink(12))
        p.setPen(QPen(_ink(26), 1))
        p.drawPath(card)
        p.save()
        p.setClipPath(card)
        for f in (0.25, 0.5, 0.75):
            y = 8 + (H - 16) * f
            p.setPen(QPen(_ink(14), 1))
            p.drawLine(QLineF(0, y, W, y))
        n = self._N
        step = W / float(n - 1)
        for series, col in ((self._mem, qcol(C.ACC2)), (self._cpu, qcol(C.PRI))):
            pts = [
                QPointF(i * step, H - 8 - (v / 100.0) * (H - 22))
                for i, v in enumerate(series)
            ]
            line = QPainterPath(pts[0])
            for pt in pts[1:]:
                line.lineTo(pt)
            area = QPainterPath(line)
            area.lineTo(W, H)
            area.lineTo(0, H)
            area.closeSubpath()
            g = QLinearGradient(0, 0, 0, H)
            top = QColor(col)
            top.setAlpha(70)
            bot = QColor(col)
            bot.setAlpha(0)
            g.setColorAt(0.0, top)
            g.setColorAt(1.0, bot)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawPath(area)
            lc = QColor(col)
            lc.setAlpha(210)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(lc, 1.4))
            p.drawPath(line)
        p.restore()
        f = QFont(_FONT, 7, QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
        p.setFont(f)
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QPointF(10, 14), "CPU")
        p.setPen(QPen(qcol(C.ACC2), 1))
        p.drawText(QPointF(38, 14), "MEM")
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 3, W - 8, 14), Qt.AlignmentFlag.AlignRight, "2 MIN")
        p.end()


class MetricBar(QWidget):
    """Glass tile: ring gauge + live value + sparkline of recent history.

    Same constructor and set_value(pct, text) as the old progress bar, so the
    rest of the app does not change."""

    _HIST = 48

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0
        self._text = "--"
        self._hist = [0.0] * self._HIST
        self.setMinimumHeight(46)
        self.setMaximumHeight(70)
        self.setMinimumWidth(100)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_value(self, pct: float, text: str):
        v = max(0.0, min(100.0, pct))
        self._hist.append(v)
        del self._hist[0]
        self._value = v
        self._text = text
        self.update()

    def _accent(self) -> str:
        """The colour was stored as a hex at build time; map it back to the live
        palette so a theme change recolours the tile too."""
        c = str(self._color).lower()
        for k, v in _PALETTE_DEFAULTS.items():
            if v.lower() == c:
                return getattr(C, k)
        return self._color

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        card = QPainterPath()
        card.addRoundedRect(QRectF(0.5, 0.5, W - 1, H - 1), 14, 14)
        g = QLinearGradient(0, 0, 0, H)
        g.setColorAt(0.0, _ink(24))
        g.setColorAt(1.0, _ink(8))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawPath(card)

        v = self._value
        if v > 85:
            col = qcol(C.RED)
        elif v > 65:
            col = qcol(C.ACC)
        else:
            col = qcol(self._accent())

        # ── sparkline, clipped to the card ───────────────────────────────────
        p.save()
        p.setClipPath(card)
        n = len(self._hist)
        step = W / float(n - 1)
        top, bot = H * 0.46, H - 5.0
        peak = max(25.0, max(self._hist))
        pts = [
            QPointF(i * step, bot - (val / peak) * (bot - top))
            for i, val in enumerate(self._hist)
        ]
        line = QPainterPath(pts[0])
        for pt in pts[1:]:
            line.lineTo(pt)
        area = QPainterPath(line)
        area.lineTo(W, H)
        area.lineTo(0, H)
        area.closeSubpath()
        ag = QLinearGradient(0, top, 0, H)
        c_top = QColor(col)
        c_top.setAlpha(80)
        c_bot = QColor(col)
        c_bot.setAlpha(0)
        ag.setColorAt(0.0, c_top)
        ag.setColorAt(1.0, c_bot)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(ag))
        p.drawPath(area)
        lc = QColor(col)
        lc.setAlpha(150)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(lc, 1.4))
        p.drawPath(line)
        p.restore()

        # ── ring gauge ───────────────────────────────────────────────────────
        rcx, rcy, rr = 34.0, H / 2.0, 16.0
        ring = QRectF(rcx - rr, rcy - rr, rr * 2, rr * 2)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_ink(34), 4))
        p.drawEllipse(ring)
        if v > 0.5:
            pen = QPen(col, 4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(ring, 90 * 16, -int(v * 3.6 * 16))
        p.setPen(Qt.PenStyle.NoPen)
        dc = QColor(col)
        dc.setAlpha(210)
        p.setBrush(QBrush(dc))
        p.drawEllipse(QPointF(rcx, rcy), 2.6, 2.6)

        # ── text ─────────────────────────────────────────────────────────────
        x0 = 62.0
        tw = max(10.0, W - x0 - 10.0)
        lf = QFont(_FONT, 8, QFont.Weight.Bold)
        lf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
        p.setFont(lf)
        p.setPen(QPen(qcol(C.TEXT_MED), 1))
        lbl = QFontMetrics(lf).elidedText(
            self._label, Qt.TextElideMode.ElideRight, int(tw)
        )
        p.drawText(
            QRectF(x0, H / 2 - 22, tw, 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            lbl,
        )

        vf = QFont(_MONO, 14, QFont.Weight.Bold)
        p.setFont(vf)
        p.setPen(QPen(col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        val = QFontMetrics(vf).elidedText(
            self._text, Qt.TextElideMode.ElideRight, int(tw)
        )
        p.drawText(
            QRectF(x0, H / 2 - 6, tw, 26),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            val,
        )
        p.end()


class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        # Cap scrollback so an hours-long session can't grow the document
        # without bound — keeps memory flat and every insert cheap. Oldest
        # lines drop off the top automatically.
        self.document().setMaximumBlockCount(600)
        self.setFont(QFont(_FONT, 10))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL2};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 10px;
                padding: 10px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 7px;
                border: none;
                margin: 5px 2px 5px 0;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 8px;
                min-height: 24px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)
        self._queue: list[str] = []
        self._typing = False
        self._text = ""
        self._pos = 0
        self._tag = "sys"
        self._ai_name_lc = "jarvis"  # updated when assistant name changes
        self._hist: list = []
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def clear_all(self) -> None:
        """Empty the log, including lines still waiting to be typed."""
        self._tmr.stop()
        self._queue.clear()
        self._typing = False
        self._text = ""
        self._pos = 0
        self._hist.clear()
        self.clear()

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text = self._queue.pop(0)
        self._pos = 0
        tl = self._text.lower()
        _ai_pfx = f"{self._ai_name_lc}:"
        if tl.startswith("you:"):
            self._tag = "you"
        elif tl.startswith(_ai_pfx) or tl.startswith("jarvis:"):
            self._tag = "ai"
        elif tl.startswith("file:"):
            self._tag = "file"
        elif "err" in tl:
            self._tag = "err"
        else:
            self._tag = "sys"
        self._hist.append([self._tag, self._text])
        del self._hist[:-600]
        self._tmr.start(6)

    def _col(self, tag):
        return {
            "you": qcol(C.WHITE),
            "ai": qcol(C.PRI),
            "err": qcol(C.RED),
            "file": qcol(C.GREEN),
            "sys": qcol(C.TEXT_MED),
        }.get(tag, qcol(C.TEXT))

    def recolor(self):
        """Re-paint the whole scrollback after a theme change."""
        from PyQt6.QtGui import QTextCharFormat

        self.clear()
        cur = self.textCursor()
        last = len(self._hist) - 1
        for i, (tag, text) in enumerate(self._hist):
            fmt = QTextCharFormat()
            fmt.setForeground(QBrush(self._col(tag)))
            if i == last and self._typing:
                cur.insertText(text[: self._pos], fmt)
            else:
                cur.insertText(text + "\n", fmt)
        self.setTextCursor(cur)
        self.ensureCursorVisible()

    def _step(self):
        if self._pos < len(self._text):
            # A few characters per tick, and a lot more when lines are queued
            # up, so the typing effect never makes the log lag behind events.
            n = 3 if not self._queue else 14
            ch = self._text[self._pos : self._pos + n]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = self._col(self._tag)
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += len(ch)
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)


_FILE_ICONS = {
    "image": ("🖼", "#00d4ff"),
    "video": ("🎬", "#ff6b00"),
    "audio": ("🎵", "#cc44ff"),
    "pdf": ("📄", "#ff4444"),
    "word": ("📝", "#4488ff"),
    "excel": ("📊", "#44bb44"),
    "code": ("💻", "#ffcc00"),
    "archive": ("📦", "#ff8844"),
    "pptx": ("📊", "#ff6622"),
    "text": ("📃", "#aaaaaa"),
    "data": ("🔧", "#88ddff"),
    "unknown": ("📎", "#888888"),
}

_EXT_TO_CAT = {
    **dict.fromkeys(
        ["jpg", "jpeg", "png", "gif", "webp", "bmp", "tiff", "svg", "ico"], "image"
    ),
    **dict.fromkeys(["mp4", "avi", "mov", "mkv", "wmv", "flv", "webm", "m4v"], "video"),
    **dict.fromkeys(
        ["mp3", "wav", "ogg", "m4a", "aac", "flac", "wma", "opus"], "audio"
    ),
    **dict.fromkeys(["pdf"], "pdf"),
    **dict.fromkeys(["doc", "docx"], "word"),
    **dict.fromkeys(["xls", "xlsx", "ods"], "excel"),
    **dict.fromkeys(["ppt", "pptx"], "pptx"),
    **dict.fromkeys(
        [
            "py",
            "js",
            "ts",
            "jsx",
            "tsx",
            "html",
            "css",
            "java",
            "c",
            "cpp",
            "cs",
            "go",
            "rs",
            "rb",
            "php",
            "swift",
            "kt",
            "sh",
            "sql",
            "lua",
        ],
        "code",
    ),
    **dict.fromkeys(["zip", "rar", "tar", "gz", "7z", "bz2", "xz"], "archive"),
    **dict.fromkeys(["txt", "md", "rst", "log"], "text"),
    **dict.fromkeys(["csv", "tsv", "json", "xml"], "data"),
}


def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")


def _fmt_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    elif size < 1024**2:
        return f"{size/1024:.1f} KB"
    elif size < 1024**3:
        return f"{size/1024**2:.1f} MB"
    else:
        return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        # The marching-ants dashed border is only meaningful while the user is
        # hovering or dragging a file over the zone. When idle, skip the repaint
        # entirely instead of redrawing the whole zone 25×/s forever — that idle
        # repaint held the GIL and stole time from the audio/response threads.
        if not (self._hovering or self._drag_over):
            return
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True
            self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False
        self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True
        self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False
        self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None
        self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select a file for JARVIS",
            str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z = self._z
        W, H = self.width(), self.height()
        pad = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = _ink(26 if z._drag_over else (10 if not z._hovering else 20))
        p.setBrush(QBrush(bg_col))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 10, 10)

        if z._current_file:
            border_col = qcol(C.GREEN, 220)
        elif z._drag_over:
            border_col = qcol(C.PRI, 235)
        elif z._hovering:
            border_col = qcol(C.PRI_DIM, 210)
        else:
            border_col = qcol(C.BORDER, 185)

        pen = QPen(border_col, 1.2, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 10, 10)

        if z._current_file:
            self._paint_file(p, W, H)
        elif z._drag_over:
            self._paint_drag_over(p, W, H)
        else:
            self._paint_idle(p, W, H, z._hovering)

        p.end()

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont(_FONT, 9))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(
            QRectF(0, cy + 8, W, 16),
            Qt.AlignmentFlag.AlignCenter,
            "Drop file here  or  Click to Browse",
        )
        p.setFont(QFont(_FONT, 8))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(
            QRectF(0, cy + 24, W, 14),
            Qt.AlignmentFlag.AlignCenter,
            "Images · Video · Audio · PDF · Docs · Code · Data",
        )

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont(_FONT, 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(QFont(_FONT, 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(
            QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load"
        )

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        p.setFont(
            QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22)
        )
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont(_FONT, 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(
            QRectF(tx, H * 0.18, tw, 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            name,
        )

        p.setFont(QFont(_FONT, 8))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(
            QRectF(tx, H * 0.18 + 18, tw, 14),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"{ext_str}  ·  {size_str}",
        )

        p.setFont(QFont(_FONT, 8))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        par = str(path.parent)
        if len(par) > 42:
            par = "…" + par[-41:]
        p.drawText(
            QRectF(tx, H * 0.18 + 34, tw, 12),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            par,
        )

        p.setFont(QFont(_FONT, 10, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class _CameraPreview(QWidget):
    """Floating overlay that briefly shows what the camera captured."""

    _W, _H = 244, 188

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            _CameraPreview {{
                background: rgba(0, 6, 10, 242);
                border: 1px solid {C.PRI};
                border-radius: 14px;
            }}
        """)
        self.setFixedWidth(self._W)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 6)
        lay.setSpacing(4)

        hdr = QHBoxLayout()
        title = QLabel("◈  VISUAL INPUT")
        title.setFont(QFont(_FONT, 8, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(16, 16)
        close_btn.setFont(QFont(_FONT, 9))
        close_btn.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; border: none;"
        )
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(self._img_lbl)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self.hide()

    def show_frame(self, img_bytes: bytes) -> None:
        px = QPixmap()
        px.loadFromData(img_bytes)
        if not px.isNull():
            max_w = self._W - 12
            scaled = px.scaled(
                max_w,
                160,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_lbl.setPixmap(scaled)
            self._img_lbl.setFixedSize(scaled.width(), scaled.height())
            self.adjustSize()
        self.show()
        self.raise_()
        self._timer.start(6_000)  # auto-dismiss after 6 s


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(_OS.lower(), "linux")
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(
            txt,
            font_size=9,
            bold=False,
            color=C.PRI,
            align=Qt.AlignmentFlag.AlignCenter,
        ):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(
                QFont(
                    _FONT,
                    _bump(font_size),
                    QFont.Weight.Bold if bold else QFont.Weight.Normal,
                )
            )
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈  INITIALISATION REQUIRED", 13, True))
        layout.addWidget(
            _lbl("Configure J.A.R.V.I.S. before first boot.", 9, color=C.PRI_DIM)
        )
        layout.addSpacing(6)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};")
        layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(
            _lbl(
                "GEMINI API KEY", 8, color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft
            )
        )
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(QFont(_FONT, 10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 8px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)
        layout.addSpacing(12)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};")
        layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(
            _lbl(
                "OPERATING SYSTEM",
                8,
                color=C.TEXT_DIM,
                align=Qt.AlignmentFlag.AlignLeft,
            )
        )
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(
            _lbl(
                f"Auto-detected: {det_name}",
                8,
                color=C.ACC2,
                align=Qt.AlignmentFlag.AlignLeft,
            )
        )

        os_row = QHBoxLayout()
        os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [
            ("windows", "⊞  Windows"),
            ("mac", "  macOS"),
            ("linux", "🐧  Linux"),
        ]:
            btn = QPushButton(label)
            btn.setFont(QFont(_FONT, 10, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = QPushButton("▸  INITIALISE SYSTEMS")
        init_btn.setFont(QFont(_FONT, 10, QFont.Weight.Bold))
        init_btn.setFixedHeight(36)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 8px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {
            "windows": (C.PRI, "#001a22"),
            "mac": (C.ACC2, "#1a1400"),
            "linux": (C.GREEN, "#001a0d"),
        }
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 8px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #000d12; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 8px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet()
                + f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        self.done.emit(key, self._sel_os)


class HueWheel(QWidget):
    """
    Circular colour picker. The user drags the handle (small white circle)
    around the wheel to choose from ALL hues. The filled circle in the centre
    is a live preview of the selected colour.
    """

    hue_picked = pyqtSignal(str)  # while dragging (live)
    hue_committed = pyqtSignal(str)  # when the handle is released

    _RING = 16  # ring thickness (px)

    def __init__(self, initial_hex: str = DEFAULT_UI_COLOR, parent=None):
        super().__init__(parent)
        self.setFixedSize(148, 148)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hue = 0.53
        self._drag = False
        self.set_color(initial_hex)

    # ── API ──────────────────────────────────────────────────────────────────
    def color(self) -> str:
        return QColor.fromHsvF(self._hue, 1.0, 1.0).name()

    def set_color(self, hex_str: str):
        c = QColor((hex_str or "").strip())
        if c.isValid() and c.hsvHueF() >= 0:
            self._hue = c.hsvHueF()
            self.update()

    # ── geometry helpers ─────────────────────────────────────────────────────
    def _ring_rect(self) -> QRectF:
        m = self._RING / 2 + 3
        return QRectF(self.rect()).adjusted(m, m, -m, -m)

    def _hue_from_pos(self, pos: QPointF) -> float:
        c = QRectF(self.rect()).center()
        dx = pos.x() - c.x()
        dy = c.y() - pos.y()  # screen y goes down — flip to math axis
        ang = math.atan2(dy, dx)  # [-π, π], counter-clockwise
        return (ang / (2 * math.pi)) % 1.0

    # ── drawing ──────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._ring_rect()
        center = rect.center()

        grad = QConicalGradient(center, 0)
        for i in range(0, 361, 20):
            grad.setColorAt(i / 360.0, QColor.fromHsvF((i % 360) / 360.0, 1.0, 1.0))
        p.setPen(QPen(QBrush(grad), self._RING))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)

        # centre preview circle
        preview = QColor.fromHsvF(self._hue, 1.0, 1.0)
        inner = rect.adjusted(30, 30, -30, -30)
        p.setPen(QPen(qcol(C.BORDER_B), 1))
        p.setBrush(QBrush(preview))
        p.drawEllipse(inner)

        # draggable handle
        r = rect.width() / 2
        ang = self._hue * 2 * math.pi
        hx = center.x() + r * math.cos(ang)
        hy = center.y() - r * math.sin(ang)
        p.setPen(QPen(QColor("#00060a"), 2))
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(QPointF(hx, hy), 7.5, 7.5)
        p.end()

    # ── fare ─────────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        self._drag = True
        self._hue = self._hue_from_pos(e.position())
        self.update()
        self.hue_picked.emit(self.color())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._hue = self._hue_from_pos(e.position())
            self.update()
            self.hue_picked.emit(self.color())

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.hue_committed.emit(self.color())


class CustomizeOverlay(QWidget):
    """Floating overlay — change assistant name, user name, UI colour and voice."""

    saved = pyqtSignal(str, str, str, str, str)  # name, user, ui_color, voice, theme
    _OW, _OH = 400, 588

    def __init__(
        self,
        assistant_name="JARVIS",
        user_name="",
        ui_color=DEFAULT_UI_COLOR,
        voice="",
        theme="dark",
        parent=None,
    ):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            CustomizeOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(8)

        def _lbl(
            txt, fs=9, bold=False, color=C.PRI, align=Qt.AlignmentFlag.AlignCenter
        ):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(
                QFont(
                    _FONT, _bump(fs), QFont.Weight.Bold if bold else QFont.Weight.Normal
                )
            )
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        _fs = (
            f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 8px; padding: 4px 8px; }}"
            f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}"
        )

        lay.addWidget(_lbl("⚙  CUSTOMISE ASSISTANT", 12, True))
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(
            _lbl(
                "ASSISTANT NAME", 8, color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft
            )
        )
        self._name_input = QLineEdit(assistant_name)
        self._name_input.setFont(QFont(_FONT, 10))
        self._name_input.setFixedHeight(32)
        self._name_input.setStyleSheet(_fs)
        lay.addWidget(self._name_input)

        lay.addSpacing(4)
        lay.addWidget(
            _lbl(
                "YOUR NAME  (leave blank for default sir / efendim)",
                8,
                color=C.TEXT_DIM,
                align=Qt.AlignmentFlag.AlignLeft,
            )
        )
        self._user_input = QLineEdit(user_name)
        self._user_input.setPlaceholderText("e.g.  Tony   (leave blank for auto)")
        self._user_input.setFont(QFont(_FONT, 10))
        self._user_input.setFixedHeight(32)
        self._user_input.setStyleSheet(_fs)
        lay.addWidget(self._user_input)

        # ── Assistant voice — Gemini prebuilt voices ─────────────────────────
        # Names are language-neutral proper nouns, so the row reads the same in
        # every locale. Selecting one and applying rebuilds the Live session.
        from memory.config_manager import AVAILABLE_VOICES, DEFAULT_VOICE

        lay.addSpacing(4)
        lay.addWidget(
            _lbl(
                "ASSISTANT VOICE", 8, color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft
            )
        )
        self._sel_voice = voice or DEFAULT_VOICE
        if self._sel_voice not in AVAILABLE_VOICES:
            self._sel_voice = DEFAULT_VOICE
        self._voice_btns: dict[str, QPushButton] = {}
        voice_row = QHBoxLayout()
        voice_row.setSpacing(4)
        for _v in AVAILABLE_VOICES:
            b = QPushButton(_v)
            b.setCheckable(True)
            b.setFixedHeight(28)
            b.setFont(QFont(_FONT, 9, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, name=_v: self._on_voice_pick(name))
            self._voice_btns[_v] = b
            voice_row.addWidget(b)
        lay.addLayout(voice_row)
        self._refresh_voice_btns()

        # ── Interface colour (one model; no separate theme modes) ────────────
        lay.addSpacing(4)
        lay.addWidget(
            _lbl(
                "INTERFACE COLOUR",
                8,
                color=C.TEXT_DIM,
                align=Qt.AlignmentFlag.AlignLeft,
            )
        )
        # Legacy theme values are deliberately ignored: colour is now the only
        # appearance setting.
        self._sel_theme = "custom"
        self._initial_theme = "custom"
        self._theme_btns: dict[str, QPushButton] = {}
        trow = QHBoxLayout()
        trow.setSpacing(6)
        for key, label in (
            ("dark", "◐  DARK"),
            ("light", "○  LIGHT"),
            ("custom", "◈  CUSTOM"),
        ):
            b = QPushButton(label)
            b.setFixedHeight(32)
            b.setFont(QFont(_FONT, 9, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self._on_theme_pick(k))
            self._theme_btns[key] = b
            trow.addWidget(b)
            b.hide()
        lay.addLayout(trow)

        self._initial_color = (ui_color or DEFAULT_UI_COLOR).strip().lower()
        self._sel_color = self._initial_color
        self.on_preview = None  # callable(hex)       — wheel / hex box
        self.on_theme_preview = None  # callable(mode, hex) — mode buttons

        # Full colour picker, with neutral presets above it.
        self._custom_box = QWidget()
        self._custom_box.setStyleSheet("background: transparent;")
        cb = QVBoxLayout(self._custom_box)
        cb.setContentsMargins(0, 0, 0, 0)
        cb.setSpacing(8)

        clr_hdr = QHBoxLayout()
        clr_hdr.addWidget(
            _lbl(
                "CUSTOM COLOUR  —  drag the handle",
                8,
                color=C.TEXT_DIM,
                align=Qt.AlignmentFlag.AlignLeft,
            )
        )
        clr_hdr.addStretch()
        df_btn = QPushButton("DEFAULT")
        df_btn.setFixedSize(64, 20)
        df_btn.setFont(QFont(_FONT, 8, QFont.Weight.Bold))
        df_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        df_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 8px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        df_btn.clicked.connect(lambda: self._set_color(DEFAULT_UI_COLOR))
        clr_hdr.addWidget(df_btn)
        cb.addLayout(clr_hdr)

        presets = QHBoxLayout()
        presets.setSpacing(6)
        for label, value in (("BLACK", "#000000"), ("WHITE", "#ffffff"), ("CYAN", DEFAULT_UI_COLOR)):
            b = QPushButton(label)
            b.setFixedHeight(26)
            b.setFont(QFont(_FONT, 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"QPushButton {{ background: transparent; color: {C.TEXT_MED}; border: 1px solid {C.BORDER}; border-radius: 7px; }} QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI}; }}")
            b.clicked.connect(lambda _=False, c=value: self._set_color(c))
            presets.addWidget(b)
        cb.addLayout(presets)

        self._wheel = HueWheel(self._sel_color)
        wheel_row = QHBoxLayout()
        wheel_row.addStretch()
        wheel_row.addWidget(self._wheel)
        wheel_row.addStretch()
        cb.addLayout(wheel_row)
        self._wheel.hue_picked.connect(self._on_wheel_pick)
        self._wheel.hue_committed.connect(self._on_wheel_commit)

        self._hex_input = QLineEdit(self._sel_color)
        self._hex_input.setPlaceholderText("#00d4ff   (custom hex colour)")
        self._hex_input.setFont(QFont(_FONT, 10))
        self._hex_input.setFixedHeight(28)
        self._hex_input.setStyleSheet(_fs)
        self._hex_input.textEdited.connect(self._on_hex_edited)
        cb.addWidget(self._hex_input)

        lay.addWidget(self._custom_box)
        self._custom_box.setVisible(True)

        lay.addSpacing(6)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        save_btn = QPushButton("▸  APPLY CHANGES")
        save_btn.setFixedHeight(34)
        save_btn.setFont(QFont(_FONT, 10, QFont.Weight.Bold))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 8px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setFixedHeight(34)
        cancel_btn.setFont(QFont(_FONT, 10))
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 8px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

    # ── voice selection ──────────────────────────────────────────────────────
    def _on_voice_pick(self, name: str):
        self._sel_voice = name
        self._refresh_voice_btns()

    def _refresh_voice_btns(self):
        """Highlight the selected voice pill; dim the rest."""
        for name, b in self._voice_btns.items():
            on = name == self._sel_voice
            b.setChecked(on)
            if on:
                b.setStyleSheet(f"""
                    QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI};
                        border: 1px solid {C.PRI}; border-radius: 8px; }}
                """)
            else:
                b.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_MED};
                        border: 1px solid {C.BORDER}; border-radius: 8px; }}
                    QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
                """)

    # ── colour flow ──────────────────────────────────────────────────────────
    def _set_color(self, hx: str, update_wheel: bool = True, preview: bool = True):
        """Updates the selected colour; hex box + wheel stay in sync, theme is live-previewed."""
        self._sel_color = hx.strip().lower()
        self._hex_input.blockSignals(True)
        self._hex_input.setText(self._sel_color)
        self._hex_input.blockSignals(False)
        if update_wheel:
            self._wheel.set_color(self._sel_color)
        if preview and self.on_preview:
            self.on_preview(self._sel_color)

    def _on_wheel_pick(self, hx: str):
        # While dragging: update the hex box, don't apply the theme yet
        self._sel_color = hx
        self._hex_input.blockSignals(True)
        self._hex_input.setText(hx)
        self._hex_input.blockSignals(False)

    def _on_wheel_commit(self, hx: str):
        # Handle released → live-preview the whole interface
        self._set_color(hx, update_wheel=False)

    def _on_hex_edited(self, text: str):
        t = text.strip().lower()
        if t.startswith("#") and len(t) == 7:
            try:
                int(t[1:], 16)
            except ValueError:
                return
            self._set_color(t, update_wheel=True, preview=True)

    # ── theme selection ──────────────────────────────────────────────────────
    def _on_theme_pick(self, key: str):
        self._sel_theme = key
        self._refresh_theme_btns()
        self._custom_box.setVisible(key == "custom")
        if self.on_theme_preview:
            self.on_theme_preview(key, self._sel_color)
        QTimer.singleShot(0, self._refit)

    def _refit(self):
        """Resize to the content and stay centred (the wheel comes and goes)."""
        self.adjustSize()
        p = self.parentWidget()
        if p is not None:
            self.move(
                max(0, (p.width() - self.width()) // 2),
                max(0, (p.height() - self.height()) // 2),
            )

    def _refresh_theme_btns(self):
        for k, b in self._theme_btns.items():
            if k == self._sel_theme:
                b.setStyleSheet(f"""
                    QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI};
                        border: 1px solid {C.PRI}; border-radius: 8px; }}
                """)
            else:
                b.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_MED};
                        border: 1px solid {C.BORDER}; border-radius: 8px; }}
                    QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
                """)

    def _cancel(self):
        # Whatever was previewed goes back to what the window opened with.
        if self.on_theme_preview and (
            self._sel_theme != self._initial_theme
            or self._sel_color != self._initial_color
        ):
            self.on_theme_preview(self._initial_theme, self._initial_color)
        self.hide()

    def _save(self):
        name = self._name_input.text().strip() or "JARVIS"
        user = self._user_input.text().strip()
        self.saved.emit(
            name,
            user,
            self._sel_color or DEFAULT_UI_COLOR,
            self._sel_voice,
            self._sel_theme,
        )
        self.hide()


class PluginManagerOverlay(QWidget):
    """Floating overlay — lists discovered plugins with per-plugin ON/OFF toggles."""

    _OW = 420

    def __init__(self, plugins: list[dict], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PluginManagerOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🧩  PLUGIN MANAGER")
        hdr.setFont(QFont(_FONT, 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        if not plugins:
            empty = QLabel("No plugins found in /plugins.")
            empty.setFont(QFont(_FONT, 9))
            empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(empty)

        for p in plugins:
            lay.addLayout(self._build_row(p))

        lay.addSpacing(4)
        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(30)
        close_btn.setFont(QFont(_FONT, 10))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 8px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        lay.addWidget(close_btn)
        self.adjustSize()

    def _build_row(self, p: dict) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)

        label_text = p["name"] if p["valid"] else f"{p['name']}  (⚠ {p['file']})"
        lbl = QLabel(label_text)
        lbl.setFont(QFont(_FONT, 9))
        lbl.setStyleSheet(
            f"color: {C.TEXT if p['valid'] else C.TEXT_DIM}; background: transparent;"
        )
        lbl.setToolTip(p["description"] if p["valid"] else p["error"])
        lbl.setWordWrap(False)
        row.addWidget(lbl, stretch=1)

        btn = QPushButton()
        btn.setFixedSize(72, 24)
        btn.setFont(QFont(_FONT, 8, QFont.Weight.Bold))
        if not p["valid"]:
            btn.setText("BROKEN")
            btn.setEnabled(False)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 8px;
                }}
            """)
        else:
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._style_toggle(btn, p["enabled"])
            btn.clicked.connect(lambda _, name=p["name"], b=btn: self._toggle(name, b))
        row.addWidget(btn)
        return row

    def _style_toggle(self, btn: QPushButton, enabled: bool):
        if enabled:
            btn.setText("ON")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 8px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            btn.setText("OFF")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 8px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
            """)

    def _toggle(self, name: str, btn: QPushButton):
        from memory.config_manager import get_plugin_enabled, save_plugin_enabled

        new_val = not get_plugin_enabled(name)
        save_plugin_enabled(name, new_val)
        self._style_toggle(btn, new_val)


class _HudOverlay(QWidget):
    """Base for the floating panels placed by hand over the HUD.

    They are children of the central widget but sit in no layout, so Qt never
    invalidates the region they occupy when they hide or shrink: the HUD keeps
    painting around them and their last frame stays on screen as a ghost. Any
    overlay positioned with _centre_overlay needs this."""

    def hideEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            # Repaint exactly what we were covering, before we stop covering it.
            p.update(self.geometry())
        super().hideEvent(e)

    def closeEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            p.update(self.geometry())
        super().closeEvent(e)


class ConfirmBanner(_HudOverlay):
    """The gate in front of an action that cannot be taken back.

    The old confirmation was a tool parameter the model filled in itself, which
    means it confirmed its own shutdown requests. This is the interface asking,
    and the answer travels from a human finger to core/confirm.py without the
    model in the loop. Nothing blocks while it is up: the assistant keeps
    talking, so this costs no latency — unlike the old gate, which spent two
    tool round trips on every power command."""

    answered = pyqtSignal(bool)
    _OW = 430

    def __init__(self, title: str, detail: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ConfirmBanner {{
                background: rgba(14, 3, 0, 250);
                border: 1px solid {C.ACC};
                border-radius: 14px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(8)

        hdr = QLabel("⚠  CONFIRM")
        hdr.setFont(QFont(_FONT, 11, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        lay.addWidget(hdr)

        ttl = QLabel(title)
        ttl.setWordWrap(True)
        ttl.setFont(QFont(_FONT, 10, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lay.addWidget(ttl)

        if detail:
            dtl = QLabel(detail)
            dtl.setWordWrap(True)
            dtl.setFont(QFont(_FONT, 9))
            dtl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            lay.addWidget(dtl)

        row = QHBoxLayout()
        row.setSpacing(8)

        yes = QPushButton("▸  CONFIRM")
        yes.setFixedHeight(32)
        yes.setFont(QFont(_FONT, 10, QFont.Weight.Bold))
        yes.setCursor(Qt.CursorShape.PointingHandCursor)
        yes.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.ACC};
                border: 1px solid {C.ACC}; border-radius: 8px; }}
            QPushButton:hover {{ background: rgba(255,107,0,40); }}
        """)
        yes.clicked.connect(lambda: self.answered.emit(True))
        row.addWidget(yes)

        no = QPushButton("CANCEL")
        no.setFixedHeight(32)
        no.setFont(QFont(_FONT, 10))
        no.setCursor(Qt.CursorShape.PointingHandCursor)
        no.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        no.clicked.connect(lambda: self.answered.emit(False))
        row.addWidget(no)
        lay.addLayout(row)

        # Default focus on CANCEL: if someone hits Enter without reading, the
        # safe answer wins.
        no.setDefault(True)
        no.setFocus()


class AudioDeviceOverlay(_HudOverlay):
    """Choose which microphone JARVIS listens to and which speakers it uses.

    Both audio streams used to open with no `device=` at all, so they always
    took the OS default — which on Windows moves by itself the moment a headset
    is plugged in. 'JARVIS can't hear me' is usually 'JARVIS is listening to the
    webcam'."""

    picked = pyqtSignal()  # emitted after Apply, when something changed
    _OW = 460

    def __init__(self, parent=None):
        super().__init__(parent)
        from core.audio_devices import list_devices, DEFAULT_LABEL
        from memory.config_manager import get_input_device, get_output_device

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            AudioDeviceOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🎧  AUDIO DEVICES")
        hdr.setFont(QFont(_FONT, 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        _combo_css = (
            f"QComboBox {{ background: #000d12; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 8px; padding: 4px 8px; }}"
            f"QComboBox:hover {{ border-color: {C.BORDER_B}; }}"
            f"QComboBox QAbstractItemView {{ background: #000d12; color: {C.TEXT}; "
            f"selection-background-color: {C.PRI_GHO}; border: 1px solid {C.BORDER}; }}"
        )

        def _row(label: str, kind: str, current: str) -> QComboBox:
            cap = QLabel(label)
            cap.setFont(QFont(_FONT, 9))
            cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(cap)

            box = QComboBox()
            box.setFont(QFont(_FONT, 10))
            box.setFixedHeight(30)
            box.setStyleSheet(_combo_css)
            # The list is served from a cache warmed on a background thread at
            # startup, so opening this panel never blocks the Qt thread on the
            # host audio API.
            box.addItem(DEFAULT_LABEL, "")
            for name in list_devices(kind):
                box.addItem(name, name)
            idx = box.findData(current) if current else 0
            box.setCurrentIndex(idx if idx >= 0 else 0)
            if current and idx < 0:
                # Saved device is not plugged in right now. Show it rather than
                # silently resetting the user's choice to default.
                box.addItem(f"{current}  (not connected)", current)
                box.setCurrentIndex(box.count() - 1)
            lay.addWidget(box)
            return box

        self._in_box = _row(
            "MICROPHONE — what JARVIS hears you with", "input", get_input_device()
        )
        lay.addSpacing(4)
        self._out_box = _row(
            "SPEAKERS — what JARVIS talks through", "output", get_output_device()
        )

        note = QLabel("Applying reconnects the session. Your conversation is kept.")
        note.setWordWrap(True)
        note.setFont(QFont(_FONT, 8))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addSpacing(6)
        lay.addWidget(note)

        row = QHBoxLayout()
        row.setSpacing(8)
        ok = QPushButton("▸  APPLY")
        ok.setFixedHeight(32)
        ok.setFont(QFont(_FONT, 10, QFont.Weight.Bold))
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 8px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """)
        ok.clicked.connect(self._apply)
        row.addWidget(ok)

        cancel = QPushButton("CLOSE")
        cancel.setFixedHeight(32)
        cancel.setFont(QFont(_FONT, 10))
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel.clicked.connect(self.hide)
        row.addWidget(cancel)
        lay.addLayout(row)

    def _apply(self):
        from memory.config_manager import (
            get_input_device,
            get_output_device,
            save_input_device,
            save_output_device,
        )

        new_in = self._in_box.currentData() or ""
        new_out = self._out_box.currentData() or ""
        changed = (new_in != get_input_device()) or (new_out != get_output_device())
        save_input_device(new_in)
        save_output_device(new_out)
        self.hide()
        # Only rebuild the session if something actually moved — a no-op Apply
        # should not cost a reconnect.
        if changed:
            self.picked.emit()


class MemoryOverlay(_HudOverlay):
    """Everything JARVIS has stored about you, and when it learned it.

    Memory used to be a 2200-character store that deleted its oldest entries
    when full and mentioned it only on stdout. The cap is gone; this panel is
    the other half of that change — a memory you cannot inspect is a memory you
    cannot trust, and 'delete' has to be something the person can do."""

    _OW = 520

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            MemoryOverlay {{
                background: rgba(0, 6, 10, 246);
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        self.setFixedWidth(self._OW)

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(20, 16, 20, 16)
        self._lay.setSpacing(5)
        self._rebuild()

    def _clear_layout(self):
        """Take every item out of the layout and detach it from the widget tree
        in this call.

        deleteLater() on its own is not enough: it queues destruction for the
        next event-loop pass, and until then the old rows are still children of
        this widget and still paint — which is what drew half of the previous
        panel over the new one. setParent(None) removes them from the tree now;
        deleteLater() then frees them safely."""
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                # hide() stops it painting in this frame; deleteLater() frees it
                # safely afterwards. setParent(None) would also stop the paint,
                # but it turns the widget into a top-level window for the moment
                # between the two calls, which is not something to leave lying
                # around inside a click handler.
                w.hide()
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                while sub.count():
                    si = sub.takeAt(0)
                    sw = si.widget()
                    if sw is not None:
                        sw.hide()
                        sw.deleteLater()
                sub.deleteLater()

    def _settle(self, before):
        """Size the panel to its content, re-centre it, and repaint what the old
        size covered.

        The re-size has to happen here rather than at the end of _rebuild
        because Qt has not polished the freshly-created children at that point,
        so the size hint it would read is the empty-layout one. Measured: a
        first adjustSize() returned 32 px for a panel whose content needed 155,
        and a second call — after the same widgets had been through the event
        loop — returned 155. So this runs twice: once now, once on the next
        turn, from _rebuild.

        The re-centre and the repaint are needed because the overlay is placed
        by hand and is in no layout: shrinking it leaves it off-centre and
        leaves its former pixels on screen, since nothing tells the parent that
        region changed. The repaint has to cover the union of the old and new
        rectangles."""
        self._lay.invalidate()
        self._lay.activate()
        self.updateGeometry()
        self.adjustSize()

        p = self.parentWidget()
        if p is None:
            self.update()
            return
        self.move(
            max(0, (p.width() - self.width()) // 2),
            max(0, (p.height() - self.height()) // 2),
        )
        p.update(before.united(self.geometry()))
        self.update()

    def _rebuild(self):
        before = self.geometry()
        self._clear_layout()

        from memory.memory_manager import all_entries_for_ui

        hdr = QLabel("🧠  WHAT JARVIS REMEMBERS")
        hdr.setFont(QFont(_FONT, 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._lay.addWidget(hdr)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        self._lay.addWidget(sep)

        rows = all_entries_for_ui()

        cap = QLabel(
            f"{len(rows)} stored facts — newest first. "
            f"Nothing here is sent anywhere; it lives in "
            f"memory/long_term.json on this machine."
        )
        cap.setWordWrap(True)
        cap.setFont(QFont(_FONT, 8))
        cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._lay.addWidget(cap)

        if not rows:
            empty = QLabel("Nothing stored yet.")
            empty.setFont(QFont(_FONT, 10))
            empty.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            self._lay.addWidget(empty)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFixedHeight(min(420, 34 * len(rows) + 10))
            scroll.setStyleSheet(
                f"QScrollArea {{ border: 1px solid {C.BORDER}; border-radius: 8px; "
                f"background: transparent; }}"
            )
            inner = QWidget()
            ilay = QVBoxLayout(inner)
            ilay.setContentsMargins(6, 6, 6, 6)
            ilay.setSpacing(3)

            for r in rows:
                line = QHBoxLayout()
                line.setSpacing(6)
                txt = QLabel(
                    f"<b>{r['key'].replace('_', ' ')}</b> "
                    f"<span style='color:{C.TEXT_MED}'>— {r['value']}</span>"
                )
                txt.setWordWrap(True)
                txt.setFont(QFont(_FONT, 9))
                txt.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
                line.addWidget(txt, 1)

                meta = QLabel(f"{r['category'][:4]} · {r['updated'] or '—'}")
                meta.setFont(QFont(_FONT, 8))
                meta.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
                line.addWidget(meta)

                rm = QPushButton("✕")
                rm.setFixedSize(20, 20)
                rm.setFont(QFont(_FONT, 9, QFont.Weight.Bold))
                rm.setCursor(Qt.CursorShape.PointingHandCursor)
                rm.setToolTip("Forget this")
                rm.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 8px; }}
                    QPushButton:hover {{ color: {C.RED}; border-color: {C.RED}; }}
                """)
                rm.clicked.connect(
                    lambda _=False, c=r["category"], k=r["key"]: self._forget(c, k)
                )
                line.addWidget(rm)

                holder = QWidget()
                holder.setLayout(line)
                ilay.addWidget(holder)

            ilay.addStretch()
            scroll.setWidget(inner)
            self._lay.addWidget(scroll)

        close = QPushButton("CLOSE")
        close.setFixedHeight(30)
        close.setFont(QFont(_FONT, 10))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close.clicked.connect(self.hide)
        self._lay.addWidget(close)

        self._settle(before)
        # …and again once Qt has polished the new children, because the size
        # hint is not final until then. Harmless when the first pass already
        # got it right: _settle is idempotent.
        QTimer.singleShot(0, lambda g=before: self._settle(g))

    def _forget(self, category: str, key: str):
        from memory.memory_manager import forget

        forget(key, category)
        # Rebuild on the NEXT event-loop turn, not inside this click handler.
        # The rebuild destroys the very ✕ button that emitted this signal, and
        # Qt is entitled to touch the sender after a slot returns; tearing it
        # down mid-emission is how a widget ends up half-alive on screen.
        QTimer.singleShot(0, self._rebuild)


class ClipboardPanel(QWidget):
    """Floating panel shown when text is copied — offers quick Jarvis actions."""

    action_requested = pyqtSignal(str)
    _W, _H = 326, 112

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ClipboardPanel {{
                background: rgba(0, 8, 14, 248);
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        self.setFixedWidth(self._W)
        self._clip_text = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 7)
        lay.setSpacing(4)

        hdr = QHBoxLayout()
        hdr.setSpacing(4)
        icon_lbl = QLabel("◈  CLIPBOARD DETECTED")
        icon_lbl.setFont(QFont(_FONT, 8, QFont.Weight.Bold))
        icon_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        hdr.addWidget(icon_lbl)
        hdr.addStretch()
        x_btn = QPushButton("✕")
        x_btn.setFixedSize(16, 16)
        x_btn.setFont(QFont(_FONT, 9))
        x_btn.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; border: none;"
        )
        x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        x_btn.clicked.connect(self.hide)
        hdr.addWidget(x_btn)
        lay.addLayout(hdr)

        self._preview = QLabel()
        self._preview.setFont(QFont(_FONT, 9))
        self._preview.setStyleSheet(f"""
            color: {C.TEXT}; background: {C.PANEL2};
            border: 1px solid {C.BORDER}; border-radius: 8px; padding: 4px 6px;
        """)
        self._preview.setWordWrap(False)
        self._preview.setFixedHeight(28)
        lay.addWidget(self._preview)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        _bs = (
            f"QPushButton {{ background: {C.PANEL2}; color: {C.TEXT_MED}; "
            f"border: 1px solid {C.BORDER}; border-radius: 6px; }}"
            f"QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}"
        )
        for label, cmd_fmt in [
            ("TRANSLATE", "Translate this text to English: {text}"),
            ("SUMMARISE", "Summarise this: {text}"),
            ("EXPLAIN", "Explain this: {text}"),
            ("FIX", "Fix grammar and spelling: {text}"),
        ]:
            b = QPushButton(label)
            b.setFixedHeight(22)
            b.setFont(QFont(_FONT, 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(_bs)
            b.clicked.connect(lambda _, c=cmd_fmt: self._trigger(c))
            btn_row.addWidget(b)
        lay.addLayout(btn_row)

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.hide)
        self.hide()

    def _trigger(self, cmd_fmt: str):
        if self._clip_text:
            self.action_requested.emit(cmd_fmt.format(text=self._clip_text[:800]))
        self.hide()

    def show_clipboard(self, text: str):
        self._clip_text = text
        preview = text[:58].replace("\n", " ")
        if len(text) > 58:
            preview += "…"
        self._preview.setText(f'"{preview}"')
        self.show()
        self.raise_()
        self._dismiss_timer.start(8000)


class PluginSettingsOverlay(QWidget):
    """Floating overlay — renders per-plugin settings forms.

    Fully generic: it iterates the settings schemas a plugin declared via its
    PLUGIN_SETTINGS constant (delivered by PluginRegistry.settings_schemas) and
    builds a form for each. It knows NOTHING about any specific plugin, so the
    core stays clean and plugins remain pure drop-in — install a plugin that
    declares fields (e.g. the 3D-printer suite) and its section appears here;
    install none and this panel simply says there's nothing to configure.
    """

    _test_done = pyqtSignal(str, bool, str)  # namespace, ok, message
    _OW = 460

    def __init__(self, sections: list[dict], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PluginSettingsOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        self._sections = sections or []
        self._widgets: dict[tuple, object] = {}  # (namespace, key) -> input widget
        self._types: dict[tuple, str] = {}  # (namespace, key) -> field type
        self._status_labels: dict[str, QLabel] = {}  # namespace -> status QLabel
        self._test_done.connect(self._on_test_done)

        self._fs = (
            f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 8px; padding: 4px 8px; }}"
            f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 16)
        root.setSpacing(8)

        root.addWidget(self._lbl("⚙  PLUGIN SETTINGS", 12, True))
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        root.addWidget(sep)

        if not self._sections:
            root.addWidget(
                self._lbl(
                    "No configurable plugins are installed.\nDrop a plugin that needs "
                    "settings (like the 3D-printer suite) into the plugins folder and "
                    "it will show up here.",
                    9,
                    color=C.TEXT_DIM,
                )
            )
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setStyleSheet("QScrollArea { background: transparent; }")
            inner = QWidget()
            inner.setStyleSheet("background: transparent;")
            form = QVBoxLayout(inner)
            form.setContentsMargins(0, 0, 6, 0)
            form.setSpacing(6)
            for sec in self._sections:
                self._build_section(form, sec)
            form.addStretch(1)
            scroll.setWidget(inner)
            root.addWidget(scroll, 1)

        # ── bottom buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        if self._sections:
            save_btn = QPushButton("▸  SAVE")
            save_btn.setFixedHeight(34)
            save_btn.setFont(QFont(_FONT, 10, QFont.Weight.Bold))
            save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            save_btn.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {C.PRI};
                    border: 1px solid {C.PRI_DIM}; border-radius: 8px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
            """)
            save_btn.clicked.connect(self._save_all)
            btn_row.addWidget(save_btn)

        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(34)
        close_btn.setFont(QFont(_FONT, 10))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── helpers ───────────────────────────────────────────────────────────────
    def _lbl(
        self, txt, fs=9, bold=False, color=C.PRI, align=Qt.AlignmentFlag.AlignLeft
    ):
        w = QLabel(txt)
        w.setAlignment(align)
        w.setWordWrap(True)
        w.setFont(
            QFont(_FONT, _bump(fs), QFont.Weight.Bold if bold else QFont.Weight.Normal)
        )
        w.setStyleSheet(f"color: {color}; background: transparent;")
        return w

    def _build_section(self, form: QVBoxLayout, sec: dict):
        ns = sec.get("namespace") or sec.get("plugin") or "plugin"
        title = sec.get("title") or ns
        fields = sec.get("fields") or []
        values = sec.get("values") or {}

        form.addSpacing(4)
        form.addWidget(self._lbl(title, 10, True, C.PRI))

        for field in fields:
            if not isinstance(field, dict) or not field.get("key"):
                continue
            key = field["key"]
            ftype = (field.get("type") or "text").lower()
            label = field.get("label") or key
            default = field.get("default")
            stored = values.get(key, default)

            form.addWidget(self._lbl(label.upper(), 8, color=C.TEXT_DIM))

            if ftype == "choice":
                w = QComboBox()
                w.addItems([str(o) for o in field.get("options", [])])
                w.setFont(QFont(_FONT, 10))
                w.setFixedHeight(30)
                w.setStyleSheet(
                    f"QComboBox {{ background: #000d12; color: {C.TEXT}; "
                    f"border: 1px solid {C.BORDER}; border-radius: 8px; padding: 2px 8px; }}"
                    f"QComboBox QAbstractItemView {{ background: #000d12; color: {C.TEXT}; "
                    f"selection-background-color: {C.PRI_GHO}; }}"
                )
                if stored is not None:
                    w.setCurrentText(str(stored))
            elif ftype == "toggle":
                w = QPushButton()
                w.setCheckable(True)
                w.setChecked(bool(stored))
                w.setFixedHeight(28)
                w.setFont(QFont(_FONT, 9, QFont.Weight.Bold))
                w.setCursor(Qt.CursorShape.PointingHandCursor)
                self._style_toggle(w)
                w.toggled.connect(lambda _=False, b=w: self._style_toggle(b))
            else:  # text / password
                w = QLineEdit("" if stored is None else str(stored))
                w.setFont(QFont(_FONT, 10))
                w.setFixedHeight(30)
                w.setStyleSheet(self._fs)
                if field.get("placeholder"):
                    w.setPlaceholderText(str(field["placeholder"]))
                if ftype == "password":
                    w.setEchoMode(QLineEdit.EchoMode.Password)

            self._widgets[(ns, key)] = w
            self._types[(ns, key)] = ftype
            form.addWidget(w)

        # optional test/connect action button + status line
        action = sec.get("action")
        if isinstance(action, dict) and callable(action.get("run")):
            form.addSpacing(2)
            ab = QPushButton(str(action.get("label") or "TEST"))
            ab.setFixedHeight(30)
            ab.setFont(QFont(_FONT, 9, QFont.Weight.Bold))
            ab.setCursor(Qt.CursorShape.PointingHandCursor)
            ab.setStyleSheet(f"""
                QPushButton {{ background: #00091a; color: {C.PRI};
                    border: 1px solid {C.PRI_DIM}; border-radius: 8px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
            """)
            ab.clicked.connect(lambda _=False, n=ns: self._run_action(n))
            form.addWidget(ab)

        status = self._lbl("", 8, color=C.TEXT_DIM)
        self._status_labels[ns] = status
        form.addWidget(status)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"color: {C.BORDER}; margin: 4px 0;")
        form.addWidget(line)

    def _style_toggle(self, btn: QPushButton):
        on = btn.isChecked()
        btn.setText("ON" if on else "OFF")
        if on:
            btn.setStyleSheet(
                f"QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI}; "
                f"border: 1px solid {C.PRI}; border-radius: 8px; }}"
            )
        else:
            btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {C.TEXT_MED}; "
                f"border: 1px solid {C.BORDER}; border-radius: 8px; }}"
            )

    # ── data ──────────────────────────────────────────────────────────────────
    def _gather(self, ns: str) -> dict:
        out = {}
        for (n, key), w in self._widgets.items():
            if n != ns:
                continue
            t = self._types.get((n, key), "text")
            if t == "choice":
                out[key] = w.currentText()
            elif t == "toggle":
                out[key] = w.isChecked()
            else:
                out[key] = w.text().strip()
        return out

    def _save_ns(self, ns: str):
        from memory.config_manager import save_plugin_config

        save_plugin_config(ns, self._gather(ns))

    def _save_all(self):
        for sec in self._sections:
            ns = sec.get("namespace") or sec.get("plugin")
            if ns:
                self._save_ns(ns)
                lbl = self._status_labels.get(ns)
                if lbl:
                    lbl.setText("Saved ✓")
                    lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")

    def _run_action(self, ns: str):
        sec = next(
            (
                s
                for s in self._sections
                if (s.get("namespace") or s.get("plugin")) == ns
            ),
            None,
        )
        if not sec:
            return
        run_fn = (sec.get("action") or {}).get("run")
        if not callable(run_fn):
            return
        self._save_ns(ns)  # persist what the user typed before testing
        values = self._gather(ns)
        lbl = self._status_labels.get(ns)
        if lbl:
            lbl.setText("Testing…")
            lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")

        def worker():
            try:
                res = run_fn(values)
                if isinstance(res, tuple) and len(res) == 2:
                    ok, msg = bool(res[0]), str(res[1])
                else:
                    ok, msg = bool(res), str(res)
            except Exception as e:
                ok, msg = False, str(e)
            self._test_done.emit(ns, ok, msg)

        threading.Thread(target=worker, daemon=True).start()

    def _on_test_done(self, ns: str, ok: bool, msg: str):
        lbl = self._status_labels.get(ns)
        if not lbl:
            return
        lbl.setText(msg)
        color = C.PRI if ok else "#ff6b6b"
        lbl.setStyleSheet(f"color: {color}; background: transparent;")


class RemoteKeyOverlay(QWidget):
    """Floating overlay — QR code for instant phone pairing + manual key fallback."""

    closed = pyqtSignal()

    _OW, _OH = 400, 465

    def __init__(
        self,
        url: str,
        key: str,
        auto_login_url: str = "",
        manual_url: str = "",
        expiry_secs: int = 600,
        parent=None,
    ):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            RemoteKeyOverlay {{
                background: rgba(0, 4, 12, 0.95);
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        self._expiry = time.time() + expiry_secs
        self._on_new_key = None
        self._auto_login_url = auto_login_url
        self._manual_url = manual_url or url

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        lay.setSpacing(5)

        def _lbl(
            txt, fs=9, bold=False, color=C.PRI, align=Qt.AlignmentFlag.AlignCenter
        ):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(
                QFont(
                    _FONT, _bump(fs), QFont.Weight.Bold if bold else QFont.Weight.Normal
                )
            )
            w.setStyleSheet(f"color: {color}; background: transparent;")
            w.setWordWrap(True)
            return w

        lay.addWidget(_lbl("◈  REMOTE ACCESS", 12, True))
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep)

        # ── QR code ───────────────────────────────────────────────────────────
        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setFixedSize(176, 176)
        self._qr_label.setStyleSheet(
            "background: white; border-radius: 10px; padding: 4px;"
        )
        qr_row = QHBoxLayout()
        qr_row.addStretch()
        qr_row.addWidget(self._qr_label)
        qr_row.addStretch()
        lay.addLayout(qr_row)

        self._update_qr(auto_login_url)

        lay.addWidget(
            _lbl("Scan with phone camera to connect instantly", 8, color=C.TEXT_DIM)
        )

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep2)

        lay.addWidget(
            _lbl(
                "Or enter manually:",
                7,
                color=C.TEXT_DIM,
                align=Qt.AlignmentFlag.AlignLeft,
            )
        )

        self._url_lbl = QLabel(self._manual_url)
        self._url_lbl.setFont(QFont(_FONT, 9))
        self._url_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        self._url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        lay.addWidget(self._url_lbl)

        self._key_lbl = QLabel(key)
        self._key_lbl.setFont(QFont(_FONT, 28, QFont.Weight.Bold))
        self._key_lbl.setStyleSheet(f"""
            color: {C.ACC};
            background: {C.PANEL2};
            border: 1px solid {C.BORDER_B};
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 10px;
        """)
        self._key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._key_lbl)

        self._timer_lbl = QLabel()
        self._timer_lbl.setFont(QFont(_FONT, 9))
        self._timer_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._timer_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        new_btn = QPushButton("NEW KEY")
        new_btn.setFixedHeight(32)
        new_btn.setFont(QFont(_FONT, 9, QFont.Weight.Bold))
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 5px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        new_btn.clicked.connect(self._refresh_key)
        btn_row.addWidget(new_btn)

        close_btn = QPushButton("DISMISS")
        close_btn.setFixedHeight(32)
        close_btn.setFont(QFont(_FONT, 9, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self._do_close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._ctimer = QTimer(self)
        self._ctimer.timeout.connect(self._tick)
        self._ctimer.start(1000)
        self._tick()

    def set_new_key_callback(self, fn) -> None:
        self._on_new_key = fn

    def _update_qr(self, url: str) -> None:
        if not url:
            self._qr_label.setText("—")
            return
        try:
            import qrcode as _qrmod
            from io import BytesIO

            qr = _qrmod.QRCode(
                box_size=5,
                border=2,
                error_correction=_qrmod.constants.ERROR_CORRECT_M,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap()
            px.loadFromData(buf.getvalue())
            self._qr_label.setPixmap(
                px.scaled(
                    170,
                    170,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        except ImportError:
            self._qr_label.setText("pip install\nqrcode[pil]")
            self._qr_label.setFont(QFont(_FONT, 9))
            self._qr_label.setStyleSheet(
                "color: #888; background: white; border-radius: 10px; padding: 4px;"
            )
        except Exception:
            self._qr_label.setText(url[:28])
            self._qr_label.setFont(QFont(_FONT, 8))
            self._qr_label.setStyleSheet(
                f"color: {C.PRI}; background: white; border-radius: 10px; padding: 4px;"
            )

    def _tick(self):
        remaining = max(0, int(self._expiry - time.time()))
        m, s = divmod(remaining, 60)
        self._timer_lbl.setText(f"Key expires in  {m:02d}:{s:02d}")
        if remaining == 0:
            self._do_close()

    def mark_connected(self) -> None:
        """Call from any thread when a phone successfully connects."""
        self._ctimer.stop()
        self._key_lbl.setText("CONNECTED")
        self._key_lbl.setStyleSheet(f"""
            color: {C.GREEN};
            background: rgba(34,197,94,0.08);
            border: 2px solid rgba(34,197,94,0.4);
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 4px;
        """)
        self._qr_label.setText("✓")
        self._qr_label.setFont(QFont(_FONT, 54, QFont.Weight.Bold))
        self._qr_label.setStyleSheet(
            "color: #00ff88; background: #001a0d; border-radius: 10px;"
        )
        self._timer_lbl.setText("Phone connected — JARVIS ready")
        self._timer_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")

    def _refresh_key(self):
        if self._on_new_key:
            result = self._on_new_key()
            if result:
                url = result[0]
                key = result[1]
                auto = result[2] if len(result) >= 3 else ""
                manual = result[3] if len(result) >= 4 else url
                self._manual_url = manual or url
                self._url_lbl.setText(self._manual_url)
                self._key_lbl.setText(key)
                self._auto_login_url = auto
                self._update_qr(auto or url)
                self._expiry = time.time() + 600
                self._key_lbl.setStyleSheet(f"""
                    color: {C.ACC};
                    background: {C.PANEL2};
                    border: 1px solid {C.BORDER_B};
                    border-radius: 8px;
                    padding: 6px 4px;
                    letter-spacing: 10px;
                """)
                self._timer_lbl.setStyleSheet(
                    f"color: {C.TEXT_MED}; background: transparent;"
                )
                self._ctimer.start(1000)
                self._tick()

    def _do_close(self):
        self._ctimer.stop()
        self.hide()
        self.closed.emit()


class _Toast(_HudOverlay):
    """A small non-blocking notification in the top-right corner."""

    closed = pyqtSignal(object)
    _W = 300
    _COL = {"info": "PRI", "ok": "GREEN", "warn": "ACC2", "err": "RED"}

    def __init__(self, title: str, text: str, level: str, ms: int, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        col = getattr(C, self._COL.get(level, "PRI"))
        self.setStyleSheet(f"""
            _Toast {{
                background: rgba(4, 14, 22, 246);
                border: 1px solid rgba(255, 255, 255, 34);
                border-left: 3px solid {col};
                border-radius: 10px;
            }}
        """)
        self.setFixedWidth(self._W)
        self._done = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 10)
        lay.setSpacing(2)
        t = QLabel(title)
        t.setFont(QFont(_DISPLAY, 9, QFont.Weight.Bold))
        t.setStyleSheet(f"color: {col}; background: transparent;")
        lay.addWidget(t)
        if text:
            b = QLabel(text)
            b.setWordWrap(True)
            b.setFont(QFont(_FONT, 9))
            b.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            lay.addWidget(b)
        self.adjustSize()
        # Owned by the toast, so it dies with it — no callback on a deleted widget.
        self._tm = QTimer(self)
        self._tm.setSingleShot(True)
        self._tm.timeout.connect(self.dismiss)
        self._tm.start(max(800, int(ms)))

    def dismiss(self):
        if self._done:
            return
        self._done = True
        self.hide()
        self.closed.emit(self)
        self.deleteLater()

    def mousePressEvent(self, e):
        self.dismiss()


class _CmdInput(QLineEdit):
    """Command line with shell-style history: Up / Down walk earlier messages."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hist: list[str] = []
        self._pos = 0
        self._draft = ""

    def remember(self, text: str) -> None:
        text = text.strip()
        if text and (not self._hist or self._hist[-1] != text):
            self._hist.append(text)
            del self._hist[:-100]
        self._pos = len(self._hist)

    def keyPressEvent(self, e):
        k = e.key()

        if k == Qt.Key.Key_Up and self._hist:
            if self._pos == len(self._hist):
                self._draft = self.text()

            self._pos = max(0, self._pos - 1)
            self.setText(self._hist[self._pos])
            return

        if k == Qt.Key.Key_Down and self._hist:
            self._pos = min(len(self._hist), self._pos + 1)
            self.setText(
                self._draft if self._pos == len(self._hist) else self._hist[self._pos]
            )
            return

        super().keyPressEvent(e)


class CommandPalette(_HudOverlay):
    """Ctrl+K — type a few letters, press Enter.

    Everything JARVIS can do from the interface is listed here, so no feature
    depends on finding its button, and none of them needs a mouse."""

    closed = pyqtSignal()
    _OW = 540

    def __init__(self, actions, parent=None, initial: str = ""):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            CommandPalette {{
                background: rgba(4, 14, 22, 248);
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        self.setFixedWidth(self._OW)
        self._actions = list(actions)
        self._shown: list = []
        self._open = True

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 10)
        lay.setSpacing(8)

        self._edit = QLineEdit()
        self._edit.setPlaceholderText("Type a command…")
        self._edit.setFont(QFont(_FONT, 11))
        self._edit.setFixedHeight(36)
        self._edit.setStyleSheet(f"""
            QLineEdit {{ background: rgba(255, 255, 255, 14); color: {C.WHITE};
                border: 1px solid {C.BORDER_B}; border-radius: 10px; padding: 0 12px; }}
            QLineEdit:focus {{ border-color: {C.PRI}; }}
        """)
        self._edit.textChanged.connect(self._filter)
        self._edit.installEventFilter(self)
        lay.addWidget(self._edit)

        self._list = QListWidget()
        self._list.setFrameShape(QFrame.Shape.NoFrame)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setStyleSheet(f"""
            QListWidget {{ background: transparent; border: none; outline: none; }}
            QListWidget::item {{ border-radius: 8px; padding: 0px; }}
            QListWidget::item:selected {{ background: {C.PRI_GHO}; }}
            QListWidget::item:hover {{ background: rgba(255, 255, 255, 14); }}
        """)
        self._list.itemClicked.connect(lambda it: self._run(self._list.row(it)))
        lay.addWidget(self._list)

        foot = QLabel("↑ ↓  navigate      ⏎  run      Esc  close")
        foot.setFont(QFont(_FONT, 8))
        foot.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addWidget(foot)

        self._edit.setText(initial)
        self._filter(initial)

    def focus_edit(self) -> None:
        self._edit.setFocus()

    def relayout(self) -> None:
        self.adjustSize()
        p = self.parentWidget()
        if p is not None:
            self.move(max(0, (p.width() - self.width()) // 2), 60)

    def _filter(self, text: str) -> None:
        words = text.lower().split()
        self._shown = [
            a
            for a in self._actions
            if all(w in (a[0] + " " + a[1]).lower() for w in words)
        ]
        self._list.clear()
        if not self._shown:
            it = QListWidgetItem("   No matching command")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            it.setSizeHint(QSize(0, 30))
            it.setForeground(QBrush(QColor(C.TEXT_DIM)))
            self._list.addItem(it)
        for title, hint, _fn in self._shown:
            it = QListWidgetItem()
            it.setSizeHint(QSize(0, 30))
            self._list.addItem(it)
            lbl = QLabel(
                f"<table width='100%'><tr>"
                f"<td style='color:{C.TEXT}'>{_html.escape(title)}</td>"
                f"<td align='right' style='color:{C.TEXT_DIM}'>{_html.escape(hint)}</td>"
                f"</tr></table>"
            )
            lbl.setFont(QFont(_FONT, 10))
            lbl.setContentsMargins(10, 0, 10, 0)
            lbl.setStyleSheet("background: transparent;")
            lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._list.setItemWidget(it, lbl)
        if self._shown:
            self._list.setCurrentRow(0)
        rows = max(1, min(8, len(self._shown)))
        self._list.setFixedHeight(rows * 30 + 6)
        self.relayout()

    def eventFilter(self, obj, ev):
        if obj is self._edit and ev.type() == QEvent.Type.KeyPress:
            k = ev.key()
            if k in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                r = self._list.currentRow() + (1 if k == Qt.Key.Key_Down else -1)
                self._list.setCurrentRow(max(0, min(self._list.count() - 1, r)))
                return True
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._run(self._list.currentRow())
                return True
            if k == Qt.Key.Key_Escape:
                self.close_palette()
                return True
        return super().eventFilter(obj, ev)

    def _run(self, row: int) -> None:
        if 0 <= row < len(self._shown):
            fn = self._shown[row][2]
            self.close_palette()
            QTimer.singleShot(0, fn)

    def close_palette(self) -> None:
        if not self._open:
            return
        self._open = False
        self.hide()
        self.closed.emit()
        self.deleteLater()


class ShortcutsOverlay(_HudOverlay):
    """F1 — every keyboard shortcut in one place."""

    closed = pyqtSignal()
    _OW = 450
    _ROWS = (
        ("Command palette", "Ctrl+K   ·   Ctrl+Shift+P"),
        ("Mute / unmute the microphone", "F4"),
        ("Stop speaking · close an overlay", "Esc"),
        ("Mini orb window", "F9"),
        ("Top dock bar", "F10"),
        ("Fullscreen", "F11"),
        ("Left sidebar", "Ctrl+B"),
        ("Right sidebar", "Ctrl+Shift+B"),
        ("Focus mode — hide every panel", "F8"),
        ("Appearance and colour", "F7"),
        ("This list", "F1"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ShortcutsOverlay {{
                background: rgba(4, 14, 22, 248);
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        self.setFixedWidth(self._OW)
        self._open = True
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(8)

        hdr = QLabel("⌨  KEYBOARD SHORTCUTS")
        hdr.setFont(QFont(_DISPLAY, 11, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};")
        lay.addWidget(sep)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(7)
        for i, (what, keys) in enumerate(self._ROWS):
            a = QLabel(what)
            a.setFont(QFont(_FONT, 10))
            a.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            k = QLabel(keys)
            k.setFont(QFont(_MONO, 9, QFont.Weight.Bold))
            k.setStyleSheet(f"""
                color: {C.PRI}; background: rgba(255, 255, 255, 14);
                border: 1px solid rgba(255, 255, 255, 34);
                border-radius: 6px; padding: 2px 8px;
            """)
            grid.addWidget(a, i, 0)
            grid.addWidget(k, i, 1, Qt.AlignmentFlag.AlignRight)
        grid.setColumnStretch(0, 1)
        lay.addLayout(grid)

        close = QPushButton("CLOSE   [Esc]")
        close.setFixedHeight(30)
        close.setFont(QFont(_FONT, 9, QFont.Weight.Bold))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close.clicked.connect(self.close_help)
        lay.addWidget(close)

    def close_help(self) -> None:
        if not self._open:
            return
        self._open = False
        self.hide()
        self.closed.emit()
        self.deleteLater()


class TopDock(QWidget):
    """Hover-expandable always-on-top presence bar for development work.

    It is deliberately a view, not a second JARVIS runtime: MainWindow owns
    state, audio callbacks and tasks, while this widget only exposes them.
    """

    _COLLAPSED_H = 42
    _EXPANDED_H = 244

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner = owner
        self._expanded = False
        self._state = "LISTENING"
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.timeout.connect(lambda: self._set_expanded(False))

        self._root = QFrame(self)
        self._root.setObjectName("TopDockRoot")
        self._root.setStyleSheet(f"""
            QFrame#TopDockRoot {{
                background: rgba(2, 12, 20, 226);
                border: 1px solid rgba(0, 212, 255, 115);
                border-radius: 0 0 13px 13px;
            }}
        """)
        lay = QVBoxLayout(self._root)
        lay.setContentsMargins(14, 6, 14, 10)
        lay.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(9)
        self._orb = QLabel("◉")
        self._orb.setFont(QFont(_DISPLAY, 17, QFont.Weight.Bold))
        self._orb.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        bar.addWidget(self._orb)
        self._name = QLabel(owner._assistant_name.upper())
        self._name.setFont(QFont(_DISPLAY, 9, QFont.Weight.Bold))
        self._name.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        bar.addWidget(self._name)
        self._status = QLabel()
        self._status.setFont(QFont(_MONO, 8, QFont.Weight.Bold))
        bar.addWidget(self._status)
        bar.addStretch(1)
        self._tasks = QLabel()
        self._tasks.setFont(QFont(_MONO, 8, QFont.Weight.Bold))
        self._tasks.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        bar.addWidget(self._tasks)
        self._clock = QLabel()
        self._clock.setFont(QFont(_MONO, 9, QFont.Weight.Bold))
        self._clock.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        bar.addWidget(self._clock)
        lay.addLayout(bar)

        self._drop = QWidget()
        drop = QVBoxLayout(self._drop)
        drop.setContentsMargins(0, 0, 0, 0)
        drop.setSpacing(7)
        title = QLabel("TASKS")
        title.setFont(QFont(_DISPLAY, 8, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        drop.addWidget(title)
        self._task_box = QWidget()
        self._task_lay = QVBoxLayout(self._task_box)
        self._task_lay.setContentsMargins(0, 0, 0, 0)
        self._task_lay.setSpacing(3)
        drop.addWidget(self._task_box)
        controls = QHBoxLayout()
        controls.setSpacing(6)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Quick task…")
        self._input.setFixedHeight(27)
        self._input.setFont(QFont(_FONT, 9))
        self._input.setStyleSheet(f"""
            QLineEdit {{ background: rgba(0, 0, 0, 58); color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 7px; padding: 3px 7px; }}
            QLineEdit:focus {{ border-color: {C.PRI}; }}
        """)
        self._input.returnPressed.connect(self._add_task)
        controls.addWidget(self._input, 1)
        for text, callback in (
            ("MIC", owner._toggle_mute),
            ("STOP", owner._do_interrupt),
            ("FULL", owner.set_dock),
        ):
            b = QPushButton(text)
            b.setFixedSize(52, 27)
            b.setFont(QFont(_MONO, 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{ color: {C.PRI}; background: transparent; border: 1px solid {C.BORDER}; border-radius: 7px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
            """)
            if text == "FULL":
                b.clicked.connect(lambda _=False: owner.set_dock(False))
            else:
                b.clicked.connect(callback)
            controls.addWidget(b)
        drop.addLayout(controls)
        lay.addWidget(self._drop)
        self._drop.hide()
        self._tick = QTimer(self)
        self._tick.timeout.connect(self.refresh)
        self._tick.start(1000)
        self.refresh()

    def resizeEvent(self, event):
        self._root.setGeometry(self.rect())
        super().resizeEvent(event)

    def enterEvent(self, event):
        self._collapse_timer.stop()
        self._set_expanded(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        delay = int(_read_full_config().get("dock_hover_delay_ms", 650) or 650)
        self._collapse_timer.start(max(150, min(3000, delay)))
        super().leaveEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        opacity = menu.addMenu("Opacity")
        choices = {}
        for pct in (100, 92, 84, 76):
            choices[opacity.addAction(f"{pct}%")] = pct / 100.0
        action = menu.exec(event.globalPos())
        if action in choices:
            self.setWindowOpacity(choices[action])
            _save_cfg(dock_opacity=choices[action])

    def _set_expanded(self, on: bool):
        if bool(on) == self._expanded:
            return
        self._expanded = bool(on)
        self._drop.setVisible(self._expanded)
        self._place()
        if self._expanded:
            self._input.setFocus()

    def _place(self):
        cfg = _read_full_config()
        width = max(420, min(1000, int(cfg.get("dock_width", 640) or 640)))
        screen = QApplication.primaryScreen().availableGeometry()
        height = self._EXPANDED_H if self._expanded else self._COLLAPSED_H
        self.setGeometry(
            screen.x() + (screen.width() - width) // 2, screen.y(), width, height
        )

    def show_dock(self):
        self._expanded = False
        self._drop.hide()
        self._place()
        self.setWindowOpacity(
            max(
                0.50,
                min(1.0, float(_read_full_config().get("dock_opacity", 0.92) or 0.92)),
            )
        )
        self.show()
        self.raise_()

    def set_state(self, state: str):
        self._state = str(state or "LISTENING")
        self.refresh()

    def _add_task(self):
        text = self._input.text().strip()
        self._input.clear()
        if text:
            self.owner._task_add(text)

    def refresh(self):
        state = "MUTED" if self.owner._muted else self._state
        color = (
            C.RED if state == "MUTED" else (C.GREEN if state == "LISTENING" else C.PRI)
        )
        self._status.setText(f"● {state}")
        self._status.setStyleSheet(f"color: {color}; background: transparent;")
        self._clock.setText(time.strftime("%H:%M"))
        tasks = list(self.owner._tasks)
        done = sum(1 for task in tasks if task.get("done"))
        self._tasks.setText(f"TASKS {done}/{len(tasks)}")
        while self._task_lay.count():
            item = self._task_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for task in tasks[:4]:
            button = QPushButton(
                ("☑  " if task.get("done") else "☐  ") + task.get("text", "")
            )
            button.setFlat(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFont(QFont(_FONT, 8))
            button.setStyleSheet(
                f"QPushButton {{ text-align: left; color: {C.TEXT_DIM if task.get('done') else C.TEXT}; background: transparent; border: none; padding: 2px; }} QPushButton:hover {{ color: {C.PRI}; }}"
            )
            button.clicked.connect(
                lambda _=False, task=task: self.owner._task_toggle(task)
            )
            self._task_lay.addWidget(button)
        if not tasks:
            empty = QLabel("No open tasks — add one below or ask JARVIS.")
            empty.setFont(QFont(_FONT, 8))
            empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            self._task_lay.addWidget(empty)


class MainWindow(QMainWindow):
    _log_sig = pyqtSignal(str)
    _state_sig = pyqtSignal(str)
    _content_sig = pyqtSignal(str, str)  # (title, text) — thread-safe content display
    _reconfig_sig = pyqtSignal()  # trigger setup overlay from any thread
    _camera_sig = pyqtSignal(bytes)  # show camera frame preview (small overlay)
    _cam_stream_sig = pyqtSignal(bool)  # True=start live stream, False=stop
    _cam_frame_sig = pyqtSignal(bytes)  # live camera frame → HUD area
    _video_open_sig = pyqtSignal(str, str, bool, str)  # video, title, muted, audio
    _wake_btns_sig = pyqtSignal()  # wake state resolved off-thread
    _video_close_sig = pyqtSignal()
    _video_mute_sig = pyqtSignal(bool)
    _clipboard_sig = pyqtSignal(str)  # clipboard text changed (thread-safe)
    _confirm_sig = pyqtSignal(str, str)  # (title, detail) — irreversible-action gate
    _confirm_hide_sig = pyqtSignal()
    _wake_dl_sig = pyqtSignal(bool, str)  # wake-word install finished (ok, message)
    _quiz_sig = pyqtSignal(str, object, object)  # (topic, questions, grader)
    _quiz_hide_sig = pyqtSignal()
    _review_sig = pyqtSignal(str, str, object, object)  # document review payload
    _mini_sig = pyqtSignal(bool)  # enter / leave mini mode from any thread
    _toast_sig = pyqtSignal(str, str, str)  # title, text, level
    _task_sig = pyqtSignal(str)  # add a task from any thread

    def __init__(self, face_path: str):
        super().__init__()
        self._face_path = face_path

        # Load customization from config
        _cfg = _read_full_config()
        self._assistant_name: str = (_cfg.get("assistant_name") or "JARVIS").strip()
        _display = self._assistant_name.upper()

        # Apply the saved theme BEFORE panels/stylesheets are built
        _ui_color = (_cfg.get("ui_color") or "").strip().lower()
        apply_ui_color(_ui_color or DEFAULT_UI_COLOR)

        self.setWindowTitle(f"{_display} — {APP_VERSION}")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width() - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command = None
        self.on_remote_clicked = None  # callable: () -> (url, key) | None
        self.on_interrupt = None  # callable: () -> None — stop JARVIS mid-speech
        self.on_voice_change = (
            None  # callable: () -> None — rebuild session with new voice
        )
        self.on_audio_device_change = (
            None  # callable: () -> None — reopen audio streams
        )
        self._confirm_overlay = None  # live ConfirmBanner, if one is on screen
        self.get_plugins = None  # callable: () -> list[dict], set by JarvisLive
        self.get_plugin_settings = (
            None  # callable: () -> list[dict] settings schemas, set by JarvisLive
        )
        self.on_wake_toggle = None  # callable: (enable: bool) -> str, set by JarvisLive
        self.on_wake_manual = None  # callable: () -> None — manual sleep/wake
        self.on_push_to_talk = None  # callable: (enable: bool) -> str scope
        self.ptt_hold = None  # callable: (held: bool) -> None — windowed chord
        self.wake_get_state = None  # callable: () -> dict {enabled, awake, ready}
        self._muted = False
        self._current_file: str | None = None
        self._remote_overlay: RemoteKeyOverlay | None = None
        self._customize_overlay: CustomizeOverlay | None = None

        # Mini (orb-only) mode state
        self._mini = False
        self._mini_on_top = True
        self._dock_active = False
        self._dock: TopDock | None = None
        self._normal_geom = None
        self._vis_content = False
        self._vis_quiz = False
        self._pending_panel = False  # a result arrived while we were mini

        # Sidebars, VS Code style: what the user chose, what the window width
        # has collapsed on its own, what was opened by hand in a narrow window,
        # and the width each sidebar was last dragged to.
        self._side_pref = {
            "left": bool(_cfg.get("ui_left_open", True)),
            "right": bool(_cfg.get("ui_right_open", True)),
        }
        self._side_auto = set()
        self._side_forced = set()
        self._side_w = {"left": _LEFT_W, "right": _RIGHT_W}
        self._split_inited = False
        self._focus = False
        self._rail_btns = {}
        self._toasts = []
        self._palette = None
        self._help = None
        self._ignore_clip = False
        self._wake_cache = None
        self._wake_downloading = False
        self._born = time.time()
        self._ft_total = 25 * 60
        self._ft_left = 25 * 60
        self._ft_running = False

        central = _AuroraRoot()
        self._aurora = central
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._header_w = self._build_header()
        root.addWidget(self._header_w)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._left_panel = self._build_left_panel()
        self._left_rail = self._build_rail("left")
        body.addWidget(self._left_rail)
        self._body_split = QSplitter(Qt.Orientation.Horizontal)
        self._body_split.setHandleWidth(3)
        self._body_split.setChildrenCollapsible(False)
        self._body_split.setStyleSheet(f"""
            QSplitter::handle:horizontal {{ background: rgba(255, 255, 255, 12); }}
            QSplitter::handle:horizontal:hover {{ background: {C.PRI_DIM}; }}
        """)
        self._body_split.addWidget(self._left_panel)

        # Center column: HUD + resizable content panel via QSplitter
        self.hud = HudCanvas(face_path, _display)
        self.hud.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.hud.on_expand = lambda: self.set_mini(False)
        self.hud.on_menu = self._mini_menu
        self.hud.mini_bar.mute_clicked.connect(self._toggle_mute)
        self.hud.mini_bar.stop_clicked.connect(self._do_interrupt)
        self.hud.mini_bar.expand_clicked.connect(lambda: self.set_mini(False))
        self._content_panel = self._build_content_panel()
        self._quiz_panel = self._build_quiz_panel()

        # Live camera container — replaces HUD when camera stream is active
        _cam_cont = QWidget()
        _cam_cont.setStyleSheet("background: #000308;")
        _cam_v = QVBoxLayout(_cam_cont)
        _cam_v.setContentsMargins(0, 0, 0, 0)
        _cam_v.setSpacing(0)
        _cam_hdr = QHBoxLayout()
        _cam_hdr.setContentsMargins(8, 5, 8, 5)
        _cam_title = QLabel("◈  CAMERA FEED")
        _cam_title.setFont(QFont(_FONT, 9, QFont.Weight.Bold))
        _cam_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _cam_hdr.addWidget(_cam_title)
        _cam_hdr.addStretch()
        _cam_x = QPushButton("✕  CLOSE")
        _cam_x.setFont(QFont(_FONT, 9, QFont.Weight.Bold))
        _cam_x.setCursor(Qt.CursorShape.PointingHandCursor)
        _cam_x.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: none; padding: 2px 6px;
            }}
            QPushButton:hover {{ color: {C.PRI}; }}
        """)
        _cam_x.clicked.connect(self.stop_camera_stream)
        _cam_hdr.addWidget(_cam_x)
        _cam_v.addLayout(_cam_hdr)
        self._cam_live_lbl = QLabel()
        self._cam_live_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cam_live_lbl.setStyleSheet("background: transparent;")
        self._cam_live_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        _cam_v.addWidget(self._cam_live_lbl, stretch=1)

        # ── Video surface ────────────────────────────────────────────────
        # Built exactly like the camera page above and stacked beside it,
        # because it is the same idea: something takes the centre of the HUD
        # for a while and then gives it back. Sharing the stack rather than
        # inventing a second mechanism means the avatar, the camera and a video
        # can never be on screen at once.
        self._video_split = False  # is the sound a separate stream?
        self._video_auto_muted = False  # did JARVIS close the mic, or the user?
        # A plain flag rather than reading the widget. video_is_playing() is
        # called from plugin threads, and reading a widget's state from one is
        # not something to rely on; an attribute is.
        self._video_on = False
        self._video_cont = QWidget()
        self._video_cont.setStyleSheet(f"background: {C.BG};")
        _vid_v = QVBoxLayout(self._video_cont)
        _vid_v.setContentsMargins(8, 6, 8, 8)
        _vid_v.setSpacing(4)

        _vid_hdr = QHBoxLayout()
        self._video_title = QLabel("▶  VIDEO")
        self._video_title.setFont(QFont(_FONT, 10, QFont.Weight.Bold))
        self._video_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _vid_hdr.addWidget(self._video_title)
        _vid_hdr.addStretch()

        def _vid_btn(text: str) -> QPushButton:
            b = QPushButton(text)
            b.setFont(QFont(_FONT, 9, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{
                    color: {C.TEXT_DIM}; background: transparent;
                    border: none; padding: 2px 6px;
                }}
                QPushButton:hover {{ color: {C.PRI}; }}
            """)
            return b

        # Muted is the default and the button says so, because a soundtrack
        # talking over JARVIS is the one way this feature could make the
        # assistant worse rather than better.
        self._video_mute_btn = _vid_btn("🔇  SOUND OFF")
        self._video_mute_btn.clicked.connect(self._toggle_video_mute)
        _vid_hdr.addWidget(self._video_mute_btn)

        _vid_x = _vid_btn("✕  CLOSE")
        _vid_x.clicked.connect(self.stop_video)
        _vid_hdr.addWidget(_vid_x)
        _vid_v.addLayout(_vid_hdr)

        if HAVE_VIDEO:
            # A GRAPHICS ITEM, NOT A QVideoWidget.
            #
            # QVideoWidget gets a native window of its own, and a native window
            # is painted by the compositor above every ordinary widget that
            # shares the screen with it — so the settings drawer opened
            # *underneath* the video and raise_() could not help, because the
            # two are not in the same painting order at all. A video item drawn
            # into a QGraphicsView goes through Qt's own painter like any other
            # widget, and anything laid over it stays over it.
            self._video_scene = QGraphicsScene(self)
            self._video_item = QGraphicsVideoItem()
            self._video_scene.addItem(self._video_item)
            self._video_widget = QGraphicsView(self._video_scene)
            self._video_widget.setStyleSheet("background: #000; border: none;")
            self._video_widget.setFrameShape(QGraphicsView.Shape.NoFrame)
            self._video_widget.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            self._video_widget.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            self._video_widget.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            _vid_v.addWidget(self._video_widget, stretch=1)

            self._video_audio = QAudioOutput()
            self._video_audio.setMuted(True)
            self._video_player = QMediaPlayer()
            self._video_player.setVideoOutput(self._video_item)
            # Keep the picture filling the view as the window is resized.
            self._video_item.nativeSizeChanged.connect(self._fit_video)
            self._video_player.setAudioOutput(self._video_audio)
            self._video_player.errorOccurred.connect(self._on_video_error)

            # A SECOND player, for sound that arrives separately.
            #
            # YouTube no longer serves a single stream carrying both picture and
            # sound — not for new uploads and not for old ones; checked against
            # three videos including the oldest on the site, and every one of
            # them offered zero combined formats. Picture and sound are two
            # URLs, so they are two players, started together and nudged back
            # into line by the timer below.
            #
            # A local file or a direct video URL still uses the first player
            # alone: one stream, one player, nothing to synchronise.
            self._video_sound = QMediaPlayer()
            self._video_sound_out = QAudioOutput()
            self._video_sound_out.setMuted(True)
            self._video_sound.setAudioOutput(self._video_sound_out)

            self._video_sync = QTimer(self)
            self._video_sync.setInterval(1000)
            self._video_sync.timeout.connect(self._sync_video_sound)
        else:
            self._video_scene = None
            self._video_item = None
            self._video_widget = None
            self._video_player = None
            self._video_audio = None
            self._video_sound = None
            self._video_sound_out = None
            self._video_sync = None
            _miss = QLabel("Video playback is not available in this Qt install.")
            _miss.setAlignment(Qt.AlignmentFlag.AlignCenter)
            _miss.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            _vid_v.addWidget(_miss, stretch=1)

        # Stack: 0 = animated HUD, 1 = live camera, 2 = video
        self._hud_cam_stack = QStackedWidget()
        self._hud_cam_stack.addWidget(self.hud)
        self._hud_cam_stack.addWidget(_cam_cont)
        self._hud_cam_stack.addWidget(self._video_cont)

        self._center_split = QSplitter(Qt.Orientation.Vertical)
        self._center_split.setStyleSheet(f"""
            QSplitter::handle {{
                background: {C.BORDER};
                height: 4px;
            }}
            QSplitter::handle:hover {{
                background: {C.PRI_DIM};
            }}
        """)
        self._center_split.addWidget(self._hud_cam_stack)
        self._center_split.addWidget(self._content_panel)
        self._center_split.addWidget(self._quiz_panel)
        self._center_split.setStretchFactor(0, 3)
        self._center_split.setStretchFactor(1, 1)
        self._center_split.setCollapsible(0, False)
        self._body_split.addWidget(self._center_split)

        self._right_panel = self._build_right_panel()
        self._body_split.addWidget(self._right_panel)
        self._body_split.setStretchFactor(0, 0)
        self._body_split.setStretchFactor(1, 1)
        self._body_split.setStretchFactor(2, 0)
        self._body_split.splitterMoved.connect(self._on_split_moved)
        body.addWidget(self._body_split, 1)
        self._right_rail = self._build_rail("right")
        body.addWidget(self._right_rail)

        root.addLayout(body, stretch=1)
        self._footer_w = self._build_footer()
        root.addWidget(self._footer_w)

        # Quick-access drawer (floating overlay, built after central widget layout is done)
        self._quick_drawer = self._build_quick_drawer()
        self._warm_wake_state()
        self._update_autostart_btn(self._check_autostart())
        from memory.config_manager import get_brief_enabled as _gbe

        self._update_brief_btn(_gbe())

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        # Metric update timer
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        self._log_sig.connect(self._log.append_log)
        self._state_sig.connect(self._apply_state)
        self._content_sig.connect(self._show_content)
        self._reconfig_sig.connect(self._show_setup)
        self._camera_sig.connect(self._show_camera_frame)
        self._confirm_sig.connect(self._show_confirm_banner)
        self._confirm_hide_sig.connect(self._hide_confirm_banner)
        self._cam_stream_sig.connect(self._on_cam_stream)
        self._cam_frame_sig.connect(self._on_cam_frame)
        self._wake_btns_sig.connect(self._refresh_wake_btns)
        self._video_open_sig.connect(self._on_video_open)
        self._video_close_sig.connect(self._on_video_close)
        self._video_mute_sig.connect(self._on_video_mute)
        self._clipboard_sig.connect(self._show_clipboard_panel)
        self._wake_dl_sig.connect(self._on_wake_install_done)
        self._quiz_sig.connect(self._show_quiz)
        self._quiz_hide_sig.connect(self._hide_quiz)
        self._review_sig.connect(self._show_review)
        self._mini_sig.connect(self.set_mini)
        self._toast_sig.connect(lambda t, x, l: self._toast(t, x, l))
        self._task_sig.connect(lambda t: self._task_add(t))
        self._cam_stop = threading.Event()

        # Camera preview overlay (child of central widget, positioned in resizeEvent)
        self._cam_preview = _CameraPreview(self.centralWidget())

        # Clipboard panel (child of central widget, bottom-center)
        self._clipboard_panel = ClipboardPanel(self.centralWidget())
        self._clipboard_panel.action_requested.connect(self._on_clipboard_action)
        QApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)

        self._overlay: SetupOverlay | None = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)

        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)

        sc_intr = QShortcut(QKeySequence("Escape"), self)
        sc_intr.activated.connect(self._do_interrupt)

        sc_input = QShortcut(QKeySequence("Ctrl+L"), self)
        sc_input.activated.connect(self._focus_input)

        sc_mini = QShortcut(QKeySequence("F9"), self)
        sc_mini.activated.connect(lambda: self.set_mini(not self._mini))

        sc_dock = QShortcut(QKeySequence("F10"), self)
        sc_dock.activated.connect(lambda: self.set_dock(not self._dock_active))

        for _seq, _fn in (
            ("Ctrl+B", lambda: self._toggle_side("left")),
            ("Ctrl+Shift+B", lambda: self._toggle_side("right")),
            ("F8", lambda: self._set_focus(not self._focus)),
            ("Ctrl+K", lambda: self._open_palette()),
            ("Ctrl+Shift+P", lambda: self._open_palette()),
            ("F1", lambda: self._open_help()),
            ("F7", self._open_customize),
        ):
            QShortcut(QKeySequence(_seq), self).activated.connect(_fn)
        self._apply_layout_state()

        # Pick up where the user left off: if JARVIS was last used as the small
        # orb, it comes back as the small orb (handy with auto-start).
        if self._ready and _cfg.get("mini_mode"):
            QTimer.singleShot(400, lambda: self.set_mini(True))
        elif self._ready and _cfg.get("presentation_mode") == "dock":
            QTimer.singleShot(400, lambda: self.set_dock(True))

    # ── Sidebars, rails, focus mode ──────────────────────────────────────────
    # The model is VS Code's. Each sidebar has a *preference* the user sets (the
    # rail, the header, Ctrl+B / Ctrl+Shift+B), and the window can override it:
    # below a width it folds a sidebar away by itself, and one the user opens by
    # hand in that narrow window is remembered as "forced" so the next resize
    # does not snatch it back. Focus mode hides everything at once.

    def _side_open(self, side: str) -> bool:
        if self._focus or self._mini:
            return False
        if side in self._side_forced:
            return True
        return bool(self._side_pref[side]) and side not in self._side_auto

    def _build_rail(self, side: str) -> QWidget:
        """Thin icon strip beside a sidebar — VS Code's activity bar."""
        name = "Rail" + side.capitalize()
        rail = QFrame()
        rail.setObjectName(name)
        rail.setFixedWidth(_RAIL_W)
        edge = "border-right" if side == "left" else "border-left"
        mark = "border-left" if side == "left" else "border-right"
        rail.setStyleSheet(f"""
            QFrame#{name} {{
                background: rgba(3, 10, 16, 150);
                {edge}: 1px solid rgba(255, 255, 255, 18);
            }}
        """)
        lay = QVBoxLayout(rail)
        lay.setContentsMargins(0, 8, 0, 8)
        lay.setSpacing(4)

        def btn(key: str, sym: str, tip: str, fn, checkable: bool = True) -> None:
            b = QPushButton(sym)
            b.setFixedSize(_RAIL_W - 1, 34)
            b.setFont(QFont("Segoe UI Symbol", 13))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tip)
            b.setCheckable(checkable)
            b.setStyleSheet(f"""
                QPushButton {{ color: {C.TEXT_DIM}; background: transparent;
                    border: none; {mark}: 2px solid transparent; border-radius: 0px; }}
                QPushButton:hover {{ color: {C.TEXT}; }}
                QPushButton:checked {{ color: {C.PRI}; {mark}: 2px solid {C.PRI};
                    background: rgba(255, 255, 255, 10); }}
            """)
            b.clicked.connect(lambda _=False: fn())
            lay.addWidget(b)
            self._rail_btns[key] = b

        if side == "left":
            btn("left:0", "▤", "System monitor  [Ctrl+B]", lambda: self._rail_click(0))
            btn(
                "left:1",
                "✓",
                "Tools — focus timer, tasks, quick actions",
                lambda: self._rail_click(1),
            )
            lay.addStretch(1)
            btn(
                "palette",
                "⌘",
                "Command palette  [Ctrl+K]",
                lambda: self._open_palette(),
                False,
            )
        else:
            btn(
                "right",
                "◨",
                "Log and command line  [Ctrl+Shift+B]",
                lambda: self._toggle_side("right"),
            )
            lay.addStretch(1)
            btn(
                "help",
                "?",
                "Keyboard shortcuts  [F1]",
                lambda: self._open_help(),
                False,
            )
        return rail

    def _rail_click(self, page: int) -> None:
        """Same page as the open sidebar → close it; otherwise show that page."""
        was_here = self._side_open("left") and self._left_stack.currentIndex() == page
        self._left_stack.setCurrentIndex(page)
        _save_cfg(ui_left_page=page)
        if was_here or not self._side_open("left"):
            self._toggle_side("left")
        else:
            self._apply_layout_state()

    def _toggle_side(self, side: str) -> None:
        if self._mini:
            return
        opening = not self._side_open(side)  # False→True also when focus mode hid it
        if opening:
            self._side_pref[side] = True
            if side in self._side_auto:  # narrow window: keep it open anyway
                self._side_auto.discard(side)
                self._side_forced.add(side)
        else:
            self._side_pref[side] = False
            self._side_forced.discard(side)
        self._focus = False
        _save_cfg(
            ui_left_open=self._side_pref["left"], ui_right_open=self._side_pref["right"]
        )
        self._apply_layout_state()

    def _auto_collapse(self) -> None:
        """Fold the sidebars away as the window narrows, bring them back when it
        widens again (with a little hysteresis so it cannot flicker)."""
        if self._mini or self.isMinimized():
            return
        w = self.width()
        before = (set(self._side_auto), set(self._side_forced))
        for side, thr in (("right", 1000), ("left", 780)):
            if w >= thr + 40:
                self._side_auto.discard(side)
                self._side_forced.discard(side)
            elif w < thr and side not in self._side_forced:
                self._side_auto.add(side)
        if before != (self._side_auto, self._side_forced):
            self._apply_layout_state()

    def _apply_layout_state(self) -> None:
        """Make the widgets match the layout state: panels, rails, status bar,
        the rail's highlighted buttons, and the splitter widths."""
        if not hasattr(self, "_left_panel") or not hasattr(self, "_footer_w"):
            return
        if not getattr(self, "_layout_loaded", False):
            self._layout_loaded = True
            cfg = _read_full_config()
            try:
                self._side_w["left"] = max(
                    176, min(340, int(cfg.get("ui_left_w", _LEFT_W)))
                )
                self._side_w["right"] = max(
                    280, min(640, int(cfg.get("ui_right_w", _RIGHT_W)))
                )
                if int(cfg.get("ui_left_page", 0)) in (0, 1):
                    self._left_stack.setCurrentIndex(int(cfg.get("ui_left_page", 0)))
            except Exception:
                pass
        if self._mini:
            return
        # The header is hidden with everything else when the window shrinks to
        # the orb, and nothing but this brings it back.
        self._header_w.setVisible(True)
        self._adapt_header()
        lo, ro = self._side_open("left"), self._side_open("right")
        self._left_panel.setVisible(lo)
        self._right_panel.setVisible(ro)
        self._left_rail.setVisible(not self._focus)
        self._right_rail.setVisible(not self._focus)
        self._footer_w.setVisible(not self._focus)
        page = self._left_stack.currentIndex()
        for key, on in (
            ("left:0", lo and page == 0),
            ("left:1", lo and page == 1),
            ("right", ro),
        ):
            b = self._rail_btns.get(key)
            if b is not None:
                b.setChecked(bool(on))
        QTimer.singleShot(0, self._apply_split_sizes)

    def _apply_split_sizes(self) -> None:
        if self._mini or not hasattr(self, "_body_split"):
            return
        total = self._body_split.width()
        if total <= 0:
            return
        lw = self._side_w["left"] if self._side_open("left") else 0
        rw = self._side_w["right"] if self._side_open("right") else 0
        room = total - 320  # the centre never gets squeezed below this
        if lw + rw > room:
            rw = min(rw, max(280 if rw else 0, room - lw))
            lw = min(lw, max(176 if lw else 0, room - rw))
        self._body_split.setSizes([lw, max(1, total - lw - rw), rw])
        self._layout_toasts()

    def _on_split_moved(self, _pos: int, _index: int) -> None:
        s = self._body_split.sizes()
        if self._side_open("left") and s[0] > 0:
            self._side_w["left"] = s[0]
        if self._side_open("right") and s[2] > 0:
            self._side_w["right"] = s[2]
        t = getattr(self, "_split_tmr", None)
        if t is None:
            t = self._split_tmr = QTimer(self)
            t.setSingleShot(True)
            t.setInterval(500)
            t.timeout.connect(
                lambda: _save_cfg(
                    ui_left_w=self._side_w["left"], ui_right_w=self._side_w["right"]
                )
            )
        t.start()

    def _right_inset(self) -> int:
        """Pixels at the right edge that belong to the right sidebar and rail,
        so floating things can stay inside the centre column."""
        n = 0
        for name in ("_right_panel", "_right_rail"):
            w = getattr(self, name, None)
            if w is not None and w.isVisible():
                n += w.width()
        return n

    def _set_focus(self, on: bool) -> None:
        if self._mini or bool(on) == self._focus:
            return
        self._focus = bool(on)
        self._apply_layout_state()
        if self._focus:
            self._toast(
                "FOCUS MODE",
                "Panels hidden — press F8 to bring them back.",
                "info",
            )

    def _focus_input(self) -> None:
        if self._mini:
            return

        if not self._side_open("right"):
            self._toggle_side("right")

        self._input.setFocus()
        self._input.selectAll()

    # ── Toasts, palette, help ────────────────────────────────────────────────

    def _toast(
        self, title: str, text: str = "", level: str = "info", ms: int = 3600
    ) -> None:
        """Small non-blocking notification. Nothing to show it on in mini mode."""
        if self._mini:
            return
        for old in list(self._toasts)[: max(0, len(self._toasts) - 3)]:
            old.dismiss()
        t = _Toast(title, text, level, ms, self.centralWidget())
        t.closed.connect(self._toast_closed)
        self._toasts.append(t)
        t.show()
        t.raise_()
        self._layout_toasts()

    def _toast_closed(self, t) -> None:
        if t in self._toasts:
            self._toasts.remove(t)
        self._layout_toasts()

    def _layout_toasts(self) -> None:
        cw = self.centralWidget()
        if cw is None:
            return
        y = getattr(self, "_header_height_px", 46) + 10
        right = cw.width() - self._right_inset()
        for t in self._toasts:
            t.move(max(8, right - t.width() - 12), y)
            t.raise_()
            y += t.height() + 8

    def _palette_actions(self) -> list:
        A = []

        def add(title: str, hint: str, fn) -> None:
            A.append((title, hint, fn))

        add("Toggle left sidebar", "Ctrl+B", lambda: self._toggle_side("left"))
        add("Toggle right sidebar", "Ctrl+Shift+B", lambda: self._toggle_side("right"))
        add("Show system monitor", "", lambda: self._rail_show(0))
        add("Show tools — timer, tasks, quick actions", "", lambda: self._rail_show(1))
        add(
            "Focus mode — hide every panel",
            "F8",
            lambda: self._set_focus(not self._focus),
        )
        add("Mini orb window", "F9", lambda: self.set_mini(True))
        add("Top dock bar", "F10", lambda: self.set_dock(not self._dock_active))
        add("Mute / unmute the microphone", "F4", self._toggle_mute)
        add("Stop speaking", "Esc", self._do_interrupt)
        add("Fullscreen", "F11", self._toggle_fullscreen)
        add("Appearance and colour", "F7", self._open_customize)
        add("Switch HUD: particle orb / animated face", "", self._toggle_hud_style)
        add("Start / pause the focus timer", "", self._ft_toggle)
        add("Customise assistant", "", self._open_customize)
        add("Audio devices", "", self._open_audio_devices)
        add("Memory — what JARVIS remembers", "", self._open_memory_panel)
        add("Plugins", "", self._open_plugin_manager)
        add("Plugin settings", "", self._open_plugin_settings)
        add("Remote control", "", self._open_remote)
        add("Copy the activity log", "", self._copy_log)
        add("Clear the activity log", "", self._clear_log)
        add("Keyboard shortcuts", "F1", self._open_help)
        for _label, title, msg in _QUICK_ACTIONS:
            add(title, "ask JARVIS", lambda m=msg: self._run_quick(m))
        return A

    def _rail_show(self, page: int) -> None:
        self._left_stack.setCurrentIndex(page)
        _save_cfg(ui_left_page=page)
        if not self._side_open("left"):
            self._toggle_side("left")
        else:
            self._apply_layout_state()

    def _open_palette(self, initial: str = "") -> None:
        if self._mini:
            return
        if self._palette is not None:
            self._palette.close_palette()
            if not initial:
                return  # Ctrl+K again closes it
        self._close_controls()
        self._close_setup()
        ov = CommandPalette(self._palette_actions(), self.centralWidget(), initial)
        ov.closed.connect(self._palette_closed)
        self._palette = ov
        ov.relayout()
        ov.show()
        ov.raise_()
        ov.focus_edit()

    def _palette_closed(self) -> None:
        self._palette = None

    def _open_help(self) -> None:
        if self._mini:
            return
        if self._help is not None:
            self._help.close_help()
            return
        self._close_controls()
        self._close_setup()
        cw = self.centralWidget()
        ov = ShortcutsOverlay(cw)
        ov.closed.connect(self._help_closed)
        ov.adjustSize()
        ov.move(
            max(0, (cw.width() - ov.width()) // 2),
            max(10, (cw.height() - ov.height()) // 2),
        )
        ov.show()
        ov.raise_()
        self._help = ov

    def _help_closed(self) -> None:
        self._help = None

    def _close_transient(self) -> bool:
        """Esc closes the topmost floating thing before it stops the assistant.
        Returns True if it closed something."""
        if self._palette is not None:
            self._palette.close_palette()
            return True
        if self._help is not None:
            self._help.close_help()
            return True
        qd, cd = getattr(self, "_quick_drawer", None), getattr(
            self, "_ctrl_drawer", None
        )
        if (qd is not None and qd.isVisible()) or (cd is not None and cd.isVisible()):
            self._close_setup()
            self._close_controls()
            return True
        return False

    # ── Log helpers ──────────────────────────────────────────────────────────

    def _flat_btn(self, text: str, tip: str, fn) -> QPushButton:
        b = QPushButton(text)
        b.setFixedHeight(20)
        b.setFont(QFont(_DISPLAY, 7, QFont.Weight.Bold))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setToolTip(tip)
        b.setStyleSheet(f"""
            QPushButton {{ color: {C.TEXT_DIM}; background: transparent;
                border: 1px solid rgba(255, 255, 255, 26); border-radius: 7px;
                padding: 0 8px; }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}
        """)
        b.clicked.connect(lambda _=False: fn())
        return b

    def _copy_log(self) -> None:
        # The clipboard watcher would answer our own copy with its "clipboard
        # detected" panel, so it is told to look away for a moment.
        self._ignore_clip = True
        QApplication.clipboard().setText(self._log.toPlainText())
        QTimer.singleShot(700, lambda: setattr(self, "_ignore_clip", False))
        self._toast("LOG COPIED", "The activity log is on your clipboard.", "ok", 2200)

    def _clear_log(self) -> None:
        self._log.clear_all()

    # ── Tools page: focus timer, tasks, quick actions ────────────────────────

    def _build_tools_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 12, 10, 10)
        lay.setSpacing(7)

        # Focus timer
        lay.addLayout(self._panel_header("FOCUS TIMER"))
        card = QFrame()
        card.setObjectName("FtCard")
        card.setStyleSheet(_glass_css("FtCard"))
        cl = QVBoxLayout(card)
        cl.setContentsMargins(10, 8, 10, 10)
        cl.setSpacing(7)
        self._ft_lbl = QLabel("25:00")
        self._ft_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ft_lbl.setFont(QFont(_DISPLAY, 22, QFont.Weight.Bold))
        self._ft_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        cl.addWidget(self._ft_lbl)
        presets = QHBoxLayout()
        presets.setSpacing(5)
        for m in (15, 25, 45):
            presets.addWidget(
                self._flat_btn(
                    f"{m} MIN",
                    f"Set the timer to {m} minutes",
                    lambda _m=m: self._ft_set(_m),
                )
            )
        cl.addLayout(presets)
        ctl = QHBoxLayout()
        ctl.setSpacing(5)
        self._ft_btn = self._flat_btn("▶  START", "Start or pause", self._ft_toggle)
        self._ft_btn.setFixedHeight(26)
        ctl.addWidget(self._ft_btn, 1)
        rst = self._flat_btn("RESET", "Back to the full time", self._ft_reset)
        rst.setFixedHeight(26)
        ctl.addWidget(rst)
        cl.addLayout(ctl)
        lay.addWidget(card)
        self._ft_tmr = QTimer(self)
        self._ft_tmr.setInterval(1000)
        self._ft_tmr.timeout.connect(self._ft_tick)
        self._ft_render()

        # Tasks
        lay.addSpacing(2)
        _th = self._panel_header("TASKS", "0/0")
        self._task_chip = _th.itemAt(_th.count() - 1).widget()
        lay.addLayout(_th)
        self._task_in = QLineEdit()
        self._task_in.setPlaceholderText("Add a task and press Enter…")
        self._task_in.setFont(QFont(_FONT, 9))
        self._task_in.setFixedHeight(30)
        self._task_in.setStyleSheet(f"""
            QLineEdit {{ background: rgba(255, 255, 255, 14); color: {C.WHITE};
                border: 1px solid rgba(255, 255, 255, 30); border-radius: 9px;
                padding: 0 10px; }}
            QLineEdit:focus {{ border-color: {C.PRI}; }}
        """)
        self._task_in.returnPressed.connect(self._task_submit)
        lay.addWidget(self._task_in)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; }}
            QScrollBar:vertical {{ background: transparent; width: 6px; border: none; }}
            QScrollBar::handle:vertical {{ background: rgba(255, 255, 255, 50);
                border-radius: 3px; min-height: 20px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        """)
        box = QWidget()
        box.setStyleSheet("background: transparent;")
        self._task_lay = QVBoxLayout(box)
        self._task_lay.setContentsMargins(0, 0, 4, 0)
        self._task_lay.setSpacing(4)
        scroll.setWidget(box)
        lay.addWidget(scroll, 1)

        # Quick actions
        lay.addSpacing(2)
        lay.addLayout(self._panel_header("QUICK ACTIONS"))
        grid = QGridLayout()
        grid.setSpacing(5)
        for i, (label, title, msg) in enumerate(_QUICK_ACTIONS):
            b = QPushButton(label.replace("  ", " "))
            b.setFixedHeight(28)
            b.setFont(QFont(_FONT, 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(title)
            b.setStyleSheet(f"""
                QPushButton {{ color: {C.TEXT_MED}; background: rgba(255, 255, 255, 12);
                    border: 1px solid rgba(255, 255, 255, 28); border-radius: 8px;
                    padding: 0 4px; }}
                QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM};
                    background: {C.PRI_GHO}; }}
            """)
            b.clicked.connect(lambda _=False, m=msg: self._run_quick(m))
            grid.addWidget(b, i // 2, i % 2)
        lay.addLayout(grid)

        self._tasks = self._tasks_load()
        self._tasks_render()
        return w

    def _run_quick(self, msg: str) -> None:
        """Send a canned prompt; "clip:" ones are templates over the clipboard."""
        if msg.startswith("clip:"):
            txt = QApplication.clipboard().text().strip()
            if not txt:
                self._toast(
                    "CLIPBOARD IS EMPTY",
                    "Copy some text first, then run this again.",
                    "warn",
                )
                return
            msg = msg[5:].replace("{text}", txt[:1500])
        self._submit_text(msg)

    # focus timer
    def _ft_render(self) -> None:
        m, s = divmod(max(0, self._ft_left), 60)
        self._ft_lbl.setText(f"{m:02d}:{s:02d}")
        self._ft_btn.setText("❚❚  PAUSE" if self._ft_running else "▶  START")

    def _ft_set(self, minutes: int) -> None:
        self._ft_total = int(minutes) * 60
        self._ft_reset()

    def _ft_reset(self) -> None:
        self._ft_left = self._ft_total
        self._ft_running = False
        self._ft_tmr.stop()
        self._ft_render()

    def _ft_toggle(self) -> None:
        if self._ft_left <= 0:
            self._ft_left = self._ft_total
        self._ft_running = not self._ft_running
        if self._ft_running:
            self._ft_tmr.start()
            self._toast(
                "FOCUS TIMER STARTED", f"{self._ft_left // 60} min — go.", "info", 2000
            )
        else:
            self._ft_tmr.stop()
        self._ft_render()

    def _ft_tick(self) -> None:
        self._ft_left -= 1
        if self._ft_left <= 0:
            self._ft_left = 0
            self._ft_running = False
            self._ft_tmr.stop()
            self._log.append_log("SYS: Focus session complete.")
            self._toast("FOCUS SESSION COMPLETE", "Take a short break.", "ok", 6000)
        self._ft_render()

    # tasks
    def _tasks_load(self) -> list:
        try:
            raw = json.loads(_tasks_file().read_text(encoding="utf-8"))
            return [
                {"text": str(t.get("text", ""))[:200], "done": bool(t.get("done"))}
                for t in raw
                if isinstance(t, dict) and str(t.get("text", "")).strip()
            ]
        except Exception:
            return []

    def _tasks_save(self) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            _tasks_file().write_text(
                json.dumps(self._tasks, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def _task_submit(self) -> None:
        txt = self._task_in.text()
        self._task_in.clear()
        self._task_add(txt)

    def _task_add(self, text: str) -> None:
        text = (text or "").strip()
        if not text or len(self._tasks) >= 100:
            return
        self._tasks.append({"text": text[:200], "done": False})
        self._tasks_save()
        self._tasks_render()
        if self._dock is not None:
            self._dock.refresh()
        self._toast("TASK ADDED", text[:80], "ok", 2200)

    def _task_toggle(self, task: dict) -> None:
        task["done"] = not task["done"]
        self._tasks_save()
        QTimer.singleShot(0, self._tasks_render)  # not inside the clicked button's slot
        if self._dock is not None:
            QTimer.singleShot(0, self._dock.refresh)

    def _task_remove(self, task: dict) -> None:
        if task in self._tasks:
            self._tasks.remove(task)
        self._tasks_save()
        QTimer.singleShot(0, self._tasks_render)
        if self._dock is not None:
            QTimer.singleShot(0, self._dock.refresh)

    def _tasks_render(self) -> None:
        lay = self._task_lay
        while lay.count():
            item = lay.takeAt(0)
            wdg = item.widget()
            if wdg is not None:
                wdg.hide()
                wdg.deleteLater()
        if not self._tasks:
            e = QLabel("No tasks yet. Add one above, or tell JARVIS “remind me to …”.")
            e.setWordWrap(True)
            e.setFont(QFont(_FONT, 9))
            e.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(e)
        for t in self._tasks:
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)
            chk = QPushButton("☑" if t["done"] else "☐")
            chk.setFixedSize(22, 22)
            chk.setFont(QFont("Segoe UI Symbol", 12))
            chk.setCursor(Qt.CursorShape.PointingHandCursor)
            chk.setStyleSheet(
                f"QPushButton {{ color: {C.GREEN if t['done'] else C.TEXT_MED}; "
                f"background: transparent; border: none; }} "
                f"QPushButton:hover {{ color: {C.PRI}; }}"
            )
            chk.clicked.connect(lambda _=False, t=t: self._task_toggle(t))
            lbl = QLabel()
            lbl.setWordWrap(True)
            lbl.setFont(QFont(_FONT, 9))
            if t["done"]:
                lbl.setText(f"<s>{_html.escape(t['text'])}</s>")
                lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            else:
                lbl.setTextFormat(Qt.TextFormat.PlainText)
                lbl.setText(t["text"])
                lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
            rm = QPushButton("✕")
            rm.setFixedSize(18, 18)
            rm.setFont(QFont(_FONT, 8, QFont.Weight.Bold))
            rm.setCursor(Qt.CursorShape.PointingHandCursor)
            rm.setToolTip("Remove")
            rm.setStyleSheet(
                f"QPushButton {{ color: {C.TEXT_DIM}; background: transparent; border: none; }} "
                f"QPushButton:hover {{ color: {C.RED}; }}"
            )
            rm.clicked.connect(lambda _=False, t=t: self._task_remove(t))
            h.addWidget(chk, 0, Qt.AlignmentFlag.AlignTop)
            h.addWidget(lbl, 1)
            h.addWidget(rm, 0, Qt.AlignmentFlag.AlignTop)
            lay.addWidget(row)
        lay.addStretch(1)
        done = sum(1 for t in self._tasks if t["done"])
        self._task_chip.setText(f"{done}/{len(self._tasks)}")

    def _show_camera_frame(self, img_bytes: bytes):
        """Slot — display camera preview overlay (main thread)."""
        if self._mini:
            return
        self._cam_preview.show_frame(img_bytes)
        cw = self.centralWidget()
        pw = _CameraPreview._W
        ph = self._cam_preview.height()
        self._cam_preview.setGeometry(
            cw.width() - self._right_inset() - pw - 12,
            cw.height() - ph - 28,
            pw,
            ph,
        )

    # --- Mini (orb-only) mode ----------------------------------------------
    #
    # The same window, stripped to the orb: header, side panels and footer are
    # hidden, the frame is removed, the window goes translucent and stays on
    # top. The HUD canvas then draws a small dark card with just the particle
    # orb and a status dot. It is dragged by the orb itself, resized by the grip
    # in the corner, and double-click (or the ⤢ button) brings the full window
    # back. The microphone, wake word and everything else keep running — only
    # the pixels change — so JARVIS keeps listening while you work in an editor.

    def _mini_hidden(self) -> list:
        return [
            self._header_w,
            self._left_rail,
            self._right_rail,
            self._left_panel,
            self._right_panel,
            self._footer_w,
            self._content_panel,
            self._quiz_panel,
        ]

    def _mini_flags(self):
        f = Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
        if self._mini_on_top:
            f |= Qt.WindowType.WindowStaysOnTopHint
        return f

    def _reflag(self, flags, translucent: bool, geom: QRect) -> None:
        """Changing window flags recreates the native window, which hides it —
        so hide first, change, restore geometry, show."""
        self.hide()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, translucent)
        self.setWindowFlags(flags)
        self.setGeometry(geom)
        self.show()
        self.raise_()

    def _mini_target_geom(self) -> QRect:
        g = _read_full_config().get("mini_geom")
        try:
            x, y, w, h = [int(v) for v in g]
            w = max(_MINI_MIN_W, w)
            h = max(_MINI_MIN_H, h)
            if QApplication.screenAt(QPoint(x + w // 2, y + h // 2)) is not None:
                return QRect(x, y, w, h)
        except Exception:
            pass
        scr = QApplication.primaryScreen().availableGeometry()
        return QRect(
            scr.right() - _MINI_W - 24, scr.bottom() - _MINI_H - 24, _MINI_W, _MINI_H
        )

    def _save_mini_geom(self) -> None:
        g = self.geometry()
        _save_cfg(mini_geom=[g.x(), g.y(), g.width(), g.height()])

    def _mini_opacity(self) -> float:
        """A persistent, bounded opacity for the unobtrusive mini orb."""
        try:
            value = float(
                _read_full_config().get("mini_opacity", _MINI_DEFAULT_OPACITY)
            )
            return max(0.45, min(1.0, value))
        except Exception:
            return _MINI_DEFAULT_OPACITY

    def set_mini(self, on: bool, persist: bool = True) -> None:
        """Enter or leave mini mode. Safe to call repeatedly."""
        on = bool(on)
        if on == self._mini:
            return
        if on and self._dock_active:
            self.set_dock(False, persist=False)
        if on and not self._ready:
            return  # first-run setup still needs the full window

        # Anything floating over the full window cannot live in a 240 px one.
        self._close_controls()
        self._close_setup()
        for attr in (
            "_remote_overlay",
            "_customize_overlay",
            "_audio_overlay",
            "_memory_overlay",
            "_plugin_manager_overlay",
            "_plugin_settings_overlay",
        ):
            ov = getattr(self, attr, None)
            if ov is not None:
                try:
                    ov.hide()
                except RuntimeError:
                    pass
        if self.isFullScreen():
            self.showNormal()

        if on:
            self._close_transient()
            for _t in list(self._toasts):
                _t.dismiss()
            self._normal_geom = self.geometry()
            self._vis_content = self._content_panel.isVisible()
            self._vis_quiz = self._quiz_panel.isVisible()
            self._cam_preview.hide()
            self._clipboard_panel.hide()
            for w in self._mini_hidden():
                w.hide()
            self._mini = True
            self._aurora.transparent = True
            self.hud.set_compact(True)
            self.hud.mini_bar.set_muted(self._muted)
            self.setMinimumSize(_MINI_MIN_W, _MINI_MIN_H)
            self._reflag(self._mini_flags(), True, self._mini_target_geom())
            self.setWindowOpacity(self._mini_opacity())
            if persist:
                _save_cfg(mini_mode=True)
                _save_cfg(presentation_mode="mini")
        else:
            self._save_mini_geom()
            self._mini = False
            self._aurora.transparent = False
            self.hud.set_compact(False)
            self.setWindowOpacity(1.0)
            self.setMinimumSize(_MIN_W, _MIN_H)
            geom = self._normal_geom or QRect(0, 0, _DEFAULT_W, _DEFAULT_H)
            self._reflag(Qt.WindowType.Window, False, geom)
            self._apply_layout_state()
            if self._vis_content or self._pending_panel:
                self._content_panel.show()
            if self._quiz is not None or self._vis_quiz:
                self._quiz_panel.show()
            self._pending_panel = False
            QTimer.singleShot(0, self._relayout_split)
            if persist:
                _save_cfg(mini_mode=False)
                _save_cfg(presentation_mode="full")

    def set_dock(self, on: bool, persist: bool = True) -> None:
        """Switch between the full HUD and the hover-expandable Top Dock."""
        on = bool(on)
        if on == self._dock_active:
            return
        if on and not self._ready:
            return
        if on:
            if self._mini:
                self.set_mini(False, persist=False)
            self._close_transient()
            self._close_controls()
            if self._dock is None:
                self._dock = TopDock(self)
            self._dock_active = True
            self._dock.set_state(self.hud.state)
            self._dock.show_dock()
            self.hide()
            if persist:
                _save_cfg(mini_mode=False, presentation_mode="dock")
        else:
            self._dock_active = False
            if self._dock is not None:
                self._dock.hide()
            self.showNormal()
            self.raise_()
            self.activateWindow()
            if persist:
                _save_cfg(presentation_mode="full")

    def _relayout_split(self) -> None:
        total = self._center_split.height()
        if self._quiz_panel.isVisible():
            self._center_split.setSizes([max(total - 250, 120), 0, 250])
        elif self._content_panel.isVisible():
            self._center_split.setSizes([max(total - 240, 120), 240, 0])

    def _mini_menu(self, gpos) -> None:
        m = QMenu(self)
        m.setStyleSheet(f"""
            QMenu {{ background: rgba(4, 14, 22, 245); color: {C.TEXT};
                     border: 1px solid {C.BORDER_B}; padding: 4px; }}
            QMenu::item {{ padding: 5px 18px; border-radius: 6px; }}
            QMenu::item:selected {{ background: {C.PRI_GHO}; color: {C.PRI}; }}
            QMenu::separator {{ height: 1px; background: {C.BORDER}; margin: 4px 6px; }}
        """)
        a_exp = m.addAction("Expand  (double-click)")
        m.addSeparator()
        a_mute = m.addAction(
            "Unmute microphone  [F4]" if self._muted else "Mute microphone  [F4]"
        )
        a_stop = m.addAction("Stop speaking  [Esc]")
        m.addSeparator()
        a_top = m.addAction("Always on top")
        a_top.setCheckable(True)
        a_top.setChecked(self._mini_on_top)
        sub = m.addMenu("Opacity")
        op_map = {}
        for pct in (100, 90, 80, 70, 60):
            op_map[sub.addAction(f"{pct}%")] = pct

        act = m.exec(gpos)
        if act is None:
            return
        if act is a_exp:
            self.set_mini(False)
        elif act is a_mute:
            self._toggle_mute()
        elif act is a_stop:
            self._do_interrupt()
        elif act is a_top:
            self._mini_on_top = a_top.isChecked()
            QTimer.singleShot(
                0, lambda: self._reflag(self._mini_flags(), True, self.geometry())
            )
        elif act in op_map:
            opacity = op_map[act] / 100.0
            self.setWindowOpacity(opacity)
            _save_cfg(mini_opacity=opacity)

    def closeEvent(self, e):
        if self._mini:
            self._save_mini_geom()
        if self._dock is not None:
            self._dock.close()
        super().closeEvent(e)

    # --- Live camera stream in HUD area ------------------------------------
    def _on_cam_stream(self, start: bool) -> None:
        if start:
            self._hud_cam_stack.setCurrentIndex(1)
        else:
            self._hud_cam_stack.setCurrentIndex(0)
            self._cam_live_lbl.clear()

    def _on_cam_frame(self, data: bytes) -> None:
        px = QPixmap()
        px.loadFromData(data)
        if not px.isNull():
            w, h = self._cam_live_lbl.width(), self._cam_live_lbl.height()
            if w > 1 and h > 1:
                self._cam_live_lbl.setPixmap(
                    px.scaled(
                        w,
                        h,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )

    def start_camera_stream(self) -> None:
        self._cam_stop.clear()
        self._cam_stream_sig.emit(True)
        t = threading.Thread(target=self._cam_loop, daemon=True, name="cam-stream")
        t.start()

    def _cam_loop(self) -> None:
        try:
            import cv2

            # Reuse camera index detected by screen_processor (cached in api_keys.json)
            cam_idx = 0
            try:
                import json as _j

                cfg = _j.loads((CONFIG_DIR / "api_keys.json").read_text())
                cam_idx = int(cfg.get("camera_index", 0))
            except Exception:
                pass
            try:
                backend = cv2.CAP_DSHOW if _OS == "Windows" else cv2.CAP_ANY
            except AttributeError:
                backend = 0
            cap = cv2.VideoCapture(cam_idx, backend)
            if not cap.isOpened():
                cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return
            # warm-up frames
            for _ in range(5):
                cap.read()
            while not self._cam_stop.wait(0.033) and cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                    self._cam_frame_sig.emit(buf.tobytes())
            cap.release()
        except Exception as e:
            print(f"[Camera] Stream error: {e}")
        finally:
            self._cam_stream_sig.emit(False)

    def stop_camera_stream(self) -> None:
        self._cam_stop.set()

    # --- Video in the HUD area ---------------------------------------------
    #
    # Everything below runs on the Qt thread. Plugins and the assistant reach it
    # through the signals above, the same way every other panel is driven, so a
    # background thread never touches a widget.
    def _on_video_open(
        self, source: str, title: str, muted: bool, audio_source: str = ""
    ) -> None:
        if not HAVE_VIDEO or not self._video_player:
            self._log.append_log(
                "SYS: Video playback is not available in this Qt " "install."
            )
            return
        # A video and the live camera cannot share the centre of the HUD.
        self._cam_stop.set()

        self._video_title.setText(f"▶  {(title or 'VIDEO')[:44].upper()}")
        self._video_split = bool(audio_source)
        self._set_video_muted(bool(muted))

        url = QUrl.fromLocalFile(source) if Path(source).exists() else QUrl(source)
        self._video_player.setSource(url)
        if self._video_split:
            self._video_sound.setSource(QUrl(audio_source))
        self._hud_cam_stack.setCurrentIndex(2)
        self._video_on = True
        # Re-run now that the video counts as playing: _set_video_muted ran
        # before this line and saw no video, so its mic check was a no-op.
        self._sync_mic_for_video()
        self._video_player.play()
        if self._video_split:
            self._video_sound.play()
            self._video_sync.start()

    def _fit_video(self, *_a) -> None:
        """Size the picture to the panel, keeping its shape."""
        if not (self._video_item and self._video_widget):
            return
        try:
            native = self._video_item.nativeSize()
            if native.isEmpty():
                return
            view = self._video_widget.viewport().size()
            scale = min(view.width() / native.width(), view.height() / native.height())
            w, h = native.width() * scale, native.height() * scale
            self._video_item.setSize(QSizeF(w, h))
            self._video_scene.setSceneRect(0, 0, w, h)
            self._video_widget.centerOn(self._video_item)
        except Exception:
            pass

    def _on_video_close(self) -> None:
        if self._video_sync:
            self._video_sync.stop()
        for p in (self._video_player, self._video_sound):
            if p:
                p.stop()
                p.setSource(QUrl())
        self._video_split = False
        self._video_on = False
        self._hud_cam_stack.setCurrentIndex(0)
        self._sync_mic_for_video()  # gives the microphone back

    def _set_video_muted(self, muted: bool) -> None:
        """Silence whichever output is carrying the sound for this video."""
        muted = bool(muted)
        if self._video_audio:
            # When the sound is a separate stream this player has none, but
            # muting it too costs nothing and keeps the two paths identical.
            self._video_audio.setMuted(muted)
        if self._video_sound_out:
            self._video_sound_out.setMuted(muted)
        self._sync_video_mute_btn()
        self._sync_mic_for_video()

    def _on_video_mute(self, muted: bool) -> None:
        self._set_video_muted(muted)

    def _sync_mic_for_video(self) -> None:
        """Close the microphone while the video is making sound.

        JARVIS subtracts its OWN output from the microphone — that is what
        core/echo.py does — but a video plays through a different output
        entirely, so the guard has never heard of it and the assistant answers
        the film. There is no arrangement in which an open microphone and a
        loudspeaker in the same room do not do that.

        So the microphone closes for exactly as long as the sound is on, and
        opens again by itself the moment it goes off or the video is closed.
        Only if JARVIS closed it: a microphone the user muted themselves stays
        muted, and pressing the mute key during a video hands the decision back
        to them for good.
        """
        sound_on = bool(
            self._video_on
            and self._video_sound_out
            and not self._video_sound_out.isMuted()
        )
        if sound_on and not self._muted:
            self._video_auto_muted = True
            self._set_muted(
                True,
                "The video's sound is on — silence it, close "
                "it, or press F4 to talk. Typing still works.",
            )
        elif not sound_on and self._video_auto_muted:
            self._video_auto_muted = False
            self._set_muted(False, "The video is quiet again.")

    def _sync_video_sound(self) -> None:
        """Keep the separate soundtrack in step with the picture.

        Two players started at the same moment do not stay together: they buffer
        independently, and measured on a real stream they were about half a
        second apart after nine seconds. So the sound is nudged back to the
        picture whenever it drifts far enough to hear — and only then, because
        correcting a smaller gap is itself audible.
        """
        if not (self._video_split and self._video_sound and self._video_player):
            return
        try:
            if (
                self._video_player.playbackState()
                != QMediaPlayer.PlaybackState.PlayingState
            ):
                return
            drift = self._video_sound.position() - self._video_player.position()
            if abs(drift) > 300:
                self._video_sound.setPosition(self._video_player.position())
        except Exception:
            pass

    def _sync_video_mute_btn(self) -> None:
        out = self._video_sound_out if self._video_split else self._video_audio
        muted = bool(out and out.isMuted())
        self._video_mute_btn.setText("🔇  SOUND OFF" if muted else "🔊  SOUND ON")

    def _toggle_video_mute(self) -> None:
        out = self._video_sound_out if self._video_split else self._video_audio
        if out:
            self._set_video_muted(not out.isMuted())

    def _on_video_error(self, *_a) -> None:
        err = ""
        try:
            err = self._video_player.errorString()
        except Exception:
            pass
        self._log.append_log(
            f"SYS: The video could not be played{(' — ' + err) if err else ''}."
        )
        self._on_video_close()

    def stop_video(self) -> None:
        self._video_close_sig.emit()

    def video_is_playing(self) -> bool:
        return bool(self._video_on)

    # ------------------------------------------------------------------
    # Icon generation — arc-reactor style, rendered with Pillow
    # ------------------------------------------------------------------
    @staticmethod
    def _build_jarvis_icon(out_path: Path) -> bool:
        """
        Render a JARVIS arc-reactor icon at 4× resolution and downsample
        for crisp results at all sizes. Saves a multi-res .ico to out_path.
        Returns True on success.
        """
        try:
            import math
            import PIL.Image
            import PIL.ImageDraw
            import PIL.ImageFilter
        except ImportError:
            return False

        CYAN = (0, 212, 255)
        DIM = (0, 100, 140)
        DARK = (0, 6, 10)
        GLOW = (0, 160, 200)
        WHITE = (220, 240, 255)

        def _render(sz: int) -> PIL.Image.Image:
            S = sz * 4  # draw at 4× then downscale
            img = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            d = PIL.ImageDraw.Draw(img)
            cx = cy = S // 2

            # ── filled background circle ──────────────────────────────────
            R = S // 2 - 2
            d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=(*DARK, 255))

            # ── outer border ring ─────────────────────────────────────────
            lw = max(2, S // 40)
            d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=(*CYAN, 220), width=lw)

            # ── mid decorative ring ───────────────────────────────────────
            R2 = int(R * 0.72)
            d.ellipse(
                [cx - R2, cy - R2, cx + R2, cy + R2],
                outline=(*DIM, 180),
                width=max(1, lw // 2),
            )

            # ── 6 radial spokes (hex bolt) ────────────────────────────────
            R_inner = int(R * 0.30)
            R_outer = int(R * 0.62)
            spoke_w = max(1, S // 80)
            for i in range(6):
                angle = math.radians(i * 60 - 30)
                x1 = cx + int(R_inner * math.cos(angle))
                y1 = cy + int(R_inner * math.sin(angle))
                x2 = cx + int(R_outer * math.cos(angle))
                y2 = cy + int(R_outer * math.sin(angle))
                d.line([x1, y1, x2, y2], fill=(*GLOW, 200), width=spoke_w)

            # ── 6 tick marks on outer ring ────────────────────────────────
            for i in range(6):
                angle = math.radians(i * 60)
                for dr in range(lw * 2):
                    rx = R - lw - dr
                    d.point(
                        [
                            cx + int(rx * math.cos(angle)),
                            cy + int(rx * math.sin(angle)),
                        ],
                        fill=(*WHITE, 220),
                    )

            # ── inner glowing ring ────────────────────────────────────────
            Ri = int(R * 0.26)
            d.ellipse(
                [cx - Ri, cy - Ri, cx + Ri, cy + Ri],
                outline=(*CYAN, 255),
                width=max(2, lw),
            )

            # ── bright glow soft blur applied before core ─────────────────
            # (draw a slightly larger cyan circle on a separate layer)
            glow_layer = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            gd = PIL.ImageDraw.Draw(glow_layer)
            Rc = int(R * 0.13)
            gd.ellipse(
                [cx - Rc * 2, cy - Rc * 2, cx + Rc * 2, cy + Rc * 2], fill=(*CYAN, 110)
            )
            glow_layer = glow_layer.filter(PIL.ImageFilter.GaussianBlur(S // 14))
            img = PIL.Image.alpha_composite(img, glow_layer)
            d = PIL.ImageDraw.Draw(img)

            # ── core dot ──────────────────────────────────────────────────
            d.ellipse([cx - Rc, cy - Rc, cx + Rc, cy + Rc], fill=(*WHITE, 255))

            # ── downscale to target size ──────────────────────────────────
            return img.resize((sz, sz), PIL.Image.LANCZOS)

        try:
            sizes = [256, 128, 64, 48, 32, 16]
            frames = [_render(s) for s in sizes]
            frames[0].save(
                out_path,
                format="ICO",
                append_images=frames[1:],
                sizes=[(s, s) for s in sizes],
            )
            return True
        except Exception as e:
            print(f"[Shortcut] ⚠️  Icon generation failed: {e}")
            return False

    @staticmethod
    def _create_lnk_windows(
        lnk: str, target: str, args: str, work_dir: str, icon_loc: str
    ) -> None:
        """
        Create a Windows .lnk shortcut WITHOUT launching PowerShell or cmd.
        Tries win32com (pywin32) first; falls back to wscript.exe + VBScript.
        wscript.exe is a GUI-mode host — it never opens a console window.
        """
        # ── Option 1: pywin32 (pure Python COM, zero subprocess) ──────────
        try:
            from win32com.client import Dispatch  # type: ignore

            sh = Dispatch("WScript.Shell")
            sc = sh.CreateShortCut(lnk)
            sc.TargetPath = target
            sc.Arguments = f'"{args}"'
            sc.WorkingDirectory = work_dir
            sc.Description = "J.A.R.V.I.S AI Assistant"
            sc.IconLocation = icon_loc
            sc.save()
            return
        except ImportError:
            pass

        # ── Option 2: wscript.exe + VBScript (always available on Windows,
        #    GUI-mode executable — never opens a console window) ────────────
        vbs = "\n".join(
            [
                'Set ws = CreateObject("WScript.Shell")',
                f'Set sc = ws.CreateShortcut("{lnk}")',
                f'sc.TargetPath = "{target}"',
                f'sc.Arguments = Chr(34) & "{args}" & Chr(34)',
                f'sc.WorkingDirectory = "{work_dir}"',
                'sc.Description = "J.A.R.V.I.S AI Assistant"',
                f'sc.IconLocation = "{icon_loc}"',
                "sc.Save",
            ]
        )
        import tempfile

        fd, tmp = tempfile.mkstemp(suffix=".vbs")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(vbs)
            proc = subprocess.Popen(
                ["wscript.exe", "/nologo", tmp],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
            proc.wait(timeout=10)
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    @staticmethod
    def _get_desktop_dir() -> Path:
        """
        Resolve the user's REAL desktop directory instead of assuming
        ~/Desktop, which breaks when:
          • OneDrive "Known Folder Move" relocates the desktop
            (C:/Users/x/OneDrive/Desktop) — very common on Win 10/11;
          • the XDG desktop is localized on Linux (~/Masaüstü,
            ~/Schreibtisch, ~/Bureau, …).
        Falls back to ~/Desktop only as a last resort.
        """
        home = Path.home()
        _os = platform.system()

        if _os == "Windows":
            # ── 1) SHGetKnownFolderPath(FOLDERID_Desktop) — the canonical
            #       answer; follows OneDrive redirection. No dependencies. ──
            try:
                import ctypes
                from ctypes import wintypes

                class _GUID(ctypes.Structure):
                    _fields_ = [
                        ("Data1", wintypes.DWORD),
                        ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD),
                        ("Data4", ctypes.c_ubyte * 8),
                    ]

                # FOLDERID_Desktop {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
                fid = _GUID(
                    0xB4BFCC3A,
                    0xDB2C,
                    0x424C,
                    (ctypes.c_ubyte * 8)(
                        0xB0, 0x29, 0x7F, 0xE9, 0x9A, 0x87, 0xC6, 0x41
                    ),
                )
                buf = ctypes.c_wchar_p()
                if (
                    ctypes.windll.shell32.SHGetKnownFolderPath(
                        ctypes.byref(fid), 0, None, ctypes.byref(buf)
                    )
                    == 0
                ):
                    p = Path(buf.value)
                    ctypes.windll.ole32.CoTaskMemFree(buf)
                    if p.is_dir():
                        return p
            except Exception:
                pass

            # ── 2) Registry: User Shell Folders (may contain %VARS%) ──────
            try:
                import winreg

                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion"
                    r"\Explorer\User Shell Folders",
                ) as key:
                    val, _t = winreg.QueryValueEx(key, "Desktop")
                p = Path(os.path.expandvars(val))
                if p.is_dir():
                    return p
            except Exception:
                pass

        elif _os == "Linux":
            # ── xdg-user-dir honours localized names (~/Masaüstü, …) ──────
            try:
                out = subprocess.run(
                    ["xdg-user-dir", "DESKTOP"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                p = Path(out.stdout.strip())
                if out.stdout.strip() and p != home and p.is_dir():
                    return p
            except Exception:
                pass
            try:
                cfg = home / ".config" / "user-dirs.dirs"
                for line in cfg.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("XDG_DESKTOP_DIR"):
                        val = line.split("=", 1)[1].strip().strip('"')
                        p = Path(val.replace("$HOME", str(home)))
                        if p != home and p.is_dir():
                            return p
            except Exception:
                pass

        # macOS: ~/Desktop is always the real path (localization is
        # display-only). Everything else lands here as a last resort.
        return home / "Desktop"

    def _create_desktop_shortcut(self):
        """
        Create a desktop shortcut on Windows / macOS / Linux.
        Never opens a terminal, console, or PowerShell window on any platform.
        """
        import stat as _stat

        script = Path(__file__).resolve().parent / "main.py"
        python = Path(sys.executable)
        desktop = self._get_desktop_dir()

        # Arc-reactor icon (.ico — also exported as .png for Linux/macOS)
        ico_path = Path(__file__).resolve().parent / "config" / "jarvis.ico"
        if not ico_path.exists():
            self._build_jarvis_icon(ico_path)

        try:
            _os = platform.system()

            # ── Windows ───────────────────────────────────────────────────────
            if _os == "Windows":
                pythonw = python.parent / "pythonw.exe"
                target = str(pythonw if pythonw.exists() else python)
                lnk = str(desktop / "J.A.R.V.I.S.lnk")
                icon_loc = str(ico_path) if ico_path.exists() else f"{target},0"
                self._create_lnk_windows(
                    lnk, target, str(script), str(script.parent), icon_loc
                )

            # ── macOS — proper .app bundle (no Terminal window) ───────────────
            elif _os == "Darwin":
                app = desktop / "J.A.R.V.I.S.app"
                mac_dir = app / "Contents" / "MacOS"
                res_dir = app / "Contents" / "Resources"
                mac_dir.mkdir(parents=True, exist_ok=True)
                res_dir.mkdir(exist_ok=True)

                # Launcher executable (bash — runs as background process,
                # macOS does NOT open Terminal for executables inside .app bundles)
                launcher = mac_dir / "JARVIS"
                launcher.write_text(
                    "#!/usr/bin/env bash\n"
                    f'cd "{script.parent}"\n'
                    f'exec "{python}" "{script}"\n'
                )
                launcher.chmod(
                    launcher.stat().st_mode
                    | _stat.S_IEXEC
                    | _stat.S_IXGRP
                    | _stat.S_IXOTH
                )

                # Minimal Info.plist (required for .app recognition)
                (app / "Contents" / "Info.plist").write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict>\n'
                    "  <key>CFBundleExecutable</key><string>JARVIS</string>\n"
                    "  <key>CFBundleIdentifier</key>"
                    "<string>com.jarvis.assistant</string>\n"
                    "  <key>CFBundleName</key><string>J.A.R.V.I.S</string>\n"
                    "  <key>CFBundlePackageType</key><string>APPL</string>\n"
                    "  <key>CFBundleVersion</key><string>1.0</string>\n"
                    "</dict></plist>\n"
                )

                # Optional: copy icon as .icns (skip silently if Pillow is missing)
                try:
                    import PIL.Image

                    icns = res_dir / "AppIcon.icns"
                    PIL.Image.open(ico_path).save(icns, format="ICNS")
                    # Inject icon reference into plist
                    plist = app / "Contents" / "Info.plist"
                    txt = plist.read_text()
                    plist.write_text(
                        txt.replace(
                            "</dict></plist>",
                            "  <key>CFBundleIconFile</key>"
                            "<string>AppIcon</string>\n</dict></plist>\n",
                        )
                    )
                except Exception:
                    pass  # icon is optional

            # ── Linux — .desktop file (Terminal=false, no console) ────────────
            else:
                # Export .ico → .png for better desktop integration
                png_path = ico_path.with_suffix(".png")
                if not png_path.exists() and ico_path.exists():
                    try:
                        import PIL.Image

                        PIL.Image.open(ico_path).resize(
                            (256, 256), PIL.Image.LANCZOS
                        ).save(png_path, format="PNG")
                    except Exception:
                        png_path = ico_path  # fallback to .ico

                icon_line = f"Icon={png_path}\n" if png_path.exists() else ""
                desk = desktop / "J.A.R.V.I.S.desktop"
                desk.write_text(
                    "[Desktop Entry]\n"
                    "Name=J.A.R.V.I.S\n"
                    f"Exec={python} {script}\n"
                    f"Path={script.parent}\n"
                    "Type=Application\n"
                    "Terminal=false\n"
                    "Categories=Utility;\n" + icon_line
                )
                desk.chmod(desk.stat().st_mode | 0o755)

            self._log.append_log("SYS: Desktop shortcut created.")
        except Exception as e:
            self._log.append_log(f"ERR: Shortcut failed — {e}")

    def _toggle_fullscreen(self):
        if self._mini:
            return
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cw = self.centralWidget()
        if self._overlay and self._overlay.isVisible():
            ow, oh = 460, 390
            self._overlay.setGeometry(
                (cw.width() - ow) // 2,
                (cw.height() - oh) // 2,
                ow,
                oh,
            )
        if self._remote_overlay and self._remote_overlay.isVisible():
            ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
            self._remote_overlay.setGeometry(
                (cw.width() - ow) // 2,
                (cw.height() - oh) // 2,
                ow,
                oh,
            )
        if self._customize_overlay and self._customize_overlay.isVisible():
            ow, oh = CustomizeOverlay._OW, self._customize_overlay.height()
            self._customize_overlay.setGeometry(
                (cw.width() - ow) // 2,
                (cw.height() - oh) // 2,
                ow,
                oh,
            )
        # Camera preview — bottom-right corner of the center/HUD area
        pw = _CameraPreview._W
        ph = self._cam_preview.height() or _CameraPreview._H
        self._cam_preview.setGeometry(
            cw.width() - self._right_inset() - pw - 12,
            cw.height() - ph - 28,
            pw,
            ph,
        )
        # Clipboard panel — bottom-center
        if hasattr(self, "_clipboard_panel") and self._clipboard_panel.isVisible():
            self._position_clipboard_panel()
        # Drawers stay centred under their own trigger buttons.
        if hasattr(self, "_quick_drawer") and self._quick_drawer.isVisible():
            self._position_quick_drawer()
        if hasattr(self, "_ctrl_drawer") and self._ctrl_drawer.isVisible():
            self._position_ctrl_drawer()
        # Stretch the header accent line with the real widget width.
        try:
            accent = self.findChild(QFrame, "HeaderAccent")
            if accent is not None:
                accent.setGeometry(
                    0, getattr(self, "_header_height_px", 72) - 1, self.width(), 1
                )
        except Exception:
            pass
        self._fit_video()
        if hasattr(self, "_body_split"):
            self._adapt_header()
            self._auto_collapse()
            self._layout_toasts()
            if self._palette is not None:
                self._palette.relayout()
            if not self._split_inited and self.isVisible():
                self._split_inited = True
                QTimer.singleShot(0, self._apply_split_sizes)

    def _update_metrics(self):
        # Nobody can see the monitor tiles in mini mode, while minimised, or
        # with the sidebar closed or showing another page, so don't spend
        # repaints on them.
        if (
            self._mini
            or self.isMinimized()
            or not self._left_panel.isVisible()
            or self._left_stack.currentIndex() != 0
        ):
            return
        snap = _metrics.snapshot()

        # CPU
        cpu = snap["cpu"]
        self._bar_cpu.set_value(cpu, f"{cpu:.0f}%")

        # MEM
        mem = snap["mem"]
        self._bar_mem.set_value(mem, f"{mem:.0f}%")

        self._load_graph.push(cpu, mem)

        # NET
        net = snap["net"]
        if net < 1.0:
            net_str = f"{net*1024:.0f}KB/s"
        else:
            net_str = f"{net:.1f}MB/s"
        net_pct = min(100, net * 10)  # 10 MB/s = %100
        self._bar_net.set_value(net_pct, net_str)

        # GPU
        gpu = snap["gpu"]
        if gpu >= 0:
            self._bar_gpu.set_value(gpu, f"{gpu:.0f}%")
        else:
            self._bar_gpu.set_value(0, "N/A")

        # TMP — a line in the list now, not a gauge
        tmp = snap["tmp"]
        if tmp >= 0:
            self._temp_lbl.setText(f"{tmp:.0f}°C")
            self._temp_lbl.setStyleSheet(
                f"color: {C.RED if tmp > 85 else (C.ACC if tmp > 70 else C.TEXT_MED)};"
                " background: transparent;"
            )
        else:
            self._temp_lbl.setText("N/A")

        try:
            boot_t = psutil.boot_time()
            elapsed = time.time() - boot_t
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            self._uptime_lbl.setText(f"{h:02d}:{m:02d}")
        except Exception:
            self._uptime_lbl.setText("--:--")

        try:
            proc_count = len(psutil.pids())
            self._proc_lbl.setText(str(proc_count))
        except Exception:
            self._proc_lbl.setText("--")

    def _build_header(self) -> QWidget:
        self._header_height_px = 54
        w = _GlassHeader()
        w.setFixedHeight(self._header_height_px)
        w.setObjectName("JarvisHeader")

        lay = QHBoxLayout(w)
        lay.setContentsMargins(14, 0, 14, 0)
        lay.setSpacing(10)

        # LEFT — brand mark, product name, status. The left and right zones
        # share the stretch equally, so the title stays centred at any width.
        left = QWidget()
        ll = QHBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(10)
        ll.addWidget(_OrbIcon(30), 0, Qt.AlignmentFlag.AlignVCenter)

        self._hdr_brand = QWidget()
        brand = QVBoxLayout(self._hdr_brand)
        brand.setSpacing(0)
        brand.setContentsMargins(0, 0, 0, 0)
        logo = QLabel("PERSONAL AI SYSTEM")
        lf = QFont(_DISPLAY, 8, QFont.Weight.Bold)
        lf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
        logo.setFont(lf)
        logo.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        brand.addWidget(logo)
        sub = QLabel(f"PROTOCOL {APP_PROTOCOL.upper()}")
        sub.setFont(QFont(_FONT, 8))
        sub.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        brand.addWidget(sub)
        ll.addWidget(self._hdr_brand)

        self._hdr_pill = QLabel("●  ONLINE")
        self._hdr_pill.setFont(QFont(_DISPLAY, 7, QFont.Weight.Bold))
        self._hdr_pill.setFixedHeight(22)
        self._hdr_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hdr_pill.setStyleSheet(f"""
            QLabel {{
                color: {C.GREEN};
                background: rgba(0, 255, 136, 24);
                border: 1px solid rgba(0, 255, 136, 80);
                border-radius: 11px;
                padding: 0 11px;
            }}
        """)
        ll.addWidget(self._hdr_pill, 0, Qt.AlignmentFlag.AlignVCenter)
        ll.addStretch(1)
        lay.addWidget(left, 1)

        # CENTER — assistant name + tagline
        center = QVBoxLayout()
        center.setSpacing(0)
        center.setContentsMargins(0, 0, 0, 0)
        center.addStretch(1)
        self._title_lbl = QLabel(self._assistant_name.upper())
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tf = QFont(_DISPLAY, 14, QFont.Weight.Bold)
        tf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 5.0)
        self._title_lbl.setFont(tf)
        self._title_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        center.addWidget(self._title_lbl)
        _disp = self._assistant_name.upper()
        _sub_text = (
            "PERSONAL INTELLIGENCE CORE"
            if _disp in ("JARVIS", "J.A.R.V.I.S")
            else "PERSONAL AI CORE"
        )
        self._sub_lbl = QLabel(_sub_text)
        self._sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub_lbl.setFont(QFont(_FONT, 8))
        self._sub_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        center.addWidget(self._sub_lbl)
        center.addStretch(1)
        lay.addLayout(center)

        # RIGHT — tools + clock
        right = QWidget()
        rl = QHBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(10)
        rl.addStretch(1)

        def hbtn(
            sym: str, tip: str, checkable: bool = False, size: int = 13
        ) -> QPushButton:
            b = QPushButton(sym)
            b.setFixedSize(34, 30)
            b.setFont(QFont("Segoe UI Symbol", size))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setCheckable(checkable)
            b.setToolTip(tip)
            b.setStyleSheet(self._header_button_style())
            return b

        nav = QHBoxLayout()
        nav.setSpacing(6)
        self._pal_btn = hbtn("⌘", "Command palette  [Ctrl+K]")
        self._pal_btn.clicked.connect(lambda _=False: self._open_palette())
        nav.addWidget(self._pal_btn)
        self._mini_btn = hbtn("▭", "Mini orb window  [F9]")
        self._mini_btn.clicked.connect(lambda _=False: self.set_mini(True))
        nav.addWidget(self._mini_btn)
        self._drawer_btn = hbtn("⚙", "Settings", True)
        self._drawer_btn.clicked.connect(self._toggle_drawer)
        nav.addWidget(self._drawer_btn)
        rl.addLayout(nav)

        self._hdr_clock = QWidget()
        cc = QVBoxLayout(self._hdr_clock)
        cc.setSpacing(0)
        cc.setContentsMargins(0, 0, 0, 0)
        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(QFont(_MONO, 13, QFont.Weight.Bold))
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._clock_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        cc.addWidget(self._clock_lbl)
        self._date_lbl = QLabel("")
        self._date_lbl.setFont(QFont(_FONT, 7))
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._date_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        cc.addWidget(self._date_lbl)
        rl.addWidget(self._hdr_clock)
        lay.addWidget(right, 1)
        return w

    def _adapt_header(self) -> None:
        """Shed the least important header parts as the window narrows."""
        w = self.width()
        self._hdr_brand.setVisible(w >= 900)
        self._hdr_pill.setVisible(w >= 1000)
        self._hdr_clock.setVisible(w >= 820)
        self._sub_lbl.setVisible(w >= 640)

    def _header_button_style(self) -> str:
        return f"""
            QPushButton {{
                color: {C.TEXT_MED};
                background: rgba(255, 255, 255, 12);
                border: 1px solid rgba(255, 255, 255, 30);
                border-radius: 12px;
            }}
            QPushButton:hover {{
                color: {C.PRI};
                border-color: {C.PRI_DIM};
                background: rgba(255, 255, 255, 24);
            }}
            QPushButton:checked {{
                color: {C.PRI};
                border-color: {C.PRI};
                background: {C.PRI_GHO};
            }}
        """

    def _tick_clock(self):
        if self._mini:
            return
        self._clock_lbl.setText(time.strftime("%H:%M:%S"))
        self._date_lbl.setText(time.strftime("%a %d %b %Y"))
        self._update_session()

    def _panel_header(
        self, text: str, tag: str = "", live: bool = False
    ) -> QHBoxLayout:
        """Section header: accent bar, title, optional chip on the right."""
        row = QHBoxLayout()
        row.setSpacing(8)
        row.setContentsMargins(2, 0, 2, 0)
        bar = QFrame()
        bar.setFixedSize(3, 12)
        bar.setStyleSheet(f"background: {C.PRI}; border: none; border-radius: 1px;")
        row.addWidget(bar)
        t = QLabel(text)
        t.setFont(QFont(_FONT, 9, QFont.Weight.Bold))
        t.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        row.addWidget(t)
        row.addStretch(1)
        if tag:
            chip = QLabel(tag)
            chip.setFont(QFont(_FONT, 8, QFont.Weight.Bold))
            if live:
                chip.setStyleSheet(f"""
                    QLabel {{ color: {C.GREEN}; background: rgba(0, 255, 136, 26);
                        border: 1px solid rgba(0, 255, 136, 80);
                        border-radius: 9px; padding: 1px 9px; }}
                """)
            else:
                chip.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            row.addWidget(chip)
        return row

    def _build_left_panel(self) -> QWidget:
        """Left sidebar: pages picked from the left rail (VS Code's activity
        bar). 0 = system monitor, 1 = tools (timer, tasks, quick actions)."""
        w = QWidget()
        w.setObjectName("SystemPanel")
        w.setMinimumWidth(176)
        w.setMaximumWidth(340)
        w.setStyleSheet(
            "QWidget#SystemPanel { background: rgba(3, 12, 20, 110); "
            "border-right: 1px solid rgba(255, 255, 255, 20); }"
        )
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        self._left_stack = QStackedWidget()
        self._left_stack.addWidget(self._build_monitor_page())
        self._left_stack.addWidget(self._build_tools_page())
        lay.addWidget(self._left_stack)
        return w

    def _build_monitor_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 12, 10, 10)
        lay.setSpacing(8)
        lay.addLayout(self._panel_header("SYSTEM MONITOR"))

        # Four round gauges, two by two. Temperature moved into the list below:
        # most Windows machines cannot report it, and a whole gauge reading
        # "N/A" is a bad use of a quarter of the panel.
        self._bar_cpu = RingGauge("CPU", "PRI")
        self._bar_mem = RingGauge("MEMORY", "ACC2")
        self._bar_net = RingGauge("NETWORK", "GREEN")
        self._bar_gpu = RingGauge("GPU", "ACC")
        self._bar_tmp = None
        self._gauges = [self._bar_cpu, self._bar_mem, self._bar_net, self._bar_gpu]
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        grid.addWidget(self._bar_cpu, 0, 0)
        grid.addWidget(self._bar_mem, 0, 1)
        grid.addWidget(self._bar_net, 1, 0)
        grid.addWidget(self._bar_gpu, 1, 1)
        lay.addLayout(grid)
        self._load_graph = _LoadGraph()
        lay.addWidget(self._load_graph)
        # Spare height goes here, not into the header row above the gauges.
        lay.addStretch(1)

        card = QFrame()
        card.setObjectName("SysCard")
        card.setStyleSheet(_glass_css("SysCard"))
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 10, 14, 10)
        cl.setSpacing(5)

        def kv(key: str, val: str, col: str, mono: bool = True) -> QLabel:
            h = QHBoxLayout()
            h.setSpacing(6)
            k = QLabel(key)
            k.setFont(QFont(_FONT, 8))
            k.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            v = QLabel(val)
            v.setFont(QFont(_MONO if mono else _FONT, 9, QFont.Weight.Bold))
            v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            v.setStyleSheet(f"color: {col}; background: transparent;")
            h.addWidget(k)
            h.addStretch(1)
            h.addWidget(v)
            cl.addLayout(h)
            return v

        self._uptime_lbl = kv("UPTIME", "--:--", C.GREEN)
        self._proc_lbl = kv("PROCESSES", "--", C.TEXT_MED)
        self._temp_lbl = kv("CPU TEMP", "N/A", C.TEXT_MED)
        os_name = {"Windows": "Windows", "Darwin": "macOS", "Linux": "Linux"}.get(
            _OS, _OS
        )
        kv("SYSTEM", os_name, C.ACC2, mono=False)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(255, 255, 255, 22); border: none;")
        cl.addWidget(sep)

        kv("AI CORE", "● ACTIVE", C.GREEN)
        kv("SECURITY", "● CLEARED", C.PRI)
        kv("PROTOCOL", APP_PROTOCOL, C.TEXT_MED)
        lay.addWidget(card)

        # One timer animates all four gauges (ease + scanner), and does nothing
        # at all while the monitor cannot be seen.
        self._gauge_tmr = QTimer(self)
        self._gauge_tmr.setInterval(33)
        self._gauge_tmr.timeout.connect(self._tick_gauges)
        self._gauge_tmr.start()
        return w

    def _tick_gauges(self) -> None:
        if (
            self._mini
            or self.isMinimized()
            or not self._left_panel.isVisible()
            or self._left_stack.currentIndex() != 0
        ):
            return
        for g in self._gauges:
            g.tick()

    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        w.setMinimumWidth(280)
        w.setMaximumWidth(640)
        w.setObjectName("CommandPanel")
        w.setStyleSheet(
            "QWidget#CommandPanel { background: rgba(3, 12, 20, 110); "
            "border-left: 1px solid rgba(255, 255, 255, 20); }"
        )
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(7)

        _hl = self._panel_header("ACTIVITY LOG")
        for _t, _tip, _fn in (
            ("COPY", "Copy the log to the clipboard", self._copy_log),
            ("CLEAR", "Clear the log", self._clear_log),
        ):
            _hl.addWidget(self._flat_btn(_t, _tip, _fn))
        lay.addLayout(_hl)
        self._log = LogWidget()
        self._log.setMinimumHeight(90)
        self._log.setFont(QFont(_MONO, 9))
        self._log.setStyleSheet(f"""
            QTextEdit {{
                background: rgba(255, 255, 255, 10);
                color: {C.TEXT};
                border: 1px solid rgba(255, 255, 255, 26);
                border-radius: 14px;
                padding: 10px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: transparent; width: 7px; border: none; margin: 6px 2px 6px 0;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255, 255, 255, 50); border-radius: 3px; min-height: 24px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        """)
        lay.addWidget(self._log, stretch=1)

        lay.addSpacing(2)
        lay.addLayout(self._panel_header("FILE UPLOAD", "INPUT"))
        self._drop_zone = FileDropZone()
        self._drop_zone.setFixedHeight(96)
        self._drop_zone.file_selected.connect(self._on_file_selected)
        lay.addWidget(self._drop_zone)

        self._file_hint = QLabel("No file loaded — drop a file or browse above")
        self._file_hint.setFont(QFont(_FONT, 8))
        self._file_hint.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._file_hint.setWordWrap(True)  #
        lay.addWidget(self._file_hint)

        # Command input
        lay.addSpacing(2)
        lay.addLayout(self._panel_header("COMMAND INPUT", "VOICE + TEXT"))
        lay.addLayout(self._build_input_row())

        lay.addSpacing(4)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        self._interrupt_btn = QPushButton("■  STOP  [ESC]")
        self._interrupt_btn.setFixedHeight(40)
        self._interrupt_btn.setFont(QFont(_FONT, 9, QFont.Weight.Bold))
        self._interrupt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._interrupt_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 51, 85, 28);
                color: {C.RED};
                border: 1px solid rgba(255, 51, 85, 120);
                border-radius: 12px;
            }}
            QPushButton:hover {{ background: rgba(255, 51, 85, 52); border-color: {C.RED}; }}
            QPushButton:pressed {{ background: rgba(255, 51, 85, 72); }}
        """)
        self._interrupt_btn.clicked.connect(self._do_interrupt)
        btns.addWidget(self._interrupt_btn, 1)

        self._mute_btn = QPushButton()
        self._mute_btn.setFixedHeight(40)
        self._mute_btn.setFont(QFont(_FONT, 9, QFont.Weight.Bold))
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        btns.addWidget(self._mute_btn, 1)
        lay.addLayout(btns)
        return w

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        self._input = _CmdInput()
        self._input.setPlaceholderText(
            "Type a command or question…     ( / opens commands )"
        )
        self._input.setFont(QFont(_FONT, 10))
        self._input.setFixedHeight(42)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(255, 255, 255, 14);
                color: {C.WHITE};
                border: 1px solid rgba(255, 255, 255, 30);
                border-radius: 12px;
                padding: 0 14px;
                selection-background-color: {C.PRI_GHO};
            }}

            QLineEdit:hover {{
                border-color: {C.BORDER_B};
            }}

            QLineEdit:focus {{
                border-color: {C.PRI};
                background: rgba(255, 255, 255, 20);
            }}
        """)

        self._input.returnPressed.connect(self._send)
        row.addWidget(self._input, stretch=1)

        send = QPushButton("➜")
        send.setFixedSize(46, 42)
        send.setFont(QFont("Segoe UI Symbol", 14, QFont.Weight.Bold))
        send.setCursor(Qt.CursorShape.PointingHandCursor)

        send.setStyleSheet(f"""
            QPushButton {{
                background: {C.PRI_GHO};
                color: {C.PRI};
                border: 1px solid {C.PRI_DIM};
                border-radius: 12px;
            }}

            QPushButton:hover {{
                border-color: {C.PRI};
                background: rgba(255, 255, 255, 22);
            }}

            QPushButton:pressed {{
                background: rgba(255, 255, 255, 10);
            }}
        """)

        send.clicked.connect(self._send)
        row.addWidget(send)

        return row

    def _build_quick_drawer(self) -> QWidget:
        """Grouped, scrollable settings drawer anchored under the gear button."""
        btn_style = f"""
            QPushButton {{
                background: rgba(255, 255, 255, 14);
                color: {C.TEXT_MED};
                border: 1px solid {C.BORDER};
                border-radius: 8px;
                text-align: left;
                padding: 0 12px;
            }}
            QPushButton:hover {{
                color: {C.PRI};
                border-color: {C.PRI_DIM};
                background: {C.PRI_GHO};
            }}
        """
        primary = f"""
            QPushButton {{
                background: {C.PRI_GHO};
                color: {C.PRI};
                border: 1px solid {C.PRI_DIM};
                border-radius: 8px;
                text-align: left;
                padding: 0 12px;
            }}
            QPushButton:hover {{ border-color: {C.PRI}; }}
        """
        self._BTN_PRI, self._BTN_DIM = primary, btn_style

        w = QWidget(self.centralWidget())
        w.setObjectName("ModernDrawer")
        w.setStyleSheet(f"""
            QWidget#ModernDrawer {{
                background: rgba(4, 14, 22, 244);
                border: 1px solid rgba(255, 255, 255, 40);
                border-radius: 16px;
            }}
        """)
        w.hide()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(14, 13, 8, 13)
        outer.setSpacing(6)

        header = QLabel("SETTINGS")
        header.setFont(QFont(_FONT, 9, QFont.Weight.Bold))
        header.setStyleSheet(
            f"color: {C.PRI}; letter-spacing: 2px; background: transparent;"
        )
        outer.addWidget(header)
        sub = QLabel("Choose a category — changes are saved automatically")
        sub.setFont(QFont(_FONT, 8))
        sub.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        outer.addWidget(sub)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ background: transparent; width: 6px; border: none; }}
            QScrollBar::handle:vertical {{ background: rgba(255, 255, 255, 50);
                border-radius: 3px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        """)
        inner = QWidget()
        inner.setObjectName("DrawerInner")
        inner.setStyleSheet("QWidget#DrawerInner { background: transparent; }")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 2, 8, 2)
        lay.setSpacing(8)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        self._drawer_lay = lay

        def category(title: str, detail: str, open_now: bool = False):
            head = QToolButton()
            head.setText(title)
            head.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            head.setArrowType(
                Qt.ArrowType.DownArrow if open_now else Qt.ArrowType.RightArrow
            )
            head.setCheckable(True)
            head.setChecked(open_now)
            head.setCursor(Qt.CursorShape.PointingHandCursor)
            head.setFont(QFont(_FONT, 9, QFont.Weight.Bold))
            head.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            head.setStyleSheet(f"""
                QToolButton {{ color: {C.TEXT}; background: {C.PANEL2};
                    border: 1px solid {C.BORDER}; border-radius: 9px;
                    text-align: left; padding: 9px 10px; }}
                QToolButton:hover, QToolButton:checked {{ color: {C.PRI};
                    border-color: {C.PRI_DIM}; background: {C.PRI_GHO}; }}
            """)
            lay.addWidget(head)

            body = QWidget()
            body_lay = QVBoxLayout(body)
            body_lay.setContentsMargins(8, 1, 4, 4)
            body_lay.setSpacing(5)
            hint = QLabel(detail)
            hint.setWordWrap(True)
            hint.setFont(QFont(_FONT, 7))
            hint.setStyleSheet(
                f"color: {C.TEXT_DIM}; padding: 2px 3px; background: transparent;"
            )
            body_lay.addWidget(hint)
            body.setVisible(open_now)

            def toggle(checked: bool, b=body, h=head):
                b.setVisible(checked)
                h.setArrowType(
                    Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
                )
                QTimer.singleShot(0, self._position_quick_drawer)

            head.toggled.connect(toggle)
            lay.addWidget(body)
            return body_lay

        def action_row(parent, title, description, slot, closes=False):
            row = QWidget()
            row_lay = QVBoxLayout(row)
            row_lay.setContentsMargins(2, 2, 2, 2)
            row_lay.setSpacing(1)
            button = QPushButton(title)
            button.setFixedHeight(31)
            button.setFont(QFont(_FONT, 8, QFont.Weight.Bold))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(btn_style)
            if closes:
                button.clicked.connect(
                    lambda _=False, s=slot: (self._close_setup(), s())
                )
            else:
                button.clicked.connect(lambda _=False, s=slot: s())
            row_lay.addWidget(button)
            note = QLabel(description)
            note.setWordWrap(True)
            note.setFont(QFont(_FONT, 7))
            note.setStyleSheet(
                f"color: {C.TEXT_DIM}; padding: 0 4px 3px 4px; background: transparent;"
            )
            row_lay.addWidget(note)
            parent.addWidget(row)
            button.row = row
            return button

        def switch_row(parent, title, description, checked, changed):
            row = QFrame()
            row.setObjectName("SwitchRow")
            row.setStyleSheet(f"""
                QFrame#SwitchRow {{ background: rgba(255, 255, 255, 8);
                    border: 1px solid {C.BORDER}; border-radius: 8px; }}
                QFrame#SwitchRow QLabel {{ background: transparent; border: none; }}
                QCheckBox {{ color: {C.TEXT_MED}; font-weight: bold; spacing: 7px;
                    background: transparent; }}
                QCheckBox:disabled {{ color: {C.TEXT_DIM}; }}
                QCheckBox::indicator {{ width: 34px; height: 18px; border-radius: 9px;
                    background: #06131a; border: 1px solid {C.BORDER_B}; }}
                QCheckBox::indicator:checked {{ background: {C.GREEN_D};
                    border-color: {C.GREEN}; }}
            """)
            row_lay = QVBoxLayout(row)
            row_lay.setContentsMargins(9, 6, 9, 6)
            row_lay.setSpacing(1)
            top = QHBoxLayout()
            label = QLabel(title)
            label.setFont(QFont(_FONT, 8, QFont.Weight.Bold))
            label.setStyleSheet(f"color: {C.TEXT};")
            top.addWidget(label)
            top.addStretch(1)
            toggle = QCheckBox("ON" if checked else "OFF")
            toggle.setChecked(checked)
            toggle.setCursor(Qt.CursorShape.PointingHandCursor)
            top.addWidget(toggle)
            row_lay.addLayout(top)
            note = QLabel(description)
            note.setWordWrap(True)
            note.setFont(QFont(_FONT, 7))
            note.setStyleSheet(f"color: {C.TEXT_DIM};")
            row_lay.addWidget(note)
            toggle._note = note

            def on_toggle(value: bool, box=toggle):
                box.setText("ON" if value else "OFF")
                changed(value)

            toggle.toggled.connect(on_toggle)
            parent.addWidget(row)
            return toggle

        assistant = category(
            "✦  ASSISTANT & APPEARANCE",
            "Identity, visual style, and the devices JARVIS uses to listen and speak.",
            True,
        )
        action_row(
            assistant,
            "CUSTOMISE ASSISTANT   [F7]",
            "Name, voice, accent colour, and appearance.",
            self._open_customize,
            True,
        )
        action_row(
            assistant,
            "AUDIO DEVICES",
            "Choose the microphone and speakers.",
            self._open_audio_devices,
            True,
        )
        self._settings_hud_btn = action_row(
            assistant,
            "HUD STYLE",
            "Switch between the animated face and the particle orb.",
            self._toggle_hud_style,
        )

        voice = category(
            "◉  VOICE & INPUT",
            "Control when JARVIS listens and how you start a conversation.",
        )
        self._settings_wake_switch = switch_row(
            voice,
            "WAKE WORD",
            "Say “Hey Jarvis” to begin.",
            False,
            self._set_wake_from_switch,
        )
        self._settings_wake_sleep_btn = action_row(
            voice,
            "SLEEP / WAKE NOW",
            "Pause or resume wake-word listening.",
            self._tap_wake_manual,
        )
        self._settings_ptt_switch = switch_row(
            voice,
            "PUSH-TO-TALK",
            "Keeps the microphone closed until you hold the configured key.",
            False,
            self._set_ptt_from_switch,
        )

        system = category(
            "▣  SYSTEM & DAILY USE",
            "Startup behaviour and window controls.",
        )
        self._settings_autostart_switch = switch_row(
            system,
            "START WITH COMPUTER",
            "Open JARVIS automatically after you sign in.",
            False,
            self._set_autostart_from_switch,
        )
        self._settings_brief_switch = switch_row(
            system,
            "MORNING BRIEF",
            "Offer a short daily overview when JARVIS starts.",
            False,
            self._set_brief_from_switch,
        )
        action_row(
            system,
            "CREATE DESKTOP SHORTCUT",
            "Add a shortcut to launch JARVIS quickly.",
            self._create_desktop_shortcut,
        )
        action_row(
            system,
            "FULLSCREEN   [F11]",
            "Expand the interface to the whole screen.",
            self._toggle_fullscreen,
            True,
        )
        action_row(
            system,
            "MINI ORB WINDOW   [F9]",
            "A small always-on-top window with only the orb.",
            lambda: self.set_mini(True),
            True,
        )
        action_row(
            system,
            "TOP DOCK BAR   [F10]",
            "A slim always-on-top bar that expands when you hover it.",
            lambda: self.set_dock(not self._dock_active),
            True,
        )
        action_row(
            system,
            "COMMAND PALETTE   [Ctrl+K]",
            "Search and run any action by name.",
            self._open_palette,
            True,
        )
        action_row(
            system,
            "KEYBOARD SHORTCUTS   [F1]",
            "Every shortcut in one list.",
            self._open_help,
            True,
        )

        data = category(
            "◆  DATA, PLUGINS & REMOTE",
            "Memory, optional integrations, and access from another device.",
        )
        action_row(
            data,
            "MEMORY",
            "Review and delete what JARVIS has stored.",
            self._open_memory_panel,
            True,
        )
        action_row(
            data,
            "PLUGINS",
            "Enable or disable installed capabilities.",
            self._open_plugin_manager,
            True,
        )
        action_row(
            data,
            "PLUGIN SETTINGS",
            "Configure plugins that provide their own settings.",
            self._open_plugin_settings,
            True,
        )
        action_row(
            data,
            "REMOTE CONTROL",
            "Pair a phone for remote access.",
            self._open_remote,
            True,
        )

        lay.addStretch(1)
        self._refresh_settings_switches()
        return w

    def _warm_wake_state(self) -> None:
        """Work out the wake-word state off the UI thread, once.

        It used to be resolved when the drawer opened, and that made opening the
        drawer take two seconds the first time — measured at 2.10s, all of it
        `import openwakeword` dragging in onnxruntime behind it. The button
        count had nothing to do with it; the Qt thread was simply waiting on an
        import. Afterwards the same check costs about a millisecond, so it only
        ever needed to happen somewhere other than in front of the user.
        """

        def work():
            try:
                self._wake_state()
            except Exception:
                pass
            self._wake_btns_sig.emit()

        threading.Thread(target=work, daemon=True, name="wake-state-warm").start()

    def _toggle_drawer(self, checked: bool):
        if checked:
            self._refresh_settings_switches()
            self._position_quick_drawer()
            self._quick_drawer.show()
            self._quick_drawer.raise_()
        else:
            self._quick_drawer.hide()

    def _close_setup(self):
        if hasattr(self, "_quick_drawer"):
            self._quick_drawer.hide()
        if hasattr(self, "_drawer_btn"):
            self._drawer_btn.setChecked(False)

    def _close_controls(self):
        if hasattr(self, "_ctrl_drawer"):
            self._ctrl_drawer.hide()
        if hasattr(self, "_ctrl_btn"):
            self._ctrl_btn.setChecked(False)

    def _place_drawer(self, drawer, button) -> None:
        if drawer is None:
            return
        cw = self.centralWidget()
        width = min(340, max(300, cw.width() // 3))
        drawer.setFixedWidth(width)
        drawer.adjustSize()
        try:
            center_x = button.mapTo(cw, QPoint(button.width() // 2, 0)).x()
        except Exception:
            center_x = cw.width() // 2
        left = int(center_x - width / 2)
        left = max(10, min(left, cw.width() - width - 10))
        top = getattr(self, "_header_height_px", 72) + 8
        drawer.setGeometry(left, top, width, drawer.sizeHint().height())

    def _position_quick_drawer(self):
        d = getattr(self, "_quick_drawer", None)
        if d is None:
            return
        cw = self.centralWidget()
        width = min(360, max(300, cw.width() // 3))
        top = getattr(self, "_header_height_px", 54) + 8
        want = self._drawer_lay.sizeHint().height() + 90
        height = min(want, max(160, cw.height() - top - 12))
        try:
            cx = self._drawer_btn.mapTo(cw, QPoint(self._drawer_btn.width() // 2, 0)).x()
        except Exception:
            cx = cw.width() // 2
        left = max(10, min(int(cx - width / 2), cw.width() - width - 10))
        d.setGeometry(left, top, width, height)

    def _build_content_panel(self) -> QWidget:
        """
        Collapsible panel below the HUD — shows search results, news, briefings.
        Hidden by default; appears when show_content() is called.
        """
        w = QWidget()
        w.setObjectName("ContentPanel")
        w.setStyleSheet(f"""
            QWidget#ContentPanel {{
                background: rgba(4, 14, 22, 215);
                border-top: 1px solid {C.BORDER_B};
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(5)

        # ── header row ───────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setSpacing(6)

        dot = QLabel("◈")
        dot.setFont(QFont(_FONT, 10, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._content_title_lbl = QLabel("BRIEFING")
        self._content_title_lbl.setFont(QFont(_FONT, 9, QFont.Weight.Bold))
        self._content_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;"
        )
        hdr.addWidget(self._content_title_lbl)
        hdr.addStretch()

        self._content_ts_lbl = QLabel("")
        self._content_ts_lbl.setFont(QFont(_FONT, 8))
        self._content_ts_lbl.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent;"
        )
        hdr.addWidget(self._content_ts_lbl)

        dismiss = QPushButton("DISMISS  ✕")
        dismiss.setFont(QFont(_FONT, 8))
        dismiss.setFixedHeight(18)
        dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 6px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        dismiss.clicked.connect(w.hide)
        hdr.addWidget(dismiss)
        lay.addLayout(hdr)

        # ── separator ─────────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};")
        lay.addWidget(sep)

        # ── text display ──────────────────────────────────────────────────────
        self._content_display = QTextEdit()
        self._content_display.setReadOnly(True)
        self._content_display.setFont(QFont(_FONT, 9))
        self._content_display.setMinimumHeight(60)
        self._content_display.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._content_display.setStyleSheet(f"""
            QTextEdit {{
                background: {C.DARK};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 8px;
                padding: 6px 8px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG}; width: 6px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B}; border-radius: 8px; min-height: 16px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; border: none;
            }}
        """)
        lay.addWidget(self._content_display)

        return w

    def _show_content(self, title: str, text: str):
        """Slot — runs on Qt main thread. Updates and shows the content panel."""
        import time as _time

        # The panel opens below the head, so the head looks down at it. It is a
        # tiny thing that answers "did that land?" before you read a word.
        self.hud.glance(0.0, -0.85, hold=1.3)
        self._content_title_lbl.setText(title.upper()[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setPlainText(text)
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start
        )
        if self._mini:
            # Don't blow the little window up while someone is working next to
            # it: keep the result, say so, show it when they expand.
            self._pending_panel = True
            self._log.append_log(
                "SYS: A result is ready — expand the window to read it."
            )
            return
        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 220, 120), 220])

    # ── document review ──────────────────────────────────────────────────────
    # Rendered as rich text into the content panel that already exists, rather
    # than into a panel of its own. A review is read, not clicked, so QTextEdit
    # gives scrolling, selection and copy for nothing, and the HUD gains no
    # widget it has to lay out. Severity decides colour and order here because
    # that is presentation; the plugin supplies no styling and knows no palette,
    # which is also what lets a re-theme repaint a review correctly.

    # Severity is marked by a symbol and a colour, not by a word. The findings
    # themselves are in the user's language, and "[SERIOUS]" sitting inside a
    # Turkish sentence is the kind of seam this project tries not to have —
    # while translating the tag would mean a table per language, which is worse.
    # A shape carries it in every language, and shape plus colour still reads
    # for someone who cannot separate red from amber. What the marks mean
    # arrives the way everything else does: JARVIS says it out loud.
    _REVIEW_MARKS = {
        "serious": ("RED", "▲"),
        "caution": ("ACC2", "●"),
        "note": ("PRI_DIM", "·"),
    }

    @staticmethod
    def _esc(s) -> str:
        return (
            str(s or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>")
        )

    def _show_review(self, title: str, summary: str, findings, unclear):
        """Slot — Qt main thread. Lays a document review into the content panel."""
        e = self._esc
        parts = [f'<div style="color:{C.TEXT}; font-family:Courier New;">']

        if summary:
            parts.append(
                f'<div style="color:{C.WHITE}; border-left:2px solid {C.PRI};'
                f' padding-left:8px; margin-bottom:10px;">{e(summary)}</div>'
            )

        for f in findings or []:
            key, mark = self._REVIEW_MARKS.get(f.get("severity"), ("PRI_DIM", "·"))
            colour = getattr(C, key)
            parts.append(f'<div style="margin-bottom:11px;">')
            parts.append(
                f'<span style="color:{colour}; font-weight:bold;">{mark}</span> '
                f'<span style="color:{C.WHITE}; font-weight:bold;">'
                f'{e(f.get("heading"))}</span>'
            )
            if f.get("detail"):
                parts.append(f'<div style="margin-left:12px;">{e(f["detail"])}</div>')
            if f.get("quote"):
                # The document's own wording, visually separated from the
                # explanation so the two are never mistaken for each other.
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_DIM};'
                    f' border-left:1px solid {C.BORDER}; padding-left:7px;">'
                    f'&ldquo;{e(f["quote"])}&rdquo;</div>'
                )
            if f.get("suggestion"):
                parts.append(
                    f'<div style="margin-left:12px; color:{C.PRI};">'
                    f'&rarr; {e(f["suggestion"])}</div>'
                )
            parts.append("</div>")

        if unclear:
            parts.append(
                f'<div style="margin-top:6px; border-top:1px solid {C.BORDER};'
                f' padding-top:7px; color:{C.TEXT_MED};">'
                "The document does not settle:</div>"
            )
            for u in unclear:
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_MED};">'
                    f"&middot; {e(u)}</div>"
                )
        parts.append("</div>")

        import time as _time

        self.hud.glance(0.0, -0.85, hold=1.3)
        # Left as written, not upper-cased. The other content-panel titles are
        # the app's own English labels, but this one is the document's name in
        # the user's language, and str.upper() applies English casing rules to
        # it: Turkish "Sözleşmesi" comes back "SÖZLEŞMESI", having lost the
        # dotted capital İ. Python has no locale-aware upper to reach for, and
        # imposing one language's rules on all of them is the bug, not the fix.
        self._content_title_lbl.setText((title or "Document")[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setHtml("".join(parts))
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start
        )
        if self._mini:
            self._pending_panel = True
            self._log.append_log(
                "SYS: A document review is ready — expand the window to read it."
            )
            return
        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 260, 120), 260, 0])

    # ── quiz panel ───────────────────────────────────────────────────────────
    # An interactive twin of the content panel. The plugin only ever hands over
    # questions; everything about asking, marking and reporting happens here,
    # and the finished result is pushed back into the conversation the same way
    # a dropped file is — as a message JARVIS reads and responds to. That keeps
    # the tool call short (it returns the moment the board is up) and leaves the
    # talking to the assistant, in the user's own language.

    def _quiz_btn(self, text: str, primary: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setFont(QFont(_FONT, 9))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setMinimumHeight(24)
        edge = C.BORDER_B if primary else C.BORDER
        col = C.PRI if primary else C.TEXT_MED
        b.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL2}; color: {col};
                border: 1px solid {edge}; border-radius: 6px;
                padding: 3px 9px; text-align: left;
            }}
            QPushButton:hover {{ color: {C.WHITE}; border-color: {C.PRI_DIM}; }}
            QPushButton:disabled {{ color: {C.TEXT_DIM}; border-color: {C.BORDER}; }}
        """)
        return b

    def _build_quiz_panel(self) -> QWidget:
        w = QWidget()
        w.setObjectName("QuizPanel")
        w.setStyleSheet(f"""
            QWidget#QuizPanel {{
                background: rgba(4, 14, 22, 215);
                border-top: 1px solid {C.BORDER_B};
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(6)

        hdr = QHBoxLayout()
        hdr.setSpacing(6)
        dot = QLabel("◈")
        dot.setFont(QFont(_FONT, 10, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._quiz_title_lbl = QLabel("QUIZ")
        self._quiz_title_lbl.setFont(QFont(_FONT, 9, QFont.Weight.Bold))
        self._quiz_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;"
        )
        hdr.addWidget(self._quiz_title_lbl)
        hdr.addStretch()

        self._quiz_count_lbl = QLabel("")
        self._quiz_count_lbl.setFont(QFont(_FONT, 8))
        self._quiz_count_lbl.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent;"
        )
        hdr.addWidget(self._quiz_count_lbl)

        quit_btn = QPushButton("DISMISS  ✕")
        quit_btn.setFont(QFont(_FONT, 8))
        quit_btn.setFixedHeight(18)
        quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 6px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        quit_btn.clicked.connect(self._hide_quiz)
        hdr.addWidget(quit_btn)
        lay.addLayout(hdr)

        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {C.BORDER};")
        lay.addWidget(rule)

        self._quiz_q_lbl = QLabel("")
        self._quiz_q_lbl.setWordWrap(True)
        self._quiz_q_lbl.setFont(QFont(_FONT, 10))
        self._quiz_q_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(self._quiz_q_lbl)

        self._quiz_answers = QWidget()
        self._quiz_answers.setStyleSheet("background: transparent;")
        self._quiz_answers_lay = QVBoxLayout(self._quiz_answers)
        self._quiz_answers_lay.setContentsMargins(0, 2, 0, 0)
        self._quiz_answers_lay.setSpacing(4)
        lay.addWidget(self._quiz_answers)

        self._quiz_note_lbl = QLabel("")
        self._quiz_note_lbl.setWordWrap(True)
        self._quiz_note_lbl.setFont(QFont(_FONT, 9))
        self._quiz_note_lbl.setStyleSheet(
            f"color: {C.TEXT_MED}; background: transparent;"
        )
        self._quiz_note_lbl.hide()
        lay.addWidget(self._quiz_note_lbl)

        foot = QHBoxLayout()
        foot.addStretch()
        self._quiz_next_btn = self._quiz_btn("NEXT  →", primary=True)
        self._quiz_next_btn.setFixedWidth(110)
        self._quiz_next_btn.clicked.connect(self._quiz_next)
        self._quiz_next_btn.hide()
        foot.addWidget(self._quiz_next_btn)
        lay.addLayout(foot)

        self._quiz = None
        return w

    def _show_quiz(self, topic: str, questions, grader=None):
        """Slot — Qt main thread. Puts a fresh quiz on the board."""
        if not questions:
            return
        # A quiz needs clicks, so it cannot wait in a 240 px window: expand.
        if self._mini:
            self.set_mini(False, persist=False)
        self._quiz = {
            "topic": topic or "",
            "questions": list(questions),
            "grader": grader,
            "i": 0,
            "results": [],
            "answered": False,
        }
        self._quiz_title_lbl.setText((topic or "quiz").upper()[:48])
        self.hud.glance(0.0, -0.85, hold=1.3)
        first_show = not self._quiz_panel.isVisible()
        self._quiz_panel.show()
        if first_show:
            QTimer.singleShot(
                0,
                lambda: self._center_split.setSizes(
                    [max(self._center_split.height() - 250, 120), 0, 250]
                ),
            )
        self._quiz_render()

    def _hide_quiz(self):
        self._quiz = None
        self._quiz_panel.hide()

    def _quiz_clear_answers(self):
        while self._quiz_answers_lay.count():
            item = self._quiz_answers_lay.takeAt(0)
            child = item.widget()
            if child is not None:
                child.setParent(None)
                child.deleteLater()

    def _quiz_render(self):
        q = self._quiz["questions"][self._quiz["i"]]
        n, total = self._quiz["i"] + 1, len(self._quiz["questions"])
        self._quiz_count_lbl.setText(f"{n} / {total}")
        self._quiz_q_lbl.setText(q.get("question", ""))
        self._quiz_note_lbl.hide()
        self._quiz_next_btn.hide()
        self._quiz["answered"] = False
        self._quiz_clear_answers()

        opts = q.get("options") or []
        if opts:
            for text in opts:
                b = self._quiz_btn("   " + text)
                b.clicked.connect(lambda _=False, t=text: self._quiz_submit(t))
                self._quiz_answers_lay.addWidget(b)
        else:
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)
            field = QLineEdit()
            field.setFont(QFont(_FONT, 10))
            field.setPlaceholderText("your answer")
            field.setStyleSheet(f"""
                QLineEdit {{
                    background: {C.PANEL2}; color: {C.WHITE};
                    border: 1px solid {C.BORDER}; border-radius: 6px; padding: 4px 7px;
                }}
                QLineEdit:focus {{ border-color: {C.PRI_DIM}; }}
            """)
            send = self._quiz_btn("ANSWER", primary=True)
            send.setFixedWidth(90)
            field.returnPressed.connect(lambda: self._quiz_submit(field.text()))
            send.clicked.connect(lambda: self._quiz_submit(field.text()))
            h.addWidget(field, stretch=1)
            h.addWidget(send)
            self._quiz_answers_lay.addWidget(row)
            field.setFocus()

    def _quiz_submit(self, given: str):
        if self._quiz is None or self._quiz["answered"]:
            return
        self._quiz["answered"] = True
        q = self._quiz["questions"][self._quiz["i"]]
        grader = self._quiz.get("grader")
        verdict = None
        if callable(grader):
            try:
                verdict = grader(q, given)
            except Exception:
                verdict = None
        self._quiz["results"].append(
            {
                "question": q.get("question", ""),
                "type": q.get("type", ""),
                "given": str(given or "").strip(),
                "answer": q.get("answer", ""),
                "correct": verdict,
            }
        )

        for i in range(self._quiz_answers_lay.count()):
            wdg = self._quiz_answers_lay.itemAt(i).widget()
            if wdg is not None:
                wdg.setEnabled(False)

        if verdict is True:
            mark, colour = "✓  correct", C.GREEN
        elif verdict is False:
            mark, colour = "✕  " + str(q.get("answer", "")), C.RED
        else:
            # Open answers and near-miss gap-fills are JARVIS's to judge. Saying
            # so is honest; marking it wrong here would be a guess.
            mark, colour = "…  noted — I'll go over this one with you", C.ACC2
        note = q.get("note") or ""
        self._quiz_note_lbl.setText(mark + (("\n" + note) if note else ""))
        self._quiz_note_lbl.setStyleSheet(f"color: {colour}; background: transparent;")
        self._quiz_note_lbl.show()

        last = self._quiz["i"] >= len(self._quiz["questions"]) - 1
        self._quiz_next_btn.setText("FINISH  →" if last else "NEXT  →")
        self._quiz_next_btn.show()
        self._quiz_next_btn.setFocus()

    def _quiz_next(self):
        if self._quiz is None:
            return
        if self._quiz["i"] >= len(self._quiz["questions"]) - 1:
            self._quiz_finish()
        else:
            self._quiz["i"] += 1
            self._quiz_render()

    def _quiz_finish(self):
        if self._quiz is None:
            return
        topic = self._quiz["topic"]
        results = self._quiz["results"]
        right = sum(1 for r in results if r["correct"] is True)
        unsure = sum(1 for r in results if r["correct"] is None)
        total = len(results)
        self._quiz_panel.hide()
        self._quiz = None

        self._log.append_log(f"QUIZ: {topic or 'quiz'} — {right}/{total} correct")

        # Hand it back to JARVIS as a message, not as a tool return: the tool
        # call ended minutes ago. This is the same channel a dropped file uses.
        lines = [
            f"[QUIZ_DONE] topic={topic or 'general'} | "
            f"auto-marked {right}/{total} correct"
            + (f", {unsure} still need your marking" if unsure else "")
        ]
        for i, r in enumerate(results, 1):
            state = (
                "correct"
                if r["correct"] is True
                else "wrong" if r["correct"] is False else "NEEDS MARKING"
            )
            lines.append(
                f"{i}. [{r['type']}] {r['question']} | they answered: "
                f"{r['given'] or '(blank)'} | expected: {r['answer']} | {state}"
            )
        lines.append(
            "Mark every question flagged NEEDS MARKING yourself — accept an answer "
            "that means the same thing. Then tell them how they did in their own "
            "language: the score, what they got wrong and why, in a couple of "
            "sentences. Offer another round only if it fits. "
            "Remember something only if it would still matter next week — that they "
            "are working through a subject, or keep missing the same thing. A score "
            "from one session is not worth a memory, and a memory per quiz would "
            "bury the things that are."
        )
        msg = "\n".join(lines)
        if self.on_text_command:
            threading.Thread(
                target=self.on_text_command, args=(msg,), daemon=True
            ).start()

    def _build_footer(self) -> QWidget:
        """VS Code-style status bar. Segments are live; the clickable ones act."""
        w = QWidget()
        w.setFixedHeight(26)
        w.setObjectName("JarvisFooter")
        w.setStyleSheet(f"""
            QWidget#JarvisFooter {{
                background: rgba(3, 10, 16, 170);
                border-top: 1px solid {C.BORDER};
            }}
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent; border: none;
                padding: 0 10px; text-align: left;
            }}
            QPushButton:hover {{ color: {C.PRI}; background: rgba(255, 255, 255, 14); }}
            QLabel {{ color: {C.TEXT_DIM}; background: transparent; border: none;
                padding: 0 10px; }}
        """)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 0, 6, 0)
        lay.setSpacing(0)
        fnt = QFont(_DISPLAY, 8, QFont.Weight.Bold)
        fnt.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)

        def seg(text: str, tip: str, fn=None):
            if fn is None:
                b = QLabel(text)
            else:
                b = QPushButton(text)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(lambda _=False: fn())
            b.setFont(fnt)
            b.setFixedHeight(26)
            b.setToolTip(tip)
            lay.addWidget(b)
            return b

        self._sb_state = seg("●  STARTING", "Assistant state")
        self._sb_mic = seg("MIC ON", "Click to mute / unmute  [F4]", self._toggle_mute)
        self._sb_hud = seg(
            "ORB",
            "Switch between the particle orb and the animated face",
            self._toggle_hud_style,
        )
        self._sb_theme = seg("COLOUR", "Appearance & colour  [F7]", self._open_customize)
        lay.addStretch(1)
        self._sb_sess = seg("SESSION 00:00", "Time since JARVIS started")
        seg("▭  MINI", "Mini orb window  [F9]", lambda: self.set_mini(True))
        seg("⌘  COMMANDS", "Command palette  [Ctrl+K]", lambda: self._open_palette())
        self._sb_name = seg(self._assistant_name.upper(), "Assistant name")
        self._sb_name.setStyleSheet(f"color: {C.PRI};")
        return w

    def _update_statusbar(self) -> None:
        if not hasattr(self, "_sb_state"):
            return
        try:
            txt, col = self.hud._status()
            self._sb_state.setText(f"●  {txt}")
            self._sb_state.setStyleSheet(f"color: {col.name()};")
            self._sb_mic.setText("MIC MUTED" if self._muted else "MIC ON")
            self._sb_mic.setStyleSheet(
                f"QPushButton {{ color: {C.MUTED_C if self._muted else C.GREEN_D}; }}"
            )
            self._sb_hud.setText("FACE" if self.hud.hud_style == "face" else "ORB")
            self._sb_theme.setText("COLOUR")
        except Exception:
            pass

    def _update_session(self) -> None:
        if not hasattr(self, "_sb_sess"):
            return
        up = int(time.time() - self._born)
        self._sb_sess.setText(f"SESSION {up // 3600:02d}:{up % 3600 // 60:02d}")

    def _on_file_selected(self, path: str):
        self._current_file = path
        p = Path(path)
        cat = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size = _fmt_size(p.stat().st_size)
        self._file_hint.setText(
            f"{icon}  {p.name}  ·  {size}  ·  Tell {self._assistant_name} what to do with it"
        )
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(
                target=self.on_text_command, args=(msg,), daemon=True
            ).start()

    def notify_phone_connected(self) -> None:
        if self._remote_overlay and self._remote_overlay.isVisible():
            self._remote_overlay.mark_connected()

    def _open_remote(self):
        if not self.on_remote_clicked:
            self._log.append_log("SYS: Dashboard not running — remote unavailable.")
            return
        result = self.on_remote_clicked()
        if not result:
            self._log.append_log("SYS: Could not generate remote key.")
            return
        url = result[0]
        key = result[1]
        auto = result[2] if len(result) >= 3 else ""
        manual = result[3] if len(result) >= 4 else url
        if self._remote_overlay:
            self._remote_overlay._do_close()
        cw = self.centralWidget()
        ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
        ov = RemoteKeyOverlay(
            url, key, auto_login_url=auto, manual_url=manual, expiry_secs=600, parent=cw
        )
        ov.set_new_key_callback(self.on_remote_clicked)
        ov.setGeometry(
            (cw.width() - ow) // 2,
            (cw.height() - oh) // 2,
            ow,
            oh,
        )
        ov.closed.connect(lambda: setattr(self, "_remote_overlay", None))
        ov.show()
        self._remote_overlay = ov
        self._log.append_log(f"SYS: Remote key generated — manual: {manual or url}")

    # ── Auto-start ──────────────────────────────────────────────────────────────

    def _check_autostart(self) -> bool:
        """Returns True if auto-start is currently registered on this OS."""
        try:
            if _OS == "Windows":
                import winreg

                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run",
                    0,
                    winreg.KEY_READ,
                )
                try:
                    winreg.QueryValueEx(key, "JARVIS_AI")
                    return True
                except FileNotFoundError:
                    return False
                finally:
                    winreg.CloseKey(key)
            elif _OS == "Darwin":
                return (
                    Path.home()
                    / "Library"
                    / "LaunchAgents"
                    / "com.jarvis.assistant.plist"
                ).exists()
            else:
                return (
                    Path.home() / ".config" / "autostart" / "jarvis.desktop"
                ).exists()
        except Exception:
            return False

    def _toggle_autostart(self):
        currently_on = self._check_autostart()
        try:
            script = str(Path(__file__).resolve().parent / "main.py")
            if _OS == "Windows":
                import winreg

                reg = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run",
                    0,
                    winreg.KEY_ALL_ACCESS,
                )
                if currently_on:
                    winreg.DeleteValue(reg, "JARVIS_AI")
                else:
                    pythonw = Path(sys.executable).parent / "pythonw.exe"
                    exe = str(pythonw if pythonw.exists() else sys.executable)
                    winreg.SetValueEx(
                        reg, "JARVIS_AI", 0, winreg.REG_SZ, f'"{exe}" "{script}"'
                    )
                winreg.CloseKey(reg)
            elif _OS == "Darwin":
                plist_dir = Path.home() / "Library" / "LaunchAgents"
                plist_dir.mkdir(parents=True, exist_ok=True)
                plist = plist_dir / "com.jarvis.assistant.plist"
                if currently_on:
                    plist.unlink(missing_ok=True)
                else:
                    plist.write_text(
                        '<?xml version="1.0" encoding="UTF-8"?>\n'
                        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                        '<plist version="1.0"><dict>\n'
                        "  <key>Label</key><string>com.jarvis.assistant</string>\n"
                        "  <key>ProgramArguments</key><array>\n"
                        f"    <string>{sys.executable}</string>\n"
                        f"    <string>{script}</string>\n"
                        "  </array>\n"
                        "  <key>RunAtLoad</key><true/>\n"
                        "</dict></plist>\n"
                    )
            else:
                desk_dir = Path.home() / ".config" / "autostart"
                desk_dir.mkdir(parents=True, exist_ok=True)
                desk = desk_dir / "jarvis.desktop"
                if currently_on:
                    desk.unlink(missing_ok=True)
                else:
                    desk.write_text(
                        "[Desktop Entry]\n"
                        f"Name={self._assistant_name}\n"
                        f"Exec={sys.executable} {script}\n"
                        "Type=Application\nTerminal=false\n"
                        "X-GNOME-Autostart-enabled=true\n"
                    )
            enabled = not currently_on
            self._update_autostart_btn(enabled)
            self._log.append_log(
                f"SYS: Auto-start {'enabled' if enabled else 'disabled'}."
            )
        except Exception as e:
            self._log.append_log(f"ERR: Auto-start failed — {e}")
            self._update_autostart_btn(self._check_autostart())

    def _update_autostart_btn(self, enabled: bool):
        self._set_settings_switch("_settings_autostart_switch", bool(enabled))

    def _set_settings_switch(self, attr: str, enabled: bool, text: str | None = None):
        """Set a drawer switch without re-running its user-change callback."""
        switch = getattr(self, attr, None)
        if switch is None:
            return
        blocked = switch.blockSignals(True)
        switch.setChecked(enabled)
        switch.setText(text or ("ON" if enabled else "OFF"))
        switch.blockSignals(blocked)

    def _set_autostart_from_switch(self, enabled: bool):
        if enabled != self._check_autostart():
            self._toggle_autostart()
        else:
            self._update_autostart_btn(enabled)

    def _set_brief_from_switch(self, enabled: bool):
        from memory.config_manager import get_brief_enabled

        if enabled != get_brief_enabled():
            self._toggle_brief()
        else:
            self._update_brief_btn(enabled)

    def _set_ptt_from_switch(self, enabled: bool):
        from memory.config_manager import get_push_to_talk_enabled

        if enabled != get_push_to_talk_enabled():
            self._toggle_ptt()
        else:
            self._refresh_talk_btns()

    def _set_wake_from_switch(self, enabled: bool):
        st = self._wake_state()
        if not st["ready"]:
            # Turning the switch on starts the one-time local download.
            if enabled:
                self._toggle_wake_word()
            else:
                self._refresh_wake_btns()
            return
        if enabled != st["enabled"]:
            self._toggle_wake_word()
        else:
            self._refresh_wake_btns()

    def _refresh_settings_switches(self):
        """Repaint the drawer from the persisted runtime settings."""
        try:
            from memory.config_manager import get_brief_enabled

            self._set_settings_switch("_settings_brief_switch", get_brief_enabled())
        except Exception:
            pass
        self._update_autostart_btn(self._check_autostart())
        for fn in (
            self._refresh_wake_btns,
            self._refresh_talk_btns,
            self._refresh_hud_btn,
        ):
            try:
                fn()
            except Exception:
                pass

    def _toggle_brief(self):
        from memory.config_manager import get_brief_enabled, save_brief_enabled

        new_val = not get_brief_enabled()
        save_brief_enabled(new_val)
        self._update_brief_btn(new_val)

    # ── Wake word settings ───────────────────────────────────────────────────

    def _wake_state(self, cached: bool = False) -> dict:
        """State of the wake word. With cached=True it never imports the wake
        engine (slow), it only reads what the background warm-up found."""
        if self.wake_get_state:
            try:
                s = self.wake_get_state()
                return {
                    "ready": bool(s.get("ready")),
                    "enabled": bool(s.get("enabled")),
                    "awake": bool(s.get("awake")),
                }
            except Exception:
                pass
        if cached:
            if self._wake_cache is not None:
                return dict(self._wake_cache)
            return {"ready": False, "enabled": False, "awake": True}
        ready, enabled = False, False
        try:
            from core.wake_word import is_ready
            from memory.config_manager import get_wake_word_enabled

            ready, enabled = is_ready(), get_wake_word_enabled()
        except Exception:
            pass
        self._wake_cache = {"ready": ready, "enabled": enabled, "awake": True}
        return dict(self._wake_cache)

    def _refresh_wake_btns(self):
        sw = getattr(self, "_settings_wake_switch", None)
        if sw is None:
            return
        note = getattr(sw, "_note", None)
        sleep_btn = getattr(self, "_settings_wake_sleep_btn", None)
        if self._wake_downloading:
            self._set_settings_switch("_settings_wake_switch", True, "…")
            sw.setEnabled(False)
            if note is not None:
                note.setText("Downloading the wake-word model (one-time)…")
            if sleep_btn is not None:
                sleep_btn.row.setVisible(False)
            return
        st = self._wake_state(cached=True)
        on = bool(st["ready"] and st["enabled"])
        sw.setEnabled(True)
        self._set_settings_switch("_settings_wake_switch", on)
        if note is not None:
            note.setText(
                "Say “Hey Jarvis” to begin."
                if st["ready"]
                else "Say “Hey Jarvis” to begin. First use downloads a small local model."
            )
        if sleep_btn is not None:
            sleep_btn.row.setVisible(on)
            sleep_btn.setText("SLEEP NOW" if st["awake"] else "WAKE NOW")

    def _refresh_talk_btns(self):
        """Repaint the push-to-talk switch from the saved setting."""
        sw = getattr(self, "_settings_ptt_switch", None)
        if sw is None:
            return
        from memory.config_manager import get_push_to_talk_enabled

        ptt = get_push_to_talk_enabled()
        self._set_settings_switch("_settings_ptt_switch", ptt)
        try:
            from core.hotkey import chord_label

            key = chord_label()
        except Exception:
            key = "the push-to-talk key"
        note = getattr(sw, "_note", None)
        if note is not None:
            note.setText(
                f"The microphone stays closed until you hold {key}."
            )

    def _refresh_hud_btn(self):
        btn = getattr(self, "_settings_hud_btn", None)
        if btn is None:
            return
        from memory.config_manager import get_hud_style

        face = get_hud_style() == "face"
        btn.setText("HUD STYLE:  ANIMATED FACE" if face else "HUD STYLE:  PARTICLE ORB")
        btn.setToolTip(
            "An animated head that speaks your words. Click to switch to the particle orb."
            if face
            else "A sphere of connected dots moving with your voice. Click to switch to the animated head."
        )

    def _toggle_hud_style(self):
        """Swap the centrepiece. Both objects stay in memory, so the change is
        instant and switching back costs nothing."""
        from memory.config_manager import get_hud_style, save_hud_style

        want = "core" if get_hud_style() == "face" else "face"
        save_hud_style(want)
        try:
            self.hud.hud_style = want
            self.hud.update()
        except Exception:
            pass
        self._refresh_hud_btn()
        self._log.append_log(
            "SYS: HUD switched to the animated face."
            if want == "face"
            else "SYS: HUD switched to the particle orb."
        )
        self._update_statusbar()

    def _toggle_ptt(self):
        from memory.config_manager import (
            get_push_to_talk_enabled,
            save_push_to_talk_enabled,
        )

        want = not get_push_to_talk_enabled()
        save_push_to_talk_enabled(want)
        scope = None
        if self.on_push_to_talk:
            try:
                scope = self.on_push_to_talk(want)
            except Exception as e:
                self._log.append_log(f"ERR: Push-to-talk failed — {e}")
                save_push_to_talk_enabled(False)
                want = False
        self._apply_ptt_shortcut(want and scope != "global")
        self._refresh_talk_btns()

    def _apply_ptt_shortcut(self, needed: bool):
        """Bind the chord inside the window when no global hook is available.

        On macOS and Linux there is no dependency-free way to read global key
        state, so the chord is at least live whenever this window has focus.
        Qt gives no key-release for a QShortcut, so a press latches the mic open
        and a short timer closes it; held down, auto-repeat keeps pushing that
        timer out, which behaves like holding a key.
        """
        from PyQt6.QtGui import QKeySequence, QShortcut
        from core.hotkey import qt_sequence

        if not needed:
            sc = getattr(self, "_ptt_sc", None)
            if sc is not None:
                sc.setEnabled(False)
                self._ptt_sc = None
            self._ptt_hold(False)
            return
        if getattr(self, "_ptt_sc", None) is not None:
            return

        self._ptt_release = QTimer(self)
        self._ptt_release.setSingleShot(True)
        self._ptt_release.setInterval(420)
        self._ptt_release.timeout.connect(lambda: self._ptt_hold(False))

        def _press():
            self._ptt_hold(True)
            self._ptt_release.start()

        self._ptt_sc = QShortcut(QKeySequence(qt_sequence()), self)
        self._ptt_sc.setAutoRepeat(True)
        self._ptt_sc.activated.connect(_press)

    def _ptt_hold(self, held: bool):
        """Report a windowed press/release to whoever owns the microphone."""
        cb = getattr(self, "ptt_hold", None)
        if cb:
            try:
                cb(bool(held))
            except Exception:
                pass

    def _toggle_wake_word(self):
        if self._wake_downloading:
            return
        st = self._wake_state()
        if not st["ready"]:
            self._wake_downloading = True
            self._refresh_wake_btns()

            def _work():
                try:
                    from core.wake_word import install_and_download

                    ok, msg = install_and_download(
                        logger=lambda m: self._log_sig.emit(f"SYS: {m}")
                    )
                except Exception as e:
                    ok, msg = False, str(e)
                if ok and self.on_wake_toggle:
                    try:
                        self.on_wake_toggle(True)
                    except Exception:
                        pass
                self._wake_dl_sig.emit(ok, msg)

            threading.Thread(target=_work, daemon=True).start()
            return
        new = not st["enabled"]
        if self.on_wake_toggle:
            try:
                self.on_wake_toggle(new)
            except Exception as e:
                self._log.append_log(f"ERR: Wake word failed — {e}")
        if self._wake_cache is not None:
            self._wake_cache["enabled"] = new
        self._refresh_wake_btns()

    def _on_wake_install_done(self, ok: bool, msg: str):
        self._wake_downloading = False
        self._wake_cache = None
        try:
            self._wake_state()
        except Exception:
            pass
        self._log_sig.emit(
            f"SYS: {'Wake word ready.' if ok else 'Wake word setup failed: ' + msg}"
        )
        self._refresh_wake_btns()

    def _tap_wake_manual(self):
        if self.on_wake_manual:
            try:
                self.on_wake_manual()
            except Exception:
                pass
        self._refresh_wake_btns()

    def _update_brief_btn(self, enabled: bool):
        self._set_settings_switch("_settings_brief_switch", bool(enabled))

    # ── Customization ────────────────────────────────────────────────────────────

    def _open_customize(self):
        cfg = _read_full_config()
        if self._customize_overlay:
            self._customize_overlay.hide()
        cw = self.centralWidget()
        ov = CustomizeOverlay(
            cfg.get("assistant_name", "JARVIS") or "JARVIS",
            cfg.get("user_name", ""),
            cfg.get("ui_color", "") or DEFAULT_UI_COLOR,
            cfg.get("voice_name", ""),
            theme=C.MODE,
            parent=cw,
        )
        ov.setFixedWidth(CustomizeOverlay._OW)
        ov.on_preview = lambda hx: self._preview_theme("custom", hx)
        ov.on_theme_preview = self._preview_theme
        ov.saved.connect(self._apply_name_update)
        ov.adjustSize()
        ov.move(
            max(0, (cw.width() - ov.width()) // 2),
            max(0, (cw.height() - ov.height()) // 2),
        )
        ov.show()
        ov.raise_()
        self._customize_overlay = ov

    # ── Theme ────────────────────────────────────────────────────────────────

    def _preview_theme(self, mode: str, hex_color: str = ""):
        """Apply the selected interface colour live (does NOT write config)."""
        old = current_palette()
        apply_ui_color(hex_color or (_read_full_config().get("ui_color") or ""))
        retheme_all_widgets(old, current_palette())
        self._aurora.update()
        self.hud._grid_cache = None
        self.hud.update()
        self._log.recolor()
        self._update_statusbar()
        if hasattr(self, "_drawer_btn"):
            self._drawer_btn.setToolTip("Settings")

    def _set_theme(self, mode: str):
        # Kept for command/plugin compatibility; colour is edited in Settings.
        self._open_customize()

    def _cycle_theme(self):
        self._open_customize()

    def _apply_name_update(
        self,
        name: str,
        user_name: str,
        ui_color: str = "",
        voice: str = "",
        theme: str = "",
    ):
        """Update all name/theme-dependent UI elements and persist to config."""
        self._assistant_name = name.strip() or "JARVIS"
        display = self._assistant_name.upper()
        self.setWindowTitle(f"{display} — {APP_VERSION}")
        self._title_lbl.setText(display)
        if display in ("JARVIS", "J.A.R.V.I.S"):
            self._sub_lbl.setText("Just A Rather Very Intelligent System")
        else:
            self._sub_lbl.setText("Personal AI Assistant")
        self._log._ai_name_lc = self._assistant_name.lower()
        self.hud._assistant_name = display
        if hasattr(self, "_sb_name"):
            self._sb_name.setText(display)

        mode = "custom"
        color = (ui_color or DEFAULT_UI_COLOR).strip().lower()
        self._preview_theme(mode, color)

        # Voice change → persist and, if it actually changed, rebuild the Live
        # session so the new voice takes effect (it's fixed at connect time).
        voice_changed = False
        if voice:
            from memory.config_manager import get_voice, save_voice

            if voice != get_voice():
                save_voice(voice)
                voice_changed = True

        try:
            data = _read_full_config()
            data["assistant_name"] = self._assistant_name
            data["user_name"] = user_name.strip()
            data["ui_theme"] = mode
            data["ui_color"] = color
            API_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
            self._log.append_log(f"SYS: Identity updated — {display}")
            self._log.append_log(f"SYS: Theme — {mode.upper()}")
            if voice_changed:
                self._log.append_log(f"SYS: Voice set — {voice}")
        except Exception as e:
            self._log.append_log(f"ERR: Config save failed — {e}")

        if voice_changed and self.on_voice_change:
            self.on_voice_change()

    def _centre_overlay(self, ov) -> None:
        """Place a floating overlay in the middle of the HUD and show it."""
        cw = self.centralWidget()
        ov.adjustSize()
        ov.setGeometry(
            max(0, (cw.width() - ov.width()) // 2),
            max(0, (cw.height() - ov.height()) // 2),
            ov.width(),
            ov.height(),
        )
        ov.show()
        ov.raise_()

    # ── Audio devices ────────────────────────────────────────────────────────

    def _open_audio_devices(self):
        ov = AudioDeviceOverlay(parent=self.centralWidget())
        ov.picked.connect(self._on_audio_devices_applied)
        self._centre_overlay(ov)
        self._audio_overlay = ov  # keep a reference so it isn't GC'd

    def _on_audio_devices_applied(self):
        self._log.append_log("SYS: Audio devices updated.")
        if self.on_audio_device_change:
            self.on_audio_device_change()

    # ── Memory panel ─────────────────────────────────────────────────────────

    def _open_memory_panel(self):
        ov = MemoryOverlay(parent=self.centralWidget())
        self._centre_overlay(ov)
        self._memory_overlay = ov

    # ── Irreversible-action confirmation ─────────────────────────────────────

    def _show_confirm_banner(self, title: str, detail: str):
        self._hide_confirm_banner()
        # The gate needs a readable window and a finger on it: if we are the
        # little orb, expand first and raise the banner on the next turn, once
        # the full layout exists.
        if self._mini:
            self.set_mini(False, persist=False)
            QTimer.singleShot(0, lambda: self._show_confirm_banner(title, detail))
            return
        ov = ConfirmBanner(title, detail, parent=self.centralWidget())
        ov.answered.connect(self._on_confirm_answered)
        self._centre_overlay(ov)
        self._confirm_overlay = ov

    def _hide_confirm_banner(self):
        ov = getattr(self, "_confirm_overlay", None)
        if ov is not None:
            ov.hide()
            ov.deleteLater()
            self._confirm_overlay = None

    def _on_confirm_answered(self, accepted: bool):
        # Tear the banner down first: core.confirm.resolve() may be about to
        # shut the machine down, and a live widget mid-callback is not where you
        # want to be when that happens.
        self._hide_confirm_banner()
        try:
            from core.confirm import resolve

            resolve(bool(accepted))
        except Exception as e:
            self._log.append_log(f"ERR: Confirmation failed — {e}")

    def _open_plugin_manager(self):
        plugins = self.get_plugins() if self.get_plugins else []
        cw = self.centralWidget()
        ov = PluginManagerOverlay(plugins, parent=cw)
        ov.adjustSize()
        ov.setGeometry(
            (cw.width() - ov.width()) // 2,
            (cw.height() - ov.height()) // 2,
            ov.width(),
            ov.height(),
        )
        ov.show()
        ov.raise_()
        self._plugin_manager_overlay = ov  # keep a reference so it isn't GC'd

    def _open_plugin_settings(self):
        sections = self.get_plugin_settings() if self.get_plugin_settings else []
        cw = self.centralWidget()
        ov = PluginSettingsOverlay(sections, parent=cw)
        ow = PluginSettingsOverlay._OW
        oh = min(560, cw.height() - 16)
        ov.setGeometry(
            (cw.width() - ow) // 2,
            (cw.height() - oh) // 2,
            ow,
            oh,
        )
        ov.show()
        ov.raise_()
        self._plugin_settings_overlay = ov  # keep a reference so it isn't GC'd

    # ── Clipboard intelligence ───────────────────────────────────────────────────

    def _on_clipboard_changed(self):
        if self._ignore_clip:
            return
        try:
            text = QApplication.clipboard().text().strip()
            if len(text) >= 10:
                self._clipboard_sig.emit(text)
        except Exception:
            pass

    def _show_clipboard_panel(self, text: str):
        # Copying code in your editor must not pop a panel over the little orb.
        if self._mini:
            return
        self._clipboard_panel.show_clipboard(text)
        self._position_clipboard_panel()

    def _position_clipboard_panel(self):
        cw = self.centralWidget()
        pw = ClipboardPanel._W
        ph = self._clipboard_panel.sizeHint().height() or ClipboardPanel._H
        x = (cw.width() - pw) // 2
        y = cw.height() - ph - 6
        self._clipboard_panel.setGeometry(x, y, pw, ph)
        self._clipboard_panel.raise_()

    def _on_clipboard_action(self, cmd: str):
        if self.on_text_command:
            threading.Thread(
                target=self.on_text_command, args=(cmd,), daemon=True
            ).start()

    # ────────────────────────────────────────────────────────────────────────────

    def _do_interrupt(self):
        if self._close_transient():
            return
        if self.on_interrupt:
            self.on_interrupt()

    def _toggle_mute(self):
        # A deliberate press settles the question: whatever the video did, or is
        # about to do, the user has now said what they want.
        self._video_auto_muted = False
        self._set_muted(not self._muted)

    def _set_muted(self, muted: bool, note: str = ""):
        muted = bool(muted)
        if muted == self._muted:
            return
        self._muted = muted
        self.hud.muted = muted
        self._style_mute_btn()
        if muted:
            self._apply_state("MUTED")
            self._log.append_log(
                "SYS: Microphone muted." + (f" {note}" if note else "")
            )
        else:
            self._apply_state("LISTENING")
            self._log.append_log(
                "SYS: Microphone active." + (f" {note}" if note else "")
            )

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("🔇  MIC MUTED")
            fg, bg, edge = (
                C.MUTED_C,
                "rgba(255, 51, 102, 30)",
                "rgba(255, 51, 102, 130)",
            )
        else:
            self._mute_btn.setText("🎙  MIC ON  [F4]")
            fg, bg, edge = C.GREEN, "rgba(0, 255, 136, 24)", "rgba(0, 255, 136, 100)"
        self._mute_btn.setStyleSheet(f"""
            QPushButton {{
                background: {bg}; color: {fg};
                border: 1px solid {edge}; border-radius: 12px;
            }}
            QPushButton:hover {{ border-color: {fg}; }}
        """)
        try:
            self.hud.mini_bar.set_muted(self._muted)
        except Exception:
            pass
        self._update_statusbar()

    def _send(self):
        txt = self._input.text().strip()

        if not txt:
            return

        self._input.clear()

        # Slash commands open the command palette.
        if txt.startswith("/"):
            self._open_palette(txt[1:].strip())
            return

        self._input.remember(txt)
        self._submit_text(txt)

    def _submit_text(self, txt: str) -> None:
        """Send a message to JARVIS exactly as if it had been typed."""
        self._log.append_log(f"You: {txt}")
        if self.on_text_command:
            threading.Thread(
                target=self.on_text_command, args=(txt,), daemon=True
            ).start()

    def _apply_state(self, state: str):
        self.hud.state = state
        self.hud.speaking = state == "SPEAKING"
        if self._dock is not None:
            self._dock.set_state(state)
        self._update_statusbar()

    def _check_config(self) -> bool:
        if not API_FILE.exists():
            return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))
        except Exception:
            return False

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = 460, 390
        ov.setGeometry(
            (cw.width() - ow) // 2,
            (cw.height() - oh) // 2,
            ow,
            oh,
        )
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    def _on_setup_done(self, key: str, os_name: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        API_FILE.write_text(
            json.dumps({"gemini_api_key": key, "os_system": os_name}, indent=4),
            encoding="utf-8",
        )
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._apply_state("LISTENING")
        self._assistant_name = (
            _read_full_config().get("assistant_name", "JARVIS") or "JARVIS"
        )
        self._log.append_log(
            f"SYS: Initialised. OS={os_name.upper()}. {self._assistant_name} online."
        )


class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app

    def mainloop(self):
        self._app.exec()

    def protocol(self, *_):
        pass


class JarvisUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        _init_fonts()
        self._app.setFont(QFont(_FONT, 10))
        self._win = MainWindow(face_path)
        self.root = _RootShim(self._app)
        self._win.show()

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return self._win._drop_zone.current_file()

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_remote_clicked(self):
        return self._win.on_remote_clicked

    @on_remote_clicked.setter
    def on_remote_clicked(self, cb):
        self._win.on_remote_clicked = cb

    @property
    def on_interrupt(self):
        return self._win.on_interrupt

    @on_interrupt.setter
    def on_interrupt(self, cb):
        self._win.on_interrupt = cb

    @property
    def on_voice_change(self):
        return self._win.on_voice_change

    @on_voice_change.setter
    def on_voice_change(self, cb):
        self._win.on_voice_change = cb

    @property
    def on_audio_device_change(self):
        return self._win.on_audio_device_change

    @on_audio_device_change.setter
    def on_audio_device_change(self, cb):
        self._win.on_audio_device_change = cb

    def show_confirm(self, title: str, detail: str) -> None:
        """Thread-safe: raise the irreversible-action gate. Called from action
        handlers running in executor threads, so it goes through a signal."""
        self._win._confirm_sig.emit(str(title)[:120], str(detail)[:300])

    def hide_confirm(self) -> None:
        """Thread-safe: take the gate down."""
        self._win._confirm_hide_sig.emit()

    @property
    def get_plugins(self):
        return self._win.get_plugins

    @get_plugins.setter
    def get_plugins(self, cb):
        self._win.get_plugins = cb

    @property
    def get_plugin_settings(self):
        return self._win.get_plugin_settings

    @get_plugin_settings.setter
    def get_plugin_settings(self, cb):
        self._win.get_plugin_settings = cb

    @property
    def on_wake_toggle(self):
        return self._win.on_wake_toggle

    @on_wake_toggle.setter
    def on_wake_toggle(self, cb):
        self._win.on_wake_toggle = cb

    @property
    def on_wake_manual(self):
        return self._win.on_wake_manual

    @on_wake_manual.setter
    def on_wake_manual(self, cb):
        self._win.on_wake_manual = cb

    @property
    def wake_get_state(self):
        return self._win.wake_get_state

    @wake_get_state.setter
    def wake_get_state(self, cb):
        self._win.wake_get_state = cb

    def set_audio_level(self, level: float) -> None:
        """Thread-safe: feed a 0.0–1.0 live audio level to the HUD waveform.
        Called from the audio threads; a plain float store is atomic under the
        GIL, so no signal/lock is needed for this cosmetic value."""
        try:
            self._win.hud.set_audio_level(level)
        except Exception:
            pass

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Ask the avatar to look somewhere for a moment (see HoloAvatar.glance)."""
        try:
            self._win.hud.glance(dx, dy, hold)
        except Exception:
            pass

    @property
    def ptt_hold(self):
        return self._win.ptt_hold

    @ptt_hold.setter
    def ptt_hold(self, cb):
        self._win.ptt_hold = cb

    @property
    def on_push_to_talk(self):
        return self._win.on_push_to_talk

    @on_push_to_talk.setter
    def on_push_to_talk(self, cb):
        self._win.on_push_to_talk = cb

    def push_visemes(self, frames, hop: float, at: float) -> None:
        """Thread-safe: post a schedule of (level, openness, width) mouth frames
        for JARVIS's own speech. `at` is the wall-clock time the batch begins to
        sound, not the time of the call. See HudCanvas.push_visemes()."""
        try:
            self._win.hud.push_visemes(frames, hop, at)
        except Exception:
            pass

    def notify_phone_connected(self) -> None:
        self._win.notify_phone_connected()

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def show_content(self, title: str, text: str):
        """Thread-safe: display content in the panel below the HUD."""
        self._win._content_sig.emit(title[:48], text[:4000])

    def show_quiz(self, topic: str, questions, grade=None) -> None:
        """Thread-safe: put an interactive quiz on the board.

        `grade(question, given)` decides each answer — the plugin supplies it so
        the marking rules live with the questions rather than being duplicated
        here. Returning None from it means "JARVIS should judge this one", which
        is how open answers and near-miss gap-fills are handled.

        Returns immediately: the user answers at their own pace and the finished
        result is delivered back through on_text_command.
        """
        self._win._quiz_sig.emit(str(topic or ""), list(questions or []), grade)

    def hide_quiz(self) -> None:
        """Thread-safe: clear any quiz currently on the board."""
        self._win._quiz_hide_sig.emit()

    def show_review(self, title: str, summary: str, findings, unclear=None) -> None:
        """Thread-safe: lay a document review into the panel below the HUD.

        `findings` is a list of {heading, detail, severity, quote, suggestion};
        severity is one of 'serious' / 'caution' / 'note' and decides colour and
        order here, so the caller supplies no styling of its own.
        """
        self._win._review_sig.emit(
            str(title or ""),
            str(summary or ""),
            list(findings or []),
            list(unclear or []),
        )

    def prompt_reconfig(self):
        """Thread-safe: show the API key setup overlay (e.g. after an auth error)."""
        self._win._ready = False
        self._win._reconfig_sig.emit()

    def show_camera_frame(self, img_bytes: bytes):
        """Thread-safe: show a webcam frame in the small overlay (screen captures)."""
        self._win._camera_sig.emit(img_bytes)

    def show_video(
        self, source: str, title: str = "", muted: bool = True, audio_source: str = ""
    ) -> None:
        """Thread-safe: play a video where the avatar normally is.

        `source` is a local path or a direct media URL. `audio_source` is for
        the case where the sound arrives as its own stream — YouTube serves no
        combined format any more — and when it is given the two are played
        together and kept in step.

        Muted by default, and that is a decision rather than a default: a
        soundtrack talking over JARVIS is the one way this could make the
        assistant worse. The user turns sound on from the header button or by
        asking, and closes it the same two ways.
        """
        self._win._video_open_sig.emit(
            str(source or ""), str(title or ""), bool(muted), str(audio_source or "")
        )

    def stop_video(self) -> None:
        """Thread-safe: close the video and give the HUD back to the avatar."""
        self._win._video_close_sig.emit()

    def set_video_muted(self, muted: bool) -> None:
        """Thread-safe: silence or unsilence whatever is playing."""
        self._win._video_mute_sig.emit(bool(muted))

    def video_is_playing(self) -> bool:
        return bool(self._win.video_is_playing())

    def start_camera_stream(self) -> None:
        """Thread-safe: start live camera feed in the full HUD area."""
        self._win.start_camera_stream()

    def stop_camera_stream(self) -> None:
        """Thread-safe: stop the live camera feed."""
        self._win.stop_camera_stream()

    def set_mini(self, on: bool) -> None:
        """Thread-safe: switch between the full window and the small orb."""
        self._win._mini_sig.emit(bool(on))

    def notify(self, title: str, text: str = "", level: str = "info") -> None:
        """Thread-safe: show a toast. level: info | ok | warn | err."""
        self._win._toast_sig.emit(str(title)[:80], str(text)[:240], str(level))

    def add_task(self, text: str) -> None:
        """Thread-safe: add an item to the Tasks list (e.g. when the user says
        "remind me to ..."). The list is saved in config/ui_tasks.json."""
        self._win._task_sig.emit(str(text)[:200])

    def get_tasks(self) -> list:
        """Current tasks as [{"text": str, "done": bool}, ...]."""
        try:
            return json.loads(_tasks_file().read_text(encoding="utf-8"))
        except Exception:
            return []

    @property
    def is_mini(self) -> bool:
        return bool(self._win._mini)

    @property
    def is_compact_mode(self) -> bool:
        """True when the full HUD is intentionally not available for panels."""
        return bool(self._win._mini or self._win._dock_active)

    @property
    def assistant_name(self) -> str:
        return self._win._assistant_name

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")
