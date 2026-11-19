"""
Live Panels — the computer's vitals and the weather as live panels on the
HUD, drawn where the avatar sits and moving in real time.

    "show me the system status"      → rings for CPU, RAM, GPU and disk, sampled
    "how is my computer doing?"        every second; per-core load, a 60-second
                                       history, network speed, the heaviest
                                       processes
    "what's the weather in Tokyo?"   → now, the next 24 hours and seven days; the
    "show me the forecast"             local clock ticks, rain or snow falls
                                       behind it, the icons move
    "close it" / "close the panel"   → the HUD goes back to the avatar

THE PANELS ARE LIVE (_live_panels_core.py)
    Not pictures: they repaint while they are open and answer the mouse.
    System    click a ring to graph that metric, HISTORY / CORES to switch the
              left panel, a process for its PID, threads and start time; hover
              the graph to read any second.
    Weather   click a day to see its hours and details; hover the curve to read
              any hour. The data refreshes every ten minutes.
    The × in the corner closes a panel. Anything else taking the centre — a
    video, the camera, a picture — closes it too.

INSTALL
    Drop live_panels.py and _live_panels_core.py into plugins/ and restart. Nothing
    else changes: no key, no account. Uses psutil and PyQt6, which the assistant already
    has; the weather comes from MET Norway (keyless, open data - CC BY 4.0, allowed
    in a paid product with its credit), the place from OpenStreetMap, through
    core/cloud: the same numbers the weather tool says. NVIDIA GPU load is read
    through NVML when the driver is there, and the GPU ring is left out otherwise.

NOTHING HERE KNOWS A LANGUAGE
    Results are facts — numbers and names as published — and the assistant says
    them in the user's language.
"""
from __future__ import annotations

import time
import types

# The weather panel reads MET Norway (open data, allowed in a paid product), so
# every edition offers it. (It read Open-Meteo before - non-commercial only -
# and the product had no weather panel.)
_WEATHER = True

PLUGIN = {
    # For the plugin manager (core/plugin_loader): never sent to a model.
    "level": "basic",
    # The weather and the computer's load shown as a panel - nothing risky.
    "starts_on": True,
    "label": "Live panels",
    # Inline, not behind use_feature (core/tool_budget): behind it the model
    # saw one line and answered computer and disk questions with the plain
    # numbers tool — measured, 2 of 12 opened the panel; inline, 11 of 12.
    "tier": "core",
    # Plain strings: the plugin list reads them without importing this file.
    "summary": ("Shows your computer's live load — CPU, memory, GPU, disk, network and the "
                "busiest programs — and the weather for any city, as panels you can click."),
    "setup": [
        "Say: \"Show me the system status\" or \"How is my computer doing?\"",
        "Say: \"Show me the weather in Istanbul\" - click a day to see its hours.",
        "Click a ring to graph it; click a program for its details.",
        "Say \"close it\" to go back.",
    ],
    "name": "live_panels",
    "brief": ("A live, clickable panel of the computer's load (CPU, RAM, GPU, disk, network, "
              "busiest processes) or of a city's weather."),
    "description": (
        "Shows a LIVE, interactive panel on the HUD and returns the facts behind it. "
        "action='system': the computer's live diagnostics — CPU, RAM, GPU, disk, network, "
        "per-core load and the heaviest processes. "
        + ("action='weather': the weather for a city — now, the next 24 hours and 7 days (pass "
           "'city'). " if _WEATHER else "")
        + "action='close': close the panel. "
        "Prefer this over system_status for any question about the computer's performance, "
        "load, memory, disk or temperature — it answers the same question AND shows the panel. "
        + ("Prefer it over weather_report for any weather, temperature, rain or forecast "
           "question. " if _WEATHER else "")
        + "Use action='close' when the user asks to close / hide the panel or dashboard."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "'system', 'weather' or 'close'",
            },
            "city": {
                "type": "STRING",
                "description": "City for action='weather', in any language",
            },
            "when": {
                "type": "STRING",
                "description": "What the user asked about for weather, e.g. 'today', 'tomorrow', 'weekend'",
            },
        },
        "required": ["action"],
    },
}


def _boards():
    try:
        from plugins import _live_panels_core as lb
    except ImportError:  # loaded outside the package
        import _live_panels_core as lb  # type: ignore
    return lb


