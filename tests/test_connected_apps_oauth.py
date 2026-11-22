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

def test_oauth_reauthorization_clears_stale_refresh_token_and_client_secret():
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

    store = Store()
    service = OAuthService(store)
    store.set_credential("c1", "refresh_token", "old-refresh")
    store.set_credential("c1", "oauth_client_secret", "old-secret")
    service._save("c1", {"access_token": "new-access"})
    store.delete_credential("c1", "refresh_token")
    store.delete_credential("c1", "oauth_client_secret")
    assert store.get_credential("c1", "access_token") == "new-access"
    assert store.get_credential("c1", "refresh_token") == ""
    assert store.get_credential("c1", "oauth_client_secret") == ""
