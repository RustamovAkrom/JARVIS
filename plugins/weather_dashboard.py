"""A visual, live weather dashboard for the JARVIS HUD.

Uses Open-Meteo's public geocoding and forecast endpoints through the shared
live-panel module.  No API key is needed and all Qt work stays in that module's
GUI-thread bridge.
"""

from __future__ import annotations

try:
    from plugins import _live_panels_core as _live_panels
except ImportError:  # Allows direct execution during plugin development.
    import _live_panels_core as _live_panels  # type: ignore


PLUGIN = {
    "name": "weather_dashboard",
    "description": (
        "Open a beautiful live weather dashboard in the JARVIS HUD for a city. "
        "Use this when the user asks to see, show, open, or visualize weather, "
        "a forecast, temperature, rain, wind, or a weather dashboard. "
        "It shows current conditions, the next 24 hours, and a seven-day forecast."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "city": {
                "type": "STRING",
                "description": "City or place to show, e.g. Tashkent or London.",
            },
            "action": {
                "type": "STRING",
                "enum": ["open", "refresh", "close"],
                "description": "open (default), refresh data for a city, or close the weather dashboard.",
            },
        },
        "required": [],
    },
    "behavior": "NON_BLOCKING",
    "scheduling": "SILENT",
}


def _place_label(place: dict) -> str:
    return ", ".join(
        part
        for part in (place.get("name"), place.get("admin1"), place.get("country"))
        if part
    )


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Fetch weather using the shared MET Norway pipeline and open the HUD board."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "open").strip().lower()

    if action == "close":
        try:
            _live_panels.close_all()
            return "Weather dashboard closed."
        except Exception as exc:
            return f"I couldn't close the weather dashboard: {type(exc).__name__}: {exc}"

    if action not in {"open", "refresh"}:
        return "Weather dashboard action must be open, refresh, or close."

    city = str(params.get("city") or "").strip()
    if not city:
        return "Which city should I show in the weather dashboard?"
    if player is None:
        return "The weather dashboard needs the JARVIS HUD to be running."

    compact_state = getattr(
        player, "is_compact_mode", getattr(player, "is_mini", False)
    )
    if compact_state() if callable(compact_state) else bool(compact_state):
        return (
            "Weather dashboard is unavailable in Mini Orb or Top Dock mode. "
            "Expand JARVIS with F9 or the FULL button first."
        )

    try:
        # The core API is weather_for(city), not the old Open-Meteo
        # geocode()/forecast() API. It uses the shared MET Norway pipeline.
        place, forecast = _live_panels.weather_for(city)
        current = forecast.get("current") or {}
        daily = forecast.get("daily") or {}
        if not current or not daily.get("time"):
            return f"I couldn't retrieve a complete forecast for {city}."

        opened = _live_panels.show_weather(player, place, forecast)
        if not opened:
            return f"I retrieved the forecast for {city}, but couldn't open the dashboard."

        temperature = round(float(current.get("temperature_2m", 0)))
        condition = _live_panels._WMO.get(
            current.get("weather_code", 0), ("weather conditions", "cloud")
        )[0].lower()
        label = _place_label(place) or city
        return f"Weather dashboard opened for {label}: {temperature}°C, {condition}."
    except Exception as exc:
        return f"I couldn't load the weather for {city}: {type(exc).__name__}: {exc}"
