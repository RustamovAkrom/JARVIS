"""OAuth provider configuration catalog (no secrets or hard-coded credentials)."""
from __future__ import annotations
from dataclasses import dataclass
from core.connected_apps.oauth import OAuthConfig

@dataclass(frozen=True,slots=True)
class OAuthProvider:
    id:str
    name:str
    authorization_url:str
    token_url:str
    default_scopes:tuple[str,...]=()
    supports_pkce:bool=True
    extra_authorize_params:dict[str,str]|None=None

    def config(self,client_id:str,redirect_uri:str,scopes=None,client_secret=""):
        return OAuthConfig(authorization_url=self.authorization_url,token_url=self.token_url,
            client_id=client_id,redirect_uri=redirect_uri,scopes=list(scopes or self.default_scopes),
            client_secret=client_secret,extra_authorize_params=self.extra_authorize_params)

PROVIDERS={
    "github":OAuthProvider("github","GitHub","https://github.com/login/oauth/authorize",
        "https://github.com/login/oauth/access_token",("repo","read:user","user:email"),
        extra_authorize_params={"allow_signup":"true"}),
    "google":OAuthProvider("google","Google","https://accounts.google.com/o/oauth2/v2/auth",
        "https://oauth2.googleapis.com/token",("openid","email","profile"),
        extra_authorize_params={"access_type":"offline","prompt":"consent"}),
    "microsoft":OAuthProvider("microsoft","Microsoft","https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "https://login.microsoftonline.com/common/oauth2/v2.0/token",("openid","profile","offline_access")),
}
def list_providers():return list(PROVIDERS.values())
def get_provider(provider_id):return PROVIDERS.get(str(provider_id or "").casefold())
