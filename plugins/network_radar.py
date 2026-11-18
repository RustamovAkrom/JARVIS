"""
Network Radar — who is on the network and how good the connection is, as a
live radar on the HUD.

    "who is on my Wi-Fi?"                 → every device on the network appears on
    "scan my network"                       a radar: phones, TVs, printers, smart
                                            plugs… with maker, address and latency
    "is there anyone I don't know?"       → devices never seen before are NEW
    "how fast is my internet?"            → a speed test: download, upload, ping,
                                            and ping while the line is busy
    "that new one is my TV"               → the device is named and trusted
    "warn me if someone joins my network" → guard: a quiet check every few minutes,
                                            and JARVIS says when a stranger joins
    "close it"                            → the HUD goes back

THE RADAR IS LIVE (_network_radar_core.py)
    This computer is in the middle; a device sits as far out as it is slow to
    answer. The sweep lights each one as it passes; a NEW device pulses. Click a
    device (on the radar or in the list) for its details — TRUST, OPEN WEB (its
    own page, e.g. the router's settings), COPY IP. LATENCY graphs the router
    and the internet second by second; SPEED TEST runs the test. While the
    radar is open it sweeps quietly every minute, so a device that joins pops
    up on its own. Nothing on any device is changed.

INSTALL
    Drop network_radar.py and _network_radar_core.py into plugins/ next to Live
    Panels' _live_panels_core.py, and restart. No key, no account, no new
    package: discovery uses Python's standard library (and Windows' own network
    functions), the radar PyQt6. The makers' list (Wireshark's public "manuf"
    file, ~3 MB) is downloaded once into memory/. The speed test uses
    Cloudflare's public speed servers; the provider's name comes from ipinfo.io.

NOTHING HERE KNOWS A LANGUAGE
    Results are facts — names, makers and numbers — and the assistant says them
    in the user's language.
"""
from __future__ import annotations

import threading
import time
import types

PLUGIN = {
    "name": "network_radar",
    "description": (
        "Shows a LIVE, clickable network radar on the HUD and returns the facts behind it. "
        "action='scan': find every device on the user's network / Wi-Fi (phones, computers, TVs, "
        "printers, consoles, smart-home plugs, cameras) with its name, type, maker, IP address and "
        "latency; flags devices never seen on this network before as NEW; also reports the "
        "connection (Wi-Fi name and signal or Ethernet, router, internet provider, latency). "
        "action='speedtest': internet speed test — download, upload, ping, jitter and ping under "
        "load. action='device': show one device's details ('device' = its name, IP, maker or type). "
        "action='name': give a device the name the user says ('device' + 'name', e.g. device="
        "'192.168.1.34', name='Living room TV'); this also marks it trusted. action='trust': stop "
        "flagging a device as new ('device', or 'all'). action='forget': remove a device from "
        "memory. action='guard': new-device alerts on/off ('on'): while on, the network is checked "
        "every few minutes and the user is told when an unknown device joins. action='close': close "
        "the radar. Use this for any question about the network, Wi-Fi, who or what is connected, "
        "an unknown device, the internet speed, ping, lag or a slow connection. An unknown device "
        "is not proof of an intruder: say what it looks like and let the user decide."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "'scan', 'speedtest', 'device', 'name', 'trust', 'forget', 'guard' or 'close'",
            },
            "device": {
                "type": "STRING",
                "description": "For device/name/trust/forget: the device's IP, its current name, maker or type; 'all' or 'new' for trust",
            },
            "name": {
                "type": "STRING",
                "description": "For 'name': the name the user gives the device",
            },
            "on": {
                "type": "BOOLEAN",
                "description": "For 'guard': true to turn new-device alerts on, false to turn them off",
            },
            "fresh": {
                "type": "BOOLEAN",
                "description": "For 'scan': true to scan again even if a scan just finished",
            },
        },
        "required": ["action"],
    },
}

_SCAN_WAIT_S = 12       # answer with the facts if the scan is this quick, report later otherwise
_SPEED_WAIT_S = 24
_REUSE_S = 60           # a full scan this recent is reused
_GUARD_S = 180          # guard: one quiet sweep this often while the radar is closed

_player = None
_guard: dict = {"stop": None}


def _log(player, text: str) -> None:
    try:
        print(f"[NetworkRadar] {text}")
    except Exception:                       # a console that cannot show a device's name
        pass
    if player:
        try:
            player.write_log(f"JARVIS: {text}")
        except Exception:
            pass


