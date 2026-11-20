"""
core/weather_met.py — the weather as real numbers: MET Norway's forecast for a
place found on OpenStreetMap. The same code Torvis uses (its core/cloud.py),
so plugins written for both (Live panels, the Holo Desk, Planet Watch) get the
same answers here; core/cloud.py re-exports these names.

Jarvis's own cloud-body weather (core/cloud.weather_today, Open-Meteo) is not
replaced: the cloud body keeps what it had.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from core.paths import data

STATE_DIR = data("cloud_state")
STATE_DIR.mkdir(parents=True, exist_ok=True)
# MET Norway's terms ask for an identifying User-Agent with a way to reach the
# operator: the program's name, and (_contact) the config's operator_contact,
# else the project's public page.
IDENT = "Jarvis-assistant"
_PROJECT_PAGE = "https://github.com/FatihMakes"


def user_tz() -> timezone:
    """The user's time zone, as the cloud body keeps it (core/cloud)."""
    from core.cloud import user_tz as _tz
    return _tz()


def user_now() -> datetime:
    return datetime.now(user_tz())


# ── Weather: MET Norway, places from OpenStreetMap ───────────────────────────
#
# MET Norway's forecast (api.met.no) is open data under CC BY 4.0 — free for a
# paid product too, with a credit ("Data from MET Norway"). Its terms, which
# this follows:
#   - an identifying User-Agent with a way to reach us (WEATHER_UA);
#   - every answer cached, never asked again before its Expires, and asked
#     again with If-Modified-Since so an unchanged forecast is not re-sent;
#   - coordinates with at most 4 decimals (5 or more is refused);
#   - HTTPS only.
# MET has no place search, so a city name becomes coordinates through
# OpenStreetMap's Nominatim, ONCE per place: its usage policy allows requests a
# person triggers, at most one a second, cached — never bulk or periodic. The
# answer is kept for good (a city does not move). Open-Meteo was dropped: its
# free service is for non-commercial use only.
MET_FORECAST = "https://api.met.no/weatherapi/locationforecast/2.0/complete"
GEOCODER = "https://nominatim.openstreetmap.org/search"


def geocoder_base() -> str:
    """Where place names are looked up: OpenStreetMap's Nominatim, unless the
    relay names another (plans.json geocoder.url, in /_relay/status). Its usage
    policy asks that an app can move away when asked, without an update: then
    only the relay's setting changes. Same API (/search, /reverse) expected."""
    try:
        from core import credits
        url = str(((credits.cached_status() or {}).get("geocoder")) or "").strip().rstrip("/")
    except Exception:
        url = ""
    if url.startswith("https://") and "?" not in url:
        return url
    return GEOCODER.rsplit("/", 1)[0]
WEATHER_CACHE = STATE_DIR / "weather_cache.json"
WEATHER_CREDIT = "Data from MET Norway (CC BY 4.0); place from OpenStreetMap contributors (ODbL)"
_GEO_GAP = 1.1                      # seconds between two place searches (policy: 1/s)
_weather_lock = threading.Lock()
_geo_last = [0.0]


def _contact() -> str:
    """How MET and OSM can reach whoever runs this: the config's
    `operator_contact` (an address or a site), else the product's site
    (core/edition), else nothing."""
    try:
        from memory.config_manager import load_api_keys
        c = str(load_api_keys().get("operator_contact") or "").strip()
        if c:
            return c[:120]
    except Exception:
        pass
    try:
        import urllib.parse
        from core import edition
        u = urllib.parse.urlparse(edition.link("terms") or edition.link("privacy"))
        if u.scheme == "https" and u.netloc:
            return f"https://{u.netloc}"
    except Exception:
        pass
    return _PROJECT_PAGE


def weather_ua() -> str:
    contact = _contact()
    return f"{IDENT} (+{contact})" if contact else IDENT


