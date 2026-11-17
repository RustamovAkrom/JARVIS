"""
Planet Watch — the live view: a globe and a map you move with your own hands.

A shared driver for planet_watch.py (the leading underscore keeps the plugin
loader from treating it as a skill), imported only when the live view is first
opened — nothing here costs anything at start-up.

    drag            spin the globe / pan the map, and fling it: it glides to a stop
    wheel, pinch    zoom toward the point under the cursor — the globe grows until
                    its curve fills the screen and becomes a map, down to streets
    double-click    zoom in there (right double-click: out)
    arrows, + / -   the same from the keyboard; Home = your location (Esc is left to
                    JARVIS, where it stops the voice; the × button closes the view)
    hover, click    a card for any quake, disaster, aircraft or the ISS; a click
                    flies there
    buttons         zoom, home, dark/satellite, one switch per layer, close

Voice drives the same view: planet_watch calls fly_to / show_state here, and a
long trip rises and descends on its way, the way a map app flies.

WHY IT IS FAST
    The globe is drawn by the GPU: one full-screen quad and a fragment shader
    that finds, for every pixel, the point of the sphere under it — lit by the
    real Sun, with the aurora, the atmosphere and the space around it. Full
    screen on an RTX 3060: the shader and its read-back take ~5 ms, a whole
    frame ~11 ms, ~60 fps while dragging — where the numpy renderer managed 2.5.
    Fully shown, the view is marked opaque, so the animated HUD underneath
    no longer makes it repaint: measured 64 needless repaints in 3 s before, 0 after. Without a
    usable GPU (a VM, remote desktop, an old driver) the same view is drawn on
    the CPU at a reduced resolution while it moves, full resolution when still.

    The map is web tiles, like every map app: fetched in parallel, kept in
    memory and on disk, and a tile still loading is drawn from its parent, so
    the screen is never blank. With no network the tiles are generated from
    the globe's own outlines, so there is always a map.

    Nothing redraws while nothing moves: frames run only during motion.

WHY IT IS NOT A QOpenGLWidget
    It was, first. Putting a QOpenGLWidget into a window makes Qt switch that
    whole window to OpenGL composition, which recreates the native window: the
    user saw JARVIS close and reopen, and full screen went black for a moment.
    So the shader draws into an offscreen framebuffer of its own context and
    the picture is handed to an ordinary widget — JARVIS's window never
    changes, and the view fades in over the HUD instead of replacing it.

NEVER TAKES JARVIS DOWN
    PyQt6 ends the process on an exception escaping a Qt callback. Every paint,
    input and timer handler here catches everything and logs it once.

PLACEMENT
    The view sits over the centre of the HUD, where the avatar is, and gives
    it back when closed — or when anything else takes that place (a video, the
    camera, a picture). On a UI without that surface it opens as its own window.
"""
from __future__ import annotations

import hashlib
import math
import threading
import time
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
from PyQt6 import sip
from PyQt6.QtCore import (QEvent, QObject, QPointF, QRectF, Qt, QTimer, pyqtSignal)
from PyQt6.QtGui import (QBrush, QColor, QCursor, QFont, QFontMetricsF, QGuiApplication, QImage,
                         QMatrix3x3, QOffscreenSurface, QOpenGLContext, QPainter, QPen, QPixmap,
                         QPolygonF, QRadialGradient)
from PyQt6.QtOpenGL import (QOpenGLFramebufferObject, QOpenGLShader, QOpenGLShaderProgram,
                            QOpenGLTexture, QOpenGLVersionFunctionsFactory, QOpenGLVersionProfile)
from PyQt6.QtWidgets import QWidget

try:
    from plugins import _planet_view as pv
except ImportError:  # loaded outside the package
    import _planet_view as pv  # type: ignore

BASE_DIR = Path(__file__).resolve().parent.parent
TILE_DIR = BASE_DIR / "reports" / "planet_watch" / "tiles"      # .gitignore: reports/
SAT_TEXTURE = BASE_DIR / "reports" / "planet_watch" / "earth_satellite.jpg"
_UA = pv._UA

EARTH_KM = 6371.0
GLOBE_MIN_SPAN = 2600.0          # below this half-height (km) the globe hands over to the map
MIN_SPAN, MAX_SPAN = 0.25, 22000.0
AIRCRAFT_SPAN = 450.0
_MERC_LAT = 85.05112878
_TILE = 256
_TILE_MEM = 900
_TILE_DISK = 6000
_ESRI_TILE = "https://services.arcgisonline.com/ArcGIS/rest/services/{svc}/MapServer/tile/{z}/{y}/{x}"
_SAT_WORLD = ("https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export"
              "?bbox=-180,-90,180,90&bboxSR=4326&imageSR=4326&size=4096,2048&format=jpg&f=image")
STYLES = {
    "dark": {"base": ("Canvas/World_Dark_Gray_Base", 16, "tint"),
             "labels": ("Canvas/World_Dark_Gray_Reference", 16, "labels")},
    "satellite": {"base": ("World_Imagery", 18, "dim"),
                  "labels": ("Reference/World_Boundaries_and_Places", 18, "plain")},
    "offline": {"base": ("offline", 9, "synth"), "labels": None},
}
# What each button does, shown when the cursor rests on it. English here; the
# provider hands back the same keys in the user's language when it can.
BUTTON_LABELS = {
    "close": "Close the map", "zin": "Zoom in", "zout": "Zoom out", "home": "Go to my location",
    "style_dark": "Satellite view", "style_satellite": "Dark map",
    "quakes_on": "Hide earthquakes", "quakes_off": "Show earthquakes",
    "events_on": "Hide disasters", "events_off": "Show disasters",
    "aircraft_on": "Hide aircraft", "aircraft_off": "Show aircraft",
    "iss_on": "Hide the ISS", "iss_off": "Show the ISS",
    "aurora_on": "Hide the northern and southern lights",
    "aurora_off": "Show the northern and southern lights",
}
LAYER_BUTTONS = (("quakes", "◉"), ("events", "◆"), ("aircraft", "✈"), ("iss", "✦"), ("aurora", "✧"))


def _print(text: str) -> None:
    pv._print(text)


_reported: set = set()


def _once(where: str, exc: BaseException) -> None:
    """Log a failure in a Qt callback once per place — never raise from one."""
    key = f"{where}:{type(exc).__name__}"
    if key not in _reported:
        _reported.add(key)
        import traceback
        _print(f"live view — {where}: {type(exc).__name__}: {exc}")
        traceback.print_exc()


# ─────────────────────────────────────────────────────────────────────────────
# Geometry
# ─────────────────────────────────────────────────────────────────────────────

def _unit(lat: float, lon: float) -> np.ndarray:
    p, l = math.radians(lat), math.radians(lon)
    return np.array([math.cos(p) * math.cos(l), math.cos(p) * math.sin(l), math.sin(p)])


