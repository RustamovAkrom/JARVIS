from __future__ import annotations

import pytest
from pathlib import Path

from memory import config_manager as config


def test_clap_launcher_defaults(monkeypatch):
    monkeypatch.setattr(config, "load_api_keys", lambda: {})
    settings = config.get_clap_launcher()

    assert settings == {
        "claps": 2,
        "sensitivity": "normal",
        "level": None,
        "notify_heard": False,
        "preload": False,
        "greet": True,
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"claps": 1, "sensitivity": "high", "level": 0.42, "greet": False}, {
            "claps": 1,
            "sensitivity": "high",
            "level": 0.42,
            "notify_heard": False,
            "preload": False,
            "greet": False,
        }),
        ({"claps": 99, "sensitivity": "invalid", "level": 2.0}, {
            "claps": 2,
            "sensitivity": "normal",
            "level": None,
            "notify_heard": False,
            "preload": False,
            "greet": True,
        }),
    ],
)
def test_clap_launcher_validates_loaded_settings(monkeypatch, raw, expected):
    monkeypatch.setattr(config, "load_api_keys", lambda: {"clap_launcher": raw})
    assert config.get_clap_launcher() == expected


def test_save_clap_launcher_updates_only_clap_settings(monkeypatch):
    persisted = {}
    monkeypatch.setattr(config, "load_api_keys", lambda: {})
    monkeypatch.setattr(config, "_patch_config", lambda **fields: persisted.update(fields))

    result = config.save_clap_launcher(claps=1, sensitivity="high", level=0.35, greet=False)

    assert result["claps"] == 1
    assert result["sensitivity"] == "high"
    assert result["level"] == 0.35
    assert result["greet"] is False
    assert result["preload"] is False
    assert persisted == {"clap_launcher": result}


@pytest.mark.parametrize(
    "updates",
    [
        {"claps": 3},
        {"sensitivity": "extreme"},
        {"level": 0.0},
        {"level": 1.5},
        {"unknown": True},
    ],
)
def test_save_clap_launcher_rejects_invalid_settings(monkeypatch, updates):
    monkeypatch.setattr(config, "load_api_keys", lambda: {})
    monkeypatch.setattr(config, "_patch_config", lambda **fields: None)

    with pytest.raises(ValueError):
        config.save_clap_launcher(**updates)


def test_clap_launcher_source_compiles():
    source = Path(__file__).resolve().parents[1] / "clap_launcher.pyw"
    compile(source.read_text(encoding="utf-8"), str(source), "exec")
