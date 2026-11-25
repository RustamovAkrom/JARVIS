from actions.presence import presence as presence_action
from core.presence import Presence
from memory import config_manager


def test_presence_action_status_uses_runtime_and_settings(monkeypatch):
    engine = Presence(away_minutes=10)
    engine.apply({"enabled": True, "away_minutes": 10, "greet": True})
    monkeypatch.setattr("core.presence.get_presence_runtime", lambda: engine)
    monkeypatch.setattr(
        config_manager,
        "get_presence_settings",
        lambda: {"enabled": True, "away_minutes": 10, "greet": True},
    )

    result = presence_action({"action": "status"})

    assert "User is present" in result
    assert "Away threshold: 10 quiet minutes" in result
    assert "no camera" in result.lower()


def test_presence_action_settings_persist_and_apply(monkeypatch):
    engine = Presence(away_minutes=10)
    monkeypatch.setattr("core.presence.get_presence_runtime", lambda: engine)
    saved = {"enabled": False, "away_minutes": 15, "greet": False}
    calls = []
    monkeypatch.setattr(
        config_manager,
        "save_presence_settings",
        lambda **kwargs: calls.append(kwargs) or saved,
    )

    result = presence_action(
        {"action": "settings", "enabled": False, "away_minutes": 15, "greet": False}
    )

    assert calls == [{"enabled": False, "away_minutes": 15, "greet": False}]
    assert engine.enabled is False
    assert engine.away_minutes == 15
    assert "Presence is off" in result


def test_presence_action_leaving_declares_runtime_away(monkeypatch):
    engine = Presence()
    monkeypatch.setattr("core.presence.get_presence_runtime", lambda: engine)

    result = presence_action({"action": "leaving"})

    assert "queued" in result
    assert engine.away is True
