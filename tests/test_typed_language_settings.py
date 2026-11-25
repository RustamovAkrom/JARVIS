import json
import memory.config_manager as config

def test_typed_language_detection_setting(monkeypatch,tmp_path):
    monkeypatch.setattr(config,"CONFIG_FILE",tmp_path/"api_keys.json")
    monkeypatch.setattr(config,"CONFIG_DIR",tmp_path)
    assert config.get_typed_language_detection_enabled() is True
    config.save_typed_language_detection_enabled(False)
    assert config.get_typed_language_detection_enabled() is False
    config.save_typed_language_detection_enabled(True)
    assert config.get_typed_language_detection_enabled() is True
