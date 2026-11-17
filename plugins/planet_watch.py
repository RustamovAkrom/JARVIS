"""
Planet Watch — the planet on the HUD, steered by voice: a globe you can spin
and zoom into, maps down to street level, earthquakes and disasters as they
are reported, live aircraft, the aurora, the weather and the ISS overhead.

Inspired by God's Eye View (Bilawal Sidhu's open-source spatial-intelligence
globe) — not a port of it: nothing here runs a web app. The same kind of open
data, asked for by voice and drawn where the avatar sits. When nothing has
been asked for, the HUD is exactly what it always is.

    "show me the world"                → a globe lit by the real Sun, with today's
                                         strong quakes, current disasters, the ISS
                                         and the aurora on the night side
    "zoom in" · "closer" · "go north"  → the SAME view moves: the globe grows
    "spin the globe" · "go back"         until its curve fills the screen, then
                                         becomes a map, down to street level
    "show me Tokyo" / "satellite view" → a map with the local time, the weather,
                                         sunrise and sunset there, live aircraft
    "any earthquakes near me?"         → EMSC + USGS, merged
    "tell me if there is a quake over 4 within 300 km"
                                       → a background watch that speaks up
    "any disasters right now?"         → cyclones, floods, volcanoes, wildfires,
                                         droughts (GDACS alert levels + NASA EONET)
    "how far is Istanbul from Tokyo?"  → the great-circle route, drawn, with the
                                         flight time and the time difference
    "can I see the northern lights?"   → the aurora forecast from NOAA
    "where is the ISS?" / "when can I see it?" / "remind me before it passes"

THE VIEW IS LIVE (_planet_live.py)
    It opens over the centre of the HUD and is moved by hand as well as by
    voice: drag and fling, wheel or pinch to zoom toward the cursor — from the
    whole globe down to streets — double-click, arrows and + / -, hover for a
    card, click to fly there, buttons for zoom, home, satellite and each layer.
    It fades in over the HUD without touching JARVIS's window (no flicker, full
    screen stays full screen). The globe is drawn by the GPU offscreen (full
    screen 2560×1440: ~60 fps while dragging, ~11 ms a frame), the map from
    tiles; nothing redraws while nothing moves. Without a
    GPU the same view runs on the CPU; "pictures" in the settings brings back
    still images (_planet_view.py), which are also what the alerts use when
    the view is closed. Anything else taking the centre — a video, the camera
    — closes the view.

REQUIREMENTS
    Nothing for the globe, maps, quakes, disasters, aircraft, aurora, weather
    and routes — no account, no key. ISS pass predictions need one small
    library, the standard NORAD orbit model:

        pip install sgp4

    Without it the plugin still shows where the ISS is (from wheretheiss.at),
    and anything that needs a prediction says the exact command to run for the
    Python that runs JARVIS. No restart is needed after installing it.

DATA (all public and keyless; timings measured 2026-09-21)
    Earthquakes   EMSC seismicportal.eu (0.5 s) + USGS (0.4 s), one entry per quake
    Disasters     GDACS (7 s — so it is fetched in the background and kept for
                  15 minutes) + NASA EONET (4.5 s, the same way)
    Aircraft      adsb.lol community ADS-B (0.5 s), only on maps (250 NM limit)
    Aurora        NOAA SWPC OVATION grid and the planetary Kp index
    Weather       Open-Meteo (0.6 s): now, sunrise, sunset, the place's time zone
    ISS           TLE from wheretheiss.at, CelesTrak second (12-15 s that day)
    Globe / maps  Natural Earth outlines once (108 KB); Esri basemaps, credited
    Places        OpenStreetMap's geocoder, any language, cached

EARTHQUAKE AND DISASTER REPORTS ARE NOT WARNINGS
    They are published after the fact. The plugin says that one happened, never
    that one is coming; the model is told so in every result that could be read
    otherwise.

YOUR LOCATION
    ⚙ → PLUGIN SETTINGS → PLANET WATCH: coordinates or a place name in any
    language. Left empty, the location saved for Aircraft report is used.

NOTHING HERE KNOWS A LANGUAGE
    Results are facts — numbers, codes, names as published — and the assistant
    says them in the user's language. Pictures use numbers, arrows and symbols.

TO RUN THE QUAKE WATCH FROM START-UP
    Add a routine that runs at start-up with the instruction to start the
    earthquake watch — a plugin's thread does not outlive the app.
"""
from __future__ import annotations

import calendar
import importlib.util
import json
import math
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

try:
    from plugins import _planet_view as view
except ImportError:  # loaded outside the package
    import _planet_view as view  # type: ignore

_NS = "planet_watch"
BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_FILE = BASE_DIR / "memory" / "planet_watch_cache.json"
OUT_DIR = BASE_DIR / "reports" / "planet_watch"
_UA = "MarkJARVIS-planet_watch/1.0 (open-source desktop assistant plugin)"

_EMSC = "https://www.seismicportal.eu/fdsnws/event/1/query"
_USGS = "https://earthquake.usgs.gov/fdsnws/event/1/query"
_GDACS = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/EVENTS4APP"
_EONET = "https://eonet.gsfc.nasa.gov/api/v3/events?status=open&days=30&limit=500"
_OVATION = "https://services.swpc.noaa.gov/json/ovation_aurora_latest.json"
_KP = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
_KP_FORECAST = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json"
_METEO = "https://api.open-meteo.com/v1/forecast"
_ADSB = "https://api.adsb.lol/v2/point/{lat:.4f}/{lon:.4f}/{nm}"
_ISS_ID = 25544
_TLE_SOURCES = (
    ("wheretheiss.at", f"https://api.wheretheiss.at/v1/satellites/{_ISS_ID}/tles"),
    ("CelesTrak", f"https://celestrak.org/NORAD/elements/gp.php?CATNR={_ISS_ID}&FORMAT=TLE"),
)
_WTIA = f"https://api.wheretheiss.at/v1/satellites/{_ISS_ID}"
_TLE_TTL = 12 * 3600
_EARTH_KM = 6371.0
_KM_PER_DEG = 111.195
_WORLD_MIN_MAG = 4.5
_ALERTS_PER_HOUR = 6
_MIN_WATCH_INTERVAL = 60.0          # both services are free; one poll a minute at most

# The view's "span" is half its height in km. The globe shows the whole
# hemisphere at 6371; below MAP_SPAN the same view continues as a map.
MAP_SPAN = 2600.0
MIN_SPAN, MAX_SPAN = 0.4, 20000.0
AIRCRAFT_SPAN = 450.0               # adsb.lol answers for 250 NM around a point
LAYERS = ("quakes", "events", "aircraft", "iss", "aurora", "weather")
_DIRECTIONS = {"n": 0, "north": 0, "ne": 45, "northeast": 45, "e": 90, "east": 90,
               "se": 135, "southeast": 135, "s": 180, "south": 180, "sw": 225, "southwest": 225,
               "w": 270, "west": 270, "nw": 315, "northwest": 315,
               "up": 0, "down": 180, "left": 270, "right": 90}


# ─────────────────────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────────────────────

def _sgp4_ok() -> bool:
    return importlib.util.find_spec("sgp4") is not None


def _install_command() -> str:
    """pip for the Python that runs JARVIS — a bare 'pip' can belong to another.
    pythonw is swapped for python so the command shows its output."""
    exe = Path(sys.executable)
    if exe.stem.lower() == "pythonw":
        exe = exe.with_name("python" + exe.suffix)
    return f'"{exe}" -m pip install sgp4'


def _test_settings(values: dict) -> tuple[bool, str]:
    """TEST: resolve the location and ask every source once."""
    values = values or {}
    typed = str(values.get("location") or "").strip()
    # What was typed is what is tested — falling back to another saved location
    # here would pass a place that does not exist.
    place = _resolve_location(typed, force=True) if typed else _resolve_location()
    if not place:
        return False, ("Location not found — enter coordinates like 41.0082, 28.9784 or a place name."
                       if typed else "No location set here or in Aircraft report.")
    radius = _num(values.get("quake_radius_km"), 500)
    with ThreadPoolExecutor(max_workers=5) as pool:
        fq = pool.submit(fetch_quakes, place["lat"], place["lon"], radius, 2.5, time.time() - 86400)
        fi = pool.submit(iss_state)
        fw = pool.submit(weather, place["lat"], place["lon"])
        fe = pool.submit(_events_feed.get, 25.0)
        fa = pool.submit(fetch_aircraft, place["lat"], place["lon"], 50.0)
        quakes, sources, problem = fq.result()
        state, wx, events, planes = fi.result(), fw.result(), fe.result(), fa.result()
    parts = [f"{place['name']} ({place['lat']:.4f}, {place['lon']:.4f})"]
    if quakes is None:
        parts.append(f"earthquake services not answering ({problem})")
    else:
        parts.append(f"{len(quakes)} quake{'' if len(quakes) == 1 else 's'} M≥2.5 within {radius:g} km "
                     f"in 24 h ({'+'.join(sources)})")
    parts.append(f"weather {wx['temp']:.0f} °C" if wx else "weather service not answering")
    parts.append(f"{len(events)} current disasters" if events is not None else "disaster feeds not answering")
    parts.append(f"{len(planes)} aircraft within 50 km" if planes is not None else "aircraft feed not answering")
    if state:
        parts.append(f"ISS at {state['lat']:.1f}, {state['lon']:.1f} ({state['source']})")
    parts.append("orbit library ✓" if _sgp4_ok() else f"passes need: {_install_command()}")
    parts.append("world outlines ✓" if view.world_available() else "world outlines not downloaded")
    return quakes is not None, " · ".join(parts)


PLUGIN_SETTINGS = {
    "namespace": _NS,
    "title": "⊕  PLANET WATCH",
    "fields": [
        {"key": "location", "label": "Your location — coordinates or a place name "
                                     "(empty: the one saved for Aircraft report)",
         "type": "text", "placeholder": "41.0082, 28.9784   or   Kadıköy, İstanbul"},
        {"key": "view_mode", "label": "View: live (move it with the mouse, GPU) or still pictures",
         "type": "choice", "options": ["live", "pictures"], "default": "live"},
        {"key": "map_style", "label": "Map style", "type": "choice",
         "options": ["dark", "satellite", "offline"], "default": "dark"},
        {"key": "units", "label": "Units", "type": "choice",
         "options": ["metric", "imperial"], "default": "metric"},
        {"key": "show_events", "label": "Show disasters (cyclones, floods, volcanoes, fires)",
         "type": "toggle", "default": True},
        {"key": "show_aircraft", "label": "Show live aircraft on close-up maps",
         "type": "toggle", "default": True},
        {"key": "show_aurora", "label": "Show the aurora on the globe", "type": "toggle", "default": True},
        {"key": "quake_radius_km", "label": "Earthquakes 'near me' and the watch: radius (km)",
         "type": "choice", "options": ["100", "250", "500", "1000", "2000"], "default": "500"},
        {"key": "quake_min_mag", "label": "Watch: alert from magnitude",
         "type": "choice", "options": ["2.5", "3", "3.5", "4", "4.5", "5", "5.5", "6"],
         "default": "4"},
        {"key": "quake_global", "label": "Watch: also any quake worldwide from magnitude",
         "type": "choice", "options": ["off", "6", "6.5", "7", "7.5"], "default": "off"},
        {"key": "quake_interval", "label": "Watch: check every (seconds)",
         "type": "choice", "options": ["60", "120", "300"], "default": "60"},
        {"key": "show_on_alert", "label": "Watch: show the map when it alerts (never over a video)",
         "type": "toggle", "default": True},
        {"key": "pass_min_el", "label": "ISS passes: lowest useful height above the horizon (°)",
         "type": "choice", "options": ["10", "20", "30"], "default": "10"},
        {"key": "remind_minutes", "label": "ISS reminder: minutes before",
         "type": "choice", "options": ["2", "5", "10", "15"], "default": "5"},
    ],
    "action": {"label": "TEST", "run": _test_settings},
}


def _setting(key: str, default: Any = "", ns: str = _NS) -> Any:
    try:
        from memory.config_manager import get_plugin_setting
        value = get_plugin_setting(ns, key, default)
    except Exception:
        return default
    return default if value in (None, "") else value


def _num(value: Any, default: float) -> float:
    try:
        if isinstance(value, str):
            value = value.strip().replace(",", ".")
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _flag(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str) and value.strip():
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


def _print(text: str) -> None:
    try:
        print(f"[planet_watch] {text}")
    except Exception:
        pass


def _log(player, text: str) -> None:
    _print(text)
    if player:
        try:
            player.write_log(f"JARVIS: {text}")
        except Exception:
            pass


def _outcome():
    try:
        from core import outcome
        return outcome
    except Exception:
        return None


def _clean(facts: dict) -> dict:
    """Drop what says nothing. A zero stays — "0 earthquakes" is an answer —
    which is why this is not `v not in (None, "", [], False)`: 0 == False."""
    return {k: v for k, v in facts.items()
            if not (v is None or v is False or (isinstance(v, (str, list, dict)) and not v))}


def _done(**facts) -> str:
    o = _outcome()
    facts = _clean(facts)
    return o.done(_NS, **facts) if o else json.dumps({"status": "done", **facts}, ensure_ascii=False)


def _failed(reason: str, **facts) -> str:
    o = _outcome()
    return o.failed(_NS, reason, **_clean(facts)) if o else f"status=failed reason={reason}"


def _ask(question: str, **facts) -> str:
    o = _outcome()
    return o.ask(_NS, question, **_clean(facts)) if o else f"status=ask question={question}"


def _accent() -> str:
    try:
        cfg = json.loads((BASE_DIR / "config" / "api_keys.json").read_text(encoding="utf-8"))
        colour = str(cfg.get("ui_color") or "")
        if re.fullmatch(r"#[0-9a-fA-F]{6}", colour):
            return colour
    except Exception:
        pass
    return "#38d6ff"


def _units() -> str:
    return "imperial" if str(_setting("units", "metric")) == "imperial" else "metric"


def _default_style() -> str:
    style = str(_setting("map_style", "dark")).strip().lower()
    return style if style in ("dark", "satellite", "offline") else "dark"


# ─────────────────────────────────────────────────────────────────────────────
# Small formats
# ─────────────────────────────────────────────────────────────────────────────

def fmt_km(km: Optional[float], units: str) -> str:
    if km is None:
        return "?"
    if units == "imperial":
        mi = km / 1.609344
        if mi < 0.5:
            return f"{km * 3280.84:,.0f} ft".replace(",", " ")
        return f"{mi:.1f} mi" if mi < 10 else f"{mi:,.0f} mi".replace(",", " ")
    if km < 1:
        return f"{km * 1000:.0f} m"
    return f"{km:.1f} km" if km < 10 else f"{km:,.0f} km".replace(",", " ")


def fmt_speed(kmh: float, units: str) -> str:
    if units == "imperial":
        return f"{kmh / 1.609344:,.0f} mph".replace(",", " ")
    return f"{kmh:,.0f} km/h".replace(",", " ")


def fmt_temp(c: float, units: str) -> str:
    return f"{c * 9 / 5 + 32:.0f} °F" if units == "imperial" else f"{c:.1f} °C"


def fmt_ago(seconds: float) -> str:
    """SI symbols only: 12 min, 3 h, 2 d."""
    seconds = max(0.0, seconds)
    if seconds < 3600:
        return f"{max(1, round(seconds / 60))} min"
    if seconds < 2 * 86400:
        return f"{seconds / 3600:.0f} h"
    return f"{seconds / 86400:.0f} d"


def _local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts).astimezone()


def _iso(ts: float) -> str:
    return _local(ts).isoformat(timespec="minutes")


def _hm(ts: float) -> str:
    return _local(ts).strftime("%H:%M")


def _offset_text(seconds: float) -> str:
    minutes = int(round(seconds / 60))
    sign = "+" if minutes >= 0 else "−"
    return f"UTC{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def _stamp() -> dict:
    now = _local(time.time())
    off = now.utcoffset()
    return {"stamp_time": f"{now:%H:%M}  {_offset_text(off.total_seconds() if off else 0)}",
            "stamp_date": f"{now:%Y-%m-%d}"}


