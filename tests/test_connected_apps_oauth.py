from core.connected_apps.providers import get_provider,list_providers
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
