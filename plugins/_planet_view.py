"""
Planet Watch's pictures — the globe, the map and the sky, drawn for the HUD.

A shared driver for planet_watch.py (the leading underscore keeps the plugin
loader from treating it as a skill). Every picture is 1600 × 1000 so it fills
the centre of the HUD the way the avatar does:

    globe   The Earth from space, lit by the real Sun — the night side is dark
            right now — at any distance: a small planet in the middle, or so
            close its curve fills the screen. Aurora glows on the night side.
    map     A map of a region: Esri's dark basemap recoloured to the HUD's
            accent, or satellite imagery, or — offline, or by choice — the
            globe's own outlines reprojected, so there is always a map.
    sky     The sky above the user as a disc: horizon at the edge, straight up
            in the middle, north marked, and the path the ISS will draw across
            it, bright where it can be seen.

One view state moves between the first two: planet_watch zooms from the globe
into the map and back out without the user noticing a switch.

LAYERS (all optional; see planet_watch.py for what fills them)
    home, pins      marked places with a label in the user's own words
    quakes          dots sized by magnitude, coloured by age
    events          GDACS / NASA disasters: cyclone, volcano, wildfire, flood,
                    drought, ice — a glyph each, ringed by the official alert level
    aircraft        live ADS-B positions, pointing where they fly (maps only)
    iss             position, the ground track and the circle it can be seen from
    lines           great-circle routes
    aurora          NOAA's aurora probability grid (globe only)
    card            readouts for the top right: [(size, colour, text)]

WHY THE GLOBE IS TEXTURE-MAPPED, NOT DRAWN AS POLYGONS
    Drawing country polygons in an orthographic projection means clipping each
    one at the horizon, and a polygon that wraps a pole (Antarctica) fills the
    whole disc when that goes wrong. So the outlines are drawn ONCE onto a flat
    equirectangular image, where there is no horizon, and each pixel of the
    globe looks up where on that image it lands. Correct at the poles and the
    date line by construction; about 0.3 s with numpy.

NOTHING HERE IS WORDS
    Numbers, arrows, SI symbols (h, d, km, min) and names as the data or the
    user gave them. The one exception is the basemap's attribution, which its
    terms require in the provider's own wording.

TEXT IS DRAWN ON THE GUI THREAD
    Pixels are computed in the caller's thread; the final QPainter pass, which
    draws text, runs on the GUI thread because fonts share the application's
    caches (the rule _build_diagram.py follows too).
"""
from __future__ import annotations

import json
import math
import random
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

W, H = 1600, 1000
BASE_DIR = Path(__file__).resolve().parent.parent
# Both kept where .gitignore already looks (memory/*_cache.json, reports/), so
# nothing a user downloads can end up in a commit.
WORLD_FILE = BASE_DIR / "memory" / "planet_watch_world_cache.json"

_UA = "MarkJARVIS-planet_watch/1.0 (open-source desktop assistant plugin)"
# Natural Earth 1:110m country outlines (public domain) as TopoJSON, 108 KB,
# fetched once and kept. Two CDNs serve the same npm package.
_WORLD_URLS = (
    "https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json",
    "https://unpkg.com/world-atlas@2/countries-110m.json",
)

_TEX_W, _TEX_H = 4096, 2048
_EARTH_M = 6_371_000.0
_EARTH_KM = 6371.0
_MERC_LAT = 85.05112878
GLOBE_R = 440.0                  # disc radius in px when the whole hemisphere shows
GLOBE_SPAN_KM = 6371.0           # ... which is this "span" (half the view's height)

# Earthquake age colours: under an hour, under a day, older.
AGE_COLOURS = ("#ff3b3b", "#ff8a1f", "#ffd23f")
LEVEL_COLOURS = {"green": "#3ddc84", "orange": "#ff9f1c", "red": "#ff3b3b"}
EVENT_COLOURS = {"cyclone": "#c49bff", "storm": "#c49bff", "volcano": "#ff5a36",
                 "wildfire": "#ffb020", "flood": "#3ea8ff", "drought": "#c8a36a", "ice": "#e6f4ff"}


def _print(text: str) -> None:
    try:
        print(f"[planet_watch] {text}")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Qt plumbing
# ─────────────────────────────────────────────────────────────────────────────

_calls: set = set()          # calls waiting for the GUI thread, kept alive until they run


def _on_gui_thread(job: Callable[[], Any], timeout: float = 20.0) -> Any:
    """Run `job` on the GUI thread and return its result (None on failure).

    The call object is moved to the GUI thread and its OWN slot is connected
    with an explicit queued connection. Connecting a plain function instead —
    the obvious way, and how this was first written — makes PyQt put the
    receiving proxy in the caller's thread, so the "GUI thread" job silently
    ran in the worker; measured when creating a widget that way hung the app.
    """
    from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal, pyqtSlot
    from PyQt6.QtGui import QGuiApplication

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
                _print(f"GUI job failed: {type(exc).__name__}: {exc}")
            finally:
                self.done.set()
                _calls.discard(self)        # released on the GUI thread, where it lives

    call = _Call()
    call.moveToThread(app.thread())
    call.fire.connect(call.run, Qt.ConnectionType.QueuedConnection)
    _calls.add(call)
    call.fire.emit()
    if not call.done.wait(timeout):
        _print("GUI job timed out")
        return None
    return call.result


def _qimage_from_array(arr: np.ndarray):
    """RGBA uint8 (h, w, 4) → an independent QImage."""
    from PyQt6.QtGui import QImage
    arr = np.ascontiguousarray(arr, dtype=np.uint8)
    h, w = arr.shape[:2]
    img = QImage(arr.data, w, h, w * 4, QImage.Format.Format_RGBA8888)
    return img.copy()


def _rgba_from_qimage(img) -> np.ndarray:
    """A QImage → RGBA uint8 (h, w, 4)."""
    from PyQt6.QtGui import QImage
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    raw = np.frombuffer(ptr, np.uint8).reshape(h, img.bytesPerLine())
    return raw[:, : w * 4].reshape(h, w, 4).copy()


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    h = hex_colour.lstrip("#")
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return 56, 214, 255


def _mix(a, b, t: float) -> tuple[int, int, int]:
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _download(url: str, timeout: float = 12.0, limit: int = 8_000_000) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(limit)


# ─────────────────────────────────────────────────────────────────────────────
# World outlines
# ─────────────────────────────────────────────────────────────────────────────

_world_lock = threading.Lock()
_world: dict = {}


def _decode_topology(topo: dict) -> dict:
    """TopoJSON countries → fill polygons, coastlines and borders.

    An arc used by one country is coast; used by two it is a border — which is
    how the coast gets drawn brighter than the borders without a second file.
    """
    tr = topo.get("transform")
    sx, sy = (tr or {}).get("scale", (1.0, 1.0))
    tx, ty = (tr or {}).get("translate", (0.0, 0.0))
    arcs: list[list[tuple[float, float]]] = []
    for arc in topo.get("arcs") or []:
        pts = []
        x = y = 0.0
        for p in arc:
            if tr:                                   # quantized: delta-encoded
                x += p[0]
                y += p[1]
                pts.append((x * sx + tx, y * sy + ty))
            else:
                pts.append((float(p[0]), float(p[1])))
        arcs.append(pts)
    uses = [0] * len(arcs)

    def ring(indices) -> list:
        out: list = []
        for i in indices:
            uses[i if i >= 0 else ~i] += 1
            pts = arcs[i] if i >= 0 else arcs[~i][::-1]
            out.extend(pts if not out else pts[1:])
        return out

    polys: list = []
    geoms = ((topo.get("objects") or {}).get("countries") or {}).get("geometries") or []
    for g in geoms:
        kind = g.get("type")
        if kind == "Polygon":
            polys.append([ring(r) for r in g.get("arcs") or []])
        elif kind == "MultiPolygon":
            polys.extend([ring(r) for r in p] for p in g.get("arcs") or [])
    return {"polys": polys,
            "coast": [arcs[i] for i, n in enumerate(uses) if n == 1],
            "border": [arcs[i] for i, n in enumerate(uses) if n >= 2]}


