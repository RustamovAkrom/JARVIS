"""Regression tests for parsing JSON embedded in research model replies."""

from core.tasks.research import parse_json


def test_parse_json_accepts_plain_and_fenced_objects():
    assert parse_json('{"goal": "test"}') == {"goal": "test"}
    assert parse_json('```json\n{"goal": "test"}\n```') == {"goal": "test"}


def test_parse_json_ignores_prose_and_braces_after_object():
    reply = 'Here is the plan: {"goal": "research", "note": "brace } inside string"} Thanks {all}'
    assert parse_json(reply) == {
        "goal": "research",
        "note": "brace } inside string",
    }


def test_parse_json_skips_invalid_braces_before_valid_object():
    assert parse_json('Reasoning {not json}; result: {"ok": true}') == {"ok": True}


def test_parse_json_returns_none_for_empty_invalid_or_non_object_replies():
    assert parse_json("") is None
    assert parse_json("no structured response") is None
    assert parse_json('["not", "an", "object"]') is None
    assert parse_json('{"unfinished": ') is None
