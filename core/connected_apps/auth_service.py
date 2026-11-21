"""Provider-configured OAuth authorization and token lifecycle."""
from __future__ import annotations
import time
from core.connected_apps.providers import get_provider
from core.connected_apps.oauth import authorize,refresh_tokens

class OAuthService:
    def __init__(self,store):self.store=store
    def authorize_connection(self,connection_id,provider_id,client_id,scopes=None,client_secret=""):
        provider=get_provider(provider_id)
        if not provider:raise ValueError(f"Unknown OAuth provider: {provider_id}")
        config=provider.config(client_id,"",scopes,client_secret)
        tokens=authorize(config)
        self._save(connection_id,tokens)
        return {"provider":provider.id,"scopes":tokens.get("scope",scopes or provider.default_scopes),
                "expires_at":time.time()+int(tokens.get("expires_in",0)) if tokens.get("expires_in") else None}
    def _save(self,cid,tokens):
        for key in ("access_token","refresh_token","id_token"):
            value=tokens.get(key)
            if value:self.store.set_credential(cid,key,str(value))
        self.store.set_credential(cid,"token_type",str(tokens.get("token_type","Bearer")))
        self.store.set_credential(cid,"expires_at",str(time.time()+int(tokens.get("expires_in",0))))
    def get_access_token(self,cid,config=None):
        token=self.store.get_credential(cid,"access_token")
        expires=float(self.store.get_credential(cid,"expires_at") or 0)
        if token and expires>time.time()+60:return token
        refresh=self.store.get_credential(cid,"refresh_token")
        if not refresh or config is None:raise RuntimeError("Authorization required; token expired or unavailable")
        updated=refresh_tokens(config,refresh);self._save(cid,updated)
        return self.store.get_credential(cid,"access_token")
    def revoke_local(self,cid):self.store.delete_credentials(cid)