def load_world() -> Optional[dict]:
    """The decoded outlines, from the kept file or downloaded once. None when
    neither is possible — the pictures are then drawn without them."""
    with _world_lock:
        if _world.get("data"):
            return _world["data"]
        if _world.get("failed_at") and time.time() - _world["failed_at"] < 300:
            return None
        topo = None
        try:
            topo = json.loads(WORLD_FILE.read_text(encoding="utf-8"))
        except Exception:
            for url in _WORLD_URLS:
                try:
                    raw = _download(url)
                    topo = json.loads(raw.decode("utf-8"))
                    WORLD_FILE.parent.mkdir(parents=True, exist_ok=True)
                    WORLD_FILE.write_bytes(raw)
                    break
                except Exception as exc:
                    _print(f"world outlines not fetched from {url}: {type(exc).__name__}")
        try:
            data = _decode_topology(topo) if topo else None
        except Exception as exc:
            _print(f"world outlines unreadable: {exc}")
            data = None
        if data and data["polys"]:
            _world["data"] = data
            return data
        _world["failed_at"] = time.time()
        return None


def world_available() -> bool:
    return load_world() is not None


# ─────────────────────────────────────────────────────────────────────────────
# The flat Earth texture every picture samples
# ─────────────────────────────────────────────────────────────────────────────

_tex_lock = threading.Lock()
_tex_cache: dict = {}

_OCEAN = (6, 16, 26)
_LAND = (16, 38, 52)


def _unwrap(ring: list) -> list:
    out = [[ring[0][0], ring[0][1]]]
    for lon, lat in ring[1:]:
        prev = out[-1][0]
        while lon - prev > 180:
            lon -= 360
        while lon - prev < -180:
            lon += 360
        out.append([lon, lat])
    return out


def _texture(accent: str) -> tuple[np.ndarray, bool]:
    """(equirectangular RGB Earth, outlines present). Built once per accent."""
    with _tex_lock:
        hit = _tex_cache.get(accent)
        if hit is not None:
            return hit
        world = load_world()
        from PyQt6.QtCore import QPointF, Qt
        from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

        acc = _rgb(accent)
        img = QImage(_TEX_W, _TEX_H, QImage.Format.Format_RGB32)
        img.fill(QColor(*_OCEAN))
        sx, sy = _TEX_W / 360.0, _TEX_H / 180.0

        def pt(lon: float, lat: float) -> QPointF:
            return QPointF((lon + 180.0) * sx, (90.0 - lat) * sy)

        p = QPainter(img)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(QPen(QColor(*acc, 34), 1.2))
            for lon in range(-180, 181, 15):
                p.drawLine(pt(lon, 90), pt(lon, -90))
            for lat in range(-75, 76, 15):
                p.drawLine(pt(-180, lat), pt(180, lat))
            p.setPen(QPen(QColor(*acc, 60), 1.6))
            p.drawLine(pt(-180, 0), pt(180, 0))

            if world:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(*_mix(_LAND, acc, 0.10)))
                for poly in world["polys"]:
                    rings = [_unwrap(r) for r in poly if len(r) > 2]
                    if not rings:
                        continue
                    for r in rings:
                        if abs(r[-1][0] - r[0][0]) > 180:     # wraps a pole: close along it
                            pole = -90.0 if sum(q[1] for q in r) < 0 else 90.0
                            r.extend([[r[-1][0], pole], [r[0][0], pole]])
                    lo = min(q[0] for r in rings for q in r)
                    hi = max(q[0] for r in rings for q in r)
                    for shift in (-360.0, 0.0, 360.0):
                        if hi + shift <= -180 or lo + shift >= 180:
                            continue
                        path = QPainterPath()
                        path.setFillRule(Qt.FillRule.OddEvenFill)
                        for r in rings:
                            path.moveTo(pt(r[0][0] + shift, r[0][1]))
                            for lon, lat in r[1:]:
                                path.lineTo(pt(lon + shift, lat))
                            path.closeSubpath()
                        p.drawPath(path)

                p.setBrush(Qt.BrushStyle.NoBrush)
                for lines, colour, width in ((world["border"], QColor(*acc, 80), 1.5),
                                             (world["coast"], QColor(*acc, 185), 2.2)):
                    p.setPen(QPen(colour, width))
                    for arc in lines:
                        path = QPainterPath()
                        path.moveTo(pt(*arc[0]))
                        for (l0, _), (l1, lat) in zip(arc, arc[1:]):
                            if abs(l1 - l0) > 180:           # the date line: lift the pen
                                path.moveTo(pt(l1, lat))
                            else:
                                path.lineTo(pt(l1, lat))
                        p.drawPath(path)
        finally:
            p.end()
        result = (_rgba_from_qimage(img)[..., :3].copy(), bool(world))
        _tex_cache.clear()                        # one accent at a time (24 MB)
        _tex_cache[accent] = result
        return result


def _sample(tex: np.ndarray, lat_deg: np.ndarray, lon_deg: np.ndarray) -> np.ndarray:
    """Bilinear lookup of the texture at many (lat, lon) → float32 (n, 3)."""
    th, tw = tex.shape[:2]
    u = ((lon_deg + 180.0) % 360.0) / 360.0 * tw - 0.5
    v = (90.0 - lat_deg) / 180.0 * th - 0.5
    u0 = np.floor(u)
    v0 = np.floor(v)
    fu = (u - u0)[:, None].astype(np.float32)
    fv = (v - v0)[:, None].astype(np.float32)
    u0 = u0.astype(np.int64)
    v0 = v0.astype(np.int64)
    ua, ub = u0 % tw, (u0 + 1) % tw
    va, vb = np.clip(v0, 0, th - 1), np.clip(v0 + 1, 0, th - 1)
    return ((tex[va, ua] * (1 - fu) + tex[va, ub] * fu) * (1 - fv)
            + (tex[vb, ua] * (1 - fu) + tex[vb, ub] * fu) * fv).astype(np.float32)


def _sample_grid(grid: np.ndarray, lat_deg: np.ndarray, lon_deg: np.ndarray) -> np.ndarray:
    """Bilinear lookup in a 1° grid: rows latitude −90…90, columns longitude 0…359."""
    r = np.clip(lat_deg + 90.0, 0.0, 180.0)
    c = np.mod(lon_deg, 360.0)
    r0 = np.floor(r).astype(np.int64)
    c0 = np.floor(c).astype(np.int64) % 360
    fr, fc = (r - r0).astype(np.float32), (c - np.floor(c)).astype(np.float32)
    r1 = np.minimum(r0 + 1, 180)
    c1 = (c0 + 1) % 360
    return ((grid[r0, c0] * (1 - fc) + grid[r0, c1] * fc) * (1 - fr)
            + (grid[r1, c0] * (1 - fc) + grid[r1, c1] * fc) * fr)


# ─────────────────────────────────────────────────────────────────────────────
# Geometry
# ─────────────────────────────────────────────────────────────────────────────

def destination(lat: float, lon: float, bearing: float, metres: float) -> tuple[float, float]:
    d = metres / _EARTH_M
    b = math.radians(bearing)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(max(-1.0, min(1.0, math.sin(p1) * math.cos(d)
                                 + math.cos(p1) * math.sin(d) * math.cos(b))))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1),
                         math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


def small_circle(lat: float, lon: float, metres: float, n: int = 120) -> list:
    return [destination(lat, lon, 360.0 * i / n, metres) for i in range(n + 1)]