_TS_RE = re.compile(r"(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)(?::(\d\d))?(?:\.(\d+))?")


def _parse_utc(text: str) -> Optional[float]:
    """An ISO time in UTC — written by hand because older Pythons' fromisoformat
    rejects both 'Z' and a one-digit fraction, and EMSC sends both."""
    m = _TS_RE.search(str(text or ""))
    if not m:
        return None
    y, mo, d, h, mi = (int(g) for g in m.groups()[:5])
    s = int(m.group(6) or 0)
    frac = float("0." + m.group(7)) if m.group(7) else 0.0
    try:
        return calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0)) + frac
    except (ValueError, OverflowError):
        return None


def _utc_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


_COMPASS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
_ARROWS = ("↑", "↗", "→", "↘", "↓", "↙", "←", "↖")


def compass(deg: float) -> str:
    return _COMPASS[int((deg % 360) / 22.5 + 0.5) % 16]


def arrow(deg: float) -> str:
    return _ARROWS[int((deg % 360) / 45 + 0.5) % 8]


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_KM * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _midpoint(a: tuple, b: tuple) -> tuple[float, float]:
    return view.great_circle(a, b, 2)[1]


# ─────────────────────────────────────────────────────────────────────────────
# Network and caches
# ─────────────────────────────────────────────────────────────────────────────

_cache_lock = threading.Lock()


def _load_cache() -> dict:
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache(data: dict) -> None:
    """Atomic write. On Windows the final rename is refused while anything —
    an antivirus scan, an indexer — has the file open (seen: WinError 5), so it
    is tried a few times before giving up."""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as exc:
        _print(f"cache not saved: {exc}")
        return
    for attempt in range(5):
        try:
            tmp.replace(CACHE_FILE)
            return
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
        except Exception as exc:
            _print(f"cache not saved: {exc}")
            return
    _print("cache not saved: the file stayed locked")


