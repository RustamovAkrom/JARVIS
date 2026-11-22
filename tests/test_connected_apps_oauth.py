import pytest

from core.connected_apps.providers import get_provider, list_providers
from core.connected_apps.auth_service import OAuthService


def test_provider_catalog_contains_supported_providers():
    ids = {provider.id for provider in list_providers()}
    assert {"github", "google", "microsoft"} <= ids
    assert get_provider("github") is not None


def test_oauth_token_storage_and_non_expiring_token(monkeypatch):
    class Store:
        def __init__(self):
            self.values = {}

        def set_credential(self, cid, key, value):
            self.values[(cid, key)] = value

        def get_credential(self, cid, key):
            return self.values.get((cid, key), "")

    store = Store()
    service = OAuthService(store)
    service._save("c1", {"access_token": "token", "token_type": "Bearer"})
    assert service.get_access_token("c1") == "token"


def test_oauth_callback_rejects_wrong_path_and_invalid_state_without_aborting():
    from urllib.error import HTTPError
    from urllib.request import urlopen
    from core.connected_apps.oauth import OAuthCallbackServer

    server = OAuthCallbackServer()
    state = server.start()
    try:
        with pytest.raises(HTTPError):
            urlopen(state.redirect_uri.replace("/callback", "/wrong"), timeout=2)
        with pytest.raises(HTTPError):
            urlopen(state.redirect_uri + "?state=wrong&code=bad", timeout=2)
        assert not state.event.is_set()

        url = state.redirect_uri + "?state=" + state.state + "&code=valid-code"
        response = urlopen(url, timeout=2)
        assert response.status == 200
        result = server.wait(timeout=0.1)
        assert result.code == "valid-code"
        assert not result.error
    finally:
        server.close()

def test_mcp_oauth_discovery_reads_protected_resource_and_server_metadata(monkeypatch):
    import core.connected_apps.oauth as oauth
    class Response:
        status_code=200
        def __init__(self,payload):self.payload=payload
        def json(self):return self.payload
    class Client:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,url):
            if "oauth-protected-resource" in url:
                return Response({"authorization_servers":["https://auth.example"],"scopes_supported":["read"]})
            if "oauth-authorization-server" in url:
                return Response({"issuer":"https://auth.example","authorization_endpoint":"https://auth.example/authorize","token_endpoint":"https://auth.example/token"})
            return Response({})
    monkeypatch.setattr("httpx.Client",Client)
    discovery=oauth.discover_mcp_oauth("https://mcp.example/api")
    assert discovery["issuer"]=="https://auth.example"
    assert discovery["authorization_server"]["token_endpoint"]=="https://auth.example/token"
    assert discovery["resource"]["scopes_supported"]==["read"]

def test_oauth_callback_missing_code_returns_http_error_and_completes(monkeypatch):
    from urllib.error import HTTPError
    from urllib.request import urlopen
    from core.connected_apps.oauth import OAuthCallbackServer

    server = OAuthCallbackServer()
    state = server.start()
    try:
        url = state.redirect_uri + "?state=" + state.state
        with pytest.raises(HTTPError) as exc:
            urlopen(url, timeout=2)
        assert exc.value.code == 400
        result = server.wait(timeout=0.1)
        assert result.error == "OAuth callback did not contain a code"
    finally:
        server.close()


def test_oauth_reauthorization_clears_stale_refresh_token_and_client_secret(monkeypatch):
    import core.connected_apps.auth_service as auth_module
    from core.connected_apps.auth_service import OAuthService

    class Store:
        def __init__(self):
            self.values = {}
        def set_credential(self, cid, key, value):
            self.values[(cid, key)] = value
        def get_credential(self, cid, key):
            return self.values.get((cid, key), "")
        def delete_credential(self, cid, key):
            self.values.pop((cid, key), None)

    class Provider:
        id = "test"
        default_scopes = ("read",)
        def config(self, client_id, redirect_uri, scopes, client_secret):
            return object()

    store = Store()
    store.set_credential("c1", "refresh_token", "old-refresh")
    store.set_credential("c1", "oauth_client_secret", "old-secret")
    monkeypatch.setattr(auth_module, "get_provider", lambda provider_id: Provider())
    monkeypatch.setattr(auth_module, "authorize", lambda config: {"access_token": "new-access"})
    service = OAuthService(store)
    service.authorize_connection("c1", "test", "client-id", client_secret="")
    assert store.get_credential("c1", "access_token") == "new-access"
    assert store.get_credential("c1", "refresh_token") == ""
    assert store.get_credential("c1", "oauth_client_secret") == ""