def _basis(lat: float, lon: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(east, north, centre) unit vectors of the view looking down at (lat, lon)."""
    p, l = math.radians(lat), math.radians(lon)
    c = np.array([math.cos(p) * math.cos(l), math.cos(p) * math.sin(l), math.sin(p)])
    e = np.array([-math.sin(l), math.cos(l), 0.0])
    n = np.array([-math.sin(p) * math.cos(l), -math.sin(p) * math.sin(l), math.cos(p)])
    return e, n, c


def _merc_px(lat: float, lon: float, ws: float) -> tuple[float, float]:
    lat = max(-_MERC_LAT, min(_MERC_LAT, lat))
    s = math.sin(math.radians(lat))
    return (lon + 180.0) / 360.0 * ws, (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * ws


def _merc_ll(x: float, y: float, ws: float) -> tuple[float, float]:
    lon = x / ws * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / ws))))
    return lat, (lon + 540.0) % 360.0 - 180.0


def _slerp(a: tuple, b: tuple, t: float) -> tuple[float, float]:
    v1, v2 = _unit(*a), _unit(*b)
    om = math.acos(max(-1.0, min(1.0, float(v1 @ v2))))
    if om < 1e-9:
        return a
    v = (math.sin((1 - t) * om) * v1 + math.sin(t * om) * v2) / math.sin(om)
    return math.degrees(math.asin(max(-1.0, min(1.0, v[2])))), math.degrees(math.atan2(v[1], v[0]))


def _ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


# ─────────────────────────────────────────────────────────────────────────────
# Tiles
# ─────────────────────────────────────────────────────────────────────────────

class TileStore(QObject):
    """Map tiles, fetched in parallel and kept in memory and on disk.

    A request made for a tile that has scrolled away before a worker reaches it
    is dropped, so a fast fling never queues a thousand downloads."""

    loaded = pyqtSignal()

    def __init__(self, accent: str):
        super().__init__()
        self.accent = accent
        self.acc = pv._rgb(accent)
        self.lock = threading.Lock()
        self.mem: OrderedDict = OrderedDict()      # key → QImage (worker) / QPixmap (GUI)
        self.pending: set = set()
        self.failed: dict = {}
        self.wanted: set = set()
        self.pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="planet-tile")
        self.fail_streak = 0

    # -- GUI thread --
    def get(self, key) -> Optional[QPixmap]:
        with self.lock:
            item = self.mem.get(key)
            if item is None:
                return None
            self.mem.move_to_end(key)
        if isinstance(item, QImage):
            item = QPixmap.fromImage(item)
            with self.lock:
                self.mem[key] = item
        return item

    def request(self, key) -> None:
        with self.lock:
            if key in self.mem or key in self.pending:
                return
            failed_at = self.failed.get(key)
            if failed_at and time.time() - failed_at < 60:
                return
            self.pending.add(key)
            self.wanted.add(key)
        self.pool.submit(self._load, key)

    def busy(self) -> int:
        with self.lock:
            return len(self.pending)

    def offline(self) -> bool:
        return self.fail_streak >= 6

    def probe(self, key) -> None:
        """While offline, try one real tile now and then; one success ends it."""
        now = time.time()
        if now - getattr(self, "_probe_at", 0.0) > 30:
            self._probe_at = now
            with self.lock:
                self.failed.pop(key, None)
            self.request(key)

    # -- workers --
    def _load(self, key) -> None:
        svc, z, x, y, kind = key
        try:
            with self.lock:
                if key not in self.wanted:
                    return                                   # scrolled away already
            img = self._synth(z, x, y) if kind == "synth" else self._fetch(svc, z, x, y, kind)
            if img is not None:
                with self.lock:
                    self.mem[key] = img
                    while len(self.mem) > _TILE_MEM:
                        self.mem.popitem(last=False)
                    self.failed.pop(key, None)
                self.loaded.emit()
            else:
                with self.lock:
                    self.failed[key] = time.time()
        except Exception as exc:
            _once("tile", exc)
        finally:
            with self.lock:
                self.pending.discard(key)

    def _fetch(self, svc: str, z: int, x: int, y: int, kind: str) -> Optional[QImage]:
        path = TILE_DIR / hashlib.sha1(svc.encode()).hexdigest()[:10] / f"{z}_{x}_{y}.img"
        raw = None
        try:
            if path.is_file():
                raw = path.read_bytes()
        except Exception:
            raw = None
        if raw is None:
            try:
                req = urllib.request.Request(_ESRI_TILE.format(svc=svc, z=z, x=x, y=y),
                                             headers={"User-Agent": _UA})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    raw = resp.read(2_000_000)
                self.fail_streak = 0
            except Exception:
                self.fail_streak += 1
                return None
            self.fail_streak = 0
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            except Exception:
                pass
        img = QImage.fromData(raw)
        if img.isNull():
            return None
        return self._process(img, kind)

    def _process(self, img: QImage, kind: str) -> QImage:
        if kind == "plain":
            return img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        arr = pv._rgba_from_qimage(img)
        if kind == "tint":
            rgb = pv._tint(arr, self.acc)
            arr = np.dstack([rgb, np.full(rgb.shape[:2], 255, np.uint8)])
        elif kind == "dim":
            arr[..., :3] = (arr[..., :3].astype(np.float32) * 0.88).astype(np.uint8)
        elif kind == "labels":
            rgb = arr[..., :3].astype(np.float32) * 0.75 + np.array(self.acc, np.float32) * 0.25
            arr[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
        out = pv._qimage_from_array(arr)
        return out.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

    def _synth(self, z: int, x: int, y: int) -> Optional[QImage]:
        """A tile drawn from the globe's own outlines — offline, and the fallback."""
        tex, _ = pv._texture(self.accent)
        n = 2 ** z
        ws = _TILE * n
        ys, xs = np.mgrid[0:_TILE, 0:_TILE].astype(np.float64)
        wx = x * _TILE + xs + 0.5
        wy = y * _TILE + ys + 0.5
        lon = wx / ws * 360.0 - 180.0
        lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * wy / ws))))
        col = pv._sample(tex, lat.ravel(), lon.ravel()).reshape(_TILE, _TILE, 3)
        arr = np.dstack([np.clip(col, 0, 255).astype(np.uint8), np.full((_TILE, _TILE), 255, np.uint8)])
        return pv._qimage_from_array(arr).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

    def prune_disk(self) -> None:
        def job():
            try:
                files = sorted(TILE_DIR.glob("*/*.img"), key=lambda f: f.stat().st_mtime, reverse=True)
                for old in files[_TILE_DISK:]:
                    old.unlink(missing_ok=True)
            except Exception:
                pass
        threading.Thread(target=job, daemon=True, name="planet-tile-prune").start()


# ─────────────────────────────────────────────────────────────────────────────
# The GPU globe
# ─────────────────────────────────────────────────────────────────────────────

_VS = """
#version 120
attribute vec2 pos;
void main() { gl_Position = vec4(pos, 0.0, 1.0); }
"""

_FS = """
#version 120
uniform sampler2D earth;
uniform sampler2D aurora;
uniform mat3 rot;
uniform vec2 centre;
uniform float R;
uniform vec3 sun;
uniform vec3 accent;
uniform float aurora_on;
uniform float lit;
uniform vec3 bg;
uniform float grid;
void main() {
    // space: the background and its grid, drawn here so the picture is opaque
    vec2 cell = mod(gl_FragCoord.xy, grid);
    float line = (cell.x < 1.0 || cell.y < 1.0) ? 0.055 : 0.0;
    vec3 space = mix(bg, accent, line);
    // gl_FragCoord is used top-down (centre is given top-down too), so the rows come
    // out in image order and the read-back needs no flip (0.9 ms a frame at 1700×1310)
    vec2 p = vec2(gl_FragCoord.x - centre.x, centre.y - gl_FragCoord.y) / R;
    float r = length(p);
    float atm = 1.09;
    if (r > atm) { gl_FragColor = vec4(space, 1.0); return; }
    vec4 air = vec4(accent, pow(clamp(1.0 - (r - 1.0) / (atm - 1.0), 0.0, 1.0), 2.4) * 0.55);
    if (r > 1.0) { gl_FragColor = vec4(mix(space, air.rgb, air.a), 1.0); return; }
    vec3 nv = vec3(p, sqrt(max(0.0, 1.0 - r * r)));
    vec3 w = rot * nv;
    float lat = asin(clamp(w.z, -1.0, 1.0));
    float lon = atan(w.y, w.x);
    vec3 col = texture2D(earth, vec2(lon * 0.15915494 + 0.5, 0.5 - lat * 0.31830989)).rgb;
    float shade = 0.58 + 0.42 * sqrt(nv.z);
    float night = 0.0;
    if (lit > 0.5) {
        float day = smoothstep(-0.10, 0.08, dot(w, sun));
        night = 1.0 - day;
        shade *= 0.30 + 0.70 * day;
        col += night * 0.05 * accent;
    }
    col *= shade;
    if (aurora_on > 0.5) {
        float a = texture2D(aurora, vec2(fract(lon * 0.15915494 + 1.0),
                                         (degrees(lat) + 90.5) / 181.0)).r;
        col += a * (0.25 + 0.75 * night) * vec3(0.24, 1.0, 0.59) * 0.95;
    }
    col += pow(1.0 - nv.z, 3.0) * 0.45 * accent;
    float edge = clamp((1.0 - r) * R + 0.5, 0.0, 1.0);
    vec3 rim = mix(space, air.rgb, air.a);
    gl_FragColor = vec4(mix(rim, clamp(col, 0.0, 1.0), edge), 1.0);
}
"""

_GL_BLEND, _GL_TRIANGLE_STRIP, _GL_DEPTH_TEST, _GL_SCISSOR_TEST = 0x0BE2, 0x0005, 0x0B71, 0x0C11


def _gl_texture(img: QImage, repeat: bool) -> QOpenGLTexture:
    t = QOpenGLTexture(img.convertToFormat(QImage.Format.Format_RGBA8888),
                       QOpenGLTexture.MipMapGeneration.DontGenerateMipMaps)
    t.setMinificationFilter(QOpenGLTexture.Filter.Linear)
    t.setMagnificationFilter(QOpenGLTexture.Filter.Linear)
    t.setWrapMode(QOpenGLTexture.CoordinateDirection.DirectionS,
                  QOpenGLTexture.WrapMode.Repeat if repeat else QOpenGLTexture.WrapMode.ClampToEdge)
    t.setWrapMode(QOpenGLTexture.CoordinateDirection.DirectionT, QOpenGLTexture.WrapMode.ClampToEdge)
    return t


class _GpuGlobe:
    """The globe shader in a context of its own, drawing into an offscreen
    framebuffer and handing back an image. Nothing here touches a window."""

    def __init__(self):
        self.ctx = QOpenGLContext()
        if not self.ctx.create():
            raise RuntimeError("no OpenGL context")
        self.surface = QOffscreenSurface()
        self.surface.setFormat(self.ctx.format())
        self.surface.create()
        if not self.surface.isValid() or not self.ctx.makeCurrent(self.surface):
            raise RuntimeError("no offscreen OpenGL surface")
        try:
            profile = QOpenGLVersionProfile()
            profile.setVersion(2, 0)
            self.f = QOpenGLVersionFunctionsFactory.get(profile, self.ctx)
            if self.f is None or not self.f.initializeOpenGLFunctions():
                raise RuntimeError("OpenGL 2.0 functions are not available")
            prog = QOpenGLShaderProgram()
            ok = (prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, _VS)
                  and prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, _FS))
            prog.bindAttributeLocation("pos", 0)
            if not ok or not prog.link():
                raise RuntimeError("shader: " + prog.log()[:300])
            self.prog = prog
        finally:
            self.ctx.doneCurrent()
        self.fbo: Optional[QOpenGLFramebufferObject] = None
        self.earth = None
        self.earth_key = None
        self.aurora = None
        self.aurora_key = None

    def render(self, w: int, h: int, earth_key: str, earth_image: Callable[[], QImage],
               aurora_key: Any, aurora_grid: Optional[np.ndarray], uniforms: dict) -> QImage:
        if not self.ctx.makeCurrent(self.surface):
            raise RuntimeError("the offscreen OpenGL context was lost")
        try:
            if self.earth is None or self.earth_key != earth_key:
                if self.earth is not None:
                    self.earth.destroy()
                self.earth = _gl_texture(earth_image(), repeat=True)
                self.earth_key = earth_key
            if self.aurora is None or self.aurora_key != aurora_key:
                if self.aurora is not None:
                    self.aurora.destroy()
                grid = aurora_grid if aurora_grid is not None else np.zeros((181, 360), np.float32)
                a = np.clip(grid * 255, 0, 255).astype(np.uint8)
                self.aurora = _gl_texture(
                    pv._qimage_from_array(np.dstack([a, a, a, np.full(a.shape, 255, np.uint8)])), repeat=True)
                self.aurora_key = aurora_key
            if self.fbo is None or self.fbo.width() != w or self.fbo.height() != h:
                self.fbo = QOpenGLFramebufferObject(w, h)
            f, prog = self.f, self.prog
            self.fbo.bind()
            f.glViewport(0, 0, w, h)
            f.glDisable(_GL_DEPTH_TEST)
            f.glDisable(_GL_SCISSOR_TEST)
            f.glDisable(_GL_BLEND)                   # one quad, each pixel written once
            f.glClearColor(0.0, 0.0, 0.0, 0.0)
            f.glClear(0x4000)
            prog.bind()
            self.earth.bind(0)
            self.aurora.bind(1)
            prog.setUniformValue("earth", 0)
            prog.setUniformValue("aurora", 1)
            prog.setUniformValue("rot", uniforms["rot"])
            prog.setUniformValue("centre", *uniforms["centre"])
            prog.setUniformValue("R", uniforms["R"])
            prog.setUniformValue("sun", *uniforms["sun"])
            prog.setUniformValue("lit", uniforms["lit"])
            prog.setUniformValue("accent", *uniforms["accent"])
            prog.setUniformValue("aurora_on", uniforms["aurora_on"])
            prog.setUniformValue("bg", *uniforms["bg"])
            prog.setUniformValue("grid", uniforms["grid"])
            prog.enableAttributeArray(0)
            prog.setAttributeArray(0, [(-1.0, -1.0), (1.0, -1.0), (-1.0, 1.0), (1.0, 1.0)])
            f.glDrawArrays(_GL_TRIANGLE_STRIP, 0, 4)
            prog.disableAttributeArray(0)
            self.aurora.release(1)
            self.earth.release(0)
            prog.release()
            img = self.fbo.toImage(False)
            self.fbo.release()
            return img
        finally:
            self.ctx.doneCurrent()

    def destroy(self) -> None:
        try:
            if self.ctx.makeCurrent(self.surface):
                for t in (self.earth, self.aurora):
                    if t is not None:
                        t.destroy()
                self.fbo = None
                self.ctx.doneCurrent()
        except Exception:
            pass