def _get(url: str, timeout: float = 10.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(12_000_000)


def _get_json(url: str, timeout: float = 10.0) -> Any:
    raw = _get(url, timeout)
    if not raw.strip():                      # FDSN answers 204 with no body for "none"
        return None
    return json.loads(raw.decode("utf-8"))


class _Feed:
    """A slow public feed kept fresh in the background.

    `get(wait)` returns what is known at once when it is fresh — or stale but
    still usable — and starts a refresh; with nothing known yet it waits up to
    `wait` seconds for the first answer. GDACS takes ~7 s to answer, and nobody
    should wait 7 s for a globe."""

    def __init__(self, name: str, fetch: Callable[[], Any], ttl: float):
        self.name, self.fetch, self.ttl = name, fetch, ttl
        self.lock = threading.Lock()
        self.value: Any = None
        self.at = 0.0
        self.busy: Optional[threading.Event] = None

    def _run(self, done: threading.Event) -> None:
        try:
            value = self.fetch()
            with self.lock:
                self.value, self.at = value, time.time()
        except Exception as exc:
            _print(f"{self.name} not fetched: {type(exc).__name__}: {exc}")
        finally:
            with self.lock:
                self.busy = None
            done.set()

    def refresh(self) -> threading.Event:
        with self.lock:
            if self.busy is None:
                self.busy = threading.Event()
                threading.Thread(target=self._run, args=(self.busy,), daemon=True,
                                 name=f"planet-{self.name}").start()
            return self.busy

    def get(self, wait: float = 0.0) -> Any:
        with self.lock:
            value, age = self.value, time.time() - self.at
        if value is not None and age < self.ttl:
            return value
        done = self.refresh()
        if value is not None and age < 6 * self.ttl:
            return value
        if wait > 0:
            done.wait(wait)
            with self.lock:
                return self.value
        return value


class _Memo:
    """A small time-limited memo for fast feeds asked about many places."""

    def __init__(self, ttl: float, size: int = 64):
        self.ttl, self.size = ttl, size
        self.lock = threading.Lock()
        self.data: dict = {}

    def get(self, key, fetch: Callable[[], Any]) -> Any:
        now = time.time()
        with self.lock:
            hit = self.data.get(key)
        if hit and now - hit[0] < self.ttl:
            return hit[1]
        value = fetch()
        if value is not None:
            with self.lock:
                self.data[key] = (now, value)
                if len(self.data) > self.size:
                    oldest = min(self.data, key=lambda k: self.data[k][0])
                    self.data.pop(oldest, None)
        return value


# OpenStreetMap's geocoder asks for one request a second at most.
_nominatim_lock = threading.Lock()
_nominatim_last = [0.0]


def _nominatim(path: str, params: dict) -> Any:
    with _nominatim_lock:
        wait = 1.1 - (time.time() - _nominatim_last[0])
        if wait > 0:
            time.sleep(wait)
        _nominatim_last[0] = time.time()
    url = f"https://nominatim.openstreetmap.org/{path}?" + urllib.parse.urlencode(params)
    return _get_json(url, timeout=10)


# ─────────────────────────────────────────────────────────────────────────────
# Location
# ─────────────────────────────────────────────────────────────────────────────

def parse_coordinates(text: str) -> Optional[tuple[float, float]]:
    """'41.0082, 28.9784', '41,0082; 28,9784', '41.0082 N 28.9784 E' — a decimal
    comma is common in half the world, so it is accepted."""
    parts = re.findall(r"[+-]?\d+(?:[.,]\d+)?", text or "")
    if len(parts) == 4 and all(re.fullmatch(r"[+-]?\d+", p) for p in parts):
        parts = [f"{parts[0]}.{parts[1]}", f"{parts[2]}.{parts[3]}"]
    if len(parts) != 2:
        return None
    rest = re.sub(r"[+-]?\d+(?:[.,]\d+)?", "", text)
    if re.search(r"[^\s,;°NSEWnsew]", rest):
        return None                          # there are words too — a place name
    try:
        lat, lon = (float(p.replace(",", ".")) for p in parts)
    except ValueError:
        return None
    if re.search(r"[Ss]", rest):
        lat = -abs(lat)
    if re.search(r"[Ww]", rest):
        lon = -abs(lon)
    if -90 <= lat <= 90 and -180 <= lon <= 180:
        return lat, lon
    return None


def _resolve_location(text: str = "", force: bool = False) -> Optional[dict]:
    """{"lat", "lon", "name", "label", "extent_km"} or None.

    With no text: this plugin's saved location, else Aircraft report's.
    `extent_km` is half the diagonal of the place's own bounding box — what
    "show me <a country>" should fit — or None for a bare coordinate.
    """
    text = (text or str(_setting("location", ""))
            or str(_setting("location", "", ns="aircraft_report"))).strip()
    if not text:
        return None
    coords = parse_coordinates(text)
    if coords:
        name = f"{coords[0]:.4f}, {coords[1]:.4f}"
        return {"lat": coords[0], "lon": coords[1], "name": name,
                "label": _coordinate_label(coords[0], coords[1]) or name, "extent_km": None}
    with _cache_lock:
        hit = (_load_cache().get("places") or {}).get(text)
    if hit and not force and "view_km" in hit:        # older entries lack view_km: look again
        return hit
    try:
        found = _nominatim("search", {"q": text, "format": "jsonv2", "limit": 1})
    except Exception as exc:
        _print(f"place lookup failed: {exc}")
        return hit
    if not found:
        return None
    first = found[0]
    lat, lon = float(first["lat"]), float(first["lon"])
    extent = None
    try:
        s, n, w, e = (float(x) for x in first.get("boundingbox") or [])
        extent = max(0.5, min(3000.0, distance_km(s, w, n, e) / 2))
    except (TypeError, ValueError):
        pass
    display = str(first.get("display_name") or text)
    place = {"lat": lat, "lon": lon, "name": display[:90],
             "label": (first.get("name") or display.split(",")[0]).strip()[:40],
             "extent_km": extent,
             "view_km": _view_size(str(first.get("addresstype") or first.get("type") or ""),
                                   int(_num(first.get("place_rank"), 16)), extent)}
    with _cache_lock:
        cache = _load_cache()
        cache.setdefault("places", {})[text] = place
        _save_cache(cache)
    return place


_KIND_KM = {"country": None, "state": 250.0, "region": 250.0, "province": 250.0,
            "state_district": 120.0, "county": 60.0, "city": 30.0, "municipality": 30.0,
            "town": 12.0, "city_district": 8.0, "borough": 8.0, "village": 4.0, "suburb": 4.0,
            "quarter": 2.5, "neighbourhood": 2.0, "island": None, "archipelago": None}


def _view_size(kind: str, rank: int, extent: Optional[float]) -> float:
    """How far a view of this place should reach (half its height, km).

    Measured: Tokyo is an 'administrative city' whose bounding box runs to
    islands 1000 km south, so "show me Tokyo" opened a 2900 km map. The kind
    of place caps the box; a country or an island uses the box itself."""
    cap = _KIND_KM.get(kind.lower(), "rank")
    if cap == "rank":
        cap = None if rank <= 4 else 250.0 if rank <= 8 else 30.0 if rank <= 16 else 4.0 if rank <= 22 else 1.2
    if cap is None:
        return max(20.0, extent or 800.0)
    return max(0.8, min(cap, extent or cap))


def _coordinate_label(lat: float, lon: float) -> Optional[str]:
    """A name for bare coordinates on the HUD — the district or town, as
    OpenStreetMap writes it locally. Looked up once, then kept."""
    key = f"{lat:.4f},{lon:.4f}"
    with _cache_lock:
        labels = _load_cache().get("labels") or {}
    if key in labels:
        return labels[key] or None
    try:
        data = _nominatim("reverse", {"lat": f"{lat:.5f}", "lon": f"{lon:.5f}",
                                      "format": "jsonv2", "zoom": 12})
        address = (data or {}).get("address") or {}
        label = next((address[k] for k in ("city_district", "town", "suburb", "village", "city",
                                           "municipality", "county", "state") if address.get(k)), "")
    except Exception:
        return None                                   # offline: try again next time
    with _cache_lock:
        cache = _load_cache()
        cache.setdefault("labels", {})[key] = str(label)[:40]
        _save_cache(cache)
    return str(label)[:40] or None



_under_memo = _Memo(ttl=7 * 86400, size=256)


def what_is_under(lat: float, lon: float, span_km: float) -> Optional[str]:
    """The name of what the middle of the view shows, at a level that fits how
    far away the view is: a country from space, a street close up. None over
    open water — the model names the sea from the coordinates."""
    zoom = 3 if span_km > 1500 else 5 if span_km > 300 else 8 if span_km > 60 else 10 if span_km > 8 else 14
    key = (round(lat, 1 if zoom < 8 else 3), round(lon, 1 if zoom < 8 else 3), zoom)

    def fetch():
        try:
            data = _nominatim("reverse", {"lat": f"{lat:.5f}", "lon": f"{lon:.5f}",
                                          "format": "jsonv2", "zoom": zoom})
        except Exception:
            return None
        if not isinstance(data, dict) or data.get("error"):
            return ""
        parts = [x.strip() for x in str(data.get("display_name") or "").split(",") if x.strip()]
        return ", ".join(parts[:2] + ([parts[-1]] if len(parts) > 2 else []))[:120]
    found = _under_memo.get(key, fetch)
    return found or None



def _home() -> Optional[dict]:
    return _resolve_location()


def _place(parameters: dict, key: str = "place") -> tuple[Optional[dict], bool]:
    """(place, named) — the place the user named under `key`, or their own location."""
    spoken = str(parameters.get(key) or (parameters.get("location") if key == "place" else "") or "").strip()
    if spoken:
        found = _resolve_location(spoken)
        if found:
            return found, True
    return _home(), False


def _need_location(what: str) -> str:
    return _ask(f"the user's location is not set and {what} needs one — ask them to enter it in "
                "Settings → Plugin settings → Planet watch (coordinates or a place name), or to "
                "name a place now and pass it as 'place'",
                reason="no location configured")


# ─────────────────────────────────────────────────────────────────────────────
# Earthquakes — EMSC and USGS, merged
# ─────────────────────────────────────────────────────────────────────────────

def _in_euromed(lat: float, lon: float) -> bool:
    """Where EMSC's own network is densest, its reading is the one kept."""
    return 25.0 <= lat <= 75.0 and -35.0 <= lon <= 65.0


def _q_emsc(since: float, min_mag: float, lat=None, lon=None, radius_km=None) -> list:
    params = {"format": "json", "start": _utc_iso(since), "minmag": f"{min_mag:.1f}",
              "limit": 500, "orderby": "time"}
    if lat is not None:
        params.update(lat=f"{lat:.4f}", lon=f"{lon:.4f}",
                      maxradius=f"{min(180.0, radius_km / _KM_PER_DEG):.3f}")
    data = _get_json(_EMSC + "?" + urllib.parse.urlencode(params), timeout=12)
    out = []
    for f in (data or {}).get("features") or []:
        p = f.get("properties") or {}
        evtype = str(p.get("evtype") or "").lower()
        if evtype and evtype not in ("ke", "se"):        # known / suspected earthquake
            continue
        ts = _parse_utc(p.get("time"))
        if ts is None or p.get("lat") is None or p.get("lon") is None:
            continue
        unid = str(p.get("unid") or f.get("id") or "")
        out.append({"id": f"emsc:{unid}", "src": "EMSC", "time": ts,
                    "mag": _num(p.get("mag"), float("nan")), "magtype": str(p.get("magtype") or ""),
                    "depth_km": _num(p.get("depth"), float("nan")),
                    "lat": float(p["lat"]), "lon": float(p["lon"]),
                    "region": str(p.get("flynn_region") or "").strip(),
                    "url": f"https://www.seismicportal.eu/eventdetails.html?unid={unid}" if unid else "",
                    "tsunami": False, "felt": None, "alert": None})
    return out


def _q_usgs(since: float, min_mag: float, lat=None, lon=None, radius_km=None) -> list:
    params = {"format": "geojson", "starttime": _utc_iso(since), "minmagnitude": f"{min_mag:.1f}",
              "orderby": "time", "limit": 500, "eventtype": "earthquake"}
    if lat is not None:
        params.update(latitude=f"{lat:.4f}", longitude=f"{lon:.4f}",
                      maxradiuskm=f"{min(20001.6, radius_km):.1f}")
    data = _get_json(_USGS + "?" + urllib.parse.urlencode(params), timeout=12)
    out = []
    for f in (data or {}).get("features") or []:
        p = f.get("properties") or {}
        c = (f.get("geometry") or {}).get("coordinates") or []
        if len(c) < 2 or p.get("time") is None:
            continue
        out.append({"id": f"usgs:{f.get('id')}", "src": "USGS", "time": float(p["time"]) / 1000.0,
                    "mag": _num(p.get("mag"), float("nan")), "magtype": str(p.get("magType") or ""),
                    "depth_km": _num(c[2] if len(c) > 2 else None, float("nan")),
                    "lat": float(c[1]), "lon": float(c[0]),
                    "region": str(p.get("place") or "").strip(),
                    "url": str(p.get("url") or ""), "tsunami": bool(p.get("tsunami")),
                    "felt": int(p["felt"]) if p.get("felt") else None,
                    "alert": p.get("alert") or None})
    return out


def _nan(value) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def merge_quakes(*lists: list) -> list:
    """One entry per earthquake, newest first. Two reports are the same quake
    when they are within 60 s and 120 km of each other."""
    merged: list = []
    for q in sorted((q for lst in lists for q in lst), key=lambda q: q["time"]):
        same = None
        for o in reversed(merged):
            if q["time"] - o["time"] > 60:
                break
            if distance_km(o["lat"], o["lon"], q["lat"], q["lon"]) <= 120:
                same = o
                break
        if same is None:
            merged.append(dict(q, sources=list(q.get("sources") or [q["src"]]),
                               ids=list(q.get("ids") or [q["id"]])))
            continue
        keep_new = (q["src"] == "EMSC") == _in_euromed(q["lat"], q["lon"])
        main, other = (q, same) if keep_new else (same, q)
        combined = dict(main, sources=sorted(set(same["sources"]) | set(q.get("sources") or [q["src"]])),
                        ids=same["ids"] + list(q.get("ids") or [q["id"]]))
        combined["tsunami"] = bool(main.get("tsunami") or other.get("tsunami"))
        combined["felt"] = main.get("felt") or other.get("felt")
        combined["alert"] = main.get("alert") or other.get("alert")
        if _nan(combined["mag"]) and not _nan(other["mag"]):
            combined["mag"], combined["magtype"] = other["mag"], other["magtype"]
        merged[merged.index(same)] = combined
    for q in merged:
        q["mag"] = None if _nan(q["mag"]) else round(q["mag"], 1)
        q["depth_km"] = None if _nan(q["depth_km"]) else q["depth_km"]
    merged.sort(key=lambda q: -q["time"])
    return merged


def fetch_quakes(lat: Optional[float], lon: Optional[float], radius_km: Optional[float],
                 min_mag: float, since: float) -> tuple[Optional[list], list, str]:
    """(quakes newest first, sources that answered, problem). None only when
    neither service answered."""
    jobs = {"EMSC": _q_emsc, "USGS": _q_usgs}
    results, problems = {}, []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {name: pool.submit(fn, since, min_mag, lat, lon, radius_km)
                   for name, fn in jobs.items()}
        for name, fut in futures.items():
            try:
                results[name] = fut.result()
            except Exception as exc:
                problems.append(f"{name}: {type(exc).__name__}")
    if not results:
        return None, [], "; ".join(problems)
    quakes = merge_quakes(*results.values())
    if lat is not None and radius_km:
        quakes = [q for q in quakes if distance_km(lat, lon, q["lat"], q["lon"]) <= radius_km * 1.01]
    quakes = [q for q in quakes if q["mag"] is None or q["mag"] >= min_mag - 1e-6]
    return quakes, sorted(results), "; ".join(problems)


_quake_memo = _Memo(ttl=120)


def quakes_for_view(lat: float, lon: float, span: float) -> Optional[list]:
    """What a view shows: the world's strong quakes of the last day from space,
    every quake of the last week close up."""
    if span >= MAP_SPAN:
        key = ("world",)

        def fetch():
            return fetch_quakes(None, None, None, _WORLD_MIN_MAG, time.time() - 86400)[0]
    else:
        radius = min(5000.0, span * 1.9)
        min_mag = 2.5 if span > 30 else 1.5
        key = (round(lat, 1), round(lon, 1), round(radius, -1), min_mag)

        def fetch():
            return fetch_quakes(lat, lon, radius, min_mag, time.time() - 7 * 86400)[0]
    return _quake_memo.get(key, fetch)


def _quake_facts(q: dict, units: str, home: Optional[dict], of: str = "user") -> dict:
    """`home` is the point distances are measured from; `of` says whose it is
    ("user", or "place" when the user asked about somewhere else)."""
    facts = {
        "magnitude": f"{q['mag']:.1f} {q['magtype']}".strip() if q["mag"] is not None else None,
        "where": q["region"] or None,
        "depth": fmt_km(q["depth_km"], units) if q["depth_km"] is not None else None,
        "time": _iso(q["time"]),
        "ago": fmt_ago(time.time() - q["time"]),
        "tsunami_flag": True if q.get("tsunami") else None,
        "felt_reports": q.get("felt"),
        "impact_alert": q.get("alert"),
    }
    if home:
        d = distance_km(home["lat"], home["lon"], q["lat"], q["lon"])
        b = bearing_deg(home["lat"], home["lon"], q["lat"], q["lon"])
        facts[f"distance_from_{of}"] = fmt_km(d, units)
        facts[f"direction_from_{of}"] = f"{compass(b)} ({b:.0f} deg)"
    return facts


def _quake_line(q: dict, units: str, home: Optional[dict]) -> str:
    mag = f"M{q['mag']:.1f}" if q["mag"] is not None else "M?"
    where = ""
    if home:
        d = distance_km(home["lat"], home["lon"], q["lat"], q["lon"])
        where = f"{fmt_km(d, units)} {arrow(bearing_deg(home['lat'], home['lon'], q['lat'], q['lon']))} · "
    depth = f"↓ {fmt_km(q['depth_km'], units)} · " if q["depth_km"] is not None else ""
    flag = "  ≈" if q.get("tsunami") else ""
    return (f"◉ {mag}  {q['region']}{flag}\n"
            f"    {where}{depth}{_hm(q['time'])} ({fmt_ago(time.time() - q['time'])})")


def _quake_layer(quakes: list) -> list:
    now = time.time()
    return [{"lat": q["lat"], "lon": q["lon"], "mag": q["mag"] or 0.0,
             "age_s": now - q["time"], "ref": q} for q in quakes]


# ─────────────────────────────────────────────────────────────────────────────
# Disasters — GDACS (official alert levels) + NASA EONET
# ─────────────────────────────────────────────────────────────────────────────

_GDACS_KIND = {"TC": "cyclone", "VO": "volcano", "WF": "wildfire", "FL": "flood", "DR": "drought"}
_EONET_KIND = {"wildfires": "wildfire", "volcanoes": "volcano", "severeStorms": "storm",
               "seaLakeIce": "ice", "floods": "flood"}
_LEVEL_RANK = {"red": 3, "orange": 2, "green": 1, "": 0}


def _fetch_events() -> list:
    """Current disasters, one entry each. GDACS's reading is kept where both
    list the same thing, because it carries the official alert level."""
    answers, problems = {}, []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {"GDACS": pool.submit(_get_json, _GDACS, 25), "EONET": pool.submit(_get_json, _EONET, 25)}
        for name, fut in futures.items():
            try:
                answers[name] = fut.result()
            except Exception as exc:
                problems.append(f"{name}: {type(exc).__name__}")
    if not answers:
        raise RuntimeError("; ".join(problems))
    events: list = []
    for f in (answers.get("GDACS") or {}).get("features") or []:
        p = f.get("properties") or {}
        kind = _GDACS_KIND.get(str(p.get("eventtype") or ""))
        coords = (f.get("geometry") or {}).get("coordinates") or []
        if not kind or len(coords) < 2 or p.get("iscurrent") in (False, "false"):
            continue
        url = p.get("url")
        events.append({"id": f"gdacs:{p.get('eventtype')}{p.get('eventid')}", "kind": kind,
                       "level": str(p.get("alertlevel") or "").lower(),
                       "lat": float(coords[1]), "lon": float(coords[0]),
                       "name": str(p.get("name") or p.get("eventname") or "").strip(),
                       "country": str(p.get("country") or "").strip(),
                       "since": _parse_utc(p.get("fromdate")),
                       "severity": re.sub(r"^Magnitude 0\s*$", "",
                                          str((p.get("severitydata") or {}).get("severitytext") or "").strip()),
                       "source": "GDACS",
                       "url": (url.get("report") if isinstance(url, dict) else str(url or ""))})
    for ev in (answers.get("EONET") or {}).get("events") or []:
        cats = [c.get("id") for c in ev.get("categories") or []]
        kind = next((_EONET_KIND[c] for c in cats if c in _EONET_KIND), None)
        geo = (ev.get("geometry") or [])[-1:]
        if not kind or not geo:
            continue
        g = geo[0]
        coords = g.get("coordinates") or []
        if g.get("type") == "Polygon" and coords:
            ring = coords[0]
            coords = [sum(c[0] for c in ring) / len(ring), sum(c[1] for c in ring) / len(ring)]
        if len(coords) < 2:
            continue
        lat, lon = float(coords[1]), float(coords[0])
        if any(e["kind"] == kind and distance_km(e["lat"], e["lon"], lat, lon) < 60 for e in events):
            continue
        mag = g.get("magnitudeValue")
        events.append({"id": f"eonet:{ev.get('id')}", "kind": kind, "level": "", "lat": lat, "lon": lon,
                       "name": str(ev.get("title") or "").strip(), "country": "",
                       "since": _parse_utc(g.get("date")),
                       "severity": (f"{mag:g} {g.get('magnitudeUnit') or ''}".strip() if mag else ""),
                       "source": "NASA EONET",
                       "url": str(((ev.get("sources") or [{}])[0]).get("url") or "")})
    for e in events:
        e["rank"] = _LEVEL_RANK.get(e["level"], 0) * 10 + (3 if e["kind"] in ("cyclone", "volcano") else 1)
    return events


_events_feed = _Feed("disasters", _fetch_events, ttl=15 * 60)


def _event_label(e: dict) -> str:
    name = e["name"]
    for prefix in ("Tropical Cyclone ", "Eruption ", "Flood in ", "Drought in ", "Forest fires in "):
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name.strip()[:28]


def _event_layer(events: list, span: float) -> list:
    """From space every small wildfire would be noise — only those GDACS rates
    orange or red are drawn there; close up, all of them."""
    out = []
    for e in events:
        if span >= MAP_SPAN and e["kind"] == "wildfire" and _LEVEL_RANK.get(e["level"], 0) < 2:
            continue
        out.append(dict(e, label=_event_label(e) if e["rank"] >= 13 or span < MAP_SPAN else ""))
    return out


def _event_facts(e: dict, units: str, ref: Optional[dict], of: str = "user") -> dict:
    facts = {"type": e["kind"], "name": e["name"] or None, "alert_level": e["level"] or None,
             "country": e["country"] or None, "severity": e["severity"] or None,
             "since": _iso(e["since"]) if e.get("since") else None, "source": e["source"]}
    if ref:
        d = distance_km(ref["lat"], ref["lon"], e["lat"], e["lon"])
        b = bearing_deg(ref["lat"], ref["lon"], e["lat"], e["lon"])
        facts[f"distance_from_{of}"] = fmt_km(d, units)
        facts[f"direction_from_{of}"] = f"{compass(b)} ({b:.0f} deg)"
    return facts


_EVENT_GLYPH = {"cyclone": "◎", "storm": "◎", "volcano": "▲", "wildfire": "◆", "flood": "≈",
                "drought": "■", "ice": "◇"}


def _event_line(e: dict, units: str, ref: Optional[dict]) -> str:
    level = {"red": "  ●●●", "orange": "  ●●", "green": "  ●"}.get(e["level"], "")
    parts = []
    if ref:
        d = distance_km(ref["lat"], ref["lon"], e["lat"], e["lon"])
        parts.append(f"{fmt_km(d, units)} {arrow(bearing_deg(ref['lat'], ref['lon'], e['lat'], e['lon']))}")
    parts += [x for x in (e["country"], e["severity"]) if x]
    return f"{_EVENT_GLYPH.get(e['kind'], '■')} {e['name']}{level}\n    " + " · ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Aircraft, weather, aurora
# ─────────────────────────────────────────────────────────────────────────────

_air_memo = _Memo(ttl=15, size=16)


def fetch_aircraft(lat: float, lon: float, radius_km: float) -> Optional[list]:
    """Live ADS-B positions around a point (community network, no key)."""
    nm = max(1, min(250, math.ceil(radius_km / 1.852)))

    def fetch():
        try:
            data = _get_json(_ADSB.format(lat=lat, lon=lon, nm=nm), timeout=8)
        except Exception as exc:
            _print(f"aircraft not fetched: {type(exc).__name__}")
            return None
        out = []
        for a in (data or {}).get("ac") or []:
            try:
                alat, alon = float(a["lat"]), float(a["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            if _num(a.get("seen_pos", a.get("seen")), 0) > 60:
                continue
            alt = a.get("alt_baro")
            out.append({"lat": alat, "lon": alon, "track": _num(a.get("track"), 0.0),
                        "speed_ms": _num(a.get("gs"), 0.0) * 0.514444,
                        "t0": time.time() - _num(a.get("seen_pos", a.get("seen")), 0.0),
                        "on_ground": alt == "ground",
                        "alt_m": None if alt in (None, "ground") else _num(alt, 0) * 0.3048,
                        "military": bool(int(_num(a.get("dbFlags"), 0)) & 1),
                        "label": str(a.get("flight") or "").strip() or str(a.get("r") or "").strip(),
                        "type": str(a.get("t") or "").strip()})
        return out
    return _air_memo.get((round(lat, 2), round(lon, 2), nm), fetch)


_wx_memo = _Memo(ttl=600, size=64)
_WMO = [((0,), "☀", "clear"), ((1,), "☀", "mainly clear"), ((2,), "◐", "partly cloudy"),
        ((3,), "☁", "overcast"), ((45, 48), "≡", "fog"), ((51, 53, 55, 56, 57), "☂", "drizzle"),
        ((61, 63, 65, 66, 67, 80, 81, 82), "☂", "rain"), ((71, 73, 75, 77, 85, 86), "❄", "snow"),
        ((95, 96, 99), "⚡", "thunderstorm")]


def weather(lat: float, lon: float) -> Optional[dict]:
    """Now, today's sunrise and sunset, and the place's own clock (Open-Meteo)."""
    def fetch():
        params = {"latitude": f"{lat:.3f}", "longitude": f"{lon:.3f}",
                  "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                             "wind_speed_10m,wind_direction_10m,weather_code,is_day",
                  "daily": "sunrise,sunset", "timezone": "auto", "forecast_days": 1}
        try:
            d = _get_json(_METEO + "?" + urllib.parse.urlencode(params), timeout=8)
        except Exception as exc:
            _print(f"weather not fetched: {type(exc).__name__}")
            return None
        cur = (d or {}).get("current") or {}
        if "temperature_2m" not in cur:
            return None
        code = int(_num(cur.get("weather_code"), -1))
        symbol, words = next(((s, w) for codes, s, w in _WMO if code in codes), ("·", "unknown"))
        daily = d.get("daily") or {}
        return {"temp": _num(cur.get("temperature_2m"), 0), "feels": _num(cur.get("apparent_temperature"), 0),
                "humidity": _num(cur.get("relative_humidity_2m"), 0),
                "wind_kmh": _num(cur.get("wind_speed_10m"), 0),
                "wind_from": _num(cur.get("wind_direction_10m"), 0),
                "symbol": symbol, "sky": words, "is_day": bool(cur.get("is_day")),
                "offset_s": _num(d.get("utc_offset_seconds"), 0), "tz": d.get("timezone") or "",
                "sunrise": ((daily.get("sunrise") or [""])[0] or "")[-5:],
                "sunset": ((daily.get("sunset") or [""])[0] or "")[-5:]}
    return _wx_memo.get((round(lat, 2), round(lon, 2)), fetch)


def _place_time(offset_s: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_s)).strftime("%H:%M")


def _weather_card(label: str, wx: dict, units: str) -> list:
    return [("big", "#eaf6ff", label[:34]),
            ("mid", None, f"{_place_time(wx['offset_s'])}   {_offset_text(wx['offset_s'])}"),
            ("mid", "#eaf6ff", f"{wx['symbol']}  {fmt_temp(wx['temp'], units)}   "
                               f"≈ {fmt_temp(wx['feels'], units)}"),
            ("small", None, f"{wx['humidity']:.0f} %   ·   {fmt_speed(wx['wind_kmh'], units)} "
                            f"{arrow(wx['wind_from'] + 180)}"),
            ("small", None, f"☀ ↑ {wx['sunrise']}      ☀ ↓ {wx['sunset']}")]


def _weather_facts(wx: dict, units: str) -> dict:
    return {"local_time": f"{_place_time(wx['offset_s'])} ({_offset_text(wx['offset_s'])}, {wx['tz']})",
            "weather": wx["sky"], "temperature": fmt_temp(wx["temp"], units),
            "feels_like": fmt_temp(wx["feels"], units), "humidity": f"{wx['humidity']:.0f} %",
            "wind": f"{fmt_speed(wx['wind_kmh'], units)} from {compass(wx['wind_from'])}",
            "sunrise_local": wx["sunrise"], "sunset_local": wx["sunset"]}


def _fetch_aurora() -> dict:
    data = _get_json(_OVATION, timeout=20)
    grid = np.zeros((181, 360), np.float32)
    for lon, lat, value in data.get("coordinates") or []:
        grid[int(lat) + 90, int(lon) % 360] = float(value)
    return {"grid": grid, "time": data.get("Forecast Time") or data.get("Observation Time") or ""}


def _fetch_kp() -> dict:
    now = _get_json(_KP, timeout=10)
    try:
        forecast = _get_json(_KP_FORECAST, timeout=10)
    except Exception:
        forecast = None

    def row_value(row):
        if isinstance(row, dict):
            return _num(row.get("Kp", row.get("kp")), float("nan"))
        return _num(row[1], float("nan"))

    def row_time(row):
        return _parse_utc(row.get("time_tag") if isinstance(row, dict) else row[0])
    upcoming = [row_value(r) for r in (forecast or []) if isinstance(r, (dict, list))
                if (lambda t: t is not None and time.time() <= t <= time.time() + 36 * 3600)(row_time(r))]
    return {"kp": row_value(now[-1]) if now else None,
            "max_next_36h": max((v for v in upcoming if not math.isnan(v)), default=None)}


_aurora_feed = _Feed("aurora", _fetch_aurora, ttl=10 * 60)
_kp_feed = _Feed("kp", _fetch_kp, ttl=15 * 60)


def _aurora_layer() -> Optional[np.ndarray]:
    got = _aurora_feed.get(0.0)
    if not got:
        return None
    # probability (%) → glow: faint from 5 %, full from 50 %
    return np.clip((got["grid"] - 4.0) / 46.0, 0.0, 1.0)


# ─────────────────────────────────────────────────────────────────────────────
# The ISS — orbit, Sun, passes
# ─────────────────────────────────────────────────────────────────────────────

_A_KM = 6378.137
_F = 1 / 298.257223563
_E2 = _F * (2 - _F)
_tle_lock = threading.Lock()
_tle_mem: dict = {}
_sat_cache: dict = {}


def _tle_epoch(line1: str) -> Optional[float]:
    try:
        yy = int(line1[18:20])
        day = float(line1[20:32])
    except ValueError:
        return None
    year = 2000 + yy if yy < 57 else 1900 + yy
    return calendar.timegm((year, 1, 1, 0, 0, 0, 0, 0, 0)) + (day - 1) * 86400


def _tle() -> Optional[dict]:
    """{"l1", "l2", "epoch", "source"}. Twelve hours in the cache; an old one is
    still better than none when both sources are down."""
    mem = _tle_mem.get("tle")
    if mem and time.time() - mem.get("fetched", 0) < _TLE_TTL:
        return mem
    with _tle_lock:
        with _cache_lock:
            cached = _load_cache().get("tle")
        if cached and time.time() - cached.get("fetched", 0) < _TLE_TTL:
            _tle_mem["tle"] = cached
            return cached
        for name, url in _TLE_SOURCES:
            try:
                raw = _get(url, timeout=15).decode("utf-8", "replace")
                if raw.lstrip().startswith("{"):
                    data = json.loads(raw)
                    l1, l2 = data.get("line1", ""), data.get("line2", "")
                else:
                    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
                    l1 = next((ln for ln in lines if ln.startswith(f"1 {_ISS_ID}")), "")
                    l2 = next((ln for ln in lines if ln.startswith(f"2 {_ISS_ID}")), "")
                if not (l1.startswith(f"1 {_ISS_ID}") and l2.startswith(f"2 {_ISS_ID}")):
                    raise ValueError("not an ISS element set")
            except Exception as exc:
                _print(f"orbit elements not fetched from {name}: {type(exc).__name__}")
                continue
            tle = {"l1": l1, "l2": l2, "epoch": _tle_epoch(l1), "source": name,
                   "fetched": time.time()}
            with _cache_lock:
                cache = _load_cache()
                cache["tle"] = tle
                _save_cache(cache)
            _tle_mem["tle"] = tle
            return tle
        return cached


def _satrec(tle: dict):
    from sgp4.api import Satrec
    key = tle["l1"]
    if key not in _sat_cache:
        _sat_cache.clear()
        _sat_cache[key] = Satrec.twoline2rv(tle["l1"], tle["l2"])
    return _sat_cache[key]


def _jd(ts: np.ndarray) -> np.ndarray:
    return ts / 86400.0 + 2440587.5


def _gmst(jd: np.ndarray) -> np.ndarray:
    t = (jd - 2451545.0) / 36525.0
    g = 280.46061837 + 360.98564736629 * (jd - 2451545.0) + t * t * (0.000387933 - t / 38710000.0)
    return np.radians(np.mod(g, 360.0))


def _sun_eci(jd: np.ndarray) -> np.ndarray:
    """Unit vector to the Sun, low-precision almanac formula (~0.01°)."""
    n = jd - 2451545.0
    L = np.radians(np.mod(280.460 + 0.9856474 * n, 360.0))
    g = np.radians(np.mod(357.528 + 0.9856003 * n, 360.0))
    lam = L + np.radians(1.915) * np.sin(g) + np.radians(0.020) * np.sin(2 * g)
    eps = np.radians(23.439 - 0.0000004 * n)
    return np.stack([np.cos(lam), np.cos(eps) * np.sin(lam), np.sin(eps) * np.sin(lam)], axis=-1)


def _to_ecef(v: np.ndarray, gmst: np.ndarray) -> np.ndarray:
    c, s = np.cos(gmst), np.sin(gmst)
    return np.stack([v[..., 0] * c + v[..., 1] * s, -v[..., 0] * s + v[..., 1] * c, v[..., 2]], axis=-1)


def _geodetic(r: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """ECEF km → (lat°, lon°, altitude km), WGS-84."""
    x, y, z = r[..., 0], r[..., 1], r[..., 2]
    lon = np.arctan2(y, x)
    p = np.hypot(x, y)
    lat = np.arctan2(z, p * (1 - _E2))
    for _ in range(3):
        n = _A_KM / np.sqrt(1 - _E2 * np.sin(lat) ** 2)
        lat = np.arctan2(z + _E2 * n * np.sin(lat), p)
    n = _A_KM / np.sqrt(1 - _E2 * np.sin(lat) ** 2)
    alt = p / np.maximum(np.cos(lat), 1e-9) - n
    return np.degrees(lat), np.degrees(lon), alt


def _observer(lat: float, lon: float) -> np.ndarray:
    p, l = math.radians(lat), math.radians(lon)
    n = _A_KM / math.sqrt(1 - _E2 * math.sin(p) ** 2)
    return np.array([n * math.cos(p) * math.cos(l), n * math.cos(p) * math.sin(l),
                     n * (1 - _E2) * math.sin(p)])


def _enu(d: np.ndarray, lat: float, lon: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    p, l = math.radians(lat), math.radians(lon)
    dx, dy, dz = d[..., 0], d[..., 1], d[..., 2]
    e = -math.sin(l) * dx + math.cos(l) * dy
    n = -math.sin(p) * math.cos(l) * dx - math.sin(p) * math.sin(l) * dy + math.cos(p) * dz
    u = math.cos(p) * math.cos(l) * dx + math.cos(p) * math.sin(l) * dy + math.sin(p) * dz
    return e, n, u


def _propagate(ts: np.ndarray) -> Optional[dict]:
    """Positions for many moments: TEME and ECEF (km), the Sun, sunlit flags."""
    tle = _tle()
    if not tle or not _sgp4_ok():
        return None
    sat = _satrec(tle)
    ts = np.asarray(ts, dtype=np.float64)
    jd = _jd(ts)
    jd_i = np.floor(jd - 0.5) + 0.5
    err, r, v = sat.sgp4_array(jd_i, jd - jd_i)
    gm = _gmst(jd)
    sun = _sun_eci(jd)
    proj = np.sum(r * sun, axis=-1)
    perp = np.linalg.norm(r - proj[:, None] * sun, axis=-1)
    return {"ts": ts, "ok": err == 0, "r": r, "v": v, "ecef": _to_ecef(r, gm),
            "sun_ecef": _to_ecef(sun, gm), "lit": (proj > 0) | (perp > _A_KM), "tle": tle}


def subsolar(ts: float) -> tuple[float, float]:
    jd = _jd(np.array([ts]))
    s = _to_ecef(_sun_eci(jd), _gmst(jd))[0]
    return math.degrees(math.asin(s[2])), math.degrees(math.atan2(s[1], s[0]))


def sun_elevation(lat: float, lon: float, ts: float) -> float:
    jd = _jd(np.array([ts]))
    s = _to_ecef(_sun_eci(jd), _gmst(jd))
    return float(np.degrees(np.arcsin(np.clip(_enu(s, lat, lon)[2], -1, 1)))[0])


def _slerp_track(points: list, per_segment: int = 12) -> list:
    """Coarse subpoints → a smooth curve along great circles between them."""
    out = []
    for a, b in zip(points, points[1:]):
        out.extend(view.great_circle(a, b, per_segment)[:-1])
    if points:
        out.append(points[-1])
    return out


def iss_state(ts: Optional[float] = None, tracks: bool = False) -> Optional[dict]:
    """Where the ISS is. From the orbit model when sgp4 is installed, otherwise
    from wheretheiss.at — the answer has the same shape either way."""
    ts = time.time() if ts is None else ts
    prop = _propagate(np.array([ts]))
    if prop is not None and prop["ok"][0]:
        lat, lon, alt = (float(a[0]) for a in _geodetic(prop["ecef"]))
        speed = float(np.linalg.norm(prop["v"][0])) * 3600
        state = {"lat": lat, "lon": lon, "alt_km": alt, "speed_kmh": speed,
                 "sunlit": bool(prop["lit"][0]), "source": "orbit model",
                 "tle_age_h": (ts - (prop["tle"].get("epoch") or ts)) / 3600}
        if tracks:
            for key, arr in (("track_past", np.arange(ts - 45 * 60, ts + 1, 60.0)),
                             ("track_next", np.arange(ts, ts + 95 * 60 + 1, 60.0))):
                pr = _propagate(arr)
                la, lo, _ = _geodetic(pr["ecef"])
                state[key] = [(float(a), float(b)) for a, b, ok in zip(la, lo, pr["ok"]) if ok]
    else:
        try:
            data = _get_json(_WTIA + "?units=kilometers", timeout=10)
            state = {"lat": float(data["latitude"]), "lon": float(data["longitude"]),
                     "alt_km": float(data["altitude"]), "speed_kmh": float(data["velocity"]),
                     "sunlit": data.get("visibility") == "daylight", "source": "wheretheiss.at"}
        except Exception as exc:
            _print(f"ISS position not fetched: {type(exc).__name__}")
            return None
        if tracks:
            try:
                past = [ts - 45 * 60 + i * 5 * 60 for i in range(10)]
                nxt = [ts + i * 95 * 60 / 9 for i in range(10)]
                for key, stamps in (("track_past", past), ("track_next", nxt)):
                    rows = _get_json(f"{_WTIA}/positions?timestamps="
                                     + ",".join(str(int(s)) for s in stamps), timeout=10)
                    pts = [(float(r["latitude"]), float(r["longitude"])) for r in rows or []]
                    state[key] = _slerp_track(pts)
            except Exception as exc:
                _print(f"ISS track not fetched: {type(exc).__name__}")
    h = max(200.0, state["alt_km"])
    state["footprint_m"] = _EARTH_KM * math.acos(_EARTH_KM / (_EARTH_KM + h)) * 1000
    return state


def _iss_card(state: dict, units: str) -> list:
    return [("big", "#eaf6ff", "ISS"),
            ("mid", None, f"{fmt_km(state['alt_km'], units)} ↑"),
            ("small", None, fmt_speed(state["speed_kmh"], units)),
            ("small", None, f"{state['lat']:.2f}, {state['lon']:.2f}"),
            ("small", None, "☀" if state.get("sunlit") else "◐")]


def _magnitude(rng_km: float, phase: float) -> float:
    """Approximate visual magnitude: the ISS's standard magnitude (−1.8 at
    1000 km, half lit) scaled by distance and by how much of its lit side faces
    the observer (a diffuse sphere)."""
    f = (math.sin(phase) + (math.pi - phase) * math.cos(phase)) / math.pi
    return -1.8 + 5 * math.log10(max(rng_km, 1.0) / 1000.0) - 2.5 * math.log10(max(f, 1e-4) * math.pi)


def iss_passes(lat: float, lon: float, days: float = 3.0, min_el: float = 10.0,
               visible_only: bool = True, limit: int = 8, start: Optional[float] = None) -> list:
    """Passes over (lat, lon). A pass is VISIBLE when, at the same moment, the
    ISS is above `min_el`, lit by the Sun, and the sky at the observer is dark
    (Sun more than 6° below the horizon). Times are found to the second."""
    start = time.time() if start is None else start
    obs = _observer(lat, lon)
    coarse = start + np.arange(0.0, days * 86400.0, 20.0)
    pr = _propagate(coarse)
    if pr is None:
        return []
    e0, n0, u0 = _enu(pr["ecef"] - obs, lat, lon)
    above = (np.degrees(np.arctan2(u0, np.hypot(e0, n0))) > 0) & pr["ok"]
    passes: list = []
    i, n = 0, len(coarse)
    while i < n and len(passes) < limit:
        if not above[i]:
            i += 1
            continue
        j = i
        while j < n and above[j]:
            j += 1
        fine_ts = np.arange(coarse[max(0, i - 1)], coarse[min(n - 1, j)] + 1.0, 1.0)
        i = j
        fp = _propagate(fine_ts)
        d = fp["ecef"] - obs
        e_, n_, u_ = _enu(d, lat, lon)
        fel = np.degrees(np.arctan2(u_, np.hypot(e_, n_)))
        faz = np.mod(np.degrees(np.arctan2(e_, n_)), 360.0)
        rng = np.linalg.norm(d, axis=-1)
        sun_el = np.degrees(np.arcsin(np.clip(_enu(fp["sun_ecef"], lat, lon)[2], -1, 1)))
        up = (fel > 0) & fp["ok"]
        if not up.any():
            continue
        peak = int(np.argmax(np.where(up, fel, -90)))
        if fel[peak] < min_el:
            continue
        vis = up & fp["lit"] & (sun_el < -6.0) & (fel >= min_el)
        if visible_only and not vis.any():
            continue
        window = vis if vis.any() else (up & (fel >= min_el))
        idx = np.flatnonzero(window)
        a, b = int(idx[0]), int(idx[-1])
        top = a + int(np.argmax(fel[a:b + 1]))
        to_obs = -d[top] / rng[top]
        phase = math.acos(max(-1.0, min(1.0, float(fp["sun_ecef"][top] @ to_obs))))
        track = [(float(faz[k]), float(fel[k]), bool(vis[k]))
                 for k in range(0, len(fine_ts), 5) if up[k]]
        passes.append({
            "visible": bool(vis.any()),
            "rise": float(fine_ts[np.flatnonzero(up)[0]]), "set": float(fine_ts[np.flatnonzero(up)[-1]]),
            "start": float(fine_ts[a]), "start_az": float(faz[a]), "start_el": float(fel[a]),
            "peak": float(fine_ts[top]), "peak_az": float(faz[top]), "peak_el": float(fel[top]),
            "end": float(fine_ts[b]), "end_az": float(faz[b]), "end_el": float(fel[b]),
            "seconds": float(fine_ts[b] - fine_ts[a]),
            "magnitude": round(_magnitude(float(rng[top]), phase), 1) if vis.any() else None,
            "track": track,
        })
    return passes


def _pass_facts(p: dict) -> dict:
    return {
        "date": _local(p["start"]).strftime("%Y-%m-%d"),
        "appears": f"{_hm(p['start'])} low in the {compass(p['start_az'])} "
                   f"({p['start_az']:.0f} deg, {p['start_el']:.0f} deg up)",
        "highest": f"{_hm(p['peak'])} in the {compass(p['peak_az'])}, {p['peak_el']:.0f} deg up",
        "disappears": f"{_hm(p['end'])} in the {compass(p['end_az'])} "
                      f"({p['end_az']:.0f} deg, {p['end_el']:.0f} deg up)",
        "visible_for": f"{max(1, round(p['seconds'] / 60))} min",
        "brightness_magnitude_approx": p["magnitude"],
        "naked_eye_visible": p["visible"],
        "starts_in": fmt_ago(p["start"] - time.time()) if p["start"] > time.time() else "now",
    }


def _pass_line(p: dict) -> str:
    star = f"  ★ {p['magnitude']:+.1f}" if p["magnitude"] is not None else "  ○"
    return (f"✦ {_local(p['start']):%Y-%m-%d}   {_hm(p['start'])} → {_hm(p['end'])}{star}\n"
            f"    {arrow(p['start_az'])} {p['start_az']:.0f}°  ·  ▲ {p['peak_el']:.0f}° "
            f"{arrow(p['peak_az'])}  ·  {arrow(p['end_az'])} {p['end_az']:.0f}°")


def _draw_sky(p: dict) -> Optional[str]:
    marks = [(p["start_az"], p["start_el"], _hm(p["start"])),
             (p["peak_az"], p["peak_el"], f"{_hm(p['peak'])} · {p['peak_el']:.0f}°"),
             (p["end_az"], p["end_el"], _hm(p["end"]))]
    brightness = f"★ {p['magnitude']:+.1f}".replace("-", "−") if p["magnitude"] is not None else ""
    return view.render_sky(_out("sky"), track=p["track"], marks=marks, accent=_accent(),
                           stamp_date=f"{_local(p['start']):%Y-%m-%d}", brightness=brightness)


def _country_below(lat: float, lon: float) -> Optional[str]:
    try:
        data = _nominatim("reverse", {"lat": f"{lat:.3f}", "lon": f"{lon:.3f}",
                                      "format": "jsonv2", "zoom": 3})
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("error"):
        return None                                        # open water
    return ((data.get("address") or {}).get("country") or data.get("name") or None)


# ─────────────────────────────────────────────────────────────────────────────
# The view — one state every picture comes from, and that voice moves
# ─────────────────────────────────────────────────────────────────────────────

_view_lock = threading.Lock()
_view_gen = [0]
_state: dict = {"now": None, "history": deque(maxlen=15)}


def _enabled_layers() -> set:
    on = {"quakes", "iss", "weather"}
    if _flag(_setting("show_events", True), True):
        on.add("events")
    if _flag(_setting("show_aircraft", True), True):
        on.add("aircraft")
    if _flag(_setting("show_aurora", True), True):
        on.add("aurora")
    return on


def _new_state(center: tuple, span: float, **extra) -> dict:
    """A view: where it looks, how far away it is, and what it shows."""
    current = _state["now"] or {}
    layers = extra.pop("layers", None)
    return {"center": (float(center[0]), float(center[1])),
            "span": max(MIN_SPAN, min(MAX_SPAN, float(span))),
            "style": extra.pop("style", None) or current.get("style") or _default_style(),
            "layers": set(layers if layers is not None else current.get("layers") or _enabled_layers()),
            "place": extra.pop("place", None), "pins": extra.pop("pins", []),
            "lines": extra.pop("lines", []), "focus_iss": extra.pop("focus_iss", False),
            "card_place": extra.pop("card_place", None), **extra}


def _out(kind: str) -> Path:
    try:
        files = sorted(OUT_DIR.glob("*.png"), key=lambda f: f.stat().st_mtime, reverse=True)
        for old in files[20:]:            # ~1 MB each
            old.unlink(missing_ok=True)
    except Exception:
        pass
    return OUT_DIR / f"{kind}_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 1000:03d}.png"


def _show(player, path: Optional[str], title: str, unasked: bool = False) -> str:
    """Put a picture on the HUD. 'shown', 'saved' (no HUD to show it on),
    'kept_off' (an unasked picture never covers a video) or 'none'."""
    if not path:
        return "none"
    if not player or not hasattr(player, "show_image"):
        return "saved"
    try:
        if unasked and callable(getattr(player, "video_is_playing", None)) and player.video_is_playing():
            return "kept_off"
        with _view_lock:
            _view_gen[0] += 1                 # any live view of ours stops here
        player.show_image(path, title)
        return "shown"
    except Exception as exc:
        _print(f"picture not shown: {exc}")
        return "saved"


def _panel(player, title: str, body: str) -> None:
    if player:
        try:
            player.show_content(title, body)
        except Exception:
            pass


def _render(st: dict, preset: Optional[dict] = None, focus_ref: Any = None,
            quick: bool = False, draw: bool = True) -> tuple[Optional[str], dict]:
    """Draw a view state. Returns (picture path, facts about what it shows).

    Everything the view needs is fetched at once, in parallel; `preset` hands in
    data an action already has (its own quake list) so nothing is asked twice.
    `quick` is for the frames of a moving picture: nothing slow is asked — the
    place-name lookup allows one request a second and would set the frame rate.
    """
    preset = preset or {}
    units = _units()
    lat0, lon0 = st["center"]
    span = st["span"]
    globe = span >= MAP_SPAN
    on = st["layers"]
    home = _home()
    card_place = st.get("card_place")

    jobs: dict = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        if "quakes" in on and "quakes" not in preset:
            jobs["quakes"] = pool.submit(quakes_for_view, lat0, lon0, span)
        if "iss" in on or st.get("focus_iss"):
            jobs["iss"] = pool.submit(iss_state, None, True)
        if "events" in on and "events" not in preset:
            jobs["events"] = pool.submit(_events_feed.get, 2.5)
        if "aircraft" in on and not globe and span <= AIRCRAFT_SPAN:
            jobs["aircraft"] = pool.submit(fetch_aircraft, lat0, lon0, span * 1.35)
        if "weather" in on and (card_place or not globe) and not quick:
            spot = card_place or {"lat": lat0, "lon": lon0}
            jobs["weather"] = pool.submit(weather, spot["lat"], spot["lon"])
        if not quick:
            jobs["under"] = pool.submit(what_is_under, lat0, lon0, span)
        got = {k: f.result() for k, f in jobs.items()}

    layers = dict(_stamp(), sun=subsolar(time.time()))
    if home:
        layers["home"] = {"lat": home["lat"], "lon": home["lon"], "label": home.get("label") or ""}
    facts: dict = {"view": "globe" if globe else "map",
                   "centre": f"{lat0:.4f}, {lon0:.4f}",
                   "centre_is_over": got.get("under") or "open water or unnamed",
                   "visible_height": fmt_km(span * 2, units)}
    quakes = preset.get("quakes", got.get("quakes"))
    if quakes is not None:
        layers["quakes"] = _quake_layer(quakes)
        facts["quakes_on_view"] = len(quakes)
        if focus_ref is not None:
            layers["focus"] = next((q for q in layers["quakes"] if q["ref"] is focus_ref), None)
    events = preset.get("events", got.get("events"))
    if events is not None:
        layers["events"] = _event_layer(events, span)
        if not globe:                                   # roughly what the map's window holds
            reach = span * 2.0
            layers["events"] = [e for e in layers["events"]
                                if distance_km(lat0, lon0, e["lat"], e["lon"]) <= reach]
        facts["disasters_on_view"] = len(layers["events"])
    elif "events" in on:
        facts["disasters"] = "still loading — ask again in a few seconds"
    planes = got.get("aircraft")
    if planes is not None:
        layers["aircraft"] = planes
        facts["aircraft_in_view"] = sum(1 for a in planes if not a["on_ground"])
    iss = got.get("iss")
    if iss:
        layers["iss"] = iss
        facts["iss_over"] = f"{iss['lat']:.1f}, {iss['lon']:.1f}"
    if globe and "aurora" in on:
        layers["aurora"] = _aurora_layer()
    for key in ("pins", "lines", "radius_m"):
        if st.get(key):
            layers[key] = st[key]
    wx = got.get("weather")
    if st.get("card"):
        layers["card"] = st["card"]
        if wx:
            facts.update(_weather_facts(wx, units))
    elif st.get("focus_iss") and iss:
        layers["card"] = _iss_card(iss, units)
    elif wx:
        label = (card_place or {}).get("label") or (got.get("under") or "").split(",")[0] \
            or f"{lat0:.2f}, {lon0:.2f}"
        layers["card"] = _weather_card(label, wx, units)
        facts.update(_weather_facts(wx, units))
    elif iss and globe:
        layers["card"] = _iss_card(iss, units)

    if not draw:
        return None, facts
    if globe:
        path = view.render_globe(_out("globe"), center=(lat0, lon0), layers=layers, accent=_accent(),
                                 span_km=span)
    else:
        path, used = view.render_map(_out("map"), center=(lat0, lon0), radius_m=span * 1000,
                                     layers=layers, accent=_accent(), style=st["style"], units=units)
        facts["basemap"] = used
    return path, facts


# ── the live view ────────────────────────────────────────────────────────────

class _Provider:
    """What the live view asks for, from its own worker threads. Everything
    here goes through the same caches as the rest of the plugin."""

    def units(self) -> str:
        return _units()

    def home(self) -> Optional[dict]:
        h = _home()
        return {"lat": h["lat"], "lon": h["lon"], "label": h.get("label") or ""} if h else None

    def events(self) -> Optional[list]:
        found = _events_feed.get(0.0)
        return [dict(e, label=_event_label(e)) for e in found] if found is not None else None

    def quakes(self, lat: float, lon: float, span: float) -> Optional[list]:
        return quakes_for_view(lat, lon, span)

    def aircraft(self, lat: float, lon: float, span: float) -> Optional[list]:
        return fetch_aircraft(lat, lon, span * 1.35)

    def iss(self) -> Optional[dict]:
        return iss_state(tracks=True)

    def iss_at(self, ts: float) -> Optional[tuple]:
        if not _sgp4_ok():
            return None
        prop = _propagate(np.array([ts]))
        if prop is None or not prop["ok"][0]:
            return None
        lat, lon, _ = _geodetic(prop["ecef"])
        return float(lat[0]), float(lon[0])

    def aurora(self) -> Optional[np.ndarray]:
        return _aurora_layer()

    def sun(self, ts: float) -> tuple:
        return subsolar(ts)

    def card(self, lat: float, lon: float, span: float, place: Optional[dict]) -> list:
        spot = place or {"lat": lat, "lon": lon}
        with ThreadPoolExecutor(max_workers=2) as pool:
            fw = pool.submit(weather, spot["lat"], spot["lon"]) if span < MAP_SPAN or place else None
            fu = pool.submit(what_is_under, lat, lon, span) if not place else None
            wx = fw.result() if fw else None
            under = fu.result() if fu else None
        label = (place or {}).get("label") or (under or "").split(",")[0].strip()
        if wx:
            return _weather_card(label or f"{lat:.2f}, {lon:.2f}", wx, _units())
        return [("big", "#eaf6ff", label)] if label else []

    def labels(self, base: dict) -> Optional[dict]:
        """The live view's button labels in the user's language.

        The language is the one JARVIS remembers the user speaking, else the
        system's. English needs nothing; any other is translated once by the
        model — through core.gemini, so it also works on another provider — and
        kept, so this costs one short request per language, ever."""
        language = _user_language()
        if not language or language.lower().startswith(("en", "english")):
            return None
        key = f"ui_labels:{language.lower()}"
        with _cache_lock:
            hit = _load_cache().get(key)
        if isinstance(hit, dict) and set(hit) >= set(base):
            return hit
        try:
            from core import gemini
            prompt = ("Translate the values of this JSON object into the language named "
                      f"'{language}'. They are tooltips on the buttons of a map, so keep them "
                      "short and natural for that language. Keep every key unchanged. Return "
                      "only the JSON object: " + json.dumps(base, ensure_ascii=False))
            got = gemini.as_json(prompt, tier=gemini.FAST)
        except Exception as exc:
            _print(f"button labels not translated: {type(exc).__name__}")
            return None
        if not isinstance(got, dict):
            return None
        clean = {k: str(got.get(k) or "").strip()[:60] for k in base}
        if sum(1 for v in clean.values() if v) < len(base) * 0.8:
            return None
        clean = {k: v or base[k] for k, v in clean.items()}
        with _cache_lock:
            cache = _load_cache()
            cache[key] = clean
            _save_cache(cache)
        return clean

    def credit(self, style: str) -> str:
        spec = view.MAP_STYLES.get(style)
        if not spec:
            return ""
        parts = [view._credit(spec["base"])]
        if view._credit(spec["labels"]) not in parts:
            parts.append(view._credit(spec["labels"]))
        return " | ".join(parts)

    def satellite_texture(self):
        """The whole Earth in satellite imagery, 4096 × 2048, fetched once (13 s)."""
        from PyQt6.QtGui import QImage
        path = OUT_DIR / "earth_satellite.jpg"
        raw = None
        try:
            if path.is_file():
                raw = path.read_bytes()
        except Exception:
            raw = None
        if raw is None:
            raw = _get(_SAT_WORLD, timeout=90)
            try:
                OUT_DIR.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            except Exception:
                pass
        img = QImage.fromData(raw)
        if img.isNull():
            return None
        # the service leaves a few black rows at the poles: repeat the nearest real row
        arr = view._rgba_from_qimage(img)
        rows = arr[..., :3].mean(axis=(1, 2))
        good = np.flatnonzero(rows > 12)
        if len(good):
            arr[: good[0]] = arr[good[0]]
            arr[good[-1] + 1:] = arr[good[-1]]
        return view._qimage_from_array(arr)


def _user_language() -> str:
    """The language JARVIS has heard the user speak, else the system's."""
    try:
        from memory.memory_manager import load_memory
        entry = (load_memory().get("identity") or {}).get("language")
        value = entry.get("value") if isinstance(entry, dict) else entry
        if value and str(value).strip():
            return str(value).strip()
    except Exception:
        pass
    try:
        from PyQt6.QtCore import QLocale
        return QLocale.languageToString(QLocale.system().language())
    except Exception:
        return ""


_SAT_WORLD = ("https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export"
              "?bbox=-180,-90,180,90&bboxSR=4326&imageSR=4326&size=4096,2048&format=jpg&f=image")
_PROVIDER = _Provider()


def _live_module():
    try:
        from plugins import _planet_live as live
    except ImportError:  # loaded outside the package
        import _planet_live as live  # type: ignore
    return live


def _live_view(player, open_it: bool = True):
    """The live view, opened if asked — or None when pictures are used instead:
    the setting says so, there is no Qt window here, or the view cannot open."""
    if str(_setting("view_mode", "live")).strip().lower() != "live":
        return None
    try:
        from PyQt6.QtWidgets import QApplication
        if QApplication.instance() is None:
            return None
        live = _live_module()
    except Exception as exc:
        _print(f"live view unavailable: {exc}")
        return None
    if not open_it:
        current = live.current()
        return current if current is not None and current.isVisible() else None
    return view._on_gui_thread(lambda: live.ensure_view(player, _PROVIDER, _accent()))


def _on_live(widget, fn):
    """Run `fn(widget)` on the GUI thread and return its result."""
    return view._on_gui_thread(lambda: fn(widget))


def _title(st: dict) -> str:
    label = (st.get("place") or {}).get("label") or st.get("title") or st.get("under") or ""
    return ("⊕ " if st["span"] >= MAP_SPAN else "▦ ") + label[:40]


def _present(st: dict, player, *, remember: bool = True, preset=None, focus_ref=None,
             title: Optional[str] = None, unasked: bool = False) -> tuple[str, dict]:
    """Make `st` the current view: fly the live view there, or draw a picture;
    remember the view before it either way."""
    lv = _live_view(player, open_it=not unasked)
    if lv is not None:
        shown_state = dict(st, preset=dict(preset or {}), focus_ref=focus_ref)
        _on_live(lv, lambda w: w.show_state(shown_state))
        _, facts = _render(st, preset, focus_ref, draw=False)
        under = str(facts.get("centre_is_over") or "")
        st["under"] = "" if under.startswith("open water") else under.split(",")[0].strip()
        if remember and _state["now"] is not None:
            _state["history"].append(_state["now"])
        _state["now"] = st
        facts["controls"] = "the user can also drag, scroll-zoom, double-click and click items on the view"
        return "live", facts
    if unasked and st.get("_live_only"):
        return "none", {}
    path, facts = _render(st, preset, focus_ref)
    under = str(facts.get("centre_is_over") or "")
    st["under"] = "" if under.startswith("open water") else under.split(",")[0].strip()
    if remember and _state["now"] is not None:
        _state["history"].append(_state["now"])
    _state["now"] = st
    return _show(player, path, title or _title(st)), facts


# ── live pictures: following the ISS, spinning the globe ─────────────────────

def _live_loop(player, gen: int, until: float, mode: str) -> None:
    """Keep the picture moving. Stops the moment the picture is closed (checked
    every half second, so a video opened afterwards is never covered), when
    another picture of ours replaces it, or at `until`."""
    last = 0.0
    step = 10.0 if mode == "iss" else 1.2
    while time.time() < until:
        time.sleep(0.5)
        if _view_gen[0] != gen:
            return
        try:
            if callable(getattr(player, "video_is_playing", None)) and not player.video_is_playing():
                return
        except Exception:
            return
        if time.time() - last < step:
            continue
        last = time.time()
        st = _state["now"]
        if not st:
            return
        if mode == "iss":
            state = iss_state()
            if state:
                st = dict(st, center=(state["lat"], state["lon"]))
        else:
            lat, lon = st["center"]
            st = dict(st, center=(lat, (lon + 12.0 + 540.0) % 360.0 - 180.0), card_place=None, place=None,
                      under="", title="")
        _state["now"] = st
        path, _ = _render(st, quick=True)
        if path and _view_gen[0] == gen:
            try:
                player.show_image(path, _title(st))
            except Exception:
                return


def _start_live(player, seconds: float, mode: str) -> Optional[int]:
    if seconds <= 0:
        return None
    lv = _live_view(player, open_it=False)
    if lv is not None:
        seconds = min(seconds, 30 * 60)
        if mode == "spin":
            _on_live(lv, lambda w: w.spin(seconds))
        else:
            _on_live(lv, lambda w: w.follow_iss(seconds))
        return int(seconds)
    if not player or not hasattr(player, "show_image"):
        return None
    seconds = min(seconds, 30 * 60)
    threading.Thread(target=_live_loop, args=(player, _view_gen[0], time.time() + seconds, mode),
                     daemon=True, name="planet-live").start()
    return int(seconds)


# ─────────────────────────────────────────────────────────────────────────────
# The earthquake watch
# ─────────────────────────────────────────────────────────────────────────────

class _Watch:
    def __init__(self):
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.config: dict = {}
        self.alerted: dict[str, float] = {}
        self.spoken = deque()
        self.polls = 0
        self.failures = 0
        self.last_error = ""
        self.last_alert: Optional[dict] = None

    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive() and not self.stop.is_set())


_watch = _Watch()


def _say(player, instruction: str) -> None:
    if player and hasattr(player, "request_say"):
        try:
            player.request_say(instruction)
        except Exception:
            pass


def _watch_poll(cfg: dict) -> tuple[Optional[list], str]:
    """New-enough quakes that meet either the local or the worldwide rule."""
    since = time.time() - 2 * 3600
    local, _, problem = fetch_quakes(cfg["lat"], cfg["lon"], cfg["radius_km"], cfg["min_mag"], since)
    found = list(local or [])
    if cfg["global_min"]:
        world, _, p2 = fetch_quakes(None, None, None, cfg["global_min"], since)
        if world:
            found = merge_quakes(found, world)
        problem = "; ".join(x for x in (problem, p2) if x)
        if local is None and world is None:
            return None, problem
    elif local is None:
        return None, problem
    return found, problem


def _is_new(q: dict, alerted: dict) -> bool:
    return not any(i in alerted for i in q.get("ids", [q["id"]]))


def _watch_loop(player) -> None:
    w, cfg, units = _watch, _watch.config, _units()
    home = {"lat": cfg["lat"], "lon": cfg["lon"], "label": cfg["label"]}
    first = True
    while not w.stop.is_set():
        if cfg["until"] and time.time() >= cfg["until"]:
            _log(player, "Earthquake watch finished.")
            _say(player, "The earthquake watch the user asked for has just ended. Mention it in one "
                         "short sentence.")
            break
        started = time.time()
        quakes, problem = _watch_poll(cfg)
        w.polls += 1
        if quakes is None:
            w.failures += 1
            w.last_error = problem
            if w.failures == 5:
                _log(player, f"Earthquake watch: the services are not answering ({problem}).")
                _say(player, "Tell the user in one short sentence that the earthquake watch cannot "
                             "reach the earthquake services right now and keeps trying.")
        else:
            if w.failures >= 5:
                _log(player, "Earthquake watch: the services are answering again.")
            w.failures, w.last_error = 0, ""
            now = time.time()
            if first:
                # What was already reported when the watch started is not news.
                for q in quakes:
                    for i in q.get("ids", [q["id"]]):
                        w.alerted[i] = now
                first = False
            else:
                fresh = [q for q in quakes if _is_new(q, w.alerted) and now - q["time"] < 3600]
                for q in fresh:
                    for i in q.get("ids", [q["id"]]):
                        w.alerted[i] = now
                if fresh:
                    _announce(player, fresh, cfg, home, units)
            for key in [k for k, t in w.alerted.items() if now - t > 6 * 3600]:
                w.alerted.pop(key, None)
        w.stop.wait(max(5.0, cfg["interval"] - (time.time() - started)))
    with w.lock:
        w.stop.set()


def _announce(player, fresh: list, cfg: dict, home: dict, units: str) -> None:
    w = _watch
    fresh = sorted(fresh, key=lambda q: -(q["mag"] or 0))
    main = fresh[0]
    w.last_alert = main
    for q in fresh:
        _log(player, _quake_line(q, units, home).replace("\n    ", " — "))
    _panel(player, f"◉ M{main['mag'] or 0:.1f}", "\n\n".join(_quake_line(q, units, home) for q in fresh[:5]))
    if _flag(_setting("show_on_alert", True), True):
        d = distance_km(home["lat"], home["lon"], main["lat"], main["lon"])
        if d <= cfg["radius_km"]:
            # between the user and the epicentre, both on screen with a margin
            center, span = _midpoint((home["lat"], home["lon"]), (main["lat"], main["lon"])), max(60.0, d * 0.7)
        else:
            center, span = (main["lat"], main["lon"]), 6371.0
        st = _new_state(center, span, layers={"quakes", "weather"} if span < MAP_SPAN else {"quakes"},
                        title=f"M{main['mag'] or 0:.1f}")
        if _live_view(player, open_it=False) is not None:
            _present(st, player, preset={"quakes": fresh}, focus_ref=main, unasked=True)
            path = None
        else:
            path, _ = _render(st, preset={"quakes": fresh}, focus_ref=main)
        if path and _show(player, path, f"◉ M{main['mag'] or 0:.1f}", unasked=True) == "shown":
            if _state["now"] is not None:
                _state["history"].append(_state["now"])
            _state["now"] = st
    now = time.time()
    while w.spoken and now - w.spoken[0] > 3600:
        w.spoken.popleft()
    if len(w.spoken) >= _ALERTS_PER_HOUR:
        return
    w.spoken.append(now)
    lines = ["; ".join(f"{k}: {v}" for k, v in _clean(_quake_facts(q, units, home)).items())
             for q in fresh[:3]]
    _say(player,
         f"[Earthquake watch — the user asked to be told about new earthquakes of magnitude "
         f"{cfg['min_mag']:g} or more within {fmt_km(cfg['radius_km'], units)} of them"
         + (f", and of {cfg['global_min']:g} or more anywhere" if cfg["global_min"] else "")
         + ".] " + " | ".join(lines)
         + " — Tell the user in one or two short, calm sentences: the magnitude, how far and in "
           "which direction from them, and how long ago. This is a report published after the "
           "event, not a warning of one: do not predict aftershocks or further quakes. Mention "
           "a tsunami flag only if one is present.")


# ─────────────────────────────────────────────────────────────────────────────
# ISS reminders
# ─────────────────────────────────────────────────────────────────────────────

class _Reminders:
    def __init__(self):
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.next: Optional[dict] = None        # {"at", "pass", "lead", "repeat", "place"}


_rem = _Reminders()


def _next_visible(place: dict, after: float) -> Optional[dict]:
    found = iss_passes(place["lat"], place["lon"], days=10,
                       min_el=_num(_setting("pass_min_el", 10), 10), limit=1, start=after)
    return found[0] if found else None


def _reminder_loop(player) -> None:
    r = _rem
    while True:
        with r.lock:
            item = r.next
        if not item:
            return
        wait = item["at"] - time.time()
        if wait > 0:
            r.wake.wait(min(wait, 600))
            r.wake.clear()
            continue                              # re-read: it may have been cancelled or moved
        p = item["pass"]
        with r.lock:
            if r.next is not item:
                continue
        if p["end"] > time.time():
            facts = _clean(_pass_facts(p))
            _say(player, "[ISS reminder — the user asked to be told before the space station passes "
                         "overhead.] " + "; ".join(f"{k}: {v}" for k, v in facts.items())
                 + " — Tell the user in one or two short sentences where to look and when it "
                   "appears, how high it gets, and that it looks like a bright, steady, fast star.")
            _show(player, _draw_sky(p), f"✦ {_hm(p['start'])}", unasked=True)
        nxt = _next_visible(item["place"], p["end"] + 60) if item["repeat"] else None
        with r.lock:
            if r.next is item:
                r.next = dict(item, **{"pass": nxt, "at": nxt["start"] - item["lead"] * 60}) if nxt else None


# ─────────────────────────────────────────────────────────────────────────────
# Actions
# ─────────────────────────────────────────────────────────────────────────────

def _layer_names(value) -> set:
    if isinstance(value, str):
        value = re.split(r"[,\s]+", value)
    names = {str(v).strip().lower() for v in value or []}
    names = {{"disasters": "events", "earthquakes": "quakes", "planes": "aircraft",
              "flights": "aircraft", "aurorae": "aurora"}.get(n, n) for n in names}
    return {n for n in names if n in LAYERS}


def _layer_changes(parameters: dict, current: set) -> set:
    """`layers` replaces the set; `show` / `hide` add to it and take from it."""
    out = set(current)
    if parameters.get("layers"):
        out = _layer_names(parameters["layers"]) | {"weather"}
    out |= _layer_names(parameters.get("show"))
    out -= _layer_names(parameters.get("hide"))
    return out


def _span_for(place: dict, named: bool, parameters: dict, default: float) -> float:
    if parameters.get("radius_km") not in (None, ""):
        return _num(parameters.get("radius_km"), default)
    if named and place.get("view_km"):
        return place["view_km"] * 1.15
    if named and place.get("extent_km"):                   # cached before view_km existed
        return max(1.0, min(250.0, place["extent_km"]) * 1.15)
    return default


_VIEW_HINT = ("In one or two short sentences in the user's language, say what is on screen now. "
              "The user can keep steering it by voice (closer, further, a direction, spin, back, "
              "satellite, another place) or move it with the mouse.")


def _answer(shown: str, facts: dict, hint: str = _VIEW_HINT) -> str:
    return _done(shown=shown, **facts) + "\n" + hint


def _globe(parameters: dict, player) -> str:
    target, named = _place(parameters)
    focus_iss = str(parameters.get("focus") or "").lower() == "iss"
    home = _home()
    span = max(MAP_SPAN, _num(parameters.get("radius_km"), 6371.0))
    center = (target["lat"], target["lon"]) if target else (20.0, 0.0)
    st = _new_state(center, span, place=target if named else home,
                    layers=_layer_changes(parameters, _enabled_layers()), focus_iss=focus_iss,
                    card_place=target if named else None)
    if focus_iss:
        s = iss_state()
        if s:
            st["center"] = (s["lat"], s["lon"])
            st["title"], st["place"] = "ISS", None
    shown, facts = _present(st, player)
    live = None
    if shown == "shown":
        if focus_iss and _num(parameters.get("live_minutes"), 0) > 0:
            live = _start_live(player, _num(parameters.get("live_minutes"), 0) * 60, "iss")
        elif _num(parameters.get("spin_seconds"), 0) > 0:
            live = _start_live(player, _num(parameters.get("spin_seconds"), 0), "spin")
    if named:
        facts["place"] = target["name"]
    if home:
        facts["at_user_now"] = "daylight" if sun_elevation(home["lat"], home["lon"], time.time()) > -0.8 else "night"
    facts["moving_for_seconds"] = live
    return _answer(shown, facts)


def _map(parameters: dict, player) -> str:
    place, named = _place(parameters)
    if not place:
        return _need_location("a map")
    span = min(MAP_SPAN - 1, max(MIN_SPAN, _span_for(place, named, parameters, 60.0)))
    style = str(parameters.get("style") or "").lower() or None
    current = (_state["now"] or {}).get("layers") or _enabled_layers()
    st = _new_state((place["lat"], place["lon"]), span, place=place, style=style,
                    layers=_layer_changes(parameters, current),
                    pins=[{"lat": place["lat"], "lon": place["lon"], "label": place["label"]}] if named else [],
                    card_place=place)
    shown, facts = _present(st, player)
    facts["place"] = place["name"]
    return _answer(shown, facts)


def _navigate(parameters: dict, player) -> str:
    """Move the view the user is looking at. Several moves in one call are
    applied in a fixed order: back or home, a place, a size, zoom, move, spin."""
    cur = _state["now"]
    home = _home()
    lv = _live_view(player, open_it=False)
    if lv is not None and cur is not None:
        cam = _on_live(lv, lambda w: w.camera())
        if cam:
            cur = dict(cur, center=(cam["lat"], cam["lon"]), span=cam["span"], style=cam["style"],
                       layers=set(cam["layers"]))
    if _flag(parameters.get("back")):
        if not _state["history"]:
            return _done(moved="nothing", reason="there is no earlier view to go back to")
        st = _state["history"].pop()
        shown, facts = _present(dict(st), player, remember=False)
        facts["moved"] = "back to the previous view"
        return _answer(shown, facts)
    if cur is None or _flag(parameters.get("home")):
        base = home or {"lat": 20.0, "lon": 0.0, "label": ""}
        span = 6371.0 if cur is None else min(cur["span"], 60.0)
        cur = _new_state((base["lat"], base["lon"]), span, place=home, card_place=home)
    st = dict(cur, layers=set(cur["layers"]), pins=list(cur.get("pins") or []),
              lines=list(cur.get("lines") or []))
    lat, lon = st["center"]
    moved = []

    spoken = str(parameters.get("place") or "").strip()
    if spoken:
        found = _resolve_location(spoken)
        if not found:
            return _failed(f"no place called '{spoken}' was found")
        lat, lon = found["lat"], found["lon"]
        st.update(place=found, card_place=found, focus_iss=False,
                  pins=[{"lat": lat, "lon": lon, "label": found["label"]}])
        if parameters.get("radius_km") in (None, "") and parameters.get("zoom") in (None, ""):
            st["span"] = _span_for(found, True, {}, st["span"])
        moved.append("flew to the place")
    if parameters.get("radius_km") not in (None, ""):
        st["span"] = _num(parameters.get("radius_km"), st["span"])
        moved.append("size set")
    zoom = str(parameters.get("zoom") or "").strip().lower()
    factor = max(1.05, min(1000.0, _num(parameters.get("zoom_factor"), 2.0)))
    if zoom in ("in", "closer"):
        st["span"] /= factor
        moved.append(f"zoomed in ×{factor:g}")
    elif zoom in ("out", "further", "farther"):
        st["span"] *= factor
        moved.append(f"zoomed out ×{factor:g}")
    direction = str(parameters.get("move") or "").strip().lower().replace("-", "").replace(" ", "")
    if direction in _DIRECTIONS:
        amount = max(0.05, min(3.0, _num(parameters.get("move_amount"), 0.5)))
        bearing = _DIRECTIONS[direction]
        # the picture is wider than it is tall, so a sideways step covers more ground
        km = amount * 2 * st["span"] * (1.6 if bearing in (90, 270) else 1.0)
        lat, lon = view.destination(lat, lon, bearing, km * 1000)
        st["focus_iss"] = False
        moved.append(f"moved {compass(bearing)} {fmt_km(km, _units())}")
    turning = parameters.get("rotate_degrees") not in (None, "") or _num(parameters.get("spin_seconds"), 0) > 0
    if turning and st["span"] < MAP_SPAN and parameters.get("zoom") in (None, ""):
        st["span"] = 6371.0                     # turning means the globe: a map cannot spin
        moved.append("switched to the globe")
    if parameters.get("rotate_degrees") not in (None, ""):
        deg = _num(parameters.get("rotate_degrees"), 0.0)
        lon = (lon + deg + 540.0) % 360.0 - 180.0
        st["focus_iss"] = False
        moved.append(f"rotated {deg:+g} deg")
    style = str(parameters.get("style") or "").lower()
    if style in ("dark", "satellite", "offline") and style != st["style"]:
        st["style"] = style
        moved.append(f"style {style}")
    layers = _layer_changes(parameters, st["layers"])
    if layers != st["layers"]:
        st["layers"] = layers
        moved.append("layers changed")
    st["span"] = max(MIN_SPAN, min(MAX_SPAN, st["span"]))
    st["center"] = (max(-85.0, min(85.0, lat)), (lon + 540.0) % 360.0 - 180.0)
    if not spoken and any(m.startswith(("moved", "rotated")) for m in moved):
        st["card_place"] = None                 # the card now follows the middle of the view
        st["place"] = None
    shown, facts = _present(st, player)
    spin = None
    if shown == "shown" and _num(parameters.get("spin_seconds"), 0) > 0 and st["span"] >= MAP_SPAN:
        spin = _start_live(player, _num(parameters.get("spin_seconds"), 0), "spin")
    facts.update(moved=", ".join(moved) or "redrawn", spinning_for_seconds=spin,
                 layers=", ".join(sorted(st["layers"] - {"weather"})))
    if st["span"] <= MIN_SPAN + 1e-6:
        facts["limit"] = "closest zoom reached"
    elif st["span"] >= MAX_SPAN - 1e-6:
        facts["limit"] = "furthest zoom reached"
    return _answer(shown, facts)


def _earthquakes(parameters: dict, player) -> str:
    units = _units()
    home = _home()
    scope = str(parameters.get("scope") or "near").lower()
    if scope == "world":
        min_mag = _num(parameters.get("min_magnitude"), _WORLD_MIN_MAG)
        # a week of M4.5+ worldwide is ~350 events — inside one request's limit
        days = max(0.1, min(7.0, _num(parameters.get("days"), 1)))
        quakes, sources, problem = fetch_quakes(None, None, None, min_mag, time.time() - days * 86400)
        if quakes is None:
            return _failed(f"no earthquake service answered ({problem})")
        biggest = max(quakes, key=lambda q: q["mag"] or 0, default=None)
        center = (biggest["lat"], biggest["lon"]) if biggest else \
            ((home or {}).get("lat", 20.0), (home or {}).get("lon", 0.0))
        st = _new_state(center, 6371.0, layers={"quakes"}, title=f"{len(quakes)} · M≥{min_mag:g}")
        shown, _ = _present(st, player, preset={"quakes": quakes}, focus_ref=biggest,
                            title=f"◉ {len(quakes)} · M≥{min_mag:g}")
        _panel(player, f"◉ M≥{min_mag:g} · {fmt_ago(days * 86400)}",
               "\n\n".join(_quake_line(q, units, home) for q in
                           sorted(quakes, key=lambda q: -(q["mag"] or 0))[:10]) or "—")
        top = sorted(quakes, key=lambda q: -(q["mag"] or 0))[:5]
        return (_done(scope="world", min_magnitude=min_mag, period=fmt_ago(days * 86400), count=len(quakes),
                      strongest=[f"M{q['mag']} {q['region']} ({fmt_ago(time.time() - q['time'])} ago)"
                                 for q in top] or None,
                      tsunami_flags=sum(1 for q in quakes if q.get("tsunami")) or None,
                      shown=shown, sources="+".join(sources), problem=problem or None)
                + "\nSummarise in one or two short sentences in the user's language; the list is "
                  "on screen. Never predict earthquakes.")

    place, named = _place(parameters)
    if not place:
        return _need_location("'earthquakes near me'")
    if parameters.get("radius_km") not in (None, ""):
        radius = max(10.0, min(5000.0, _num(parameters.get("radius_km"), 500)))
    elif named:
        radius = max(100.0, min(3000.0, (place.get("view_km") or place.get("extent_km") or 100.0) * 1.2))
    else:
        radius = _num(_setting("quake_radius_km", 500), 500)
    min_mag = _num(parameters.get("min_magnitude"), 2.5)
    days = max(0.1, min(30.0, _num(parameters.get("days"), 7)))
    quakes, sources, problem = fetch_quakes(place["lat"], place["lon"], radius, min_mag,
                                            time.time() - days * 86400)
    if quakes is None:
        return _failed(f"no earthquake service answered ({problem})")
    day_ago = time.time() - 86400
    latest = quakes[0] if quakes else None
    biggest = max(quakes, key=lambda q: q["mag"] or 0, default=None)
    nearest = min(quakes, key=lambda q: distance_km(place["lat"], place["lon"], q["lat"], q["lon"]),
                  default=None)
    st = _new_state((place["lat"], place["lon"]), radius * 1.1, place=place, radius_m=radius * 1000,
                    layers={"quakes", "events", "weather"}, card_place=place if named else None,
                    pins=[{"lat": place["lat"], "lon": place["lon"], "label": place["label"]}] if named else [])
    shown, _ = _present(st, player, preset={"quakes": quakes},
                        title=f"◉ {len(quakes)} · {place.get('label') or ''}")
    _panel(player, f"◉ {len(quakes)} · {fmt_km(radius, units)} · {fmt_ago(days * 86400)}",
           "\n\n".join(_quake_line(q, units, place) for q in quakes[:10]) or "—")
    of = "place" if named else "user"

    def brief(q):
        return "; ".join(f"{k}: {v}" for k, v in _clean(_quake_facts(q, units, place, of)).items()) if q else None
    return (_done(place=place["name"], radius=fmt_km(radius, units), min_magnitude=min_mag,
                  period=fmt_ago(days * 86400), count=len(quakes),
                  count_last_24h=sum(1 for q in quakes if q["time"] >= day_ago),
                  latest=brief(latest), strongest=brief(biggest) if biggest is not latest else None,
                  nearest=brief(nearest) if nearest not in (latest, biggest) else None,
                  shown=shown, sources="+".join(sources), problem=problem or None)
            + "\nAnswer in one or two short sentences in the user's language. If there were none, "
              "say so plainly. Never predict earthquakes.")


def _disasters(parameters: dict, player) -> str:
    units = _units()
    events = _events_feed.get(25.0)
    if events is None:
        return _failed("the disaster feeds (GDACS, NASA EONET) are not answering")
    kinds = parameters.get("kinds") or parameters.get("kind")
    if isinstance(kinds, str):
        kinds = re.split(r"[,\s]+", kinds)
    aliases = {"fire": "wildfire", "fires": "wildfire", "wildfires": "wildfire", "hurricane": "cyclone",
               "typhoon": "cyclone", "cyclones": "cyclone", "floods": "flood", "volcanoes": "volcano",
               "storms": "storm", "droughts": "drought", "iceberg": "ice", "icebergs": "ice"}
    wanted = {aliases.get(str(k).strip().lower(), str(k).strip().lower()) for k in kinds or [] if str(k).strip()}
    chosen = [e for e in events if not wanted or e["kind"] in wanted]
    place, named = _place(parameters)
    if str(parameters.get("scope") or "").lower() == "near" or named:
        if not place:
            return _need_location("disasters near the user")
        radius = max(500.0, _span_for(place, named, parameters, 1500.0))
        chosen = [e for e in chosen if distance_km(place["lat"], place["lon"], e["lat"], e["lon"]) <= radius]
        ref, of = place, ("place" if named else "user")
        st = _new_state((place["lat"], place["lon"]), max(radius * 1.1, 50.0), place=place,
                        layers={"events", "quakes", "weather"}, card_place=place if named else None)
    else:
        ref, of = _home(), "user"
        top = max(chosen, key=lambda e: e["rank"], default=None)
        center = (top["lat"], top["lon"]) if top else ((ref or {}).get("lat", 20.0), (ref or {}).get("lon", 0.0))
        st = _new_state(center, 6371.0, layers={"events"})
    chosen.sort(key=lambda e: (-e["rank"], -(e.get("since") or 0)))
    shown, _ = _present(st, player, preset={"events": chosen}, title=f"◆ {len(chosen)}")
    _panel(player, f"◆ {len(chosen)}", "\n\n".join(_event_line(e, units, ref) for e in chosen[:12]) or "—")
    counts: dict = {}
    for e in chosen:
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1
    listed = ["; ".join(f"{k}: {v}" for k, v in _clean(_event_facts(e, units, ref, of)).items())
              for e in chosen[:6]]
    return (_done(count=len(chosen), by_type=", ".join(f"{k} {n}" for k, n in sorted(counts.items())) or None,
                  most_serious=listed or None, shown=shown,
                  red_alerts=sum(1 for e in chosen if e["level"] == "red"),
                  orange_alerts=sum(1 for e in chosen if e["level"] == "orange"),
                  sources="GDACS (official alert levels) + NASA EONET")
            + "\nSummarise in two short sentences in the user's language: what is happening and "
              "where, the most serious first. These are reports of ongoing events, not forecasts.")


def _route(parameters: dict, player) -> str:
    units = _units()
    dest = str(parameters.get("to") or parameters.get("place") or "").strip()
    if not dest:
        return _ask("which place should the route go to?", reason="no destination named")
    b = _resolve_location(dest)
    if not b:
        return _failed(f"no place called '{dest}' was found")
    a, _ = _place(parameters, "from")
    if not a:
        return _need_location("a route from the user's location")
    d = distance_km(a["lat"], a["lon"], b["lat"], b["lon"])
    brg = bearing_deg(a["lat"], a["lon"], b["lat"], b["lon"])
    path = view.great_circle((a["lat"], a["lon"]), (b["lat"], b["lon"]), max(40, int(d / 40)))
    mid = path[len(path) // 2]
    span = max(40.0, d * 0.62)
    if span >= MAP_SPAN:
        span = 6371.0 if d < 12000 else 9000.0          # the whole route on one side of the globe
    with ThreadPoolExecutor(max_workers=2) as pool:
        fa, fb = pool.submit(weather, a["lat"], a["lon"]), pool.submit(weather, b["lat"], b["lon"])
        wa, wb = fa.result(), fb.result()
    hours = d / 830.0 + 0.5                               # cruise plus climb and descent
    card = [("big", "#eaf6ff", fmt_km(d, units)),
            ("mid", None, f"✈ {int(hours)} h {int((hours % 1) * 60):02d} min"),
            ("small", None, f"{arrow(brg)} {brg:.0f}°")]
    if wa and wb:
        card += [("small", None, f"Δ {(wb['offset_s'] - wa['offset_s']) / 3600:+g} h"),
                 ("small", None, f"{b['label'][:24]}  {_place_time(wb['offset_s'])}  "
                                 f"{wb['symbol']} {fmt_temp(wb['temp'], units)}")]
    st = _new_state(mid, span, lines=[path], layers={"weather"} if span < MAP_SPAN else {"aurora"},
                    pins=[{"lat": a["lat"], "lon": a["lon"], "label": a["label"]},
                          {"lat": b["lat"], "lon": b["lon"], "label": b["label"]}],
                    place={"label": f"{a['label']} → {b['label']}"}, card=card)
    shown, _ = _present(st, player, title=f"⊕ {a['label']} → {b['label']}")
    facts = {"from": a["name"], "to": b["name"], "great_circle_distance": fmt_km(d, units),
             "initial_heading": f"{compass(brg)} ({brg:.0f} deg)",
             "direct_flight_estimate": f"{int(hours)} h {int((hours % 1) * 60):02d} min "
                                       f"(non-stop, typical airliner)",
             "shown": shown}
    if wa and wb:
        facts.update(time_difference=f"{(wb['offset_s'] - wa['offset_s']) / 3600:+g} h",
                     local_time_at_destination=_place_time(wb["offset_s"]),
                     weather_at_destination=f"{wb['sky']}, {fmt_temp(wb['temp'], units)}")
    return _done(**facts) + "\nAnswer in one or two short sentences in the user's language."


def _aurora(parameters: dict, player) -> str:
    place, _ = _place(parameters)
    kp = _kp_feed.get(10.0)
    grid = _aurora_feed.get(20.0)
    if grid is None:
        return _failed("NOAA's aurora forecast is not answering")
    facts: dict = {"kp_now": round(kp["kp"], 1) if kp and kp.get("kp") is not None else None,
                   "kp_max_next_36h": kp.get("max_next_36h") if kp else None,
                   "forecast_for": grid["time"], "source": "NOAA SWPC OVATION"}
    if place:
        g = grid["grid"]

        def at(lat):
            return float(view._sample_grid(g, np.array([lat]), np.array([place["lon"]]))[0])
        pole = 1.0 if place["lat"] >= 0 else -1.0
        # an aurora up to ~1000 km poleward still shows low on the horizon
        horizon = max(at(max(-90.0, min(90.0, place["lat"] + pole * k))) for k in range(0, 10))
        facts.update(place=place["name"], probability_overhead=f"{at(place['lat']):.0f} %",
                     probability_on_poleward_horizon=f"{horizon:.0f} %",
                     sky_dark_now=sun_elevation(place["lat"], place["lon"], time.time()) < -12)
    center = (65.0 if not place or place["lat"] >= 0 else -65.0, place["lon"] if place else 0.0)
    st = _new_state(center, 7000.0, layers={"aurora"}, place=place, title="aurora")
    shown, _ = _present(st, player, title="⊕ ✧")
    facts["shown"] = shown
    return _done(**facts) + ("\nAnswer in one or two short sentences in the user's language: the "
                             "chance of seeing it from where they are, and whether the sky is dark.")


def _quake_watch(parameters: dict, player) -> str:
    units = _units()
    place, _ = _place(parameters)
    if not place:
        return _need_location("the earthquake watch")
    radius = max(10.0, min(5000.0, _num(parameters.get("radius_km"),
                                        _num(_setting("quake_radius_km", 500), 500))))
    min_mag = max(1.0, min(9.0, _num(parameters.get("min_magnitude"),
                                     _num(_setting("quake_min_mag", 4), 4))))
    g = parameters.get("global_min_magnitude")
    if g in (None, ""):
        g = _setting("quake_global", "off")
    global_min = None if str(g).strip().lower() in ("off", "0", "none", "") else \
        max(5.0, min(9.0, _num(g, 6.5)))
    interval = max(_MIN_WATCH_INTERVAL, min(900.0, _num(parameters.get("interval_seconds"),
                                                        _num(_setting("quake_interval", 60), 60))))
    minutes = _num(parameters.get("duration_minutes"), 0)
    cfg = {"lat": place["lat"], "lon": place["lon"], "label": place.get("label") or "",
           "radius_km": radius, "min_mag": min_mag, "global_min": global_min, "interval": interval,
           "until": time.time() + minutes * 60 if minutes > 0 else 0.0, "started": time.time()}
    with _watch.lock:
        if _watch.running():
            _watch.stop.set()
            _watch.thread.join(timeout=2)
        _watch.stop = threading.Event()
        _watch.config = cfg
        _watch.alerted.clear()
        _watch.spoken.clear()
        _watch.polls = _watch.failures = 0
        _watch.last_alert = None
        _watch.thread = threading.Thread(target=_watch_loop, args=(player,), daemon=True,
                                         name="planet-quake-watch")
        _watch.thread.start()
    _log(player, f"Earthquake watch on — M≥{min_mag:g} within {fmt_km(radius, units)}, "
                 f"every {interval:.0f} s.")
    return _done(watch="started", location=place["name"], radius=fmt_km(radius, units),
                 min_magnitude=min_mag, also_worldwide_from=global_min,
                 check_every=f"{interval:.0f} s", for_minutes=int(minutes) if minutes > 0 else None,
                 until_stopped=True if minutes <= 0 else None,
                 note="reports arrive minutes after a quake — this is not an early-warning system")


def _stop_quake_watch(player) -> str:
    with _watch.lock:
        was = _watch.running()
        _watch.stop.set()
    if not was:
        return _done(watch="was_not_running")
    _log(player, "Earthquake watch off.")
    return _done(watch="stopped", checks_done=_watch.polls)


def _iss(parameters: dict, player) -> str:
    units = _units()
    state = iss_state(tracks=True)
    if not state:
        return _failed("the ISS position could not be fetched (no orbit elements and no position service)")
    home = _home()
    current = (_state["now"] or {}).get("layers") or _enabled_layers()
    st = _new_state((state["lat"], state["lon"]), 6371.0, focus_iss=True, title="ISS",
                    layers=set(current) | {"iss"})
    shown, _ = _present(st, player, title="✦ ISS")
    live = _start_live(player, _num(parameters.get("live_minutes"), 0) * 60, "iss") if shown == "shown" else None
    facts = {"over_country": _country_below(state["lat"], state["lon"]),
             "position": f"{state['lat']:.2f}, {state['lon']:.2f}",
             "altitude": fmt_km(state["alt_km"], units), "speed": fmt_speed(state["speed_kmh"], units),
             "in_sunlight": state["sunlit"], "in_earth_shadow": not state["sunlit"],
             "shown": shown, "following_for_seconds": live, "source": state["source"]}
    if facts["over_country"] is None:
        facts["over_open_water"] = True
    if home:
        d = distance_km(home["lat"], home["lon"], state["lat"], state["lon"])
        facts["distance_from_user_ground"] = fmt_km(d, units)
        facts["direction_from_user"] = compass(bearing_deg(home["lat"], home["lon"], state["lat"], state["lon"]))
        prop = _propagate(np.array([time.time()]))
        if prop is not None:
            e, n, u = _enu(prop["ecef"] - _observer(home["lat"], home["lon"]), home["lat"], home["lon"])
            el = float(np.degrees(np.arctan2(u, np.hypot(e, n)))[0])
            dark = sun_elevation(home["lat"], home["lon"], time.time()) < -6
            facts["above_user_horizon"] = el > 0
            facts["visible_from_user_now"] = el > 10 and state["sunlit"] and dark
    return (_done(**facts) + "\nAnswer in one or two short sentences in the user's language (name "
            "the ocean from the coordinates when it is over open water).")


def _iss_passes(parameters: dict, player) -> str:
    if not _sgp4_ok():
        return _failed(f"pass predictions need the 'sgp4' library, which is not installed. Tell the "
                       f"user to run: pip install sgp4 — the exact command for the Python running "
                       f"JARVIS is: {_install_command()} (no restart needed)")
    place, _ = _place(parameters)
    if not place:
        return _need_location("ISS passes")
    days = max(1.0, min(10.0, _num(parameters.get("days"), 3)))
    all_passes = _flag(parameters.get("all_passes"))
    min_el = _num(_setting("pass_min_el", 10), 10)
    passes = iss_passes(place["lat"], place["lon"], days=days, min_el=min_el,
                        visible_only=not all_passes, limit=10)
    tle = _tle() or {}
    age_h = (time.time() - (tle.get("epoch") or time.time())) / 3600
    if not passes:
        return (_done(place=place["name"], days=int(days), visible_passes=0,
                      note="none where the ISS is sunlit while the sky is dark — this happens for "
                           "stretches of days; try more days")
                + "\nSay so in one short sentence in the user's language.")
    shown = _show(player, _draw_sky(passes[0]), f"✦ {_hm(passes[0]['start'])}")
    _panel(player, f"✦ ISS · {place.get('label') or ''}", "\n\n".join(_pass_line(p) for p in passes))
    first = _clean(_pass_facts(passes[0]))
    others = [f"{_local(p['start']):%Y-%m-%d} {_hm(p['start'])}, {p['peak_el']:.0f} deg up"
              for p in passes[1:6]]
    return (_done(place=place["name"], next_pass="; ".join(f"{k}: {v}" for k, v in first.items()),
                  later_passes=others or None, count=len(passes), days=int(days), shown=shown,
                  times_are="the computer's local time",
                  elements_age_hours=round(age_h) if age_h > 48 else None)
            + "\nAnswer in two short sentences in the user's language: when and where to look for "
              "the next one, and how many more there are. The sky chart is on screen.")


def _iss_remind(parameters: dict, player) -> str:
    if not _sgp4_ok():
        return _failed(f"the reminder needs pass predictions, which need the 'sgp4' library. Tell "
                       f"the user to run: pip install sgp4 — exactly: {_install_command()}")
    place, _ = _place(parameters)
    if not place:
        return _need_location("the ISS reminder")
    lead = max(1.0, min(60.0, _num(parameters.get("lead_minutes"),
                                   _num(_setting("remind_minutes", 5), 5))))
    repeat = _flag(parameters.get("repeat"))
    p = _next_visible(place, time.time() + lead * 60)
    if not p:
        return _done(reminder="not_set", reason="no visible pass in the next 10 days")
    with _rem.lock:
        _rem.next = {"at": p["start"] - lead * 60, "pass": p, "lead": lead, "repeat": repeat,
                     "place": place}
        if not (_rem.thread and _rem.thread.is_alive()):
            _rem.thread = threading.Thread(target=_reminder_loop, args=(player,), daemon=True,
                                           name="planet-iss-reminder")
            _rem.thread.start()
    _rem.wake.set()
    _log(player, f"ISS reminder set for {_hm(p['start'] - lead * 60)}.")
    return _done(reminder="set", remind_at=_iso(p["start"] - lead * 60),
                 lead=f"{lead:.0f} min", every_visible_pass=repeat or None,
                 next_pass="; ".join(f"{k}: {v}" for k, v in _clean(_pass_facts(p)).items()),
                 note="kept while JARVIS is running")


def _cancel_reminders(player) -> str:
    with _rem.lock:
        had = _rem.next is not None
        _rem.next = None
    _rem.wake.set()
    if had:
        _log(player, "ISS reminder cancelled.")
    return _done(reminder="cancelled" if had else "none_was_set")


def _close(player) -> str:
    lv = _live_view(player, open_it=False)
    if lv is not None:
        _on_live(lv, lambda w: w.close_view())
        return _done(closed="live view")
    if player is not None and callable(getattr(player, "stop_video", None)) and \
            callable(getattr(player, "video_is_playing", None)) and player.video_is_playing():
        player.stop_video()
        return _done(closed="picture")
    return _done(closed="nothing was open")


def _status() -> str:
    units = _units()
    facts: dict = {"quake_watch": "on" if _watch.running() else "off"}
    if _watch.running():
        cfg = _watch.config
        facts.update(watch_radius=fmt_km(cfg["radius_km"], units), watch_min_magnitude=cfg["min_mag"],
                     watch_worldwide_from=cfg["global_min"], watch_checks=_watch.polls,
                     watch_problem=_watch.last_error or None,
                     minutes_left=int((cfg["until"] - time.time()) / 60) if cfg["until"] else None,
                     last_alert=(f"M{_watch.last_alert['mag']} {_watch.last_alert['region']}"
                                 if _watch.last_alert else None))
    with _rem.lock:
        item = _rem.next
    facts["iss_reminder"] = _iso(item["at"]) if item else "none"
    if item:
        facts["reminder_repeats"] = item["repeat"] or None
    st = _state["now"]
    if st:
        facts["current_view"] = (f"{'globe' if st['span'] >= MAP_SPAN else 'map'} at "
                                 f"{st['center'][0]:.2f}, {st['center'][1]:.2f}, "
                                 f"{fmt_km(st['span'] * 2, units)} high")
        facts["layers"] = ", ".join(sorted(st["layers"] - {"weather"}))
    facts["orbit_library"] = "installed" if _sgp4_ok() else f"missing — {_install_command()}"
    return _done(**facts)


_ACTIONS = ("globe", "map", "navigate", "earthquakes", "disasters", "route", "aurora",
            "quake_watch", "stop_quake_watch", "iss", "iss_passes", "iss_remind",
            "cancel_reminders", "close", "status")


def run(parameters: dict, player=None, session_memory=None) -> str:
    parameters = parameters if isinstance(parameters, dict) else {}
    action = str(parameters.get("action") or "globe").strip().lower()
    if action not in _ACTIONS:
        import difflib
        close = difflib.get_close_matches(action, _ACTIONS, n=1, cutoff=0.6)
        action = close[0] if close else "globe"
    # the slow feeds start loading on first use, so they are ready when asked for
    _events_feed.get(0.0)
    _aurora_feed.get(0.0)
    handlers = {"globe": _globe, "map": _map, "navigate": _navigate, "earthquakes": _earthquakes,
                "disasters": _disasters, "route": _route, "aurora": _aurora,
                "quake_watch": _quake_watch, "iss": _iss, "iss_passes": _iss_passes,
                "iss_remind": _iss_remind}
    try:
        if action in handlers:
            return handlers[action](parameters, player)
        if action == "stop_quake_watch":
            return _stop_quake_watch(player)
        if action == "cancel_reminders":
            return _cancel_reminders(player)
        if action == "close":
            return _close(player)
        return _status()
    except Exception as exc:
        _log(player, f"Planet watch failed: {type(exc).__name__}: {exc}")
        return _failed(f"{type(exc).__name__}: {exc}")


PLUGIN = {
    "name": "planet_watch",
    "description": (
        "The planet on the HUD, steered by voice: a 3-D globe lit by the real Sun that can be spun "
        "and zoomed into, continuing as a map down to street level (dark or satellite), with "
        "earthquakes (EMSC+USGS), current disasters (GDACS alert levels + NASA EONET), live "
        "aircraft on close-up maps, the aurora, local time and weather of the place shown, and the "
        "ISS. No account needed. Use in ANY language whenever the user asks to see the Earth, the "
        "world, a globe, a place or a map; to move what is on screen; about earthquakes, "
        "disasters, cyclones, floods, volcanoes, wildfires, the northern or southern lights, the "
        "distance or route between places, or the ISS / space station. "
        "Actions: 'globe' (default) — the globe on the user, on 'place', or on the ISS with focus "
        "'iss'; 'map' — a map of 'place' or the user's area (radius_km, style); 'navigate' — MOVE "
        "THE VIEW ALREADY ON SCREEN; use it for every relative request after a globe or map is "
        "shown: zoom in/out → zoom 'in'|'out' (zoom_factor, default 2); a direction → move "
        "n|ne|e|se|s|sw|w|nw|up|down|left|right (move_amount = fraction of the view, default 0.5); "
        "turning the globe → rotate_degrees (+ east) or spin_seconds for a slow turn; another "
        "place → place; an exact size → radius_km; the previous view → back true; the user's own "
        "location → home true; style dark|satellite|offline; layers → show / hide with quakes, "
        "events, aircraft, iss, aurora. 'earthquakes' — scope near (default) or world, "
        "min_magnitude, days, radius_km; 'disasters' — scope world (default) or near / 'place', "
        "optional kinds (cyclone, flood, volcano, wildfire, drought, storm, ice); 'route' — from "
        "(default the user) to 'to': distance, flight time, time difference, drawn on the globe or "
        "map; 'aurora' — chance of seeing it; 'quake_watch' — background alert (radius_km, "
        "min_magnitude, global_min_magnitude, duration_minutes); 'stop_quake_watch'; 'iss' — where "
        "it is now (live_minutes follows it); 'iss_passes' — naked-eye passes over the next 'days' "
        "with a sky chart; 'iss_remind' — speak lead_minutes before the next visible pass (repeat "
        "for every one); 'cancel_reminders'; 'close' — close the globe or map; 'status'. The view is "
        "live: the user can also drag, zoom and click it by hand, and 'navigate' continues from "
        "wherever they left it. Earthquake and disaster reports come after "
        "the fact: never present them as warnings and never predict them. For a list of aircraft "
        "or one flight use aircraft_report. Answer briefly from the facts returned."
    ),
    "brief": "Planet on the HUD, steerable by voice: globe↔map zoom/pan/spin, quakes, disasters, "
             "aircraft, aurora, weather, routes, ISS passes/reminders, quake alerts.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "enum": list(_ACTIONS),
                       "description": "globe (default), map, navigate, earthquakes, disasters, route, "
                                      "aurora, quake_watch, stop_quake_watch, iss, iss_passes, "
                                      "iss_remind, cancel_reminders, close or status."},
            "place": {"type": "STRING",
                      "description": "A place the user named, in their own words; omit to use "
                                     "their saved location (navigate: fly there)."},
            "from": {"type": "STRING", "description": "route: start place; omit for the user."},
            "to": {"type": "STRING", "description": "route: destination place."},
            "focus": {"type": "STRING", "enum": ["user", "iss"],
                      "description": "globe: centre on the user (default) or on the ISS."},
            "zoom": {"type": "STRING", "enum": ["in", "out"], "description": "navigate: zoom."},
            "zoom_factor": {"type": "NUMBER", "description": "navigate: how much (default 2)."},
            "move": {"type": "STRING",
                     "enum": ["n", "ne", "e", "se", "s", "sw", "w", "nw", "up", "down", "left", "right"],
                     "description": "navigate: pan the view."},
            "move_amount": {"type": "NUMBER",
                            "description": "navigate: pan distance as a fraction of the view (0.5)."},
            "rotate_degrees": {"type": "NUMBER",
                               "description": "navigate: turn the globe east (+) or west (-)."},
            "spin_seconds": {"type": "NUMBER",
                             "description": "globe / navigate: keep the globe turning this long."},
            "back": {"type": "BOOLEAN", "description": "navigate: return to the previous view."},
            "home": {"type": "BOOLEAN", "description": "navigate: go to the user's location."},
            "radius_km": {"type": "NUMBER",
                          "description": "Area radius in km (map, navigate, earthquakes, "
                                         "disasters, quake_watch)."},
            "style": {"type": "STRING", "enum": ["dark", "satellite", "offline"],
                      "description": "map / navigate: basemap."},
            "show": {"type": "ARRAY", "items": {"type": "STRING"},
                     "description": "Layers to add: quakes, events, aircraft, iss, aurora."},
            "hide": {"type": "ARRAY", "items": {"type": "STRING"},
                     "description": "Layers to remove."},
            "scope": {"type": "STRING", "enum": ["near", "world"],
                      "description": "earthquakes / disasters: around the user or place, or worldwide."},
            "kinds": {"type": "ARRAY", "items": {"type": "STRING"},
                      "description": "disasters: only these kinds."},
            "min_magnitude": {"type": "NUMBER",
                              "description": "earthquakes / quake_watch: smallest magnitude."},
            "global_min_magnitude": {"type": "NUMBER",
                                     "description": "quake_watch: also alert for quakes anywhere "
                                                    "from this magnitude."},
            "days": {"type": "NUMBER",
                     "description": "earthquakes: how many days back; iss_passes: days ahead."},
            "duration_minutes": {"type": "NUMBER",
                                 "description": "quake_watch: stop after this many minutes."},
            "interval_seconds": {"type": "NUMBER",
                                 "description": "quake_watch: seconds between checks (60-900)."},
            "live_minutes": {"type": "NUMBER",
                             "description": "iss / globe with focus iss: keep following it this long."},
            "all_passes": {"type": "BOOLEAN",
                           "description": "iss_passes: include passes in daylight (not visible)."},
            "lead_minutes": {"type": "NUMBER",
                             "description": "iss_remind: how many minutes before the pass."},
            "repeat": {"type": "BOOLEAN",
                       "description": "iss_remind: remind before every visible pass."},
        },
        "required": [],
    },
}
