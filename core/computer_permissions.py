"""Central policy for computer-action aliases and capability requirements.

Keep action normalization and permissions in one place so the application
dispatcher and the action implementation cannot drift apart.
"""

from __future__ import annotations

COMPUTER_CONTROL_ALIASES = {
    "quit_app": "close_app",
    "close_application": "close_app",
}

COMPUTER_CONTROL_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "screenshot": ("observe_screen",),
    "screen_find": ("find_elements",),
    "focus_window": ("find_elements",),
    "move": ("move_mouse",),
    "scroll": ("move_mouse",),
    "drag": ("move_mouse", "click"),
    "click": ("click",),
    "left_click": ("click",),
    "double_click": ("click",),
    "right_click": ("click",),
    "screen_click": ("find_elements", "click"),
    "close_app": ("click",),
    "copy": ("clipboard",),
    "type": ("type",),
    "smart_type": ("type",),
    "paste": ("type", "clipboard"),
    "hotkey": ("type",),
    "press": ("type",),
    "clear_field": ("type",),
}

# Legacy computer_settings actions are retained for compatibility. Every
# keyboard-driven reload spelling must require the same explicit capability.
COMPUTER_SETTINGS_PERMISSIONS: dict[str, str | tuple[str, ...]] = {
    "type_text": ("type", "clipboard"),
    "write_on_screen": ("type", "clipboard"),
    "type": "type",
    "write": "type",
    "press_key": "type",
    "enter": "type",
    "escape": "type",
    "volume_up": "type",
    "volume_down": "type",
    "mute": "type",
    "unmute": "type",
    "toggle_mute": "type",
    "brightness_up": "type",
    "brightness_down": "type",
    "sleep_display": "type",
    "screen_off": "type",
    "pause_video": "type",
    "play_pause": "type",
    "full_screen": "type",
    "fullscreen": "type",
    "minimize": "type",
    "maximize": "type",
    "snap_left": "type",
    "snap_right": "type",
    "switch_window": "type",
    "show_desktop": "type",
    "task_manager": "type",
    "focus_search": "type",
    "refresh_page": "type",
    "reload": "type",
    "reload_n": "type",
    "refresh_n": "type",
    "reload_page_n": "type",
    "close_tab": "type",
    "new_tab": "type",
    "next_tab": "type",
    "prev_tab": "type",
    "go_back": "type",
    "go_forward": "type",
    "zoom_in": "type",
    "zoom_out": "type",
    "zoom_reset": "type",
    "find_on_page": "type",
    "undo": "type",
    "redo": "type",
    "select_all": "type",
    "save": "type",
    "lock_screen": "type",
    "open_settings": "type",
    "file_explorer": "type",
    "open_run": "type",
    "dark_mode": "type",
    "toggle_wifi": "type",
    "restart": "type",
    "shutdown": "type",
    "paste": ("type", "clipboard"),
    "cut": ("type", "clipboard"),
    "copy": "clipboard",
    "screenshot": "observe_screen",
    "scroll_up": "move_mouse",
    "scroll_down": "move_mouse",
    "scroll_top": "move_mouse",
    "scroll_bottom": "move_mouse",
    "page_up": "move_mouse",
    "page_down": "move_mouse",
    "close_app": "click",
    "close_application": "click",
    "close_window": "click",
}


def normalize_computer_action(tool_name: str, action: str) -> str:
    """Return the canonical action name before permissions and dispatch."""
    value = str(action or "").strip().lower().replace("-", "_").replace(" ", "_")
    if tool_name == "computer_control":
        return COMPUTER_CONTROL_ALIASES.get(value, value)
    if tool_name == "computer_settings":
        return {"close_application": "close_app"}.get(value, value)
    return value


def required_permissions_for_action(
    tool_name: str,
    action: str,
) -> tuple[str, ...]:
    """Return the capabilities required by one canonical action."""
    value = normalize_computer_action(tool_name, action)
    if tool_name == "computer_control":
        return COMPUTER_CONTROL_PERMISSIONS.get(value, ())
    if tool_name == "computer_settings":
        configured = COMPUTER_SETTINGS_PERMISSIONS.get(value, ())
        if isinstance(configured, tuple):
            return configured
        return (configured,) if configured else ()
    return ()