def _cpu_globe(tex: np.ndarray, basis, R: float, cx: float, cy: float, w: int, h: int,
               sun: Optional[np.ndarray], accent, aurora: Optional[np.ndarray]) -> np.ndarray:
    """The same picture as the shader, in numpy — for machines without a GPU."""
    e, n, c = basis
    atm = 1.09
    out = np.zeros((h, w, 4), np.float32)
    x0, x1 = max(0, int(cx - R * atm) - 1), min(w, int(cx + R * atm) + 2)
    y0, y1 = max(0, int(cy - R * atm) - 1), min(h, int(cy + R * atm) + 2)
    if x1 <= x0 or y1 <= y0:
        return out.astype(np.uint8)
    ys, xs = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    px, py = (xs + 0.5 - cx) / R, (cy - ys - 0.5) / R
    r = np.sqrt(px * px + py * py)
    acc = np.array(accent, np.float32)
    region = out[y0:y1, x0:x1]
    ring = (r > 1.0) & (r < atm)
    region[ring, :3] = acc
    region[ring, 3] = np.clip(1.0 - (r[ring] - 1.0) / (atm - 1.0), 0, 1) ** 2.4 * 0.55 * 255
    inside = r <= 1.0
    ix, iy = px[inside], py[inside]
    nz = np.sqrt(np.clip(1.0 - ix * ix - iy * iy, 0, 1))
    wv = ix[:, None] * e + iy[:, None] * n + nz[:, None] * c
    lat = np.degrees(np.arcsin(np.clip(wv[:, 2], -1, 1)))
    lon = np.degrees(np.arctan2(wv[:, 1], wv[:, 0]))
    col = pv._sample(tex, lat, lon)
    shade = 0.58 + 0.42 * np.sqrt(nz)
    night = np.zeros_like(nz)
    if sun is not None:
        d = wv @ sun
        day = np.clip((d + 0.10) / 0.18, 0, 1)
        day = day * day * (3 - 2 * day)
        night = 1 - day
        shade = shade * (0.30 + 0.70 * day)
        col = col + (night * 0.05)[:, None] * acc
    col = col * shade[:, None]
    if aurora is not None:
        glow = np.clip(pv._sample_grid(aurora, lat, lon), 0, 1) * (0.25 + 0.75 * night)
        col = col + glow[:, None] * np.array((60.0, 255.0, 150.0), np.float32) * 0.95
    col = col + (np.clip(1 - nz, 0, 1) ** 3 * 0.45)[:, None] * acc
    region[inside, :3] = np.clip(col, 0, 255)
    region[inside, 3] = 255
    return out.astype(np.uint8)


# ─────────────────────────────────────────────────────────────────────────────
# The widget
# ─────────────────────────────────────────────────────────────────────────────

class _Bridge(QObject):
    """Results from worker threads, delivered on the GUI thread."""
    data = pyqtSignal(str, object)