def great_circle(a: tuple, b: tuple, n: int = 160) -> list:
    """Points along the shortest path on the sphere from a to b."""
    def vec(lat, lon):
        p, l = math.radians(lat), math.radians(lon)
        return np.array([math.cos(p) * math.cos(l), math.cos(p) * math.sin(l), math.sin(p)])
    v1, v2 = vec(*a), vec(*b)
    om = math.acos(max(-1.0, min(1.0, float(v1 @ v2))))
    if om < 1e-9:
        return [a, b]
    out = []
    for i in range(n + 1):
        t = i / n
        v = (math.sin((1 - t) * om) * v1 + math.sin(t * om) * v2) / math.sin(om)
        out.append((math.degrees(math.asin(max(-1.0, min(1.0, v[2])))),
                    math.degrees(math.atan2(v[1], v[0]))))
    return out


def _ortho(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float, bool]:
    p, l = math.radians(lat), math.radians(lon)
    p0, l0 = math.radians(lat0), math.radians(lon0)
    cosc = math.sin(p0) * math.sin(p) + math.cos(p0) * math.cos(p) * math.cos(l - l0)
    x = math.cos(p) * math.sin(l - l0)
    y = math.cos(p0) * math.sin(p) - math.sin(p0) * math.cos(p) * math.cos(l - l0)
    return x, y, cosc >= 0.0


def globe_radius(span_km: float) -> float:
    return GLOBE_R * GLOBE_SPAN_KM / max(500.0, span_km)


# ─────────────────────────────────────────────────────────────────────────────
# Drawing primitives
# ─────────────────────────────────────────────────────────────────────────────

def _fonts():
    from PyQt6.QtGui import QFont, QGuiApplication
    base = QFont(QGuiApplication.font())
    big, mid, small = QFont(base), QFont(base), QFont(base)
    big.setPixelSize(30)
    big.setBold(True)
    mid.setPixelSize(21)
    small.setPixelSize(17)
    return big, mid, small


def _background(p, accent) -> None:
    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QBrush, QColor, QPen, QRadialGradient
    p.fillRect(0, 0, W, H, QColor("#03070b"))
    p.setPen(QPen(QColor(*accent, 16), 1))
    for x in range(0, W, 40):
        p.drawLine(x, 0, x, H)
    for y in range(0, H, 40):
        p.drawLine(0, y, W, y)
    g = QRadialGradient(QPointF(W / 2, H / 2), W * 0.62)
    g.setColorAt(0.0, QColor(0, 0, 0, 0))
    g.setColorAt(1.0, QColor(0, 0, 0, 170))
    p.fillRect(0, 0, W, H, QBrush(g))


def _frame(p, accent) -> None:
    """Corner brackets and a ruler of ticks along the top and bottom edges."""
    from PyQt6.QtGui import QColor, QPen
    p.setPen(QPen(QColor(*accent, 210), 3))
    m, L = 18, 46
    for (x, y, dx, dy) in ((m, m, 1, 1), (W - m, m, -1, 1), (m, H - m, 1, -1), (W - m, H - m, -1, -1)):
        p.drawLine(x, y, x + dx * L, y)
        p.drawLine(x, y, x, y + dy * L)
    p.setPen(QPen(QColor(*accent, 90), 1))
    for x in range(80, W - 79, 20):
        t = 10 if x % 100 == 0 else 5
        p.drawLine(x, m, x, m + t)
        p.drawLine(x, H - m, x, H - m - t)


def _glass(p, rect, accent) -> None:
    """The HUD's panel: dark, translucent, a hairline of the accent."""
    from PyQt6.QtGui import QColor, QPen
    p.setPen(QPen(QColor(*accent, 70), 1.2))
    p.setBrush(QColor(3, 8, 14, 196))
    p.drawRoundedRect(rect, 8, 8)


def _panel_text(p, x: float, y: float, lines: list, align_right: bool = False,
                accent=None) -> float:
    """Stacked readouts: [(font, QColor, text), ...] from (x, y) downwards, on a
    glass panel so a map's own labels never run through them. Returns the
    bottom edge."""
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QFontMetricsF
    lines = [ln for ln in lines if ln[2]]
    if not lines:
        return y
    width = max(QFontMetricsF(f).horizontalAdvance(t) for f, _, t in lines)
    height = sum(f.pixelSize() + 10 for f, _, _ in lines)
    pad = 14
    box = QRectF((x - width - pad) if align_right else (x - pad), y - pad / 2,
                 width + 2 * pad, height + pad)
    if accent is not None:
        _glass(p, box, accent)
    flags = (Qt.AlignmentFlag.AlignRight if align_right else Qt.AlignmentFlag.AlignLeft) \
        | Qt.AlignmentFlag.AlignVCenter
    for font, colour, text in lines:
        p.setFont(font)
        h = font.pixelSize() + 10
        p.setPen(colour)
        rect = QRectF(x - 900, y, 900, h) if align_right else QRectF(x, y, 900, h)
        p.drawText(rect, flags, text)
        y += h
    return box.bottom()


_halo_paths: dict = {}           # (text, font) → outline path; used on the GUI thread only


def _halo_text(p, rect, flags, text: str, colour) -> None:
    """Text with a dark outline, legible on any map or photo underneath."""
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QColor, QFontMetricsF, QPainterPath, QPen
    fm = QFontMetricsF(p.font())
    w = fm.horizontalAdvance(text)
    if flags & Qt.AlignmentFlag.AlignRight:
        x = rect.right() - w
    elif flags & Qt.AlignmentFlag.AlignHCenter:
        x = rect.center().x() - w / 2
    else:
        x = rect.left()
    y = rect.center().y() + (fm.ascent() - fm.descent()) / 2
    key = (text, p.font().key())
    path = _halo_paths.get(key)
    if path is None:                  # glyph outlines are the costly part: make them once
        path = QPainterPath()
        path.addText(QPointF(0, 0), p.font(), text)
        if len(_halo_paths) > 600:
            _halo_paths.clear()
        _halo_paths[key] = path
    p.save()
    p.translate(x, y)
    p.setPen(QPen(QColor(2, 6, 10, 220), 4.5))
    p.setBrush(QColor(0, 0, 0, 0))
    p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(colour))
    p.drawPath(path)
    p.restore()


def _place_label(p, placed: list, x: float, y: float, r: float, text: str, colour, font) -> None:
    """A label beside a marker, in the first spot no earlier label occupies."""
    from PyQt6.QtCore import QRectF, Qt
    p.setFont(font)
    w = p.fontMetrics().horizontalAdvance(text) + 8
    h = font.pixelSize() + 8
    for dx, dy in ((r + 6, -h / 2), (-r - 6 - w, -h / 2), (-w / 2, r + 3), (-w / 2, -r - h - 3)):
        rect = QRectF(x + dx, y + dy, w, h)
        if not any(rect.intersects(o) for o in placed):
            placed.append(rect)
            _halo_text(p, rect, Qt.AlignmentFlag.AlignCenter, text, colour)
            return


def quake_style(mag: float, age_s: float) -> tuple[str, float]:
    colour = AGE_COLOURS[0] if age_s < 3600 else AGE_COLOURS[1] if age_s < 86400 else AGE_COLOURS[2]
    return colour, max(4.0, min(30.0, 4.0 + (mag - 2.0) * 3.4))


def _draw_quake(p, x: float, y: float, mag: float, age_s: float, focus: bool = False) -> None:
    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QColor, QPen
    colour, r = quake_style(mag, age_s)
    c = QColor(colour)
    if age_s < 3600 or focus:
        for k, a in ((2.6, 40), (1.8, 80)):
            halo = QColor(c)
            halo.setAlpha(a)
            p.setPen(QPen(halo, 2))
            p.setBrush(QColor(0, 0, 0, 0))
            p.drawEllipse(QPointF(x, y), r * k, r * k)
    fill = QColor(c)
    fill.setAlpha(150)
    p.setPen(QPen(c, 2))
    p.setBrush(fill)
    p.drawEllipse(QPointF(x, y), r, r)