def _weather_fetch(url: str, headers: dict) -> tuple[int, dict, bytes]:
    """(status, headers, body) — 304 is an answer, not an error. Replaced in tests."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": weather_ua(),
                                               "Accept": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return 304, {k.lower(): v for k, v in (e.headers or {}).items()}, b""
        raise RuntimeError(f"the weather service answered {e.code}") from None


def _weather_cache() -> dict:
    try:
        doc = json.loads(WEATHER_CACHE.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def _weather_save(doc: dict) -> None:
    try:
        WEATHER_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = WEATHER_CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, WEATHER_CACHE)
    except OSError as e:
        print(f"[weather] cache not saved: {e}")


def _expires(headers: dict, default_s: float = 1800.0) -> float:
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(headers["expires"]).timestamp()
    except Exception:
        return time.time() + default_s


def place_of(city: str) -> dict:
    """{name, lat, lon} for a place name — from the cache, else one search."""
    import urllib.parse
    key = " ".join(str(city or "").split()).casefold()
    if not key:
        raise RuntimeError("no place was given")
    with _weather_lock:
        cache = _weather_cache()
        hit = (cache.get("places") or {}).get(key)
        if hit:
            return hit
        wait = _GEO_GAP - (time.monotonic() - _geo_last[0])
        if wait > 0:
            time.sleep(wait)
        _geo_last[0] = time.monotonic()
        status, _h, body = _weather_fetch(
            geocoder_base() + "/search?" + urllib.parse.urlencode({"q": city.strip()[:120], "format": "jsonv2",
                                                     "limit": 1}), {})
        rows = json.loads(body.decode("utf-8") or "[]") if status == 200 else []
        if not rows:
            raise RuntimeError(f"no place called '{city}' was found")
        r = rows[0]
        name = str(r.get("display_name") or r.get("name") or city).split(",")
        place = {"name": ", ".join(p.strip() for p in (name[0], name[-1]) if p.strip())
                 if len(name) > 1 else name[0].strip(),
                 "lat": round(float(r["lat"]), 4), "lon": round(float(r["lon"]), 4)}
        places = cache.setdefault("places", {})
        places[key] = place
        for old in list(places)[:-50]:          # the few places this body is asked about
            places.pop(old, None)
        _weather_save(cache)
        return place


def _forecast(lat: float, lon: float) -> dict:
    """MET's forecast for a point, cached until it Expires, revalidated after."""
    import urllib.parse
    lat, lon = round(float(lat), 4), round(float(lon), 4)
    key = f"{lat:.4f},{lon:.4f}"
    with _weather_lock:
        cache = _weather_cache()
        entry = (cache.get("forecasts") or {}).get(key) or {}
        if entry.get("data") and time.time() < float(entry.get("expires") or 0):
            return entry["data"]
        extra = {"If-Modified-Since": entry["last_modified"]} \
            if entry.get("data") and entry.get("last_modified") else {}
        status, headers, body = _weather_fetch(
            MET_FORECAST + "?" + urllib.parse.urlencode({"lat": f"{lat:.4f}", "lon": f"{lon:.4f}"}),
            extra)
        if status == 304 and entry.get("data"):
            entry["expires"] = _expires(headers)
        elif status == 200:
            entry = {"data": json.loads(body.decode("utf-8")), "expires": _expires(headers),
                     "last_modified": headers.get("last-modified", "")}
        else:
            raise RuntimeError(f"the weather service answered {status}")
        forecasts = cache.setdefault("forecasts", {})
        forecasts[key] = entry
        # Only the places this body actually asks about: a handful.
        for old in sorted(forecasts, key=lambda k: forecasts[k].get("expires", 0))[:-20]:
            forecasts.pop(old, None)
        _weather_save(cache)
        return entry["data"]


def _symbol(block: dict | None) -> str:
    """MET's symbol code ('lightrainshowers_day') as data for the model: the
    part that names the weather. The model writes the words, in any language."""
    code = str(((block or {}).get("summary") or {}).get("symbol_code") or "")
    for tail in ("_day", "_night", "_polartwilight"):
        code = code.removesuffix(tail)
    return code


def weather_today(city: str) -> dict:
    """Today's weather where the user is, as numbers and codes (no prose)."""
    place = place_of(city)
    series = (_forecast(place["lat"], place["lon"]).get("properties") or {}).get("timeseries") or []
    if not series:
        raise RuntimeError("the forecast came back empty")
    tz = user_tz()
    today = user_now().date()

    def local(row):
        return datetime.fromisoformat(str(row["time"]).replace("Z", "+00:00")).astimezone(tz)

    rows = [r for r in series if local(r).date() == today] or series[:24]
    first = rows[0]["data"]
    temps, winds, rain = [], [], []
    for r in rows:
        d = r.get("data") or {}
        inst = (d.get("instant") or {}).get("details") or {}
        if inst.get("air_temperature") is not None:
            temps.append(float(inst["air_temperature"]))
        if inst.get("wind_speed") is not None:
            winds.append(float(inst["wind_speed"]))
        for span in ("next_1_hours", "next_6_hours"):
            p = ((d.get(span) or {}).get("details") or {}).get("probability_of_precipitation")
            if p is not None:
                rain.append(float(p))
    return {"place": place["name"],
            "now_c": ((first.get("instant") or {}).get("details") or {}).get("air_temperature"),
            "now": _symbol(first.get("next_1_hours") or first.get("next_6_hours")),
            "min_c": min(temps) if temps else None, "max_c": max(temps) if temps else None,
            "rain_chance_pct": round(max(rain)) if rain else None,
            "wind_max_kmh": round(max(winds) * 3.6) if winds else None,
            "today": _symbol(first.get("next_12_hours") or first.get("next_6_hours")),
            "source": WEATHER_CREDIT}


