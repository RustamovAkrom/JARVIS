import pytest\nfrom core.connected_apps.providers import get_provider,list_providers
from core.connected_apps.auth_service import OAuthService

def test_provider_catalog_contains_supported_providers():
    ids={provider.id for provider in list_providers()}
    assert {"github","google","microsoft"} <= ids
    assert get_provider("github") is not None

def test_oauth_token_storage_and_non_expiring_token(monkeypatch):
    class Store:
        def __init__(self):self.values={}
        def set_credential(self,cid,key,value):self.values[(cid,key)]=value
        def get_credential(self,cid,key):return self.values.get((cid,key),"")
    store=Store();service=OAuthService(store)
    service._save("c1",{"access_token":"token","token_type":"Bearer"})
    assert service.get_access_token("c1")=="token"


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