class PlanetWidget(QWidget):
    closed = pyqtSignal()

    def __init__(self, provider, accent: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.provider = provider
        self.accent = accent
        self.acc = pv._rgb(accent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.grabGesture(Qt.GestureType.PinchGesture)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self.setMinimumSize(320, 220)

        # camera
        self.lat, self.lon, self.span = 25.0, 20.0, 6371.0
        self.fly: Optional[dict] = None
        self.zoom_to: Optional[dict] = None          # {"span", "anchor": (sx, sy), "geo": (lat, lon)}
        self.vel = (0.0, 0.0)
        self.spin_rate, self.spin_until = 0.0, 0.0
        self.follow_until = 0.0
        self.drag: Optional[dict] = None
        self.last_tick = time.time()
        self.last_change = time.time()

        # what is drawn
        self.style = "dark"
        self.layers = {"quakes", "events", "aircraft", "iss", "aurora", "weather"}
        self.quakes: list = []
        self.events: list = []
        self.aircraft: list = []
        self.iss: Optional[dict] = None
        self.aurora_grid: Optional[np.ndarray] = None
        self.card: list = []
        self.card_place: Optional[dict] = None
        self.card_override: Optional[list] = None
        self.home: Optional[dict] = None
        self.pins: list = []
        self.lines: list = []
        self.radius_m: Optional[float] = None
        self.focus_ref: Any = None
        self.override_quakes = False
        self.override_events = False
        self.title_hint = ""
        self.credit = ""

        # machinery
        self.tiles = TileStore(accent)
        self.tiles.loaded.connect(self._on_tile)
        self.bridge = _Bridge()
        self.bridge.data.connect(self._on_data)
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="planet-data")
        self.jobs: dict = {}
        self.stamps: dict = {}
        self.hits: list = []
        self.buttons: list = []
        self.hover: Optional[dict] = None
        self.hover_pos = QPointF()
        self.gl_ok = False
        self.gl_tried = False
        self.gpu: Optional[_GpuGlobe] = None
        self.fade, self.fade_from, self.fade_to, self.fade_t0 = 0.0, 0.0, 1.0, time.time()
        self.cpu_cache: Optional[tuple] = None
        self.frames = 0
        self.frame_ms = deque_like(120)
        self.sat_image: Optional[QImage] = None
        self.labels = dict(BUTTON_LABELS)

        self.setAutoFillBackground(False)            # the HUD shows through while fading
        self.pacer = QTimer(self)                    # the next frame, while anything moves
        self.pacer.setSingleShot(True)
        self.pacer.setTimerType(Qt.TimerType.PreciseTimer)
        self.pacer.timeout.connect(self.update)
        self.settle = QTimer(self)
        self.settle.setSingleShot(True)
        self.settle.setInterval(450)
        self.settle.timeout.connect(self._settled)
        self.live = QTimer(self)                     # moving layers: aircraft, the ISS
        self.live.setInterval(100)
        self.live.timeout.connect(self._live_tick)
        self.refresh = QTimer(self)                  # periodic data
        self.refresh.setInterval(5000)
        self.refresh.timeout.connect(self._periodic)
        self.refresh.start()
        self.clock = QTimer(self)
        self.clock.setInterval(30000)
        self.clock.timeout.connect(self.update)
        self.clock.start()
        self._fetch_static()
        self._settled()

    # ── public API (GUI thread) ──────────────────────────────────────────────

    def camera(self) -> dict:
        return {"lat": self.lat, "lon": self.lon, "span": self.span, "style": self.style,
                "layers": set(self.layers), "visible": self.isVisible()}

    def fly_to(self, lat: float, lon: float, span: float, seconds: Optional[float] = None) -> None:
        """A cinematic trip: far places are reached by rising and descending."""
        span = max(MIN_SPAN, min(MAX_SPAN, span))
        dist = EARTH_KM * math.acos(max(-1.0, min(1.0, float(_unit(self.lat, self.lon) @ _unit(lat, lon)))))
        peak = max(self.span, span, min(9000.0, dist * 0.9))
        if seconds is None:
            octaves = abs(math.log2(peak / max(min(self.span, span), 1e-3)))
            seconds = max(0.8, min(3.0, 0.7 + 0.12 * octaves + dist / 12000.0))
        self._stop_motion()
        self.fly = {"a": (self.lat, self.lon), "b": (lat, lon), "s0": self.span, "s1": span,
                    "peak": peak, "t0": time.time(), "dur": max(0.3, seconds)}
        self._kick()

    def show_state(self, st: dict, fly: bool = True) -> None:
        """What planet_watch decided to show: where, how far, and what with it."""
        self.style = st.get("style") or self.style
        self.layers = set(st.get("layers") or self.layers)
        self.pins = list(st.get("pins") or [])
        self.lines = list(st.get("lines") or [])
        self.radius_m = st.get("radius_m")
        self.card_place = st.get("card_place")
        self.card_override = st.get("card")
        self.title_hint = (st.get("place") or {}).get("label") or st.get("title") or ""
        preset = st.get("preset") or {}
        self.override_quakes = "quakes" in preset
        self.override_events = "events" in preset
        if self.override_quakes:
            self.quakes = list(preset["quakes"] or [])
        if self.override_events:
            self.events = list(preset["events"] or [])
        self.focus_ref = st.get("focus_ref")
        self.follow_until = time.time() + 30 * 60 if st.get("focus_iss") else 0.0
        self.card = []
        lat, lon = st["center"]
        if getattr(self, "opening", False):
            # a fresh open: start further out, over the same spot, and come in
            self.opening = False
            self._stop_motion()
            self.lat, self.lon = lat, lon
            self.span = min(MAX_SPAN, max(st["span"] * 2.4, 9000.0 if st["span"] >= GLOBE_MIN_SPAN else st["span"] * 2.4))
            self.fly_to(lat, lon, st["span"], 1.1)
        elif fly and self.isVisible():
            self.fly_to(lat, lon, st["span"])
        else:
            self._stop_motion()
            self.lat, self.lon, self.span = lat, lon, st["span"]
            self._changed()
        self._fetch_static()
        self._kick()

    def spin(self, seconds: float, rate: float = 8.0) -> None:
        if self.span < GLOBE_MIN_SPAN:
            self.fly_to(self.lat, self.lon, 6371.0, 1.0)
        self.spin_rate, self.spin_until = rate, time.time() + max(0.0, seconds)
        self._kick()

    def follow_iss(self, seconds: float) -> None:
        self.follow_until = time.time() + max(0.0, seconds)
        self._kick()

    def set_style(self, style: str) -> None:
        if style in STYLES and style != self.style:
            self.style = style
            self._fetch_static()
            self.update()

    def stats(self) -> dict:
        ms = sorted(self.frame_ms.items)
        return {"frames": self.frames, "gl": self.gl_ok,
                "frame_ms_median": ms[len(ms) // 2] if ms else None,
                "frame_ms_p95": ms[int(len(ms) * 0.95)] if ms else None,
                "tiles_pending": self.tiles.busy()}

    def close_view(self, fade: bool = True) -> None:
        self._stop_motion()
        self.live.stop()
        if fade and self.isVisible():
            self.fade_from, self.fade_to, self.fade_t0 = self.fade, 0.0, time.time()
            self.update()                              # hides itself when the fade ends
        else:
            self.hide()
        self.tiles.prune_disk()
        self.closed.emit()

    # ── motion ───────────────────────────────────────────────────────────────

    def _stop_motion(self) -> None:
        self.fly = None
        self.zoom_to = None
        self.vel = (0.0, 0.0)
        self.spin_until = 0.0

    def _moving(self) -> bool:
        now = time.time()
        return bool(self.fly or self.zoom_to or abs(self.vel[0]) + abs(self.vel[1]) > 1.0
                    or self.spin_until > now)

    def _kick(self) -> None:
        self.last_tick = time.time()
        self.update()

    def _fading(self) -> bool:
        return time.time() - self.fade_t0 < 0.3 and self.fade != self.fade_to

    def _fade_step(self) -> None:
        t = max(0.0, min(1.0, (time.time() - self.fade_t0) / 0.28))
        self.fade = self.fade_from + (self.fade_to - self.fade_from) * _ease(t)
        # Fully shown, the view covers every pixel: saying so lets Qt stop repainting
        # it whenever the animated HUD underneath updates. While fading, the HUD must
        # show through, so the view is not opaque then.
        opaque = self.fade >= 1.0
        if self.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent) != opaque:
            self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, opaque)
        if t >= 1.0:
            self.fade = self.fade_to
            if self.fade_to == 0.0 and self.isVisible():
                self.hide()                            # faded out: give the centre back

    def fade_in(self) -> None:
        self.fade_from, self.fade_to, self.fade_t0 = self.fade if self.isVisible() else 0.0, 1.0, time.time()
        self.update()

    def _changed(self) -> None:
        self.last_change = time.time()
        self.settle.start()

    def _step(self) -> None:
        """Advance every motion by the time since the last frame."""
        now = time.time()
        dt = min(0.1, max(0.0, now - self.last_tick))
        self.last_tick = now
        moved = False
        if self.fly:
            f = self.fly
            t = (now - f["t0"]) / f["dur"]
            e = _ease(t)
            self.lat, self.lon = _slerp(f["a"], f["b"], e)
            base = math.exp(math.log(f["s0"]) + (math.log(f["s1"]) - math.log(f["s0"])) * e)
            lift = math.sin(math.pi * e) * max(0.0, math.log(f["peak"] / max(f["s0"], f["s1"])))
            self.span = max(MIN_SPAN, min(MAX_SPAN, base * math.exp(lift)))
            if t >= 1.0:
                self.lat, self.lon, self.span = f["b"][0], f["b"][1], f["s1"]
                self.fly = None
            moved = True
        if self.zoom_to:
            z = self.zoom_to
            k = 1.0 - math.exp(-dt * 16.0)
            new = math.exp(math.log(self.span) + (math.log(z["span"]) - math.log(self.span)) * k)
            if abs(math.log(new / z["span"])) < 0.002:
                new = z["span"]
            self._set_span_anchored(new, z["anchor"], z["geo"])
            if new == z["span"]:
                self.zoom_to = None
            moved = True
        vx, vy = self.vel
        if abs(vx) + abs(vy) > 1.0 and not self.drag:
            self._pan_pixels(vx * dt, vy * dt)
            decay = math.exp(-dt * 4.5)
            self.vel = (vx * decay, vy * decay)
            if abs(self.vel[0]) + abs(self.vel[1]) < 12.0:
                self.vel = (0.0, 0.0)
            moved = True
        if self.spin_until > now:
            self.lon = (self.lon + self.spin_rate * dt + 540.0) % 360.0 - 180.0
            moved = True
        if self.follow_until > now and "iss" in self.layers and not self.drag and not self.fly:
            pos = self._iss_now()
            if pos:
                self.lat += (pos[0] - self.lat) * min(1.0, dt * 3)
                dl = (pos[1] - self.lon + 540.0) % 360.0 - 180.0
                self.lon = (self.lon + dl * min(1.0, dt * 3) + 540.0) % 360.0 - 180.0
                moved = True
        if moved:
            self._changed()

    # ── projections ──────────────────────────────────────────────────────────

    def _globe(self) -> bool:
        return self.span >= GLOBE_MIN_SPAN

    def _globe_R(self) -> float:
        return self.height() * 0.44 * pv.GLOBE_SPAN_KM / max(1.0, self.span)

    def _centre(self) -> tuple[float, float]:
        return self.width() / 2.0, self.height() / 2.0

    def _ws(self) -> float:
        """World size in px for the map at this span: `span` km is half the height."""
        mpp = self.span * 1000.0 / max(1.0, self.height() / 2.0)
        return 40_075_016.686 * math.cos(math.radians(max(-_MERC_LAT, min(_MERC_LAT, self.lat)))) / mpp

    def _projector(self) -> Callable:
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        if self._globe():
            (ex, ey, ez), (nx, ny, nz), (kx, ky, kz) = (tuple(map(float, v)) for v in _basis(self.lat, self.lon))
            R = self._globe_R()
            rad, cos, sin = math.radians, math.cos, math.sin

            def proj(lat, lon):
                p, l = rad(lat), rad(lon)
                cp = cos(p)
                vx, vy, vz = cp * cos(l), cp * sin(l), sin(p)
                if vx * kx + vy * ky + vz * kz < 0:
                    return None
                return cx + (vx * ex + vy * ey + vz * ez) * R, cy - (vx * nx + vy * ny + vz * nz) * R
            return proj
        ws = self._ws()
        mx, my = _merc_px(self.lat, self.lon, ws)

        def proj(lat, lon):
            lon = self.lon + ((lon - self.lon + 540.0) % 360.0 - 180.0)
            x, y = _merc_px(lat, lon, ws)
            return x - mx + cx, y - my + cy
        return proj

    def _screen_to_geo(self, sx: float, sy: float) -> Optional[tuple[float, float]]:
        cx, cy = self._centre()
        if self._globe():
            R = self._globe_R()
            x, y = (sx - cx) / R, (cy - sy) / R
            r2 = x * x + y * y
            if r2 > 1.0:
                return None
            e, n, c = _basis(self.lat, self.lon)
            v = x * e + y * n + math.sqrt(1 - r2) * c
            return math.degrees(math.asin(max(-1, min(1, v[2])))), math.degrees(math.atan2(v[1], v[0]))
        ws = self._ws()
        mx, my = _merc_px(self.lat, self.lon, ws)
        lat, lon = _merc_ll(mx + sx - cx, my + sy - cy, ws)
        return lat, lon

    def _pan_pixels(self, dx: float, dy: float) -> None:
        """Move the view so that the ground moves by (dx, dy) screen pixels."""
        if self._globe():
            R = self._globe_R()
            self.lat = max(-89.5, min(89.5, self.lat + math.degrees(dy / R)))
            self.lon = (self.lon - math.degrees(dx / (R * max(0.2, math.cos(math.radians(self.lat)))))
                        + 540.0) % 360.0 - 180.0
        else:
            ws = self._ws()
            mx, my = _merc_px(self.lat, self.lon, ws)
            lat, lon = _merc_ll(mx - dx, my - dy, ws)
            self.lat, self.lon = max(-84.0, min(84.0, lat)), lon

    def _set_span_anchored(self, span: float, anchor: Optional[tuple], geo: Optional[tuple]) -> None:
        """Zoom keeping the ground under `anchor` where it is (the cursor)."""
        self.span = max(MIN_SPAN, min(MAX_SPAN, span))
        if not anchor or not geo:
            return
        now_geo = self._screen_to_geo(*anchor)
        if not now_geo:
            return
        proj = self._projector()
        a = proj(*geo)
        b = proj(*now_geo)
        if a and b:
            self._pan_pixels(anchor[0] - a[0], anchor[1] - a[1])

    def _zoom(self, factor: float, at: Optional[QPointF] = None, animate: bool = True) -> None:
        self.fly = None
        self.spin_until = 0.0
        base = self.zoom_to["span"] if self.zoom_to else self.span
        target = max(MIN_SPAN, min(MAX_SPAN, base * factor))
        anchor = (at.x(), at.y()) if at is not None else None
        geo = self._screen_to_geo(*anchor) if anchor else None
        if animate:
            self.zoom_to = {"span": target, "anchor": anchor, "geo": geo}
        else:
            self._set_span_anchored(target, anchor, geo)
            self._changed()
        self._kick()

    # ── data ─────────────────────────────────────────────────────────────────

    def _submit(self, key: str, fn: Callable, *args) -> None:
        if self.jobs.get(key):
            return
        self.jobs[key] = True
        self.stamps[key] = time.time()

        def run():
            try:
                value = fn(*args)
            except Exception as exc:
                _once(f"data {key}", exc)
                value = None
            try:
                self.bridge.data.emit(key, value)
            except RuntimeError:
                pass                                   # the view was deleted meanwhile
        self.pool.submit(run)

    def _fetch_static(self) -> None:
        """Things that do not depend on where the view is."""
        self._submit("home", self.provider.home)
        if self.labels is BUTTON_LABELS or not self.stamps.get("labels"):
            self._submit("labels", getattr(self.provider, "labels", lambda base: None), dict(BUTTON_LABELS))
        if not self.override_events:
            self._submit("events", self.provider.events)
        self._submit("iss", self.provider.iss)
        self._submit("aurora", self.provider.aurora)
        self._submit("credit", self.provider.credit, self.style)
        if self.style == "satellite" and self.sat_image is None:
            self._submit("satellite", self.provider.satellite_texture)

    def _settled(self) -> None:
        """The view stopped moving: ask for what belongs to this place."""
        try:
            self._live_timer()                        # the right rate follows the zoom
            lat, lon, span = self.lat, self.lon, self.span
            if "quakes" in self.layers and not self.override_quakes:
                self._submit("quakes", self.provider.quakes, lat, lon, span)
            if "aircraft" in self.layers and span <= AIRCRAFT_SPAN:
                self._submit("aircraft", self.provider.aircraft, lat, lon, span)
            if self.card_override is None:
                place = self.card_place
                if place and (self._globe() or math.hypot(place["lat"] - lat, place["lon"] - lon) > span / 111.0):
                    place = None                        # moved away from the named place
                self._submit("card", self.provider.card, lat, lon, span, place)
            self.update()
        except Exception as exc:
            _once("settle", exc)

    def _periodic(self) -> None:
        try:
            if not self.isVisible():
                return
            now = time.time()
            if "aircraft" in self.layers and self.span <= AIRCRAFT_SPAN and now - self.stamps.get("aircraft", 0) > 10:
                self._submit("aircraft", self.provider.aircraft, self.lat, self.lon, self.span)
            if "quakes" in self.layers and not self.override_quakes and now - self.stamps.get("quakes", 0) > 120:
                self._submit("quakes", self.provider.quakes, self.lat, self.lon, self.span)
            if now - self.stamps.get("iss", 0) > 60:
                self._submit("iss", self.provider.iss)
            if not self.override_events and (now - self.stamps.get("events", 0) > 300 or not self.events):
                self._submit("events", self.provider.events)
            if now - self.stamps.get("aurora", 0) > 600 or self.aurora_grid is None:
                self._submit("aurora", self.provider.aurora)
            self._live_timer()
        except Exception as exc:
            _once("periodic", exc)

    def _on_data(self, key: str, value: Any) -> None:
        try:
            self.jobs[key] = False
            if key == "quakes" and value is not None and not self.override_quakes:
                self.quakes = value
            elif key == "events" and value is not None and not self.override_events:
                self.events = value
            elif key == "aircraft":
                self.aircraft = value or []
            elif key == "iss":
                self.iss = value
            elif key == "aurora" and value is not None:
                self.aurora_grid = value
            elif key == "card":
                self.card = value or []
            elif key == "home":
                self.home = value
            elif key == "credit":
                self.credit = value or ""
            elif key == "labels" and isinstance(value, dict):
                self.labels = {k: str(value.get(k) or v) for k, v in BUTTON_LABELS.items()}
            elif key == "satellite" and value is not None:
                self.sat_image = value                # the GPU picks it up by its key
            self._live_timer()
            self.update()
        except Exception as exc:
            _once("data", exc)

    def _live_timer(self) -> None:
        """Repaint moving things only as often as they visibly move: the ISS
        crosses half a pixel a second on the whole globe, an airliner several
        pixels a second on a city map. Nothing moving → no repaints at all."""
        px_per_s = 0.0
        if "iss" in self.layers and self.iss:
            km_per_px = self.span / max(1.0, self.height() / 2.0)
            px_per_s = max(px_per_s, 7.66 / km_per_px)
        if self.follow_until > time.time():
            px_per_s = max(px_per_s, 2.0)
        if "aircraft" in self.layers and self.aircraft and not self._globe():
            fastest = max((a.get("speed_ms") or 0.0 for a in self.aircraft), default=0.0)
            m_per_px = self.span * 1000.0 / max(1.0, self.height() / 2.0)
            px_per_s = max(px_per_s, fastest / m_per_px)
        if px_per_s <= 0.05 or not self.isVisible():
            if self.live.isActive():
                self.live.stop()
            return
        interval = int(max(100, min(1000, 1000.0 / (px_per_s * 2.0))))   # ~half a pixel a frame
        if not self.live.isActive() or abs(self.live.interval() - interval) > 30:
            self.live.start(interval)

    def _live_tick(self) -> None:
        try:
            if not self._moving():
                self.update()
        except Exception as exc:
            _once("live", exc)

    def _iss_now(self) -> Optional[tuple]:
        try:
            pos = self.provider.iss_at(time.time())
        except Exception:
            pos = None
        if pos is None and self.iss:
            return self.iss["lat"], self.iss["lon"]
        return pos

    def _on_tile(self) -> None:
        try:
            if not self._globe():
                self.update()
        except Exception as exc:
            _once("tile update", exc)

    # ── painting ─────────────────────────────────────────────────────────────

    def _gpu_globe(self) -> Optional[_GpuGlobe]:
        if not self.gl_tried:
            self.gl_tried = True
            try:
                self.gpu = _GpuGlobe()
                self.gl_ok = True
            except Exception as exc:
                self.gl_ok = False
                _print(f"live view: no GPU drawing ({exc}) — the globe is drawn on the CPU")
        return self.gpu if self.gl_ok else None

    def paintEvent(self, e) -> None:
        started = time.perf_counter()
        p = None
        try:
            self._step()
            self._fade_step()
            if not self.isVisible():
                return
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            p.setOpacity(self.fade)
            if self._globe():
                if self._gpu_globe() is None:
                    self._paint_space(p)
                self._paint_globe(p)
            else:
                self._paint_map(p)
            self._paint_layers(p)
            self._paint_chrome(p)
        except Exception as exc:
            _once("paint", exc)
        finally:
            if p is not None and p.isActive():
                p.end()
        self.frames += 1
        spent = (time.perf_counter() - started) * 1000
        self.frame_ms.add(spent)
        try:
            if self.isVisible() and (self._moving() or self._fading() or self.fade != self.fade_to):
                # aim at 60 frames a second: wait only what this frame left of 16 ms
                self.pacer.start(max(0, int(15.5 - spent)))
        except Exception as exc:
            _once("pace", exc)

    def _paint_space(self, p: QPainter) -> None:
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor("#03070b"))
        p.setPen(QPen(QColor(*self.acc, 14), 1))
        step = 40
        for x in range(0, w, step):
            p.drawLine(x, 0, x, h)
        for y in range(0, h, step):
            p.drawLine(0, y, w, y)

    def _paint_globe(self, p: QPainter) -> None:
        w, h = self.width(), self.height()
        cx, cy = self._centre()
        R = self._globe_R()
        sun_ll = self.provider.sun(time.time())
        sun = _unit(*sun_ll) if sun_ll else None
        aurora_on = "aurora" in self.layers and self.aurora_grid is not None
        gpu = self._gpu_globe()
        if gpu is not None:
            try:
                dpr = self.devicePixelRatioF()
                wp, hp = max(1, int(round(w * dpr))), max(1, int(round(h * dpr)))
                e, n, c = _basis(self.lat, self.lon)
                m = np.column_stack([e, n, c])             # world = m · view
                satellite = self.style == "satellite" and self.sat_image is not None

                def earth_image() -> QImage:
                    if satellite:
                        return self.sat_image
                    tex, _ = pv._texture(self.accent)
                    return pv._qimage_from_array(np.dstack([tex, np.full(tex.shape[:2], 255, np.uint8)]))
                img = gpu.render(
                    wp, hp, "satellite" if satellite else f"dark{self.accent}", earth_image,
                    id(self.aurora_grid), self.aurora_grid,
                    {"rot": QMatrix3x3([float(x) for x in m.ravel()]),
                     "centre": (float(cx * dpr), float(cy * dpr)), "R": float(R * dpr),
                     "sun": tuple(float(x) for x in (sun if sun is not None else (1.0, 0.0, 0.0))),
                     "lit": 1.0 if sun is not None else 0.0,
                     "accent": tuple(v / 255.0 for v in self.acc),
                     "aurora_on": 1.0 if aurora_on else 0.0,
                     "bg": (3 / 255.0, 7 / 255.0, 11 / 255.0), "grid": float(40 * dpr)})
                # Opaque, so premultiplied and plain RGB are the same bytes: reinterpreting
                # (no copy) lets it land on the window's RGB32 backing store as a memcpy
                # instead of a per-pixel conversion (measured 8 ms → under 1 ms).
                img.reinterpretAsFormat(QImage.Format.Format_RGB32)
                img.setDevicePixelRatio(dpr)
                if p.opacity() >= 1.0:
                    # the picture is opaque: copy it, nothing to blend with
                    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
                p.drawImage(QPointF(0, 0), img)
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
                return
            except Exception as exc:
                _once("gpu globe", exc)
                self.gl_ok = False
        # CPU: a coarse picture while moving, a sharp one when still
        scale = 0.34 if self._moving() or self.drag else 1.0
        key = (round(self.lat, 3), round(self.lon, 3), round(self.span, 1), w, h, scale,
               self.style, aurora_on, int(time.time() / 120))
        if self.cpu_cache and self.cpu_cache[0] == key:
            img = self.cpu_cache[1]
        else:
            iw, ih = max(1, int(w * scale)), max(1, int(h * scale))
            if self.style == "satellite" and self.sat_image is not None:
                tex = self._sat_array()
            else:
                tex = pv._texture(self.accent)[0]
            arr = _cpu_globe(tex, _basis(self.lat, self.lon), R * scale, cx * scale, cy * scale, iw, ih,
                             sun, self.acc, self.aurora_grid if aurora_on else None)
            img = pv._qimage_from_array(arr)
            self.cpu_cache = (key, img)
        p.drawImage(QRectF(0, 0, w, h), img)

    _sat_cache: Optional[np.ndarray] = None

    def _sat_array(self) -> np.ndarray:
        if self._sat_cache is None and self.sat_image is not None:
            self._sat_cache = pv._rgba_from_qimage(self.sat_image)[..., :3].copy()
        return self._sat_cache

    def _paint_map(self, p: QPainter) -> None:
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor("#03070b"))
        spec = STYLES.get(self.style) or STYLES["dark"]
        ws = self._ws()
        z = math.log2(max(1.0, ws / _TILE))
        dpr = self.devicePixelRatioF()
        mx, my = _merc_px(self.lat, self.lon, ws)
        x0, y0 = mx - w / 2.0, my - h / 2.0
        wanted: set = set()
        offline = self.style == "offline" or self.tiles.offline()
        layers = [spec["base"]] if offline or not spec.get("labels") else [spec["base"], spec["labels"]]
        if offline:
            if self.style != "offline":
                svc, maxz, kind = spec["base"]
                zp = max(0, min(maxz, int(round(z))))
                cxp, cyp = _merc_px(self.lat, self.lon, _TILE * 2 ** zp)
                self.tiles.probe((svc, zp, int(cxp // _TILE) % (2 ** zp), int(cyp // _TILE), kind))
            layers = [STYLES["offline"]["base"]]
        for i, (svc, maxz, kind) in enumerate(layers):
            is_labels = i == 1
            # base: crisp on this screen; labels: one level lower, so names stay large enough to read
            zi = int(round(z + (math.log2(dpr) if not is_labels else -0.35)))
            zi = max(0, min(maxz, zi))
            n = 2 ** zi
            scale = ws / (n * _TILE)                       # tile px → screen px
            size = _TILE * scale
            tx0, tx1 = int(math.floor(x0 / size)), int(math.floor((x0 + w) / size))
            ty0, ty1 = max(0, int(math.floor(y0 / size))), min(n - 1, int(math.floor((y0 + h) / size)))
            for ty in range(ty0, ty1 + 1):
                for tx in range(tx0, tx1 + 1):
                    key = (svc, zi, tx % n, ty, kind)
                    wanted.add(key)
                    target = QRectF(tx * size - x0, ty * size - y0, size + 0.5, size + 0.5)
                    pix = self.tiles.get(key)
                    if pix is not None:
                        p.drawPixmap(target, pix, QRectF(0, 0, pix.width(), pix.height()))
                        continue
                    self.tiles.request(key)
                    if is_labels:
                        continue
                    for k in range(1, 7):                   # a parent tile, enlarged, until it arrives
                        pz = zi - k
                        if pz < 0:
                            break
                        pkey = (svc, pz, (tx % n) >> k, ty >> k, kind)
                        ppix = self.tiles.get(pkey)
                        if ppix is not None:
                            sub = ppix.width() / (2 ** k)
                            sx, sy = ((tx % n) % (2 ** k)) * sub, (ty % (2 ** k)) * sub
                            p.drawPixmap(target, ppix, QRectF(sx, sy, sub, sub))
                            break
        with self.tiles.lock:
            self.tiles.wanted = wanted
        p.drawPixmap(0, 0, self._map_shade(w, h))

    def _map_shade(self, w: int, h: int) -> QPixmap:
        """The vignette over the map, drawn once per size (a full-screen
        gradient every frame cost ~5 ms in software)."""
        dpr = self.devicePixelRatioF()
        key = (w, h, dpr)
        if getattr(self, "_shade_key", None) != key:
            pix = QPixmap(int(w * dpr), int(h * dpr))
            pix.setDevicePixelRatio(dpr)
            pix.fill(QColor(0, 0, 0, 0))
            sp = QPainter(pix)
            g = QRadialGradient(QPointF(w / 2, h / 2), max(w, h) * 0.7)
            g.setColorAt(0.55, QColor(0, 0, 0, 0))
            g.setColorAt(1.0, QColor(0, 0, 0, 150))
            sp.fillRect(0, 0, w, h, QBrush(g))
            sp.end()
            self._shade_pix, self._shade_key = pix, key
        return self._shade_pix

    def _fonts(self):
        s = max(0.72, min(1.35, self.height() / 900.0))
        base = QFont(QGuiApplication.font())
        big, mid, small = QFont(base), QFont(base), QFont(base)
        big.setPixelSize(int(28 * s))
        big.setBold(True)
        mid.setPixelSize(int(19 * s))
        small.setPixelSize(int(15 * s))
        return big, mid, small, s

    def _paint_layers(self, p: QPainter) -> None:
        proj = self._projector()
        w, h = self.width(), self.height()
        globe = self._globe()
        big, mid, small, s = self._fonts()
        now = time.time()
        hits: list = []

        def where(lat, lon):
            q = proj(lat, lon)
            if q is None or not (-60 <= q[0] <= w + 60 and -60 <= q[1] <= h + 60):
                return None
            return q

        def runs(points):
            out, cur, last = [], [], None
            for lat, lon in points:
                q = proj(lat, lon)
                if q is None or (last is not None and abs(q[0] - last[0]) > w):
                    if len(cur) > 1:
                        out.append(cur)
                    cur = [] if q is None else [q]
                else:
                    cur.append(q)
                last = q
            if len(cur) > 1:
                out.append(cur)
            return out

        layers: dict = {}
        if self.home:
            layers["home"] = self.home
        if "quakes" in self.layers or self.override_quakes:
            qs = [dict(q, age_s=now - q["time"], mag=q.get("mag") or 0.0) for q in self.quakes]
            layers["quakes"] = qs
            if self.focus_ref is not None:
                layers["focus"] = next((c for c, q in zip(qs, self.quakes) if q is self.focus_ref), None)
        if "events" in self.layers or self.override_events:
            layers["events"] = [e for e in self.events
                                if not (globe and e["kind"] == "wildfire" and e.get("level") not in ("orange", "red"))]
        if "aircraft" in self.layers and not globe:
            planes = []
            for a in self.aircraft:
                age = min(60.0, now - a.get("t0", now))
                if a.get("speed_ms") and not a.get("on_ground") and age > 0:
                    lat, lon = pv.destination(a["lat"], a["lon"], a.get("track") or 0.0, a["speed_ms"] * age)
                    planes.append(dict(a, lat=lat, lon=lon))
                else:
                    planes.append(a)
            layers["aircraft"] = planes
        if "iss" in self.layers and self.iss:
            iss = dict(self.iss)
            pos = self._iss_now()
            if pos:
                iss["lat"], iss["lon"] = pos
            layers["iss"] = iss
        if self.pins:
            layers["pins"] = self.pins
        if self.lines:
            layers["lines"] = self.lines
        if self.radius_m:
            layers["radius_m"] = self.radius_m
        pv._draw_layers(p, layers, self.acc, lambda la, lo: where(la, lo), runs, (big, mid, small))

        # what the cursor can point at, nearest-first on hover
        for q in layers.get("quakes") or []:
            pos = where(q["lat"], q["lon"])
            if pos:
                hits.append((pos[0], pos[1], max(8.0, pv.quake_style(q["mag"], q["age_s"])[1]), "quake", q))
        for e in layers.get("events") or []:
            pos = where(e["lat"], e["lon"])
            if pos:
                hits.append((pos[0], pos[1], 14.0, "event", e))
        for a in layers.get("aircraft") or []:
            pos = where(a["lat"], a["lon"])
            if pos:
                hits.append((pos[0], pos[1], 11.0, "plane", a))
        if layers.get("iss"):
            pos = where(layers["iss"]["lat"], layers["iss"]["lon"])
            if pos:
                hits.append((pos[0], pos[1], 22.0, "iss", layers["iss"]))
        self.hits = hits
        self._layers_drawn = layers

    def _paint_chrome(self, p: QPainter) -> None:
        w, h = self.width(), self.height()
        big, mid, small, s = self._fonts()
        acc = self.acc
        # corner brackets
        p.setPen(QPen(QColor(*acc, 210), 3))
        m, L = 12, int(38 * s)
        for (x, y, dx, dy) in ((m, m, 1, 1), (w - m, m, -1, 1), (m, h - m, 1, -1), (w - m, h - m, -1, -1)):
            p.drawLine(x, y, x + dx * L, y)
            p.drawLine(x, y, x, y + dy * L)
        # time and where the middle of the view is
        now = time.localtime()
        off = -time.altzone if now.tm_isdst > 0 else -time.timezone
        stamp = [(big, QColor("#eaf6ff"), time.strftime("%H:%M", now) + f"  {_offset(off)}"),
                 (small, QColor("#8ea6b8"), time.strftime("%Y-%m-%d", now)),
                 (small, QColor("#8ea6b8"), f"◉ {self.lat:.4f}, {self.lon:.4f}"),
                 (small, QColor("#8ea6b8"), _span_text(self.span * 2))]
        bottom = pv._panel_text(p, 32, 26, stamp, accent=acc)
        if self.tiles.busy() or any(self.jobs.values()):
            a = (time.time() * 360) % 360
            p.setPen(QPen(QColor(*acc), 2.2))
            p.drawArc(QRectF(32, bottom + 12, 16, 16), int(a * 16), 270 * 16)
            QTimer.singleShot(80, self.update)
        # card, top right (below the close button)
        lines = self.card_override if self.card_override is not None else self.card
        if lines:
            size = {"big": big, "mid": mid, "small": small}
            pv._panel_text(p, w - 32, 62 * s, [(size.get(k, small), QColor(c) if c else QColor("#8ea6b8"), t)
                                                for k, c, t in lines], align_right=True, accent=acc)
        # legend, bottom left
        self._legend(p, small, s)
        if not self._globe():
            ws = self._ws()
            mpp = 40_075_016.686 * math.cos(math.radians(self.lat)) / ws
            self._scale_bar(p, w - 32, h - 58 * s, mpp, small)
            if self.credit and self.style != "offline" and not self.tiles.offline():
                tiny = QFont(small)
                tiny.setPixelSize(max(9, int(11 * s)))
                p.setFont(tiny)
                cw = QFontMetricsF(tiny).horizontalAdvance(self.credit) + 16
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(3, 8, 14, 170))
                p.drawRoundedRect(QRectF(w - 28 - cw, h - 30 * s, cw, 18 * s), 4, 4)
                p.setPen(QColor(200, 214, 224, 190))
                p.drawText(QRectF(w - 28 - cw, h - 30 * s, cw, 18 * s), Qt.AlignmentFlag.AlignCenter,
                           self.credit)
        self._paint_buttons(p, s)
        if self.hover:
            self._tooltip(p, small, mid, s)

    def _legend(self, p: QPainter, small: QFont, s: float) -> None:
        seen: dict = {}
        for _x, _y, _r, kind, data in self.hits:              # only what landed on screen
            seen.setdefault(kind, []).append(data)
        layers = {"quakes": seen.get("quake", []), "events": seen.get("event", []),
                  "aircraft": seen.get("plane", [])}
        parts = []
        qs = [q for q in layers.get("quakes") or []]
        if qs:
            mags = [q["mag"] for q in qs if q.get("mag")]
            parts.append(("◉", QColor(pv.AGE_COLOURS[1]), f"{len(qs)}" + (f"  M {min(mags):.1f}–{max(mags):.1f}"
                                                                           if mags else "")))
        ev = layers.get("events") or []
        if ev:
            parts.append(("◆", QColor(pv.EVENT_COLOURS["wildfire"]), f"{len(ev)}"))
        pl = [a for a in layers.get("aircraft") or [] if not a.get("on_ground")]
        if pl:
            parts.append(("✈", QColor("#f2f8fc"), f"{len(pl)}"))
        if not parts:
            return
        p.setFont(small)
        fm = QFontMetricsF(small)
        widths = [fm.horizontalAdvance(f"{g}  {t}") + 26 for g, _, t in parts]
        box = QRectF(24, self.height() - 26 * s - 30 * s, sum(widths) + 20, 30 * s)
        pv._glass(p, box, self.acc)
        x = box.left() + 12
        for (glyph, colour, text), wd in zip(parts, widths):
            p.setPen(colour)
            p.drawText(QRectF(x, box.top(), 30, box.height()), Qt.AlignmentFlag.AlignVCenter, glyph)
            p.setPen(QColor("#b9cbd8"))
            p.drawText(QRectF(x + fm.horizontalAdvance(glyph) + 8, box.top(), wd, box.height()),
                       Qt.AlignmentFlag.AlignVCenter, text)
            x += wd

    def _scale_bar(self, p: QPainter, right: float, y: float, mpp: float, font: QFont) -> None:
        units = getattr(self.provider, "units", lambda: "metric")()
        pv._scale_bar(p, right, y, mpp, units, font, self.acc)

    def _paint_buttons(self, p: QPainter, s: float) -> None:
        w, h = self.width(), self.height()
        r = 17 * s
        items = [("close", "×", True)]
        col_x = w - 24 - r
        buttons = [("zin", "+", False), ("zout", "−", False), ("home", "⌂", False),
                   ("style", "◐", self.style == "satellite")]
        buttons += [(name, glyph, name in self.layers) for name, glyph in LAYER_BUTTONS]
        total = len(buttons) * (2 * r + 8 * s)
        y = max(130 * s, h / 2 - total / 2)
        self.buttons = [("close", QPointF(w - 24 - r, 24 + r - 6), r * 0.85)]
        for name, glyph, active in buttons:
            self.buttons.append((name, QPointF(col_x, y + r), r))
            y += 2 * r + 8 * s
        glyphs = {"close": "×", **{n: g for n, g, _ in buttons}}
        active = {"close": False, **{n: a for n, _, a in buttons}}
        font = QFont(QGuiApplication.font())
        font.setPixelSize(int(17 * s))
        p.setFont(font)
        hover = self.mapFromGlobal(QCursor.pos())
        hovered = None
        for name, c, rr in self.buttons:
            over = math.hypot(hover.x() - c.x(), hover.y() - c.y()) <= rr
            p.setPen(QPen(QColor(*self.acc, 200 if over else 110), 1.4))
            p.setBrush(QColor(*self.acc, 150) if active[name] else QColor(3, 8, 14, 205 if not over else 235))
            p.drawEllipse(c, rr, rr)
            p.setPen(QColor("#03070b") if active[name] else QColor("#dbe9f3"))
            p.drawText(QRectF(c.x() - rr, c.y() - rr, 2 * rr, 2 * rr), Qt.AlignmentFlag.AlignCenter,
                       glyphs[name])
            if over:
                hovered = (name, c, rr)
        if hovered:
            self._button_tip(p, *hovered, s)

    def _button_key(self, name: str) -> str:
        if name == "style":
            return "style_satellite" if self.style == "satellite" else "style_dark"
        if name in dict(LAYER_BUTTONS):
            return f"{name}_{'on' if name in self.layers else 'off'}"
        return name

    def _button_tip(self, p: QPainter, name: str, c: QPointF, r: float, s: float) -> None:
        """The button's purpose in words, to its left (below it for close)."""
        text = self.labels.get(self._button_key(name)) or BUTTON_LABELS.get(self._button_key(name), "")
        if not text:
            return
        font = QFont(QGuiApplication.font())
        font.setPixelSize(max(11, int(15 * s)))
        fm = QFontMetricsF(font)
        tw, th = fm.horizontalAdvance(text) + 22 * s, fm.height() + 12 * s
        box = QRectF(c.x() - r - 10 * s - tw, c.y() - th / 2, tw, th)   # to its left, same row
        box.moveLeft(max(6.0, box.left()))
        p.setPen(QPen(QColor(*self.acc, 110), 1.2))
        p.setBrush(QColor(3, 8, 14, 245))                 # opaque: the card may be underneath
        p.drawRoundedRect(box, 8, 8)
        p.setFont(font)
        p.setPen(QColor("#eaf6ff"))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def _tooltip(self, p: QPainter, small: QFont, mid: QFont, s: float) -> None:
        item = self.hover
        kind, d = item["kind"], item["data"]
        units = getattr(self.provider, "units", lambda: "metric")()
        lines: list = []
        if kind == "quake":
            lines = [(mid, QColor(pv.quake_style(d["mag"], d["age_s"])[0]), f"M {d['mag']:.1f}  {d.get('magtype', '')}"),
                     (small, QColor("#eaf6ff"), str(d.get("region") or "")[:48]),
                     (small, QColor("#8ea6b8"), f"↓ {_dist(d.get('depth_km'), units)}   ·   "
                                                f"{time.strftime('%H:%M', time.localtime(d['time']))}"
                                                f"  ({_ago(d['age_s'])})")]
        elif kind == "event":
            dots = {"red": "●●●", "orange": "●●", "green": "●"}.get(d.get("level") or "", "")
            lines = [(mid, QColor(pv.EVENT_COLOURS.get(d["kind"], "#fff")), f"{d.get('name', '')[:40]}  {dots}"),
                     (small, QColor("#eaf6ff"), " · ".join(x for x in (d.get("country"), d.get("severity")) if x)[:60])]
        elif kind == "plane":
            alt = "⏚" if d.get("on_ground") else _dist((d.get("alt_m") or 0) / 1000.0, units, alt=True)
            spd = f"{(d.get('speed_ms') or 0) * 3.6:,.0f} km/h".replace(",", " ") if units != "imperial" \
                else f"{(d.get('speed_ms') or 0) * 1.94384:,.0f} kt"
            lines = [(mid, QColor("#f2f8fc"), f"✈ {d.get('label') or '?'}"),
                     (small, QColor("#8ea6b8"), " · ".join(x for x in (d.get("type"), alt, spd) if x))]
        elif kind == "iss":
            lines = [(mid, QColor("#eaf6ff"), "✦ ISS"),
                     (small, QColor("#8ea6b8"), f"{_dist(d.get('alt_km'), units, alt=True)}  ·  "
                                                f"{d.get('speed_kmh', 0):,.0f} km/h".replace(",", " "))]
        lines = [ln for ln in lines if ln[2].strip()]
        if not lines:
            return
        x, y = self.hover_pos.x() + 18, self.hover_pos.y() + 14
        fm_w = max(QFontMetricsF(f).horizontalAdvance(t) for f, _, t in lines)
        if x + fm_w + 40 > self.width():
            x = self.hover_pos.x() - fm_w - 40
        if y + 90 * s > self.height():
            y = self.hover_pos.y() - 90 * s
        pv._panel_text(p, x + 14, y + 8, lines, accent=self.acc)

    # ── input ────────────────────────────────────────────────────────────────

    def _button_at(self, pos: QPointF) -> Optional[str]:
        for name, c, r in self.buttons:
            if math.hypot(pos.x() - c.x(), pos.y() - c.y()) <= r:
                return name
        return None

    def _hit_at(self, pos: QPointF) -> Optional[dict]:
        best, best_d = None, 1e9
        for x, y, r, kind, data in self.hits:
            d = math.hypot(pos.x() - x, pos.y() - y)
            if d <= r + 4 and d < best_d:
                best, best_d = {"kind": kind, "data": data}, d
        return best

    def _press_button(self, name: str) -> None:
        cx, cy = self._centre()
        if name == "close":
            self.close_view()
        elif name == "zin":
            self._zoom(0.5, QPointF(cx, cy))
        elif name == "zout":
            self._zoom(2.0, QPointF(cx, cy))
        elif name == "home":
            if self.home:
                self.card_place = self.home
                self.fly_to(self.home["lat"], self.home["lon"], min(self.span, 60.0))
        elif name == "style":
            self.set_style("satellite" if self.style != "satellite" else "dark")
        elif name in dict(LAYER_BUTTONS):
            self.layers ^= {name}
            self._settled()
            self._live_timer()
        self.update()

    def mousePressEvent(self, e) -> None:
        try:
            pos = e.position()
            self.setFocus()
            if e.button() == Qt.MouseButton.LeftButton:
                if self._button_at(pos):
                    self._pressed_button = self._button_at(pos)
                    return
                self._stop_motion()
                self.follow_until = 0.0
                self.drag = {"start": pos, "last": pos, "t": time.time(), "moved": 0.0,
                             "hist": [(time.time(), pos.x(), pos.y())]}
                self.vel = (0.0, 0.0)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
        except Exception as exc:
            _once("press", exc)

    def mouseMoveEvent(self, e) -> None:
        try:
            pos = e.position()
            if self.drag:
                now = time.time()
                dx, dy = pos.x() - self.drag["last"].x(), pos.y() - self.drag["last"].y()
                dt = max(1e-3, now - self.drag["t"])
                self._pan_pixels(dx, dy)
                hist = self.drag["hist"]
                hist.append((now, pos.x(), pos.y()))
                while len(hist) > 2 and now - hist[0][0] > 0.1:
                    hist.pop(0)
                self.drag.update(last=pos, t=now, moved=self.drag["moved"] + abs(dx) + abs(dy))
                self.hover = None
                self._changed()
                self.update()
                return
            hit = self._hit_at(pos)
            button = self._button_at(pos)
            self.setCursor(Qt.CursorShape.PointingHandCursor if (hit or button) else Qt.CursorShape.OpenHandCursor)
            changed = (hit or {}).get("data") is not (self.hover or {}).get("data") \
                or button != getattr(self, "_hover_button", None)
            self._hover_button = button
            if changed or hit:
                self.hover, self.hover_pos = hit, pos
                self.update()
        except Exception as exc:
            _once("move", exc)

    def mouseReleaseEvent(self, e) -> None:
        try:
            pos = e.position()
            pressed = getattr(self, "_pressed_button", None)
            self._pressed_button = None
            if pressed:
                if self._button_at(pos) == pressed:
                    self._press_button(pressed)
                return
            if not self.drag:
                return
            d = self.drag
            self.drag = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            if d["moved"] < 5:                            # a click, not a drag
                hit = self._hit_at(pos)
                self.vel = (0.0, 0.0)
                if hit:
                    data = hit["data"]
                    span = {"quake": 150.0, "event": 250.0, "plane": 40.0, "iss": 6371.0}[hit["kind"]]
                    self.fly_to(data["lat"], data["lon"], min(self.span, span) if hit["kind"] != "iss" else span)
                return
            # the fling: how fast the hand moved over its last 100 ms, not between two
            # events — a 1000 Hz mouse reports moves 1 ms apart and one of them is enough
            # to throw the world round twice
            hist = d["hist"]
            span_t = hist[-1][0] - hist[0][0] if len(hist) > 1 else 0.0
            if time.time() - d["t"] > 0.08 or span_t < 0.02:
                self.vel = (0.0, 0.0)                     # held still before letting go
            else:
                vx = (hist[-1][1] - hist[0][1]) / span_t
                vy = (hist[-1][2] - hist[0][2]) / span_t
                speed = math.hypot(vx, vy)
                cap = 3500.0
                if speed > cap:
                    vx, vy = vx * cap / speed, vy * cap / speed
                self.vel = (vx, vy)
            self._kick()
        except Exception as exc:
            _once("release", exc)

    def mouseDoubleClickEvent(self, e) -> None:
        try:
            if self._button_at(e.position()):
                return
            factor = 2.0 if e.button() == Qt.MouseButton.RightButton else 0.5
            self._zoom(factor, e.position())
        except Exception as exc:
            _once("double click", exc)

    def wheelEvent(self, e) -> None:
        try:
            delta = e.angleDelta().y()
            if delta == 0:
                delta = e.pixelDelta().y() * 2
            if delta:
                self._zoom(2 ** (-delta / 120.0 * 0.42), e.position())
            e.accept()
        except Exception as exc:
            _once("wheel", exc)

    def keyPressEvent(self, e) -> None:
        try:
            k = e.key()
            w, h = self.width(), self.height()
            steps = {Qt.Key.Key_Left: (w * 0.25, 0), Qt.Key.Key_Right: (-w * 0.25, 0),
                     Qt.Key.Key_Up: (0, h * 0.25), Qt.Key.Key_Down: (0, -h * 0.25)}
            if k in steps:
                self._stop_motion()
                dx, dy = steps[k]
                self.vel = (dx * 4.5, dy * 4.5)               # glide there with the fling's friction
                self._kick()
            elif k in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                self._zoom(0.5)
            elif k in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
                self._zoom(2.0)
            elif k == Qt.Key.Key_Home:
                self._press_button("home")
            else:
                e.ignore()                                    # leave other keys to the app
        except Exception as exc:
            _once("key", exc)

    def event(self, ev) -> bool:
        try:
            t = ev.type()
            if t == QEvent.Type.NativeGesture:
                if ev.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                    self._zoom(1.0 / max(0.2, 1.0 + ev.value()), ev.position(), animate=False)
                    return True
            elif t == QEvent.Type.Gesture:
                pinch = ev.gesture(Qt.GestureType.PinchGesture)
                if pinch is not None:
                    sf = pinch.scaleFactor()
                    if sf > 0:
                        self._zoom(1.0 / sf, pinch.centerPoint(), animate=False)
                    ev.accept()
                    return True
            elif t == QEvent.Type.Leave:
                self.hover = None
                self.update()
        except Exception as exc:
            _once("event", exc)
        try:
            return super().event(ev)
        except Exception as exc:
            _once("event base", exc)
            return False

    def hideEvent(self, e) -> None:
        try:
            self.live.stop()
        except Exception:
            pass

    def showEvent(self, e) -> None:
        try:
            self._live_timer()
            self.fade_in()
            self._kick()
        except Exception:
            pass


class deque_like:
    """The last N frame times, for stats()."""

    def __init__(self, n: int):
        self.n = n
        self.items: list = []

    def add(self, v: float) -> None:
        self.items.append(v)
        if len(self.items) > self.n:
            del self.items[0]


def _offset(seconds: float) -> str:
    minutes = int(round(seconds / 60))
    sign = "+" if minutes >= 0 else "−"
    return f"UTC{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def _span_text(km: float) -> str:
    return f"↕ {km:,.0f} km".replace(",", " ") if km >= 10 else f"↕ {km:.1f} km"


def _dist(km: Optional[float], units: str, alt: bool = False) -> str:
    if km is None:
        return "?"
    if units == "imperial":
        return f"{km * 3280.84:,.0f} ft".replace(",", " ") if alt or km < 1 else f"{km / 1.609344:.0f} mi"
    return f"{km * 1000:,.0f} m".replace(",", " ") if km < 1 or (alt and km < 20) else f"{km:,.0f} km".replace(",", " ")


def _ago(seconds: float) -> str:
    if seconds < 3600:
        return f"{max(1, round(seconds / 60))} min"
    if seconds < 2 * 86400:
        return f"{seconds / 3600:.0f} h"
    return f"{seconds / 86400:.0f} d"


# ─────────────────────────────────────────────────────────────────────────────
# Placement on the HUD
# ─────────────────────────────────────────────────────────────────────────────

class _HostWatch(QObject):
    """Keeps the view the size of the HUD's centre and gives the place back."""

    def __init__(self, view: PlanetWidget, host: QWidget):
        super().__init__(host)
        self.view = view
        self.host = host

    def eventFilter(self, obj, ev) -> bool:
        try:
            if ev.type() == QEvent.Type.Resize and not sip.isdeleted(self.view):
                self.view.setGeometry(self.host.rect())
        except Exception as exc:
            _once("host resize", exc)
        return False

    def page_changed(self, *_a) -> None:
        # a video, the camera or a picture took the centre: it is theirs now
        try:
            if not sip.isdeleted(self.view) and self.view.isVisible():
                self.view.close_view()
        except Exception as exc:
            _once("host page", exc)


_view: Optional[PlanetWidget] = None


def find_host(player) -> Optional[QWidget]:
    win = getattr(player, "_win", None) if player is not None else None
    host = getattr(win, "_hud_cam_stack", None)
    if isinstance(host, QWidget) and not sip.isdeleted(host):
        return host
    return None


def ensure_view(player, provider, accent: str) -> Optional[PlanetWidget]:
    """The one live view, created on first use, shown over the HUD's centre.
    Must be called on the GUI thread."""
    global _view
    try:
        if _view is not None and sip.isdeleted(_view):
            _view = None
        if _view is not None and _view.accent != accent:
            _view.close_view()
            _view.deleteLater()
            _view = None
        host = find_host(player)
        if _view is None:
            _view = PlanetWidget(provider, accent, host)
            if host is not None:
                watch = _HostWatch(_view, host)
                host.installEventFilter(watch)
                if hasattr(host, "currentChanged"):
                    host.currentChanged.connect(watch.page_changed)
                _view._host_watch = watch
            else:
                _view.setWindowTitle("⊕")
                _view.resize(1280, 800)
        if host is not None:
            _view.setGeometry(host.rect())
        if not _view.isVisible() or _view.fade_to == 0.0:
            _view.opening = True
            if _view.isVisible():                     # it was fading out: turn it round
                _view.fade_in()
        _view.show()
        _view.raise_()
        _view.setFocus()
        return _view
    except Exception as exc:
        _once("open", exc)
        return None


def current() -> Optional[PlanetWidget]:
    if _view is not None and not sip.isdeleted(_view):
        return _view
    return None
