from memory import config_manager as config


def test_presence_settings_defaults_and_validation(monkeypatch):
    monkeypatch.setattr(config, "load_api_keys", lambda: {})
    assert config.get_presence_settings() == {
        "enabled": True, "away_minutes": 10, "greet": True,
    }


def test_presence_settings_clamps_loaded_value(monkeypatch):
    monkeypatch.setattr(config, "load_api_keys", lambda: {
        "presence": {"enabled": False, "away_minutes": 999, "greet": False}
    })
    assert config.get_presence_settings() == {
        "enabled": False, "away_minutes": 240, "greet": False,
    }


def test_save_presence_settings_validates_and_patches(monkeypatch):
    captured = {}
    monkeypatch.setattr(config, "load_api_keys", lambda: {})
    monkeypatch.setattr(config, "_patch_config", lambda **kwargs: captured.update(kwargs))
    saved = config.save_presence_settings(enabled=False, away_minutes=15, greet=False)
    assert saved == {"enabled": False, "away_minutes": 15, "greet": False}
    assert captured == {"presence": saved}


def test_save_presence_settings_rejects_out_of_range(monkeypatch):
    monkeypatch.setattr(config, "load_api_keys", lambda: {})
    monkeypatch.setattr(config, "_patch_config", lambda **kwargs: None)
    try:
        config.save_presence_settings(away_minutes=0)
    except ValueError as exc:
        assert "between 1 and 240" in str(exc)
    else:
        raise AssertionError("Expected invalid presence timeout to be rejected")