def _event_path(kind: str, s: float):
    """The glyph for a kind of event, centred on 0,0, about 2s across."""
    from PyQt6.QtCore import QPointF, QRectF
    from PyQt6.QtGui import QPainterPath, QPolygonF
    path = QPainterPath()
    if kind == "volcano":
        path.addPolygon(QPolygonF([QPointF(0, -s), QPointF(s, s * 0.8), QPointF(-s, s * 0.8)]))
        path.closeSubpath()
    elif kind == "wildfire":
        path.moveTo(0, -s * 1.1)
        path.cubicTo(s * 0.9, -s * 0.2, s * 0.8, s * 0.9, 0, s)
        path.cubicTo(-s * 0.8, s * 0.9, -s * 0.9, -s * 0.2, 0, -s * 1.1)
    elif kind in ("cyclone", "storm"):
        path.addEllipse(QRectF(-s * 0.35, -s * 0.35, s * 0.7, s * 0.7))
        path.moveTo(0, -s)
        path.arcTo(QRectF(-s, -s, 2 * s, 2 * s), 90, 120)
        path.moveTo(0, s)
        path.arcTo(QRectF(-s, -s, 2 * s, 2 * s), 270, 120)
    elif kind == "flood":
        for k in (-0.5, 0.1, 0.7):
            path.moveTo(-s, k * s)
            path.cubicTo(-s * 0.5, (k - 0.35) * s, s * 0.5, (k + 0.35) * s, s, k * s)
    elif kind == "ice":
        pts = [QPointF(s * math.cos(math.radians(a)), s * math.sin(math.radians(a))) for a in range(30, 390, 60)]
        path.addPolygon(QPolygonF(pts))
        path.closeSubpath()
    else:                                   # drought and anything unknown: a square
        path.addRect(QRectF(-s * 0.8, -s * 0.8, s * 1.6, s * 1.6))
    return path


def _draw_event(p, x: float, y: float, kind: str, level: str, s: float = 10.0) -> None:
    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QColor, QPen
    colour = QColor(EVENT_COLOURS.get(kind, "#ffffff"))
    ring = LEVEL_COLOURS.get(level or "")
    if ring and level != "green":
        halo = QColor(ring)
        p.setPen(QPen(halo, 2.5))
        halo.setAlpha(50)
        p.setBrush(halo)
        p.drawEllipse(QPointF(x, y), s * 1.9, s * 1.9)
    p.save()
    p.translate(x, y)
    path = _event_path(kind, s)
    open_shape = kind in ("flood", "cyclone", "storm")
    p.setPen(QPen(QColor(2, 6, 10, 230), 5 if open_shape else 3))
    p.setBrush(QColor(0, 0, 0, 0))
    p.drawPath(path)
    p.setPen(QPen(colour, 2.4))
    fill = QColor(colour)
    fill.setAlpha(0 if open_shape else 200)
    p.setBrush(fill)
    p.drawPath(path)
    p.restore()


def _draw_plane(p, x: float, y: float, track: Optional[float], colour) -> None:
    from PyQt6.QtGui import QColor, QPainterPath, QPen
    p.save()
    p.translate(x, y)
    p.rotate(track or 0.0)
    shape = QPainterPath()
    shape.moveTo(0, -11)
    shape.lineTo(7, 8)
    shape.lineTo(0, 4)
    shape.lineTo(-7, 8)
    shape.closeSubpath()
    p.setPen(QPen(QColor("#03070b"), 1.6))
    p.setBrush(QColor(colour))
    p.drawPath(shape)
    p.restore()


def _draw_pin(p, x: float, y: float, accent) -> None:
    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QColor, QPen, QPolygonF
    p.setPen(QPen(QColor("#ffffff"), 2))
    p.setBrush(QColor(*_mix(accent, (255, 255, 255), 0.3)))
    p.drawPolygon(QPolygonF([QPointF(x, y - 11), QPointF(x + 8, y), QPointF(x, y + 11), QPointF(x - 8, y)]))


def _draw_home(p, x: float, y: float, accent) -> None:
    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QColor, QPen
    col = QColor(*accent)
    p.setPen(QPen(QColor("#ffffff"), 2))
    p.setBrush(col)
    p.drawEllipse(QPointF(x, y), 7, 7)
    p.setPen(QPen(col, 2))
    p.setBrush(QColor(0, 0, 0, 0))
    p.drawEllipse(QPointF(x, y), 16, 16)
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        p.drawLine(QPointF(x + dx * 20, y + dy * 20), QPointF(x + dx * 30, y + dy * 30))


def _draw_iss(p, x: float, y: float, heading: float, accent) -> None:
    """A small station: a body and two solar wings, turned along its track."""
    from PyQt6.QtCore import QPointF, QRectF
    from PyQt6.QtGui import QBrush, QColor, QPen, QRadialGradient
    glow = QRadialGradient(QPointF(x, y), 40)
    glow.setColorAt(0, QColor(*accent, 130))
    glow.setColorAt(1, QColor(*accent, 0))
    p.setPen(QPen(QColor(0, 0, 0, 0)))
    p.setBrush(QBrush(glow))
    p.drawEllipse(QPointF(x, y), 40, 40)
    p.save()
    p.translate(x, y)
    p.rotate(heading)
    p.setPen(QPen(QColor("#03070b"), 1.5))
    p.setBrush(QColor("#ffffff"))
    p.drawRect(QRectF(-4, -9, 8, 18))
    p.setBrush(QColor(*_mix(accent, (255, 255, 255), 0.35)))
    p.drawRect(QRectF(-22, -5, 15, 10))
    p.drawRect(QRectF(7, -5, 15, 10))
    p.restore()


def _draw_runs(p, runs: list, pen) -> None:
    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QColor, QPainterPath
    p.setPen(pen)
    p.setBrush(QColor(0, 0, 0, 0))
    for run in runs:
        if len(run) < 2:
            continue
        path = QPainterPath()
        path.moveTo(QPointF(*run[0]))
        for q in run[1:]:
            path.lineTo(QPointF(*q))
        p.drawPath(path)


# ─────────────────────────────────────────────────────────────────────────────
# Layers — one drawing pass shared by the globe and the map
# ─────────────────────────────────────────────────────────────────────────────