def test_oauth_authorization_url_rejects_unsafe_endpoint_and_missing_pkce():
    from core.connected_apps.oauth import OAuthConfig, OAuthState, build_authorization_url

    config = OAuthConfig(
        authorization_url="http://attacker.example/authorize",
        token_url="https://auth.example/token",
        client_id="client",
        redirect_uri="http://127.0.0.1:1234/callback",
        scopes=["read"],
    )
    state = OAuthState(state="state", verifier="verifier", redirect_uri=config.redirect_uri)
    with pytest.raises(ValueError, match="HTTPS"):
        build_authorization_url(config, state, "challenge")

    config.authorization_url = "https://auth.example/authorize"
    state.verifier = ""
    with pytest.raises(ValueError, match="PKCE"):
        build_authorization_url(config, state, "challenge")


@pytest.mark.parametrize("url", ["http://evil.example/token", "file:///tmp/token", "https://user:pass@auth.example/token"])
def test_oauth_token_endpoint_rejects_unsafe_url(url):
    from core.connected_apps.oauth import OAuthConfig, refresh_tokens

    config = OAuthConfig(
        authorization_url="https://auth.example/authorize",
        token_url=url,
        client_id="client",
        redirect_uri="http://127.0.0.1:1234/callback",
        scopes=[],
    )
    with pytest.raises(ValueError):
        refresh_tokens(config, "refresh-token")


def test_oauth_refresh_rotation_is_persisted_and_reuses_refreshed_token(monkeypatch):
    import core.connected_apps.auth_service as auth_module

    class Store:
        def __init__(self):
            self.values = {}
        def set_credential(self, cid, key, value):
            self.values[(cid, key)] = value
        def get_credential(self, cid, key):
            return self.values.get((cid, key), "")
        def delete_credential(self, cid, key):
            self.values.pop((cid, key), None)

    class Provider:
        id = "test"
        def config(self, client_id, redirect_uri, scopes, client_secret):
            return object()

    store = Store()
    store.set_credential("c1", "access_token", "expired-access")
    store.set_credential("c1", "refresh_token", "old-refresh")
    store.set_credential("c1", "expires_at", "1")
    store.set_credential("c1", "oauth_provider", "test")
    store.set_credential("c1", "oauth_client_id", "client")
    monkeypatch.setattr(auth_module, "get_provider", lambda provider_id: Provider())
    calls = []
    monkeypatch.setattr(auth_module, "refresh_tokens", lambda config, token: calls.append(token) or {
        "access_token": "fresh-access", "refresh_token": "rotated-refresh", "expires_in": 3600
    })

    service = OAuthService(store)
    assert service.get_access_token("c1") == "fresh-access"
    assert store.get_credential("c1", "refresh_token") == "rotated-refresh"
    assert service.get_access_token("c1") == "fresh-access"
    assert calls == ["old-refresh"]


def test_oauth_save_atomically_clears_stale_optional_tokens():
    class Store:
        def __init__(self):
            self.values = {"refresh_token": "old-refresh", "id_token": "old-id"}
            self.batch_calls = []
        def set_credentials(self, cid, values, *, delete=()):
            self.batch_calls.append((cid, dict(values), tuple(delete)))
            for key in delete:
                self.values.pop(key, None)
            self.values.update(values)
        def get_credential(self, cid, key):
            return self.values.get(key, "")

    store = Store()
    service = OAuthService(store)
    service._save("c1", {"access_token": "new-access", "expires_in": 3600})

    assert store.values["access_token"] == "new-access"
    assert "refresh_token" not in store.values
    assert "id_token" not in store.values
    assert len(store.batch_calls) == 1
    assert set(store.batch_calls[0][2]) == {"refresh_token", "id_token"}


@pytest.mark.parametrize("redirect_uri", [
    "https://attacker.example/callback",
    "http://192.168.1.10/callback",
    "http://127.0.0.1/other",
    "http://127.0.0.1/callback?next=https://attacker.example",
    "http://user:pass@127.0.0.1/callback",
])
def test_oauth_redirect_uri_must_be_loopback_callback(redirect_uri):
    from core.connected_apps.oauth import OAuthConfig, OAuthState, build_authorization_url

    config = OAuthConfig(
        authorization_url="https://auth.example/authorize",
        token_url="https://auth.example/token",
        client_id="client",
        redirect_uri=redirect_uri,
        scopes=[],
    )
    state = OAuthState(state="state", verifier="verifier", redirect_uri=redirect_uri)
    with pytest.raises(ValueError, match="redirect URI"):
        build_authorization_url(config, state, "challenge")


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "example.com"])
def test_oauth_callback_server_rejects_non_loopback_bind(host):
    from core.connected_apps.oauth import OAuthCallbackServer

    with pytest.raises(ValueError, match="loopback"):
        OAuthCallbackServer(host=host)


