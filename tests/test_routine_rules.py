from datetime import datetime

import pytest

from core.routines.rules import next_fire, parse_days, parse_hhmm, rule_from_args


def test_parse_hhmm_normalizes_and_rejects_invalid_values():
    assert parse_hhmm("8.5") == (8, 5)
    assert parse_hhmm("08:30") == (8, 30)
    with pytest.raises(ValueError):
        parse_hhmm("24:00")


def test_parse_days_defaults_and_deduplicates():
    assert parse_days() == [1, 2, 3, 4, 5, 6, 7]
    assert parse_days("1,3,3,7") == [1, 3, 7]
    with pytest.raises(ValueError):
        parse_days("0,8")


def test_weekly_rule_returns_strictly_future_time():
    after = datetime(2026, 11, 19, 8, 0)  # Thursday
    rule = {"type": "weekly", "time": "08:00", "days": [4]}
    assert next_fire(rule, after) == datetime(2026, 11, 26, 8, 0)


def test_monthly_rule_clamps_to_last_day():
    after = datetime(2026, 2, 1, 0, 0)
    rule = {"type": "monthly", "day": 31, "time": "09:00"}
    assert next_fire(rule, after) == datetime(2026, 2, 28, 9, 0)


def test_interval_rule_advances_and_respects_window():
    rule = {"type": "interval", "minutes": 30, "start": "09:00", "end": "10:30", "days": [1]}
    after = datetime(2026, 11, 23, 9, 40)  # Monday
    assert next_fire(rule, after) == datetime(2026, 11, 23, 10, 0)
    assert next_fire(rule, datetime(2026, 11, 23, 10, 30)) == datetime(2026, 11, 30, 9, 0)


def test_rule_from_args_validates_and_normalizes():
    assert rule_from_args({"time": "9.30", "days": [1, 5]}, None) == {
        "type": "weekly", "time": "09:30", "days": [1, 5]
    }
    assert rule_from_args({"every_minutes": 15, "between": "08:00-18:00"}, None)["type"] == "interval"
    with pytest.raises(ValueError):
        rule_from_args({"every_minutes": 2}, None)
    with pytest.raises(ValueError):
        rule_from_args({"at": "invalid"}, None)