def _draw_layers(p, layers: dict, acc, where: Callable, runs: Callable, fonts) -> None:
    """`where(lat, lon)` → (x, y) or None when not on screen; `runs(points)` →
    screen polylines, already split at the horizon or the date line."""
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QColor, QPen
    big, mid, small = fonts
    light = _mix(acc, (255, 255, 255), 0.45)

    iss = layers.get("iss")
    if iss:
        if iss.get("footprint_m"):
            pen = QPen(QColor(*acc, 120), 1.6)
            pen.setStyle(Qt.PenStyle.DashLine)
            _draw_runs(p, runs(small_circle(iss["lat"], iss["lon"], iss["footprint_m"])), pen)
        _draw_runs(p, runs(iss.get("track_past") or []), QPen(QColor(*acc, 110), 2.2, Qt.PenStyle.DotLine))
        _draw_runs(p, runs(iss.get("track_next") or []), QPen(QColor(*light, 230), 2.6))
    home = layers.get("home")
    if home and layers.get("radius_m"):
        pen = QPen(QColor(*acc, 170), 2)
        pen.setStyle(Qt.PenStyle.DashLine)
        _draw_runs(p, runs(small_circle(home["lat"], home["lon"], layers["radius_m"])), pen)
    for line in layers.get("lines") or []:
        pen = QPen(QColor(*_mix(acc, (255, 255, 255), 0.6)), 3.2)
        _draw_runs(p, runs(line), QPen(QColor(2, 6, 10, 200), 6.5))
        _draw_runs(p, runs(line), pen)

    for e in layers.get("events") or []:
        pos = where(e["lat"], e["lon"])
        if pos:
            _draw_event(p, pos[0], pos[1], e["kind"], e.get("level", ""))
    quakes = layers.get("quakes") or []
    focus = layers.get("focus")
    for q in sorted(quakes, key=lambda q: -q["age_s"]):          # newest on top
        pos = where(q["lat"], q["lon"])
        if pos:
            _draw_quake(p, pos[0], pos[1], q["mag"] or 0, q["age_s"], focus is not None and q is focus)
    planes = layers.get("aircraft") or []
    for a in planes:
        pos = where(a["lat"], a["lon"])
        if pos:
            _draw_plane(p, pos[0], pos[1], a.get("track"),
                        "#ffb020" if a.get("military") else "#f2f8fc" if not a.get("on_ground") else "#61717d")

    placed: list = []
    for pin in layers.get("pins") or []:
        pos = where(pin["lat"], pin["lon"])
        if pos:
            _draw_pin(p, pos[0], pos[1], acc)
            placed.append(QRectF(pos[0] - 12, pos[1] - 12, 24, 24))
    if home:
        pos = where(home["lat"], home["lon"])
        if pos:
            _draw_home(p, pos[0], pos[1], acc)
            placed.append(QRectF(pos[0] - 30, pos[1] - 30, 60, 60))
    if iss:
        pos = where(iss["lat"], iss["lon"])
        if pos:
            heading = 0.0
            for lat, lon in iss.get("track_next") or []:
                nxt = where(lat, lon)
                if nxt and math.hypot(nxt[0] - pos[0], nxt[1] - pos[1]) > 6:
                    heading = math.degrees(math.atan2(nxt[0] - pos[0], -(nxt[1] - pos[1])))
                    break
            _draw_iss(p, pos[0], pos[1], heading, acc)
            placed.append(QRectF(pos[0] - 24, pos[1] - 24, 48, 48))

    # labels, most important first, each in the first free spot
    if home and home.get("label"):
        pos = where(home["lat"], home["lon"])
        if pos:
            _place_label(p, placed, pos[0], pos[1], 30, home["label"], "#eaf6ff", mid)
    for pin in layers.get("pins") or []:
        pos = where(pin["lat"], pin["lon"])
        if pos and pin.get("label"):
            _place_label(p, placed, pos[0], pos[1], 14, pin["label"], "#eaf6ff", mid)
    for q in sorted(quakes, key=lambda q: -(q.get("mag") or 0))[:8]:
        pos = where(q["lat"], q["lon"])
        if pos:
            colour, r = quake_style(q["mag"] or 0, q["age_s"])
            _place_label(p, placed, pos[0], pos[1], r, f"{q['mag']:.1f}", colour, small)
    for e in sorted(layers.get("events") or [], key=lambda e: -e.get("rank", 0))[:6]:
        pos = where(e["lat"], e["lon"])
        if pos and e.get("label"):
            _place_label(p, placed, pos[0], pos[1], 16, e["label"], EVENT_COLOURS.get(e["kind"], "#fff"), small)
    center = (W / 2, H / 2)
    near_first = sorted((a for a in planes if not a.get("on_ground")),
                        key=lambda a: math.hypot(*[(where(a["lat"], a["lon"]) or (1e9, 1e9))[i] - center[i]
                                                   for i in (0, 1)]))
    for a in near_first[:18]:
        pos = where(a["lat"], a["lon"])
        if pos and a.get("label"):
            _place_label(p, placed, pos[0], pos[1], 12, a["label"], "#cfe2ee", small)


def _legends(p, layers: dict, acc, small) -> None:
    """Bottom-left: what the colours and glyphs mean, as symbols and counts."""
    from PyQt6.QtCore import QPointF, QRectF, Qt
    from PyQt6.QtGui import QColor, QFontMetricsF, QPen
    rows = []
    quakes = layers.get("quakes") or []
    if quakes:
        rows.append("quakes")
    kinds: dict = {}
    for e in layers.get("events") or []:
        kind = "cyclone" if e["kind"] == "storm" else e["kind"]        # one glyph, one row
        kinds[kind] = kinds.get(kind, 0) + 1
    if kinds:
        rows.append("events")
    planes = [a for a in layers.get("aircraft") or [] if not a.get("on_ground")]
    if planes:
        rows.append("aircraft")
    if not rows:
        return
    fm = QFontMetricsF(small)
    y = H - 44 - 34 * len(rows) - (34 if quakes else 0)
    _glass(p, QRectF(30, y - 10, 520, 34 * len(rows) + (34 if quakes else 0) + 18), acc)
    p.setFont(small)
    x0 = 44
    for row in rows:
        cx = x0
        if row == "quakes":
            ages = [q["age_s"] for q in quakes]
            present = (any(a < 3600 for a in ages), any(3600 <= a < 86400 for a in ages),
                       any(a >= 86400 for a in ages))
            for colour, label, show in zip(AGE_COLOURS, ("< 1 h", "< 24 h", "> 24 h"), present):
                if not show:
                    continue
                c = QColor(colour)
                p.setPen(QPen(c, 2))
                f = QColor(c)
                f.setAlpha(150)
                p.setBrush(f)
                p.drawEllipse(QPointF(cx + 8, y + 12), 8, 8)
                p.setPen(QColor("#b9cbd8"))
                p.drawText(QRectF(cx + 24, y, 160, 24), Qt.AlignmentFlag.AlignVCenter, label)
                cx += 30 + fm.horizontalAdvance(label) + 24
            y += 34
            mags = [q["mag"] for q in quakes if q.get("mag") is not None]
            p.setPen(QColor("#8ea6b8"))
            text = f"●  {len(quakes)}" + (f"   ·   M {min(mags):.1f} – {max(mags):.1f}" if mags else "")
            p.drawText(QRectF(x0, y, 500, 24), Qt.AlignmentFlag.AlignVCenter, text)
        elif row == "events":
            for kind, n in sorted(kinds.items(), key=lambda kv: -kv[1]):
                _draw_event(p, cx + 10, y + 12, kind, "", 8)
                p.setPen(QColor("#b9cbd8"))
                p.setFont(small)
                label = f"× {n}"
                p.drawText(QRectF(cx + 24, y, 80, 24), Qt.AlignmentFlag.AlignVCenter, label)
                cx += 34 + fm.horizontalAdvance(label) + 18
        elif row == "aircraft":
            _draw_plane(p, cx + 10, y + 12, 45.0, "#f2f8fc")
            p.setPen(QColor("#b9cbd8"))
            p.drawText(QRectF(cx + 26, y, 200, 24), Qt.AlignmentFlag.AlignVCenter, f"× {len(planes)}")
        y += 34


def _card(p, layers: dict, acc, fonts) -> None:
    """Top-right readouts, given as [(size, colour or None, text)]."""
    from PyQt6.QtGui import QColor
    size = {"big": fonts[0], "mid": fonts[1], "small": fonts[2]}
    lines = [(size.get(s, fonts[2]), QColor(c) if c else QColor("#8ea6b8"), t)
             for s, c, t in layers.get("card") or []]
    if lines:
        _panel_text(p, W - 44, 40, lines, align_right=True, accent=acc)


def _stamp_panel(p, layers: dict, acc, fonts, lat0: float, lon0: float) -> None:
    from PyQt6.QtGui import QColor
    big, mid, small = fonts
    white, dim = QColor("#eaf6ff"), QColor("#8ea6b8")
    _panel_text(p, 44, 40, [(big, white, layers.get("stamp_time") or ""),
                            (small, dim, layers.get("stamp_date") or ""),
                            (small, dim, f"◉ {lat0:.4f}, {lon0:.4f}"),
                            (small, dim, layers.get("stamp_extra") or "")], accent=acc)


# ─────────────────────────────────────────────────────────────────────────────
# Globe
# ─────────────────────────────────────────────────────────────────────────────

