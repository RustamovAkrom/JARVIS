from core.security_logging import redact_sensitive_mapping, redact_sensitive_text


def test_redacts_quoted_and_unquoted_credentials():
    assert redact_sensitive_text('password="two words"') == "password=[REDACTED]"
    assert redact_sensitive_text('"api_key": "abc def"') == '"api_key": [REDACTED]'
    assert redact_sensitive_text("bot_token:abc123") == "bot_token:[REDACTED]"


def test_preserves_unrelated_json_keys():
    assert redact_sensitive_mapping({"author": "Ada", "compass": "north"}) == {
        "author": "Ada",
        "compass": "north",
    }


def test_redacts_nested_credential_fields_without_mutating_input():
    value = {"config": {"password": "hunter2"}, "items": [{"api_key": "abc"}]}
    assert redact_sensitive_mapping(value) == {
        "config": {"password": "[REDACTED]"},
        "items": [{"api_key": "[REDACTED]"}],
    }
    assert value["config"]["password"] == "hunter2"


def test_redacts_full_multiword_password():
    assert redact_sensitive_text(
        'password: "correct horse battery staple"'
    ) == "password: [REDACTED]"