def place_zone(lat: float, lon: float):
    """The place's own time zone, summer time included, or None.

    MET gives UTC and no time zone. The zone comes from the coordinates,
    offline: tzfpy names it from the time-zone boundary data (OpenStreetMap's,
    ODbL - the same credit as the place search), zoneinfo knows its rules.
    Any point on Earth, no list of countries; the open sea gets its nautical
    zone. The guess this replaced gave every city within two hours of this
    computer's own zone that zone, and no summer time: from Istanbul, Berlin's
    sunset was 18:52 (16:47) and New York ran an hour behind (2026-10-25)."""
    try:
        import tzfpy
        from zoneinfo import ZoneInfo
        name = tzfpy.get_tz(float(lon), float(lat))
        return ZoneInfo(name) if name else None
    except Exception as exc:
        print(f"[weather] the place's time zone is unknown here ({type(exc).__name__}) - "
              f"estimated from its longitude")
        return None


def _local_offset_s(lon: float) -> int:
    """Only when place_zone() cannot answer (the lookup missing from a build):
    the longitude's hour (lon/15), unless this computer's own offset is within
    two hours of it (Istanbul's longitude says +2, its clock says +3). No
    summer time, and wrong for a neighbour in another zone."""
    guess = round(float(lon) / 15.0) * 3600
    try:
        here = int(datetime.now().astimezone().utcoffset().total_seconds())
    except Exception:
        here = guess
    return here if abs(here - guess) <= 2 * 3600 else guess


def _sun_times(lat: float, lon: float, day, offset_s: int) -> tuple[str, str]:
    """Sunrise and sunset ("HH:MM", local) by the NOAA formula - no network.
    ("", "") in polar day or night."""
    import math
    n = day.timetuple().tm_yday
    gamma = 2 * math.pi / 365 * (n - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
                       - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
            - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
            - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma))
    phi = math.radians(lat)
    cos_ha = (math.cos(math.radians(90.833)) / (math.cos(phi) * math.cos(decl))
              - math.tan(phi) * math.tan(decl))
    if not -1.0 <= cos_ha <= 1.0:
        return "", ""
    ha = math.degrees(math.acos(cos_ha))

    def clock(minutes_utc: float) -> str:
        m = (minutes_utc + offset_s / 60.0) % 1440
        return f"{int(m // 60):02d}:{int(m % 60):02d}"
    return clock(720 - 4 * (lon + ha) - eqtime), clock(720 - 4 * (lon - ha) - eqtime)