def _globe_layer(tex: np.ndarray, lat0: float, lon0: float, R: float, cx: float, cy: float,
                 sun: Optional[tuple], accent, aurora: Optional[np.ndarray]) -> np.ndarray:
    """The lit globe and its atmosphere as a W×H RGBA layer. Only the part of
    the disc that lands on the canvas is computed — close up, most is not."""
    atm = 1.09
    out = np.zeros((H, W, 4), np.uint8)
    x0, x1 = max(0, int(cx - R * atm) - 1), min(W, int(cx + R * atm) + 2)
    y0, y1 = max(0, int(cy - R * atm) - 1), min(H, int(cy + R * atm) + 2)
    if x1 <= x0 or y1 <= y0:
        return out
    ys, xs = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    nx = (xs + 0.5 - cx) / R
    ny = (cy - ys - 0.5) / R
    rho = np.sqrt(nx * nx + ny * ny)
    region = np.zeros((y1 - y0, x1 - x0, 4), np.float32)

    acc = np.array(accent, np.float32)
    ring = (rho > 1.0) & (rho < atm)                         # atmosphere behind the disc
    region[ring, :3] = acc
    region[ring, 3] = np.clip(1.0 - (rho[ring] - 1.0) / (atm - 1.0), 0, 1) ** 2.4 * 0.55

    inside = rho <= 1.0 + 1.0 / R
    ix, iy = nx[inside], ny[inside]
    nz = np.sqrt(np.clip(1.0 - ix * ix - iy * iy, 0.0, 1.0))
    p0, l0 = math.radians(lat0), math.radians(lon0)
    lat = np.arcsin(np.clip(nz * math.sin(p0) + iy * math.cos(p0), -1, 1))
    lon = l0 + np.arctan2(ix, nz * math.cos(p0) - iy * math.sin(p0))
    lat_d, lon_d = np.degrees(lat), np.degrees(lon)
    colour = _sample(tex, lat_d, lon_d)

    shade = 0.58 + 0.42 * np.sqrt(nz)                          # limb darkening
    night = np.zeros_like(nz)
    if sun is not None:
        sl, sn = math.radians(sun[0]), math.radians(sun[1])
        d = (np.cos(lat) * np.cos(lon) * math.cos(sl) * math.cos(sn)
             + np.cos(lat) * np.sin(lon) * math.cos(sl) * math.sin(sn)
             + np.sin(lat) * math.sin(sl))
        day = np.clip((d + 0.10) / 0.18, 0, 1)
        day = day * day * (3 - 2 * day)                        # smoothstep through twilight
        night = 1.0 - day
        shade = shade * (0.30 + 0.70 * day)
        colour = colour + (night * 0.05)[:, None] * acc
    colour = colour * shade[:, None]
    if aurora is not None:
        glow = np.clip(_sample_grid(aurora, lat_d, lon_d), 0, 1) * (0.25 + 0.75 * night)
        colour = colour + glow[:, None] * np.array((60.0, 255.0, 150.0), np.float32) * 0.95
    colour = colour + (np.clip(1.0 - nz, 0, 1) ** 3 * 0.45)[:, None] * acc   # rim light
    edge = np.clip((1.0 - rho[inside]) * R + 0.5, 0.0, 1.0)

    prev_rgb, prev_a = region[inside, :3], region[inside, 3]
    a = edge + prev_a * (1 - edge)
    region[inside, :3] = (colour * edge[:, None] + prev_rgb * (prev_a * (1 - edge))[:, None]) \
        / np.maximum(a, 1e-6)[:, None]
    region[inside, 3] = a
    region[..., :3] = np.clip(region[..., :3], 0, 255)
    region[..., 3] = np.clip(region[..., 3] * 255, 0, 255)
    out[y0:y1, x0:x1] = region.astype(np.uint8)
    return out


