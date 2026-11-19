"""Optional plugin runtime boundary.

The core imports this module, but the plugin loader is imported only when the
user has enabled the optional plugin subsystem. A plugin discovery failure
returns a no-op registry and must never prevent JARVIS from starting.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class DisabledPluginRegistry:
    reason: str = "disabled"

    def get_tool_declarations(self) -> list[dict]:
        return []

    def has(self, name: str) -> bool:
        return False

    def scheduling(self, name: str):
        return None

    def run(self, name: str, parameters: dict, player=None, session_memory=None) -> str:
        return f"Plugin '{name}' is unavailable ({self.reason})."

    def settings_schemas(self) -> list[dict]:
        return []

    def list_for_ui(self) -> list[dict]:
        return []

    def start_enabled_plugins(self, player=None) -> None:
        return None


def load_plugin_registry(
    *,
    enabled: bool,
    plugins_dir,
    user_plugins_dir=None,
    core_tool_names: set[str] | None = None,
    logger: Callable[[str], None] = print,
    notify: Callable[[str], None] | None = None,
):
    """Load optional plugins safely; core remains usable on every failure."""
    if not enabled:
        logger("[Plugins] Optional plugin subsystem disabled by settings.")
        return DisabledPluginRegistry("disabled by settings")

    try:
        # Deliberately lazy: core startup does not import the plugin subsystem
        # when the user has disabled it.
        from core.plugin_loader import discover_plugins

        return discover_plugins(
            plugins_dir=plugins_dir,
            user_plugins_dir=user_plugins_dir,
            core_tool_names=core_tool_names or set(),
            logger=logger,
            notify=notify,
        )
    except Exception as exc:
        logger(f"[Plugins] Optional subsystem failed: {type(exc).__name__}: {exc}")
        if notify:
            try:
                notify("Optional plugin subsystem failed to initialize. Core features remain available.")
            except Exception:
                pass
        return DisabledPluginRegistry(f"initialization failed: {type(exc).__name__}")