def forecast_days(city: str, days: int = 7) -> dict:
    """The weather for a place as plain data: now, every hour from local
    midnight today, and one row per day - from MET Norway, the place from
    OpenStreetMap. Times are the place's local "YYYY-MM-DDTHH:MM".

        {"place", "lat", "lon", "utc_offset_s", "source",
         "now":    {"time", "temp_c", "feels_c", "humidity", "wind_kmh",
                    "wind_from", "pressure_hpa", "symbol", "is_day",
                    "rain_mm_next_hour"},
         "hourly": [{"time", "temp_c", "rain_mm", "rain_pct"}],      # rain_pct may be None
         "daily":  [{"date", "symbol", "min_c", "max_c", "rain_mm", "rain_pct",
                     "sunrise", "sunset", "uv"}]}

    Symbols are MET's codes ("heavyrain", "partlycloudy"); the model and the
    panel name them. Raises with a short reason when the place or the
    service cannot be reached."""
    place = place_of(city)
    lat, lon = place["lat"], place["lon"]
    series = (_forecast(lat, lon).get("properties") or {}).get("timeseries") or []
    if not series:
        raise RuntimeError("the forecast came back empty")
    zone = place_zone(lat, lon) or timezone(timedelta(seconds=_local_offset_s(lon)))

    rows = []
    for r in series:
        when = datetime.fromisoformat(str(r["time"]).replace("Z", "+00:00")).astimezone(zone)
        d = r.get("data") or {}
        inst = (d.get("instant") or {}).get("details") or {}
        one, six = d.get("next_1_hours") or {}, d.get("next_6_hours") or {}
        block = one or six
        span = 1 if one else 6
        amount = ((block.get("details") or {}).get("precipitation_amount"))
        pct = ((block.get("details") or {}).get("probability_of_precipitation"))
        rows.append({"at": when, "inst": inst, "symbol": _symbol(block), "span": span,
                     "rain_mm": None if amount is None else float(amount) / span,
                     "rain_pct": pct, "raw_symbol": str(((block.get("summary") or {})
                                                          .get("symbol_code")) or "")})
    first = rows[0]
    fi = first["inst"]
    now = {"time": first["at"].strftime("%Y-%m-%dT%H:%M"),
           "temp_c": fi.get("air_temperature"),
           "feels_c": fi.get("apparent_air_temperature", fi.get("air_temperature")),
           "humidity": fi.get("relative_humidity"),
           "wind_kmh": None if fi.get("wind_speed") is None else round(float(fi["wind_speed"]) * 3.6),
           "wind_from": fi.get("wind_from_direction"),
           "pressure_hpa": fi.get("air_pressure_at_sea_level"),
           "symbol": first["symbol"],
           "is_day": not first["raw_symbol"].endswith(("_night", "_polartwilight")),
           "rain_mm_next_hour": first["rain_mm"]}

    # Every hour from local midnight today: temperature between MET's points
    # (hourly for ~2 days, then every 6 hours), rain spread over its block.
    midnight = first["at"].replace(hour=0, minute=0, second=0, microsecond=0)
    hourly = []
    for h in range(days * 24):
        at = midnight + timedelta(hours=h)
        before = [r for r in rows if r["at"] <= at and r["inst"].get("air_temperature") is not None]
        after = [r for r in rows if r["at"] >= at and r["inst"].get("air_temperature") is not None]
        if before and after:
            a, b = before[-1], after[0]
            ta, tb = float(a["inst"]["air_temperature"]), float(b["inst"]["air_temperature"])
            gap = (b["at"] - a["at"]).total_seconds()
            temp = ta if gap <= 0 else ta + (tb - ta) * (at - a["at"]).total_seconds() / gap
        elif after:
            temp = float(after[0]["inst"]["air_temperature"])
        elif before:
            temp = float(before[-1]["inst"]["air_temperature"])
        else:
            temp = None
        cover = [r for r in rows if r["at"] <= at < r["at"] + timedelta(hours=r["span"])]
        src = cover[-1] if cover else None
        hourly.append({"time": at.strftime("%Y-%m-%dT%H:%M"),
                       "temp_c": None if temp is None else round(temp, 1),
                       "rain_mm": None if src is None or src["rain_mm"] is None else round(src["rain_mm"], 2),
                       "rain_pct": None if src is None else src["rain_pct"]})

    daily = []
    for k in range(days):
        day = (midnight + timedelta(days=k)).date()
        on_day = [r for r in rows if r["at"].date() == day]
        temps = [float(r["inst"]["air_temperature"]) for r in on_day
                 if r["inst"].get("air_temperature") is not None]
        if not temps:
            continue
        rain = sum(h["rain_mm"] or 0.0 for h in hourly if h["time"].startswith(day.isoformat()))
        pcts = [r["rain_pct"] for r in on_day if r["rain_pct"] is not None]
        uvs = [float(r["inst"]["ultraviolet_index_clear_sky"]) for r in on_day
               if r["inst"].get("ultraviolet_index_clear_sky") is not None]
        # The day's weather: the symbol around midday, else the commonest.
        noon = min(on_day, key=lambda r: abs(r["at"].hour - 13))
        # That day's own offset: a forecast can run across a change of clocks.
        day_offset = int(datetime.combine(day, datetime.min.time().replace(hour=12), zone)
                         .utcoffset().total_seconds())
        rise, sett = _sun_times(lat, lon, day, day_offset)
        daily.append({"date": day.isoformat(), "symbol": noon["symbol"],
                      "min_c": round(min(temps), 1), "max_c": round(max(temps), 1),
                      "rain_mm": round(rain, 1), "rain_pct": round(max(pcts)) if pcts else None,
                      "sunrise": rise, "sunset": sett, "uv": round(max(uvs), 1) if uvs else None})
    if daily and daily[0]["sunrise"] and daily[0]["sunset"]:
        # A rain symbol has no day or night form: the sun decides.
        clock = now["time"][11:16]
        now["is_day"] = daily[0]["sunrise"] <= clock < daily[0]["sunset"]
    offset = int(datetime.now(zone).utcoffset().total_seconds())
    return {"place": place["name"], "lat": lat, "lon": lon, "utc_offset_s": offset,
            "zone": getattr(zone, "key", "") or "",
            "source": WEATHER_CREDIT, "now": now, "hourly": hourly, "daily": daily}