_NEEDS_LIVE_PANELS = (
    "Network Radar needs the Live Panels plugin, which is not installed. Download Live Panels "
    "from the Academy, put _live_panels_core.py into the plugins folder and restart JARVIS."
)


def _has_live_panels() -> bool:
    """The radar is drawn in Live Panels' frame; checked by file so the user hears what to
    install instead of an import error."""
    from pathlib import Path
    return Path(__file__).with_name("_live_panels_core.py").is_file()


def _load():
    try:
        from plugins import _live_panels_core as lb
        from plugins import _network_radar_core as nr
    except ImportError:  # loaded outside the package
        import _live_panels_core as lb  # type: ignore
        import _network_radar_core as nr  # type: ignore
    return lb, nr


def _hook_close(player, lb) -> None:
    """Make "close it" (the UI's stop_video) close the panels too — once, on this UI.
    The same flag as Live Panels', so the plugins never wrap it twice."""
    if player is None or getattr(player, "_live_panels_hooked", False):
        return
    original = getattr(player, "stop_video", None)
    if not callable(original):
        return

    def stop_video(self, *a, **kw):
        try:
            lb.close_all()
        except Exception:
            pass
        return original(*a, **kw)

    try:
        player.stop_video = types.MethodType(stop_video, player)
        player._live_panels_hooked = True
    except Exception:
        pass


def _say(text: str) -> None:
    say = getattr(_player, "request_say", None)
    if callable(say):
        try:
            say(text)
        except Exception:
            pass


# ── new-device alerts ────────────────────────────────────────────────────────

def _announce(nr, devs: list) -> None:
    """A sweep the user did not ask for found devices never seen here before."""
    bits = []
    for d in devs[:4]:
        kind = nr.classify(d)
        name = nr.display_name(d, kind)
        maker = d.get("vendor") or ("a private, randomised hardware address" if d.get("private")
                                    else "unknown maker")
        bits.append(f"{name} (looks like a {nr.TYPES[kind][0].lower()}, {maker}, {d.get('ip')})")
    _log(_player, "New device on the network: " + "; ".join(bits))
    _say("[Network Radar] A device that has never been on this network before has just joined: "
         + "; ".join(bits) + ". Tell the user briefly, in their language. It is not necessarily "
         "a threat (a guest's phone, a new gadget); they can say it is theirs to name and trust "
         "it. Do not call any tool for this.")


def _guard_loop(nr, stop: threading.Event) -> None:
    while not stop.wait(_GUARD_S):
        try:
            if not nr.visible_now():          # the open radar sweeps by itself
                nr.start_scan("auto", light=True)
        except Exception as e:
            print(f"[NetworkRadar] guard: {e}")


def _guard_on(nr) -> None:
    stop = _guard.get("stop")
    if stop is not None and not stop.is_set():
        return
    stop = _guard["stop"] = threading.Event()
    threading.Thread(target=_guard_loop, args=(nr, stop), name="net-radar-guard", daemon=True).start()


def _guard_off() -> None:
    stop = _guard.get("stop")
    if stop is not None:
        stop.set()


# ── finding the device the user means ───────────────────────────────────────

_TYPE_WORDS = {
    "router": ("router", "modem", "gateway", "yönlendirici", "yonlendirici"),
    "pc": ("computer", "pc", "desktop", "bilgisayar", "masaüstü"),
    "laptop": ("laptop", "notebook", "dizüstü", "dizustu"),
    "phone": ("phone", "telefon", "mobile", "cep"),
    "tablet": ("tablet", "ipad"),
    "tv": ("tv", "television", "televizyon", "chromecast"),
    "speaker": ("speaker", "hoparlör", "hoparlor"),
    "printer": ("printer", "yazıcı", "yazici"),
    "console": ("console", "konsol", "playstation", "xbox"),
    "camera": ("camera", "kamera"),
    "iot": ("plug", "priz", "smart", "akıllı", "akilli", "bulb", "ampul", "lamp", "lamba"),
}


