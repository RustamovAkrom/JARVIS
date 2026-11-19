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