@pytest.mark.parametrize("metadata", [
    {"issuer": "https://other.example", "authorization_endpoint": "https://auth.example/authorize", "token_endpoint": "https://auth.example/token"},
    {"issuer": "https://auth.example", "authorization_endpoint": "http://evil.example/authorize", "token_endpoint": "https://auth.example/token"},
    {"issuer": "https://auth.example", "authorization_endpoint": "https://auth.example/authorize", "token_endpoint": "https://auth.example/token", "scopes_supported": "read"},
])
def test_mcp_oauth_discovery_rejects_invalid_authorization_server_metadata(monkeypatch, metadata):
    import core.connected_apps.oauth as oauth

    class Response:
        status_code = 200
        def __init__(self, payload):
            self.payload = payload
        def json(self):
            return self.payload

    class Client:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, url):
            if "oauth-protected-resource" in url:
                return Response({"resource": "https://mcp.example/api", "authorization_servers": ["https://auth.example"]})
            if "oauth-authorization-server" in url:
                return Response(metadata)
            return Response({})

    monkeypatch.setattr("httpx.Client", Client)
    with pytest.raises(RuntimeError):
        oauth.discover_mcp_oauth("https://mcp.example/api")


@pytest.mark.parametrize("scopes", [["read scope"], [""], ["read", "x\nmalicious"]])
def test_oauth_authorization_url_rejects_invalid_scope_tokens(scopes):
    from core.connected_apps.oauth import OAuthConfig, OAuthState, build_authorization_url

    config = OAuthConfig(
        authorization_url="https://auth.example/authorize",
        token_url="https://auth.example/token",
        client_id="client",
        redirect_uri="http://127.0.0.1:1234/callback",
        scopes=scopes,
    )
    state = OAuthState(state="state", verifier="verifier", redirect_uri=config.redirect_uri)
    with pytest.raises(ValueError, match="scopes"):
        build_authorization_url(config, state, "challenge")


def test_oauth_exchange_error_does_not_leak_provider_response(monkeypatch):
    from core.connected_apps.oauth import OAuthConfig, exchange_code

    class Response:
        def raise_for_status(self):
            raise RuntimeError("response contains secret-token")
    monkeypatch.setattr("httpx.post", lambda *args, **kwargs: Response())
    config = OAuthConfig(
        authorization_url="https://auth.example/authorize",
        token_url="https://auth.example/token",
        client_id="client",
        redirect_uri="http://127.0.0.1:1234/callback",
        scopes=[],
    )
    with pytest.raises(RuntimeError) as exc:
        exchange_code(config, "code", "verifier", config.redirect_uri)
    assert "secret-token" not in str(exc.value)
    assert "OAuth token exchange failed" in str(exc.value)


def test_oauth_refresh_error_does_not_leak_provider_response(monkeypatch):
    from core.connected_apps.oauth import OAuthConfig, refresh_tokens

    class Response:
        def raise_for_status(self):
            raise RuntimeError("response contains refresh-secret")
    monkeypatch.setattr("httpx.post", lambda *args, **kwargs: Response())
    config = OAuthConfig(
        authorization_url="https://auth.example/authorize",
        token_url="https://auth.example/token",
        client_id="client",
        redirect_uri="",
        scopes=[],
    )
    with pytest.raises(RuntimeError) as exc:
        refresh_tokens(config, "refresh-token")
    assert "refresh-secret" not in str(exc.value)
    assert "OAuth token refresh failed" in str(exc.value)


def test_oauth_revoke_local_removes_all_credentials_and_refresh_lock():
    class Store:
        def __init__(self):
            self.values = {"access_token": "a", "refresh_token": "r", "id_token": "i"}
        def delete_credentials(self, cid):
            self.values.clear()

    store = Store()
    service = OAuthService(store)
    service._get_refresh_lock("c1")
    assert "c1" in service._refresh_locks
    service.revoke_local("c1")
    assert store.values == {}
    assert "c1" not in service._refresh_locks