def _find(nr, query: str, online_only: bool = False) -> list:
    q = str(query or "").strip().strip("\"'").casefold()
    rows = [r for r in nr.NET.rows() if r["online"] or not online_only]
    if not q:
        return []
    if q in ("new", "yeni", "unknown", "bilinmeyen", "yabancı", "yabanci", "that", "it", "bu", "o"):
        return [r for r in rows if nr.NET.is_new(r)]
    for test in (lambda r: r.get("ip") == q or r.get("mac", "").casefold() == q,
                 lambda r: r["name"].casefold() == q,
                 lambda r: q in r["name"].casefold(),
                 lambda r: q in (r.get("vendor") or "").casefold()):
        hit = [r for r in rows if test(r)]
        if hit:
            return hit
    if q.isdigit():                                       # "the one ending in 34"
        hit = [r for r in rows if r.get("ip", "").endswith("." + q)]
        if hit:
            return hit
    for kind, words in _TYPE_WORDS.items():
        if any(w in q for w in words):
            hit = [r for r in rows if r["kind"] == kind or (kind == "pc" and r["kind"] == "laptop")]
            if hit:
                return hit
    return []


def _one(nr, query: str, online_only: bool = False):
    """(row, None) or (None, the answer to give)."""
    if not query:
        return None, "status=ask question=which device the user means (name, IP, maker or type)"
    hits = _find(nr, query, online_only)
    if not hits:
        return None, (f"status=failed reason=no device matches '{query}'. Devices: "
                      + "; ".join(f"{r['name']} ({r['ip']})" for r in nr.NET.rows()[:20]))
    if len(hits) > 1 and query.strip().casefold() not in ("new", "yeni"):
        return None, ("status=ask question=which one the user means: "
                      + "; ".join(f"{r['name']} ({nr.TYPES[r['kind']][0].lower()}, "
                                  f"{r.get('vendor') or 'unknown maker'}, {r['ip']})" for r in hits[:6]))
    return hits[0], None


def _ensure_scan(nr, wait: float) -> None:
    """Make sure there is something to look at: scan if this session has not."""
    with nr.NET.lock:
        empty = not nr.NET.devs
        running = nr.NET.scan["state"] == "running"
    if empty and not running:
        nr.start_scan("tool")
    if empty or running:
        nr.NET.scan_done.wait(wait)


# ── actions ─────────────────────────────────────────────────────────────────

def _scan(player, nr, fresh: bool) -> str:
    shown = bool(player) and nr.show(player)
    with nr.NET.lock:
        sc = dict(nr.NET.scan)
        have = bool(nr.NET.devs)
    recent = (sc["state"] == "done" and not sc.get("light")
              and time.time() - sc.get("t_end", 0) < _REUSE_S)
    if fresh or not (recent and have):
        nr.start_scan("tool")
    _log(player, "Network radar: sweeping the network")
    lead = ("A live network radar is now on the HUD (the user can click any device). "
            if shown else "The radar could not be shown. ")
    if nr.NET.scan_done.wait(_SCAN_WAIT_S):
        return (lead + nr.describe() + " Answer briefly in the user's language: how many devices, "
                "anything NEW or unrecognised, and anything notable about the connection. Do not "
                "read out IP or hardware addresses unless asked.")

    def report() -> None:
        if not nr.visible_now():
            return                                  # closed meanwhile: the result waits on the radar
        _say("[Network Radar] The network scan has finished. " + nr.describe()
             + " Tell the user briefly, in their language, how many devices there are and anything "
               "new. Do not call any tool for this.")
    with nr.NET.lock:
        nr.NET.on_scan_done.append(report)
        found = nr.NET.scan.get("found", 0)
    return (lead + f"status=running The sweep is still going ({found} devices so far); the radar "
            "fills in live and the result will be reported. Tell the user briefly it is underway.")


def _speed(player, nr) -> str:
    shown = bool(player) and nr.show(player, tab="speed")
    with nr.NET.lock:
        running = nr.NET.speed.get("state") == "running"
    if not running:
        nr.start_speed()
    _log(player, "Network radar: speed test")
    lead = "The speed test is running live on the HUD. " if shown else ""
    if nr.NET.speed_done.wait(_SPEED_WAIT_S):
        return (lead + nr.describe_speed() + " Answer briefly in the user's language; say what the "
                "numbers mean in everyday terms (streaming, calls, games, downloads).")

    def report() -> None:
        _say("[Network Radar] The speed test has finished. " + nr.describe_speed()
             + " Tell the user briefly, in their language. Do not call any tool for this.")
    with nr.NET.lock:
        if nr.NET.speed.get("state") == "running":
            nr.NET.on_speed_done.append(report)
            return lead + "status=running The speed test is still running; the result will be reported."
    return lead + nr.describe_speed()


