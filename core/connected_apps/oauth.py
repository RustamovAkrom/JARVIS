"""OAuth 2.0 Authorization Code + PKCE helpers for provider integrations.

Provider-specific endpoints/scopes must be supplied by a descriptor. This module
never assumes that every service uses the same OAuth configuration.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from dataclasses import dataclass
from typing import Any

@dataclass(slots=True)
class OAuthConfig:
    authorization_url: str
    token_url: str
    client_id: str
    redirect_uri: str
    scopes: list[str]
    client_secret: str = ""
    extra_authorize_params: dict[str, str] | None = None

@dataclass(slots=True)
class OAuthState:
    state: str
    verifier: str
    redirect_uri: str
    code: str = ""
    error: str = ""
    event: threading.Event | None = None

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

def create_pkce() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge

def build_authorization_url(config: OAuthConfig, state: OAuthState, challenge: str) -> str:
    params = {
        "response_type": "code", "client_id": config.client_id,
        "redirect_uri": config.redirect_uri, "scope": " ".join(config.scopes),
        "state": state.state, "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    params.update(config.extra_authorize_params or {})
    return config.authorization_url + ("&" if "?" in config.authorization_url else "?") + urllib.parse.urlencode(params)

class OAuthCallbackServer:
    """One-shot loopback callback with strict state validation."""
    def __init__(self, host="127.0.0.1", port=0):
        self.host, self.port = host, port
        self.state = OAuthState(state=secrets.token_urlsafe(32), verifier="",
                                redirect_uri="", event=threading.Event())
        self._server = None
        self._thread = None

    def start(self):
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                supplied = query.get("state", [""])[0]
                if not secrets.compare_digest(supplied, owner.state.state):
                    owner.state.error = "OAuth state validation failed"
                    self.send_response(400)
                    body = b"Invalid OAuth state. You can close this window."
                elif query.get("error"):
                    owner.state.error = query["error"][0]
                    self.send_response(400)
                    body = b"Authorization was not completed. You can close this window."
                elif not query.get("code"):
                    owner.state.error = "OAuth callback did not contain a code"
                    self.send_response(400)
                    body = b"Missing authorization code."
                else:
                    owner.state.code = query["code"][0]
                    self.send_response(200)
                    body = b"JARVIS authorization received. You can close this window."
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                owner.state.event.set()
            def log_message(self, *_):
                return
        self._server = HTTPServer((self.host, self.port), Handler)
        self.port = self._server.server_address[1]
        self.state.redirect_uri = f"http://{self.host}:{self.port}/callback"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="oauth-callback")
        self._thread.start()
        return self.state

    def wait(self, timeout=180):
        if not self.state.event.wait(timeout):
            self.state.error = "OAuth authorization timed out"
        return self.state

    def close(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

def exchange_code(config: OAuthConfig, code: str, verifier: str, redirect_uri: str) -> dict[str, Any]:
    """Exchange code for tokens. HTTP client is optional and imported lazily."""
    import httpx
    payload = {"grant_type":"authorization_code", "code":code, "redirect_uri":redirect_uri,
               "client_id":config.client_id, "code_verifier":verifier}
    if config.client_secret:
        payload["client_secret"] = config.client_secret
    response = httpx.post(config.token_url, data=payload, headers={"Accept":"application/json"}, timeout=30)
    response.raise_for_status()
    data=response.json()
    if not isinstance(data, dict) or not data.get("access_token"):
        raise RuntimeError("OAuth token response did not contain an access_token")
    return data

def refresh_tokens(config: OAuthConfig, refresh_token: str) -> dict[str, Any]:
    """Refresh OAuth tokens when the provider supports refresh_token grants."""
    import httpx
    payload={"grant_type":"refresh_token","refresh_token":refresh_token,"client_id":config.client_id}
    if config.client_secret:
        payload["client_secret"]=config.client_secret
    response=httpx.post(config.token_url,data=payload,headers={"Accept":"application/json"},timeout=30)
    response.raise_for_status()
    data=response.json()
    if not isinstance(data,dict) or not data.get("access_token"):
        raise RuntimeError("OAuth refresh response did not contain an access_token")
    return data

def authorize(config: OAuthConfig, *, timeout=180, open_browser=True) -> dict[str, Any]:
    """Run PKCE authorization in the user's browser and return token response."""
    import webbrowser
    callback=OAuthCallbackServer()
    state=callback.start()
    verifier,challenge=create_pkce()
    state.verifier=verifier
    config.redirect_uri=state.redirect_uri
    url=build_authorization_url(config,state,challenge)
    try:
        if open_browser:
            if not webbrowser.open(url):
                raise RuntimeError("Could not open the authorization browser")
        result=callback.wait(timeout)
        if result.error:
            raise RuntimeError(result.error)
        return exchange_code(config,result.code,result.verifier,result.redirect_uri)
    finally:
        callback.close()
