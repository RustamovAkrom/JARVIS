from pathlib import Path
import sys
import types

from core.plugin_runtime import DisabledPluginRegistry, load_plugin_registry


def test_disabled_plugin_runtime_does_not_import_loader(monkeypatch, tmp_path):
    monkeypatch.delitem(sys.modules, "core.plugin_loader", raising=False)
    registry = load_plugin_registry(
        enabled=False,
        plugins_dir=tmp_path / "plugins",
        user_plugins_dir=tmp_path / "user",
        logger=lambda _: None,
    )
    assert isinstance(registry, DisabledPluginRegistry)
    assert registry.get_tool_declarations() == []
    assert registry.list_for_ui() == []
    assert registry.settings_schemas() == []
    assert not registry.has("anything")
    assert registry.run("anything", {}) == "Plugin 'anything' is unavailable (disabled by settings)."
    assert "core.plugin_loader" not in sys.modules


def test_plugin_loader_failure_is_non_fatal(monkeypatch, tmp_path):
    fake = types.ModuleType("core.plugin_loader")
    fake.discover_plugins = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("broken"))
    monkeypatch.setitem(sys.modules, "core.plugin_loader", fake)
    messages = []
    registry = load_plugin_registry(
        enabled=True,
        plugins_dir=tmp_path / "plugins",
        user_plugins_dir=tmp_path / "user",
        core_tool_names={"core_action"},
        logger=messages.append,
        notify=messages.append,
    )
    assert isinstance(registry, DisabledPluginRegistry)
    assert "initialization failed" in registry.reason
    assert any("Optional subsystem failed" in msg for msg in messages)
    assert registry.get_tool_declarations() == []


def test_enabled_plugin_runtime_delegates_discovery(monkeypatch, tmp_path):
    fake = types.ModuleType("core.plugin_loader")
    sentinel = object()
    fake.discover_plugins = lambda **kwargs: sentinel
    monkeypatch.setitem(sys.modules, "core.plugin_loader", fake)
    registry = load_plugin_registry(
        enabled=True,
        plugins_dir=tmp_path / "plugins",
        user_plugins_dir=tmp_path / "user",
        core_tool_names={"core_action"},
        logger=lambda _: None,
    )
    assert registry is sentinel
