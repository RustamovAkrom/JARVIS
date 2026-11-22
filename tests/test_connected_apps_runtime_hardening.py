from __future__ import annotations

import pytest

from core.connected_apps.agent import _validate_arguments
from core.connected_apps.runtime import _validate_http_endpoint
from core.connected_apps.store import ConnectedAppsStore


def test_http_endpoint_rejects_non_http_scheme():
    with pytest.raises(ValueError):
        _validate_http_endpoint("file:///etc/passwd")


@pytest.mark.parametrize("url", [
    "https://user:password@example.com/mcp",
    "https://example.com/mcp#fragment",
    "not-a-url",
])
def test_http_endpoint_rejects_unsafe_url_forms(url):
    with pytest.raises(ValueError):
        _validate_http_endpoint(url)


def test_http_endpoint_accepts_valid_endpoint():
    assert _validate_http_endpoint("https://example.com/mcp") == "https://example.com/mcp"


def test_schema_validation_checks_nested_types_and_enum():
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "enum": [1, 2, 3]},
            "filters": {
                "type": "array",
                "items": {"type": "object", "properties": {
                    "enabled": {"type": "boolean"}
                }}
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    _validate_arguments(schema, {"query": "hello", "limit": 2, "filters": [{"enabled": True}]})
    with pytest.raises(ValueError):
        _validate_arguments(schema, {"query": 123})
    with pytest.raises(ValueError):
        _validate_arguments(schema, {"query": "hello", "limit": 5})
    with pytest.raises(ValueError):
        _validate_arguments(schema, {"query": "hello", "unexpected": True})


def test_store_does_not_silently_reset_corrupt_data(tmp_path):
    path = tmp_path / "connected_apps.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unreadable"):
        ConnectedAppsStore(path).list_connections()
