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
    """Fetch a forecast and queue the WeatherBoard on the HUD."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "open").strip().lower()

    if action == "close":
        try:
            closed = _live_panels.close_weather()
        except Exception as exc:
            return f"I couldn't close the weather dashboard: {exc}"
        return "Weather dashboard closed." if closed else "The weather dashboard is not open."

    if action not in {"open", "refresh"}:
        return "Weather dashboard action must be open, refresh, or close."

    city = str(params.get("city") or "").strip()
    if not city:
        return "Which city should I show in the weather dashboard?"
    if player is None:
        return "The weather dashboard needs the JARVIS HUD to be running."
    compact_state = getattr(player, "is_compact_mode", getattr(player, "is_mini", False))
    if compact_state() if callable(compact_state) else bool(compact_state):
        return "Weather dashboard is unavailable in Mini Orb or Top Dock mode. Expand JARVIS with F9 or the FULL button first."

    try:
        place = _live_panels.geocode(city)
        if not place:
            return f"I couldn't find a location called '{city}'. Please try a city and country."

        forecast = _live_panels.forecast(place["latitude"], place["longitude"])
        if not forecast.get("current") or not forecast.get("daily"):
            return f"I couldn't retrieve a complete forecast for {_place_label(place) or city}."

        if not _live_panels.open_panel(
            player,
            "weather",
            _live_panels.WeatherBoard,
            lambda board: board.set_data(place, forecast),
        ):
            return "I got the forecast, but couldn't open the weather dashboard."

        current = forecast["current"]
        temperature = round(float(current.get("temperature_2m", 0)))
        condition = _live_panels._WMO.get(
            current.get("weather_code", 0), ("weather conditions", "cloud")
        )[0].lower()
        return (
            f"Weather dashboard opened for {_place_label(place) or city}: "
            f"{temperature}°, {condition}."
        )
    except Exception as exc:
        return f"I couldn't load the weather for {city}: {exc}"
