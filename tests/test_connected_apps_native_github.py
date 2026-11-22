from __future__ import annotations

import base64
import json

import pytest

from core.connected_apps.adapters.github.adapter import GitHubAdapter
from core.connected_apps.native import NativeAppRegistry


class Store:
    def __init__(self, token="test-token"):
        self.token = token
        self.rows = {}
    def get_credential(self, connection_id, name):
        return self.token if name == "access_token" else ""


def test_github_adapter_catalog_has_read_and_write_tools():
    names = {tool.name for tool in GitHubAdapter.tools}
    assert {
        "github_get_profile",
        "github_list_repositories",
        "github_search_repositories",
        "github_list_issues",
        "github_get_issue",
        "github_list_pull_requests",
        "github_get_file",
        "github_create_issue",
    } <= names
    create = next(tool for tool in GitHubAdapter.tools if tool.name == "github_create_issue")
    assert create.read_only is False
    assert create.requires_confirmation is True


def test_native_registry_rejects_duplicate_and_normalizes_lookup():
    registry = NativeAppRegistry()
    adapter = GitHubAdapter(Store())
    registry.register(adapter)
    assert registry.get("GITHUB") is adapter
    with pytest.raises(ValueError):
        registry.register(adapter)


def test_github_repository_validation():
    adapter = GitHubAdapter(Store())
    assert adapter._repo("owner/repo") == "owner/repo"
    with pytest.raises(ValueError):
        adapter._repo("owner")
    with pytest.raises(ValueError):
        adapter._repo("owner/../repo")


def test_github_profile_request_uses_token_and_api_headers(monkeypatch):
    adapter = GitHubAdapter(Store())
    captured = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self, limit): return b'{"login":"akrom"}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = adapter.execute("c1", "github_get_profile", {})
    assert result["login"] == "akrom"
    assert captured["url"] == "https://api.github.com/user"
    assert captured["timeout"] == 20
    assert captured["headers"]["Authorization"] == "Bearer test-token"
    assert captured["headers"]["Accept"] == "application/vnd.github+json"


def test_github_search_repository_encodes_query(monkeypatch):
    adapter = GitHubAdapter(Store())
    captured = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def read(self, limit): return b'{"items":[]}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter.execute("c1", "github_search_repositories", {"query":"fast api", "limit":5})
    assert "q=fast+api" in captured["url"]
    assert "per_page=5" in captured["url"]


def test_github_create_issue_builds_payload(monkeypatch):
    adapter = GitHubAdapter(Store())
    captured = {}

    def fake_request(connection_id, method, path, query=None, payload=None):
        captured.update(method=method, path=path, payload=payload)
        return {"number": 10}

    monkeypatch.setattr(adapter, "_request", fake_request)
    result = adapter.execute("c1", "github_create_issue", {
        "repository":"owner/repo", "title":"Bug", "body":"Details", "labels":["bug"]
    })
    assert result["number"] == 10
    assert captured == {
        "method":"POST",
        "path":"/repos/owner/repo/issues",
        "payload":{"title":"Bug","body":"Details","labels":["bug"]},
    }


def test_github_api_error_does_not_expose_response_secrets(monkeypatch):
    adapter = GitHubAdapter(Store())

    class Error:
        code = 401
        def read(self, limit): return b'{"message":"Bad credentials"}'

    def fake_urlopen(request, timeout):
        raise Error()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(PermissionError, match="authorization failed"):
        adapter.execute("c1", "github_get_profile", {})
