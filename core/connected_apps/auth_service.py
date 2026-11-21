"""Provider-configured OAuth authorization and refresh lifecycle."""
from __future__ import annotations
import time
from core.connected_apps.providers import get_provider
from core.connected_apps.oauth import authorize,refresh_tokens

class OAuthService:
    def __init__(self,store):self.store=store
    def authorize_connection(self,connection_id,provider_id,client_id,scopes=None,client_secret=""):
        provider=get_provider(provider_id)
        if not provider:raise ValueError(f"Unknown OAuth provider: {provider_id}")
        selected_scopes=list(scopes or provider.default_scopes)
        config=provider.config(client_id,"",selected_scopes,client_secret)
        tokens=authorize(config)
        self._save(connection_id,tokens)
        self.store.set_credential(connection_id,"oauth_provider",provider.id)
        self.store.set_credential(connection_id,"oauth_client_id",client_id)
        if client_secret:self.store.set_credential(connection_id,"oauth_client_secret",client_secret)
        self.store.set_credential(connection_id,"oauth_scopes"," ".join(selected_scopes))
        return {"provider":provider.id,"scopes":tokens.get("scope",selected_scopes),
            "expires_at":time.time()+int(tokens.get("expires_in",0)) if tokens.get("expires_in") else None}
    def _save(self,cid,tokens):
        for key in ("access_token","refresh_token","id_token"):
            value=tokens.get(key)
            if value:self.store.set_credential(cid,key,str(value))
        self.store.set_credential(cid,"token_type",str(tokens.get("token_type","Bearer")))
        self.store.set_credential(cid,"expires_at",str(time.time()+int(tokens.get("expires_in",0))))
    def get_access_token(self,cid,refresh=True):
        token=self.store.get_credential(cid,"access_token")
        expires=float(self.store.get_credential(cid,"expires_at") or 0)
        if token and expires>time.time()+60:return token
        refresh_token=self.store.get_credential(cid,"refresh_token")
        provider=get_provider(self.store.get_credential(cid,"oauth_provider"))
        client_id=self.store.get_credential(cid,"oauth_client_id")
        client_secret=self.store.get_credential(cid,"oauth_client_secret")
        scopes=self.store.get_credential(cid,"oauth_scopes").split()
        if not refresh or not refresh_token or not provider or not client_id:
            raise RuntimeError("OAuth authorization required; token expired or unavailable")
        config=provider.config(client_id,"",scopes,client_secret)
        updated=refresh_tokens(config,refresh_token)
        self._save(cid,updated)
        return self.store.get_credential(cid,"access_token")
    def revoke_local(self,cid):self.store.delete_credentials(cid)