def render_globe(out_path: Path, *, center: tuple, layers: dict, accent: str,
                 span_km: float = GLOBE_SPAN_KM) -> Optional[str]:
    """The globe centred on `center` (lat, lon). `span_km` is how much of the
    Earth the view's half-height covers: 6371 shows the whole hemisphere,
    smaller comes closer until the curve fills the screen."""
    acc = _rgb(accent)
    lat0, lon0 = float(center[0]), float(center[1])
    R = globe_radius(span_km)
    cx, cy = W / 2.0, H / 2.0 + 10
    try:
        tex, _ = _texture(accent)
        globe = _globe_layer(tex, lat0, lon0, R, cx, cy, layers.get("sun"), acc, layers.get("aurora"))
    except Exception as exc:
        _print(f"globe not drawn: {type(exc).__name__}: {exc}")
        return None

    def proj(lat, lon):
        x, y, vis = _ortho(lat, lon, lat0, lon0)
        return cx + x * R, cy - y * R, vis

    def where(lat, lon):
        x, y, vis = proj(lat, lon)
        return (x, y) if vis and -80 <= x <= W + 80 and -80 <= y <= H + 80 else None

    def runs(points):
        out, cur = [], []
        for lat, lon in points:
            x, y, vis = proj(lat, lon)
            if vis:
                cur.append((x, y))
            elif cur:
                out.append(cur)
                cur = []
        if cur:
            out.append(cur)
        return out

    def job():
        from PyQt6.QtGui import QImage, QPainter
        img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
        p = QPainter(img)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            fonts = _fonts()
            _background(p, acc)
            p.drawImage(0, 0, _qimage_from_array(globe))
            _draw_layers(p, layers, acc, where, runs, fonts)
            _frame(p, acc)
            _stamp_panel(p, layers, acc, fonts, lat0, lon0)
            _card(p, layers, acc, fonts)
            _legends(p, layers, acc, fonts[2])
        finally:
            p.end()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        return str(out_path) if img.save(str(out_path), "PNG") else None

    try:
        return _on_gui_thread(job)
    except Exception as exc:
        _print(f"globe not drawn: {type(exc).__name__}: {exc}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Map
# ─────────────────────────────────────────────────────────────────────────────

_ESRI = "https://services.arcgisonline.com/ArcGIS/rest/services/"
# Esri's public basemaps: no key for light, non-commercial use, attribution
# required (read from each service, so it is always the current wording).
# CARTO was the first choice and was measured on 2026-09-21 stamping every
# keyless tile with "API KEY REQUIRED". Esri's export draws exactly the view
# asked for in one request, which also avoids stitching dozens of tiles.
MAP_STYLES = {
    "dark": {"base": "Canvas/World_Dark_Gray_Base", "labels": "Canvas/World_Dark_Gray_Reference",
             "tint": True},
    "satellite": {"base": "World_Imagery", "labels": "Reference/World_Boundaries_and_Places",
                  "tint": False},
}
_MAP_DIR = BASE_DIR / "reports" / "planet_watch" / "basemaps"
_MAP_KEEP = 80
_MERC_R = 6_378_137.0
_MERC_MAX = math.pi * _MERC_R
_credits: dict = {}


def _merc(lat: float, lon: float) -> tuple[float, float]:
    lat = max(-_MERC_LAT, min(_MERC_LAT, lat))
    return (_MERC_R * math.radians(lon),
            _MERC_R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def map_view(lat0: float, lon0: float, radius_m: float) -> dict:
    """The Web-Mercator window, W×H px, that shows `radius_m` around a point."""
    lat0 = max(-80.0, min(80.0, lat0))
    ground = max(0.25, 2.0 * radius_m / (0.82 * H))              # metres per pixel
    mpp = min(ground / max(0.05, math.cos(math.radians(lat0))), 2 * _MERC_MAX / H)
    cx, cy = _merc(lat0, lon0)
    cy = max(-_MERC_MAX + H / 2 * mpp, min(_MERC_MAX - H / 2 * mpp, cy))
    return {"x0": cx - W / 2 * mpp, "y1": cy + H / 2 * mpp, "mpp": mpp,
            "lat0": lat0, "lon0": lon0, "ground_mpp": mpp * math.cos(math.radians(lat0))}


def _credit(service: str) -> str:
    if service not in _credits:
        try:
            meta = json.loads(_download(_ESRI + service + "/MapServer?f=json", timeout=8).decode("utf-8"))
            _credits[service] = str(meta.get("copyrightText") or "Esri").replace("(c)", "©").strip()
        except Exception:
            return "Esri"
    return _credits[service]


def _prune_maps() -> None:
    try:
        files = sorted(_MAP_DIR.glob("*.img"), key=lambda f: f.stat().st_mtime, reverse=True)
        for old in files[_MAP_KEEP:]:
            old.unlink(missing_ok=True)
    except Exception:
        pass


def _export(service: str, bbox: tuple, size: tuple, transparent: bool):
    """One server-drawn picture of `bbox` (Web Mercator metres) → QImage or None.

    Kept on disk for a month: a watch alert or a repeated question about the
    same place costs nothing the second time.
    """
    import hashlib
    from PyQt6.QtGui import QImage
    fmt = "png32" if transparent else "jpg"
    query = (f"export?bbox={bbox[0]:.1f},{bbox[1]:.1f},{bbox[2]:.1f},{bbox[3]:.1f}"
             f"&bboxSR=3857&imageSR=3857&size={int(size[0])},{int(size[1])}&format={fmt}"
             + ("&transparent=true" if transparent else "") + "&f=image")
    path = _MAP_DIR / (hashlib.sha1((service + query).encode()).hexdigest()[:20] + ".img")
    try:
        if path.is_file() and time.time() - path.stat().st_mtime < 30 * 86400:
            img = QImage.fromData(path.read_bytes())
            if not img.isNull():
                return img
    except Exception:
        pass
    try:
        raw = _download(_ESRI + service + "/MapServer/" + query, timeout=20, limit=12_000_000)
    except Exception as exc:
        _print(f"map layer {service} not fetched: {type(exc).__name__}")
        return None
    img = QImage.fromData(raw)
    if img.isNull():                                      # an error page, not a picture
        return None
    try:
        _MAP_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        _prune_maps()
    except Exception:
        pass
    return img


def _layer(service: str, view: dict, scale: int, transparent: bool) -> Optional[np.ndarray]:
    """The view as one RGBA array, drawn by the server at 1/scale resolution.

    A window that crosses the date line is asked for in two pieces — the
    service draws nothing past ±180° (measured: the far half came back white).
    """
    from PyQt6.QtCore import Qt
    mpp = view["mpp"]
    x0, y1 = view["x0"], view["y1"]
    y0 = y1 - H * mpp
    out = np.zeros((H, W, 4), np.uint8)
    got = False
    seams: list = []
    for shift in (-2 * _MERC_MAX, 0.0, 2 * _MERC_MAX):
        a = max(x0, -_MERC_MAX + shift)
        b = min(x0 + W * mpp, _MERC_MAX + shift)
        px0 = int(round((a - x0) / mpp))
        px1 = int(round((b - x0) / mpp))
        if px1 - px0 < 2:
            continue
        size = (max(1, (px1 - px0) // scale), max(1, H // scale))
        img = _export(service, (a - shift, y0, b - shift, y1), size, transparent)
        if img is None:
            return None
        if img.width() != px1 - px0 or img.height() != H:
            img = img.scaled(px1 - px0, H, Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        out[:, px0:px1] = _rgba_from_qimage(img)[:, : px1 - px0]
        seams.extend(x for x in (px0, px1) if 0 < x < W)
        got = True
    for x in seams:                   # each server picture has an edge column of its own
        out[:, x - 1] = out[:, max(0, x - 2)]
        out[:, x] = out[:, min(W - 1, x + 1)]
    return out if got else None


def _tint(rgb: np.ndarray, accent) -> np.ndarray:
    """The grey basemap → the HUD's colour: water near black, land a dark shade
    of the accent, roads and borders a brighter one."""
    f = rgb[..., :3].astype(np.float32) / 255.0
    lum = 0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]
    t = np.clip((lum - 0.11) / 0.30, 0, 1) ** 1.15
    base = np.array((2, 7, 12), np.float32)
    hi = np.array(_mix(accent, (255, 255, 255), 0.25), np.float32)
    return (base + (hi - base) * (t * 0.62)[..., None]).astype(np.uint8)


def _offline_layer(view: dict, accent: str) -> np.ndarray:
    """The globe's texture reprojected into the same window."""
    tex, _ = _texture(accent)
    ys, xs = np.mgrid[0:H, 0:W].astype(np.float64)
    X = view["x0"] + (xs + 0.5) * view["mpp"]
    Y = view["y1"] - (ys + 0.5) * view["mpp"]
    lon = np.degrees(X / _MERC_R)
    lat = np.degrees(np.arctan(np.sinh(Y / _MERC_R)))
    col = _sample(tex, lat.ravel(), lon.ravel()).reshape(H, W, 3)
    return np.clip(col, 0, 255).astype(np.uint8)


def render_map(out_path: Path, *, center: tuple, radius_m: float, layers: dict, accent: str,
               style: str = "dark", units: str = "metric") -> tuple[Optional[str], str]:
    """(path, basemap used: 'dark' | 'satellite' | 'offline')."""
    acc = _rgb(accent)
    lat0, lon0 = float(center[0]), float(center[1])
    view = map_view(lat0, lon0, radius_m)
    base, used, credit = None, "offline", ""
    try:
        spec = MAP_STYLES.get(style)
        if spec:
            with ThreadPoolExecutor(max_workers=2) as pool:
                fb = pool.submit(_layer, spec["base"], view, 1, False)
                # Labels drawn at half resolution and doubled: fewer, larger
                # names, readable once the HUD scales the picture down.
                fl = pool.submit(_layer, spec["labels"], view, 2, True)
                b, lab = fb.result(), fl.result()
            if b is not None:
                rgb = _tint(b, acc).astype(np.float32) if spec["tint"] else b[..., :3].astype(np.float32) * 0.82
                if lab is not None:
                    a = lab[..., 3:4].astype(np.float32) / 255.0
                    txt = lab[..., :3].astype(np.float32)
                    if spec["tint"]:
                        txt = txt * 0.75 + np.array(acc, np.float32) * 0.25
                    rgb = rgb * (1 - a) + txt * a
                base, used = np.clip(rgb, 0, 255).astype(np.uint8), style
                parts = [_credit(spec["base"])]
                if lab is not None and _credit(spec["labels"]) not in parts:
                    parts.append(_credit(spec["labels"]))
                credit = " | ".join(parts)
        if base is None:
            base = _offline_layer(view, accent)
    except Exception as exc:
        _print(f"map base not drawn: {type(exc).__name__}: {exc}")
        return None, used
    rgba = np.dstack([base, np.full((H, W), 255, np.uint8)])

    def proj(lat, lon):
        lon = lon0 + ((lon - lon0 + 540.0) % 360.0 - 180.0)   # the copy nearest the centre
        X, Y = _merc(lat, lon)
        return (X - view["x0"]) / view["mpp"], (view["y1"] - Y) / view["mpp"]

    def where(lat, lon):
        x, y = proj(lat, lon)
        return (x, y) if -60 <= x <= W + 60 and -60 <= y <= H + 60 else None

    def runs(points):
        out, cur = [], []
        for lat, lon in points:
            x, y = proj(lat, lon)
            if cur and abs(x - cur[-1][0]) > W:               # jumped to the next world copy
                out.append(cur)
                cur = []
            cur.append((x, y))
        if cur:
            out.append(cur)
        return out

    def job():
        from PyQt6.QtCore import QPointF, QRectF, Qt
        from PyQt6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPolygonF, QRadialGradient
        img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
        p = QPainter(img)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            fonts = _fonts()
            p.drawImage(0, 0, _qimage_from_array(rgba))
            g = QRadialGradient(QPointF(W / 2, H / 2), W * 0.7)
            g.setColorAt(0.55, QColor(0, 0, 0, 0))
            g.setColorAt(1.0, QColor(0, 0, 0, 160))
            p.fillRect(0, 0, W, H, QBrush(g))
            p.setPen(QPen(QColor(*acc, 18), 1))
            for x in range(0, W, 80):
                p.drawLine(x, 0, x, H)
            for y in range(0, H, 80):
                p.drawLine(0, y, W, y)
            _draw_layers(p, layers, acc, where, runs, fonts)
            _frame(p, acc)
            _stamp_panel(p, layers, acc, fonts, lat0, lon0)
            _card(p, layers, acc, fonts)
            _legends(p, layers, acc, fonts[2])
            _scale_bar(p, W - 60, H - 96, view["ground_mpp"], units, fonts[2], acc)
            # north: the triangle aircraft_report uses — no letter
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(*acc))
            nx, ny = W - 70, H - 180                           # above the scale bar
            p.drawPolygon(QPolygonF([QPointF(nx, ny), QPointF(nx - 11, ny + 26), QPointF(nx + 11, ny + 26)]))
            if credit:
                tiny = _fonts()[2]
                tiny.setPixelSize(14)
                p.setFont(tiny)
                cw = p.fontMetrics().horizontalAdvance(credit) + 20
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(3, 8, 14, 170))
                p.drawRoundedRect(QRectF(W - 60 - cw, H - 48, cw + 10, 26), 5, 5)
                p.setPen(QColor(200, 214, 224, 190))
                p.drawText(QRectF(W - 1260, H - 46, 1200, 22),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, credit)
        finally:
            p.end()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        return str(out_path) if img.save(str(out_path), "PNG") else None

    try:
        return _on_gui_thread(job), used
    except Exception as exc:
        _print(f"map not drawn: {type(exc).__name__}: {exc}")
        return None, used


def _scale_bar(p, right: float, y: float, mpp: float, units: str, font, acc) -> None:
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QColor, QPen
    unit_m, unit = (1609.344, "mi") if units == "imperial" else (1000.0, "km")
    if units != "imperial" and 220 * mpp < 1000:
        unit_m, unit = 1.0, "m"
    elif units == "imperial" and 220 * mpp < 1609.344:
        unit_m, unit = 0.3048, "ft"
    target = 220 * mpp / unit_m
    step = 10 ** math.floor(math.log10(max(target, 1e-6)))
    for k in (5, 2, 1):
        if k * step <= target:
            step *= k
            break
    px = step * unit_m / mpp
    p.setPen(QPen(QColor(*acc), 3))
    p.drawLine(int(right - px), int(y), int(right), int(y))
    p.drawLine(int(right - px), int(y - 8), int(right - px), int(y + 8))
    p.drawLine(int(right), int(y - 8), int(right), int(y + 8))
    p.setFont(font)
    _halo_text(p, QRectF(right - px, y - 34, px, 24), Qt.AlignmentFlag.AlignCenter,
               f"{step:g} {unit}", "#dbe9f3")


# ─────────────────────────────────────────────────────────────────────────────
# Sky
# ─────────────────────────────────────────────────────────────────────────────

def render_sky(out_path: Path, *, track: list, marks: list, accent: str,
               stamp_date: str = "", brightness: str = "") -> Optional[str]:
    """The sky as a disc.

    track  [(az, el, visible)] every few seconds across the pass.
    marks  [(az, el, text)] — the start, highest point and end of the visible part.
    Azimuth is drawn the way a compass reads (north up, east right), because the
    user is told the direction to face, not asked to lie on their back.
    """
    acc = _rgb(accent)
    cx, cy, R = W / 2.0, H / 2.0 + 8, 440.0

    def pos(az: float, el: float) -> tuple[float, float]:
        r = (90.0 - max(0.0, el)) / 90.0 * R
        a = math.radians(az)
        return cx + r * math.sin(a), cy - r * math.cos(a)

    def job():
        from PyQt6.QtCore import QPointF, QRectF, Qt
        from PyQt6.QtGui import (QBrush, QColor, QImage, QPainter, QPen, QPolygonF,
                                 QRadialGradient)
        img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
        p = QPainter(img)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            big, mid, small = _fonts()
            _background(p, acc)
            g = QRadialGradient(QPointF(cx, cy), R)
            g.setColorAt(0.0, QColor(10, 22, 40))
            g.setColorAt(1.0, QColor(4, 9, 18))
            p.setPen(QPen(QColor(*acc, 200), 2.5))
            p.setBrush(QBrush(g))
            p.drawEllipse(QPointF(cx, cy), R, R)
            rnd = random.Random(7)                       # the same stars every time
            for _ in range(260):
                a, r = rnd.random() * 2 * math.pi, R * math.sqrt(rnd.random())
                s = rnd.random()
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, int(40 + 150 * s * s)))
                p.drawEllipse(QPointF(cx + r * math.cos(a), cy + r * math.sin(a)), 0.6 + 1.3 * s * s,
                              0.6 + 1.3 * s * s)
            p.setBrush(QColor(0, 0, 0, 0))
            p.setFont(small)
            for el in (30, 60):
                rr = (90 - el) / 90 * R
                p.setPen(QPen(QColor(*acc, 70), 1.3, Qt.PenStyle.DashLine))
                p.drawEllipse(QPointF(cx, cy), rr, rr)
                p.setPen(QColor("#6f8798"))
                p.drawText(QRectF(cx + 6, cy - rr - 2, 80, 22),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"{el}°")
            for az in range(0, 360, 45):
                x1, y1 = pos(az, 90)
                x2, y2 = pos(az, 0)
                p.setPen(QPen(QColor(*acc, 45 if az % 90 else 80), 1.2))
                p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
                lx, ly = cx + (R + 34) * math.sin(math.radians(az)), cy - (R + 34) * math.cos(math.radians(az))
                if az:
                    p.setPen(QColor("#8ea6b8"))
                    p.drawText(QRectF(lx - 40, ly - 12, 80, 24), Qt.AlignmentFlag.AlignCenter, f"{az}°")
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(*acc))
            top = cy - R - 16
            p.drawPolygon(QPolygonF([QPointF(cx, top - 28), QPointF(cx - 12, top), QPointF(cx + 12, top)]))
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(QPointF(cx, cy), 3, 3)

            # the pass: dim where it cannot be seen, bright where it can
            pts = [(pos(az, el), vis) for az, el, vis in track if el >= 0]
            for want_vis, pen in ((False, QPen(QColor(*acc, 90), 2.4, Qt.PenStyle.DashLine)),
                                  (True, QPen(QColor(*_mix(acc, (255, 255, 255), 0.55)), 4.5))):
                runs, cur = [], []
                for (xy, vis) in pts:
                    if vis == want_vis:
                        cur.append(xy)
                    elif cur:
                        cur.append(xy)
                        runs.append(cur)
                        cur = []
                if cur:
                    runs.append(cur)
                _draw_runs(p, [r for r in runs if len(r) > 1], pen)
            vis_pts = [xy for xy, vis in pts if vis] or [xy for xy, _ in pts]
            if len(vis_pts) >= 2:                      # arrowhead: which way it moves
                (x1, y1), (x2, y2) = vis_pts[-2], vis_pts[-1]
                ang = math.atan2(y2 - y1, x2 - x1)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#ffffff"))
                tip = QPointF(x2 + 14 * math.cos(ang), y2 + 14 * math.sin(ang))
                p.drawPolygon(QPolygonF([
                    tip,
                    QPointF(x2 + 12 * math.cos(ang + 2.5), y2 + 12 * math.sin(ang + 2.5)),
                    QPointF(x2 + 12 * math.cos(ang - 2.5), y2 + 12 * math.sin(ang - 2.5))]))
            placed: list = []
            for az, el, text in marks:
                x, y = pos(az, el)
                p.setPen(QPen(QColor("#03070b"), 2))
                p.setBrush(QColor(*acc))
                p.drawEllipse(QPointF(x, y), 8, 8)
                _place_label(p, placed, x, y, 12, text, "#eaf6ff", mid)
            _frame(p, acc)
            white, dim = QColor("#eaf6ff"), QColor("#8ea6b8")
            _panel_text(p, 44, 40, [(big, white, "ISS"), (small, dim, stamp_date)], accent=acc)
            if brightness:
                _panel_text(p, W - 44, 40, [(big, white, brightness)], align_right=True, accent=acc)
        finally:
            p.end()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        return str(out_path) if img.save(str(out_path), "PNG") else None

    try:
        return _on_gui_thread(job)
    except Exception as exc:
        _print(f"sky not drawn: {type(exc).__name__}: {exc}")
        return None
