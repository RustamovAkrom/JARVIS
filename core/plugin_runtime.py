
"""Failure-safe boundary around the optional plugin subsystem.

Core code imports this module, never the plugin loader directly. The loader is
imported lazily and all discovery/startup failures are converted into an
unavailable registry, allowing built-in actions and the rest of JARVIS to run.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class PluginSubsystemStatus:
    enabled: bool = True
    available: bool = True
    error: str = ""


class DisabledPluginRegistry:
    """No-op registry used when plugins are disabled or unavailable."""

    def __init__(self, reason: str = "Plugin subsystem disabled") -> None:
        self.status = PluginSubsystemStatus(enabled=False, available=False, error=reason)
    def get_tool_declarations(self) -> list[dict]:
        return []

    def list_for_ui(self) -> list[dict]:
        return []

    def settings_schemas(self) -> list[dict]:
        return []

    def start_enabled_plugins(self, player=None) -> None:
        return None

    def stop_enabled_plugins(self, timeout: float = 2.0) -> None:
        return None

    def has(self, name: str) -> bool:
        return False

    def run(self, name: str, parameters: dict, player=None, session_memory=None) -> str:
        return f"Plugin subsystem unavailable; '{name}' was not executed."

    def scheduling(self, name: str) -> str:
        return "WHEN_IDLE"


def safe_discover_plugins(
    *,
    plugins_dir,
    user_plugins_dir=None,
    core_tool_names: set[str] | None = None,
    logger: Callable[[str], None] = print,
    notify: Callable[[str], None] | None = None,
):
    """Load the optional plugin loader lazily and fail closed.

    A plugin import/discovery failure must not escape into Core startup. The
    returned fallback preserves the registry interface used by main.py.
    """
    try:
        from core.plugin_loader import discover_plugins

        registry = discover_plugins(
            plugins_dir=plugins_dir,
            user_plugins_dir=user_plugins_dir,
            core_tool_names=core_tool_names or set(),
            logger=logger,
            notify=notify,
        )
        registry.status = PluginSubsystemStatus(enabled=True, available=True)
        return registry
    except Exception as exc:
        message = f"Plugin subsystem unavailable: {type(exc).__name__}: {exc}"
        try:
            logger(message)
        except Exception:
            pass
        try:
            if notify:
                notify("Plugins are unavailable. Built-in actions remain active.")
        except Exception:
            pass
        return DisabledPluginRegistry(message)
