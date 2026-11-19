import pytest
from core.presence import Presence, build_return_prompt


class Clock:
    def __init__(self): self.now = 1000.0
    def __call__(self): return self.now
    def advance(self, seconds): self.now += seconds


def test_unknown_idle_never_marks_away():
    clock = Clock()
    presence = Presence(away_minutes=1, clock=clock)
    clock.advance(120)
    assert presence.poll(None, None) is None
    assert not presence.away


def test_idle_marks_away_and_fresh_activity_returns():
    clock = Clock()
    presence = Presence(away_minutes=1, clock=clock)
    clock.advance(65)
    assert presence.poll(65, False) == ("away", 0.0)
    assert presence.away
    presence.note_activity()
    assert presence.poll(0, False) == ("back", 65)
    assert not presence.away


def test_lock_debounced():
    clock = Clock()
    presence = Presence(clock=clock)
    assert presence.poll(0, True) is None
    clock.advance(4)
    assert presence.poll(0, True) is None
    clock.advance(2)
    assert presence.poll(0, True) == ("away", 0.0)
    assert presence.reason == "screen locked"


def test_declared_away_has_grace_before_return_detection():
    clock = Clock()
    presence = Presence(clock=clock)
    presence.declare_away()
    clock.advance(5)
    presence.note_activity()
    assert presence.poll(0, False) is None
    clock.advance(20)
    presence.note_activity()
    assert presence.poll(0, False) == ("back", 25)


def test_inbox_deduplicates_and_is_bounded():
    presence = Presence()
    assert presence.hold("task", "result")
    assert not presence.hold("task", "result")
    for i in range(20):
        presence.hold("task", f"result {i}")
    assert presence.inbox_size() == 12
    assert len(presence.take_inbox()) == 12
    assert presence.inbox_size() == 0


def test_disabled_presence_resets_state():
    presence = Presence()
    presence.declare_away()
    presence.apply({"enabled": False})
    assert not presence.away
    assert presence.poll(0, False) is None


def test_away_minutes_is_clamped_and_invalid_values_ignored():
    presence = Presence(away_minutes=10)
    presence.apply({"away_minutes": 0})
    assert presence.away_minutes == 1
    presence.apply({"away_minutes": "invalid"})
    assert presence.away_minutes == 1


def test_return_prompt_greeting_and_disabled_greeting():
    assert "Welcome" in build_return_prompt([], greet=True)
    assert build_return_prompt([], greet=False) is None


def test_return_prompt_digest_is_bounded_and_sanitizes_text():
    items = [{"text": f"Notification {i}\nwith whitespace"} for i in range(10)]
    prompt = build_return_prompt(items)
    assert prompt is not None
    assert prompt.count("- Notification") == 8
    assert "with whitespace" in prompt
