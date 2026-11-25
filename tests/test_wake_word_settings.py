import pytest
import json

import memory.config_manager as config


def test_wake_threshold_defaults_and_validation(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "api_keys.json")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    assert config.get_wake_word_threshold() == 0.5
    assert config.save_wake_word_threshold(0.73) == 0.73
    assert config.get_wake_word_threshold() == 0.73
    with pytest.raises(ValueError):
        config.save_wake_word_threshold(1.0)


def test_invalid_stored_wake_threshold_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "api_keys.json")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    (tmp_path / "api_keys.json").write_text(json.dumps({"wake_word_threshold": "bad"}), encoding="utf-8")
    assert config.get_wake_word_threshold() == 0.5
