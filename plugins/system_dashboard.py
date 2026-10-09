"""Live system diagnostics panel for the JARVIS HUD.

This plugin deliberately uses the same shared live-panel framework as the
weather and trading dashboards.  It prevents visual drift and keeps all Qt
work on the GUI thread while telemetry collection stays in background workers.
"""

from __future__ import annotations

try:
    from plugins import _live_panels_core as _live_panels
except ImportError:  # Allows direct execution while developing the plugin.
    import _live_panels_core as _live_panels  # type: ignore


PLUGIN = {
    "name": "system_dashboard",
    "description": (
        "Open a polished live system dashboard in the JARVIS HUD. Use it when "
        "the user asks to see system performance, CPU, memory, GPU, disk, "
        "network traffic, battery, cores, or running processes visually. "
        "The dashboard has clickable metric rings, history/core views, and "
        "process details."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["open", "close", "toggle", "status"],
                "description": "open (default), close, toggle, or check the dashboard.",
            }
        },
        "required": [],
    },
    "behavior": "NON_BLOCKING",
    "scheduling": "SILENT",
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Queue the common live SystemBoard without blocking the assistant."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "open").strip().lower()

    if action not in {"open", "close", "toggle", "status"}:
        return "System dashboard action must be open, close, toggle, or status."

    try:
        is_open = _live_panels.panel_status("system")
        if action == "status":
            return (
                "System dashboard is open."
                if is_open
                else "System dashboard is closed."
            )

        if action == "close":
            return (
                "System dashboard closed."
                if _live_panels.close_panel("system")
                else "The system dashboard is not open."
            )

        if action == "toggle" and is_open:
            _live_panels.close_panel("system")
            return "System dashboard closed."

        if player is None:
            return "The system dashboard needs the JARVIS HUD to be running."
        compact_state = getattr(
            player, "is_compact_mode", getattr(player, "is_mini", False)
        )
        if compact_state() if callable(compact_state) else bool(compact_state):
            return "System dashboard is unavailable in Mini Orb or Top Dock mode. Expand JARVIS with F9 or the FULL button first."
        if not _live_panels.open_panel(player, "system", _live_panels.SystemBoard):
            return "I couldn't open the system dashboard."
        return "Live system dashboard opened on the HUD."
    except Exception as exc:
        return f"I couldn't control the system dashboard: {exc}"
