
"""Failure-safe boundary around the optional plugin subsystem.

Core code imports this module, never the plugin loader directly. The loader is
imported lazily and all discovery/startup failures are converted into an
unavailable registry, allowing built-in actions and the rest of JARVIS to run.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

_active_registry = None


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
        global _active_registry
        _active_registry = registry
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


def shutdown_optional_plugin_services(
    timeout: float = 2.5,
    logger: Callable[[str], None] = print,
) -> None:
    """Stop optional bundled plugin services without depending on registry APIs.

    PluginRegistry intentionally has no global stop method: startup hooks are
    optional and individual long-running services own their shutdown contracts.
    This cleanup therefore targets the bundled services that expose explicit
    stop/shutdown APIs and isolates every failure.
    """
    try:
        from memory.config_manager import get_plugins_system_enabled
        if not get_plugins_system_enabled():
            return
    except Exception:
        # Configuration failure must not make Core shutdown unsafe.
        return

    import importlib

    try:
        gesture_control = importlib.import_module("plugins.gesture_control")
        stop = getattr(gesture_control, "stop", None)
        if callable(stop):
            stop()
    except Exception as exc:
        try:
            logger(f"[Shutdown] Gesture Control cleanup failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass

    try:
        hands = importlib.import_module("plugins._hands")
        service = getattr(hands, "SERVICE", None)
        shutdown = getattr(service, "shutdown", None)
        if callable(shutdown):
            stopped = shutdown(timeout=timeout)
            if stopped is False:
                logger("[Shutdown] Hand tracking worker did not stop before timeout.")
    except Exception as exc:
        try:
            logger(f"[Shutdown] Hand service cleanup failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