def run(parameters: dict, player=None, session_memory=None) -> str:
    global _player
    action = str(parameters.get("action") or "scan").strip().lower()
    device = str(parameters.get("device") or "").strip()
    name = str(parameters.get("name") or "").strip()
    fresh = str(parameters.get("fresh") or "").strip().lower() in ("true", "1", "yes")
    on_raw = parameters.get("on")
    if not _has_live_panels():
        _log(player, _NEEDS_LIVE_PANELS)
        return f"status=failed reason={_NEEDS_LIVE_PANELS} Tell the user exactly this."
    try:
        lb, nr = _load()
    except ImportError as e:
        return f"status=failed reason={_NEEDS_LIVE_PANELS} ({e})"
    try:
        if player is not None:
            _player = player
        nr.ANNOUNCE = lambda devs: _announce(nr, devs)
        _hook_close(player, lb)
        if nr.NET.store.watch:
            _guard_on(nr)

        if action in ("close", "hide", "stop"):
            lb.close_all()
            return "status=done closed=network radar"

        if action in ("speedtest", "speed", "speed_test", "test"):
            return _speed(player, nr)

        if action == "guard":
            on = on_raw if isinstance(on_raw, bool) else str(on_raw).strip().lower() not in (
                "false", "0", "no", "off", "")
            nr.NET.store.watch = on
            if on:
                _guard_on(nr)
                _ensure_scan(nr, _SCAN_WAIT_S)
                return ("status=done Guard is ON: the network is checked every few minutes (and every "
                        "minute while the radar is open) and the user is told when a device that has "
                        "never been here joins. It stays on after a restart and resumes the first "
                        "time the radar is used.")
            _guard_off()
            return "status=done Guard is OFF: no more new-device alerts."

        if action in ("device", "select", "focus", "show_device", "name", "rename", "trust",
                      "forget", "delete", "remove"):
            _ensure_scan(nr, _SCAN_WAIT_S)

        if action in ("device", "select", "focus", "show_device"):
            row, err = _one(nr, device, online_only=True)
            if err:
                return err
            if player:
                nr.show(player, tab="dev")
            nr.select(row["ip"])
            nr.set_tab("dev")
            facts = [f"{row['name']}: {nr.TYPES[row['kind']][0].lower()}",
                     f"maker {row.get('vendor') or 'unknown'}", f"IP {row['ip']}",
                     f"hardware address {row.get('mac', '?')}" + (" (private/randomised)" if row.get("private") else ""),
                     f"latency {nr.fmt_ms(row.get('lat'))}"]
            if row.get("services"):
                facts.append("services: " + ", ".join(row["services"]))
            if row.get("first"):
                facts.append(f"first seen {nr.ago(row['first'])}")
            facts.append("NEW / not trusted" if nr.NET.is_new(row) else "trusted")
            return "status=done Showing on the radar: " + "; ".join(facts) + "."

        if action in ("name", "rename"):
            if not name:
                return "status=ask question=what name the user wants to give the device"
            row, err = _one(nr, device or "new")
            if err:
                return err
            if not nr.NET.set_name(row["mac"], name):
                return "status=failed reason=the device is not in this network's memory yet; scan first"
            return f"status=done The device at {row['ip']} ({row['name']}) is now called '{name}' and trusted."

        if action == "trust":
            q = device.casefold()
            if q in ("all", "hepsi", "tümü", "tumu", "everything", "") or q in ("new", "yeni"):
                rows = [r for r in nr.NET.rows() if nr.NET.is_new(r)]
            else:
                row, err = _one(nr, device)
                if err:
                    return err
                rows = [row]
            n = nr.NET.trust([r["mac"] for r in rows if r.get("mac")])
            return f"status=done {n} device(s) are now trusted: " + "; ".join(r["name"] for r in rows) + "."

        if action in ("forget", "delete", "remove"):
            row, err = _one(nr, device)
            if err:
                return err
            nr.NET.forget(row["mac"])
            return f"status=done {row['name']} was removed from memory; it counts as new if it comes back."

        return _scan(player, nr, fresh)
    except Exception as e:
        return f"status=failed reason=network_radar: {type(e).__name__}: {e}"
