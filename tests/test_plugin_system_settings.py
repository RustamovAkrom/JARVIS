import memory.config_manager as config


def test_global_plugin_system_setting_persists(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "api_keys.json")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    assert config.get_plugins_system_enabled() is True
    config.save_plugins_system_enabled(False)
    assert config.get_plugins_system_enabled() is False
    config.save_plugins_system_enabled(True)
    assert config.get_plugins_system_enabled() is True
