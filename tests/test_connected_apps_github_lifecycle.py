from __future__ import annotations

from core.connected_apps.service import ConnectedAppsService


class MemoryStore:
    def __init__(self):
        self.rows = {}
        self.credentials = {}

    def get_connection(self, cid):
        row = self.rows.get(cid)
        return dict(row) if row else None

    def upsert_connection(self, row):
        self.rows[row["id"]] = dict(row)

    def list_connections(self):
        return [dict(row) for row in self.rows.values()]

    def set_credential(self, cid, key, value):
        self.credentials[(cid, key)] = value

    def get_credential(self, cid, key):
        return self.credentials.get((cid, key), "")

    def delete_credential(self, cid, key):
        return self.credentials.pop((cid, key), None) is not None


def _connected_github_row(cid="github-native"):
    return {
        "id": cid,
        "app_id": "github",
        "display_name": "GitHub",
        "status": "connected",
        "enabled": True,
        "auth_type": "oauth2",
        "scopes": ["repo", "read:user", "user:email"],
        "created_at": 1,
        "updated_at": 1,
        "last_error": "",
        "metadata": {
            "native": True,
            "native_adapter": "github",
            "health": "healthy",
        },
    }


def test_github_reauthorize_discards_old_token_and_uses_fresh_oauth(monkeypatch):
    from core.connected_apps.auth_service import OAuthService

    store = MemoryStore()
    store.upsert_connection(_connected_github_row())
    store.set_credential("github-native", "access_token", "revoked-token")
    store.set_credential("github-native", "oauth_client_id", "saved-client-id")
    store.set_credential("github-native", "oauth_client_secret", "saved-client-secret")

    service = ConnectedAppsService(store=store)
    monkeypatch.setattr(service._native_adapters["github"], "health_check", lambda cid: True)
    calls = []

    def authorize(auth_service, cid, provider_id, client_id, client_secret=""):
        # Reauthorization must clear the previous bearer token before launching
        # the provider flow, so an invalid token can never be reused.
        assert store.get_credential(cid, "access_token") == ""
        calls.append((cid, provider_id, client_id, client_secret))
        store.set_credential(cid, "access_token", "fresh-token")
        store.set_credential(cid, "oauth_client_id", client_id)
        store.set_credential(cid, "oauth_client_secret", client_secret)

    monkeypatch.setattr(OAuthService, "authorize_connection", authorize)

    result = service.reauthorize_github(
        client_id="saved-client-id",
        client_secret="saved-client-secret",
        connection_id="github-native",
    )

    assert calls == [("github-native", "github", "saved-client-id", "saved-client-secret")]
    assert result["status"] == "connected"
    assert store.get_credential("github-native", "access_token") == "fresh-token"


def test_github_reauthorize_failure_does_not_restore_old_token(monkeypatch):
    from core.connected_apps.auth_service import OAuthService

    store = MemoryStore()
    store.upsert_connection(_connected_github_row())
    store.set_credential("github-native", "access_token", "revoked-token")
    service = ConnectedAppsService(store=store)

    def fail_authorize(*args, **kwargs):
        raise PermissionError("OAuth authorization failed")

    monkeypatch.setattr(OAuthService, "authorize_connection", fail_authorize)

    try:
        service.reauthorize_github(
            client_id="client-id",
            client_secret="client-secret",
            connection_id="github-native",
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("OAuth failure must be propagated")

    row = store.get_connection("github-native")
    assert row["status"] == "error"
    assert "OAuth authorization failed" in row["last_error"]
    assert store.get_credential("github-native", "access_token") == ""


def test_github_disconnect_clears_bearer_tokens_but_keeps_oauth_app_config():
    store = MemoryStore()
    store.upsert_connection(_connected_github_row())
    store.set_credential("github-native", "access_token", "access-token")
    store.set_credential("github-native", "refresh_token", "refresh-token")
    store.set_credential("github-native", "expires_at", "12345")
    store.set_credential("github-native", "token_type", "Bearer")
    store.set_credential("github-native", "oauth_client_id", "client-id")
    store.set_credential("github-native", "oauth_client_secret", "client-secret")
    store.set_credential("github-native", "oauth_provider", "github")
    store.set_credential("github-native", "oauth_scopes", "repo read:user")

    service = ConnectedAppsService(store=store)
    assert service.disconnect("github-native") is True

    row = store.get_connection("github-native")
    assert row["status"] == "disconnected"
    assert row["metadata"]["health"] == "disconnected"
    for key in ("access_token", "refresh_token", "expires_at", "token_type"):
        assert store.get_credential("github-native", key) == ""
    assert store.get_credential("github-native", "oauth_client_id") == "client-id"
    assert store.get_credential("github-native", "oauth_client_secret") == "client-secret"
    assert store.get_credential("github-native", "oauth_provider") == "github"
    assert store.get_credential("github-native", "oauth_scopes") == "repo read:user"


def test_github_invalid_token_health_check_marks_connection_error(monkeypatch):
    store = MemoryStore()
    service = ConnectedAppsService(store=store)

    def unauthorized(_cid):
        raise PermissionError("GitHub authorization failed (401): Bad credentials")

    monkeypatch.setattr(service._native_adapters["github"], "health_check", unauthorized)
    result = service.connect_native(
        "github", access_token="invalid-token", connection_id="github-native"
    )

    assert result["status"] == "error"
    assert "401" in result["last_error"]
    assert store.get_credential("github-native", "access_token") == "invalid-token"
