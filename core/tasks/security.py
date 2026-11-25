"""Fail-closed authorization for persistent background Task Engine tools."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""


SAFE_TASK_TOOLS = frozenset({
    "search_web", "web_search", "fetch_page", "notes",
    "write_file", "file_processor", "recall_memory",
})

COMPUTER_TOOL_PERMISSIONS: Mapping[str, Mapping[str, tuple[str, ...]]] = {
    "computer_control": {
        "screenshot": ("observe_screen",),
        "screen_find": ("find_elements",),
        "focus_window": ("find_elements",),
        "move": ("move_mouse",),
        "scroll": ("move_mouse",),
        "drag": ("move_mouse", "click"),
        "click": ("click",),
        "double_click": ("click",),
        "right_click": ("click",),
        "screen_click": ("find_elements", "click"),
        "type": ("type",),
        "smart_type": ("type",),
        "press": ("type",),
        "hotkey": ("type",),
        "copy": ("clipboard",),
        "paste": ("type", "clipboard"),
        "clear_field": ("type",),
        "close_app": ("click",),
    },
}


def authorize_task_tool(name: str, args: dict | None = None) -> Decision:
    """Authorize each call against live JARVIS permissions. Unknown calls deny."""
    if name in SAFE_TASK_TOOLS:
        return Decision(True)

    action_map = COMPUTER_TOOL_PERMISSIONS.get(name)
    if action_map is None:
        return Decision(False, f"Tool '{name}' is not approved for background tasks.")

    action = str((args or {}).get("action") or "").strip().lower().replace("-", "_")
    required = action_map.get(action)
    if required is None:
        return Decision(False, f"Unknown or unsupported computer action '{action}'.")

    try:
        from memory.config_manager import get_computer_access_settings, get_computer_permissions
        access = get_computer_access_settings()
        grants = get_computer_permissions()
    except Exception:
        return Decision(False, "Permission settings unavailable; denied by default.")

    if access.get("mode") != "agent":
        return Decision(False, "Computer control is disabled in JARVIS Agent mode.")
    if access.get("access_level") != "full":
        return Decision(False, "This action requires temporary Full Access. Background approval requests are not yet supported.")
    missing = [cap for cap in required if not bool(grants.get(cap, False))]
    if missing:
        return Decision(False, "Denied by JARVIS Computer Permissions: " + ", ".join(missing))
    return Decision(True)


def task_tool_allowed_at_profile_load(name: str) -> bool:
    """Profile allowlist. Per-call runtime authorization is still required."""
    return name in SAFE_TASK_TOOLS
