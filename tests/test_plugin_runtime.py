
"""Tests for the optional plugin runtime boundary."""

from core.plugin_runtime import DisabledPluginRegistry, safe_discover_plugins


def test_disabled_registry_is_noop():
    registry = DisabledPluginRegistry("disabled")
    assert registry.get_tool_declarations() == []
    assert registry.list_for_ui() == []
    assert registry.settings_schemas() == []
    assert registry.has("some_plugin") is False
    registry.start_enabled_plugins()
    registry.stop_enabled_plugins()
    assert registry.scheduling("some_plugin") == "WHEN_IDLE"


def test_discovery_import_failure_falls_back(monkeypatch, tmp_path):
    import core.plugin_loader

    def broken_discovery(**kwargs):
        raise RuntimeError("simulated loader failure")

    monkeypatch.setattr(core.plugin_loader, "discover_plugins", broken_discovery)
    registry = safe_discover_plugins(
        plugins_dir=tmp_path / "plugins",
        user_plugins_dir=tmp_path / "user_plugins",
        core_tool_names={"built_in"},
        logger=lambda _message: None,
    )

    assert isinstance(registry, DisabledPluginRegistry)
    assert registry.get_tool_declarations() == []
    assert registry.has("built_in") is False


def test_shutdown_optional_plugin_services_stops_bundled_services(monkeypatch):
    import importlib
    from types import SimpleNamespace
    import core.plugin_runtime as runtime
    import memory.config_manager as config

    calls = []
    logs = []
    gesture = SimpleNamespace(stop=lambda: calls.append("gesture"))
    hands = SimpleNamespace(SERVICE=SimpleNamespace(
        shutdown=lambda timeout: calls.append(("hands", timeout)) or True,
    ))
    monkeypatch.setattr(config, "get_plugins_system_enabled", lambda: True)
    original_import = importlib.import_module

    def fake_import(name, package=None):
        if name == "plugins.gesture_control":
            return gesture
        if name == "plugins._hands":
            return hands
        return original_import(name, package)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    runtime.shutdown_optional_plugin_services(timeout=1.25, logger=logs.append)

    assert calls == ["gesture", ("hands", 1.25)]
    assert logs == []