def _log(player, text: str) -> None:
    # The log file only: the activity panel shows what the assistant says.
    print(f"[LivePanels] {text}")


def _hook_close(player, lb) -> None:
    """Make "close it" (the UI's stop_video) close the panels too — once, on this UI."""
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


# ─────────────────────────────────────────────────────────────────────────────
# Actions
# ─────────────────────────────────────────────────────────────────────────────

def _on_cover(kind: str, **options) -> str:
    """Whatever covers the screen and shows panels itself (the Holo Desk,
    core/situation.show_panel): its name when it showed this one, else ""."""
    try:
        from core import situation
        return situation.show_panel(kind, **options) if hasattr(situation, "show_panel") else ""
    except ImportError:                     # an older host (Jarvis)
        return ""


def _where(cover: str) -> str:
    return f"on the {cover}" if cover else "on the HUD"


def _system(player, lb) -> str:
    import psutil

    cover = _on_cover("system")
    shown = bool(cover) or (bool(player) and lb.show_system(player))
    psutil.cpu_percent(None)
    time.sleep(0.4)
    cpu = psutil.cpu_percent(None)
    vm = psutil.virtual_memory()
    du = psutil.disk_usage(lb.system_drive())
    gpu = lb.gpu_load()
    up = time.time() - psutil.boot_time()
    facts = (
        f"CPU {cpu:.0f}%, RAM {vm.percent:.0f}% ({vm.used / 1024 ** 3:.1f} of "
        f"{vm.total / 1024 ** 3:.1f} GB), "
        + (f"GPU {gpu:.0f}%, " if gpu is not None else "")
        + f"disk {du.percent:.0f}% full ({du.free / 1024 ** 3:.0f} GB free), "
        f"uptime {int(up // 3600)}h {int(up % 3600 // 60)}m, {len(psutil.pids())} processes. "
        "The busiest processes are listed on the panel."
    )
    _log(player, f"System: CPU {cpu:.0f}%, RAM {vm.percent:.0f}%")
    return ((f"A live diagnostics panel is now {_where(cover)} (it updates every second). "
             if shown else "The panel could not be shown. ")
            + facts + " Answer briefly in the user's language; mention anything near its limit.")


def _weather(player, lb, city: str, when: str) -> str:
    if not city:
        try:                                # the city asked about last, else the user's own
            from actions import weather_report
            city = (weather_report.last_city() if hasattr(weather_report, "last_city")
                    else weather_report._remembered_city())
        except Exception:
            city = ""
    if not city:
        return "status=ask question=which city the user wants the weather for"
    try:
        place, fc = lb.weather_for(city)
    except Exception as e:
        return (f"status=failed reason=the weather for {city} could not be fetched "
                f"({str(e) or type(e).__name__}); say so - do not guess the weather")
    cover = _on_cover("weather", city=city)
    shown = bool(cover) or (bool(player) and lb.show_weather(player, place, fc))
    try:
        from actions import weather_report
        weather_report.remember_asked(city)
    except Exception:
        pass
    _log(player, f"Weather for {place.get('name')}: {fc['current']['temperature_2m']}°C"
         + (f" (on the {cover})" if cover else ""))
    try:
        from actions.weather_report import facts_for
        facts = facts_for(city, when)          # the same numbers, cached a moment ago
    except Exception as e:
        facts = f"(the facts could not be written: {type(e).__name__})"
    return ((f"A live weather panel is now {_where(cover)}. " if shown
             else "The panel could not be shown. ") + facts)


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = str(parameters.get("action") or "").strip().lower()
    city = str(parameters.get("city") or "").strip()
    when = str(parameters.get("when") or "").strip()
    if not action:
        action = "weather" if city else "system"
    try:
        lb = _boards()
        _hook_close(player, lb)
        if action in ("close", "hide", "stop"):
            lb.close_all()
            return "status=done closed=live panel"
        if action in ("weather", "forecast"):
            if not _WEATHER:
                return ("status=failed reason=this edition shows no weather panel. Answer the "
                        "weather question with the weather tool instead.")
            return _weather(player, lb, city, when)
        return _system(player, lb)
    except Exception as e:
        return f"status=failed reason=live_panels: {type(e).__name__}: {e}"
