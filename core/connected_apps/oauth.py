"""OAuth 2.0 Authorization Code + PKCE helpers."""
from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
    # Provider-specific extras must never override PKCE/state/redirect fields.
    params = dict(config.extra_authorize_params or {})
    params.update({
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "scope": " ".join(config.scopes),
        "state": state.state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    separator = "&" if "?" in config.authorization_url else "?"
    return config.authorization_url + separator + urllib.parse.urlencode(params)


class OAuthCallbackServer:
    """One-shot loopback callback with strict state validation."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.host, self.port = host, port
        self.state = OAuthState(
            state=secrets.token_urlsafe(32),
            verifier="",
            redirect_uri="",
            event=threading.Event(),
        )
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._callback_lock = threading.Lock()

    def start(self):
        if self._server is not None:
            raise RuntimeError("OAuth callback server is already started")
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                if parsed.path != "/callback":
                    self.send_response(404)
                    body = b"Not found."
                else:
                    supplied = query.get("state", [""])[0]
                    with owner._callback_lock:
                        if owner.state.event.is_set():
                            self.send_response(409)
                            body = b"Authorization callback already received."
                        elif not secrets.compare_digest(supplied, owner.state.state):
                            self.send_response(400)
                            body = b"Invalid OAuth state. You can close this window."
                        elif query.get("error"):
                            owner.state.error = query["error"][0][:200]
                            self.send_response(400)
                            body = b"Authorization was not completed. You can close this window."
                        elif not query.get("code") or not query["code"][0]:
                            owner.state.error = "OAuth callback did not contain a code"
                            self.send_response(400)
                            body = b"Missing authorization code."
                        else:
                            owner.state.code = query["code"][0]
                            self.send_response(200)
                            body = b"JARVIS authorization received. You can close this window."
                        if owner.state.code or owner.state.error:
                            owner.state.event.set()
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                return

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self.state.redirect_uri = f"http://{self.host}:{self.port}/callback"
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="oauth-callback",
        )
        self._thread.start()
        return self.state

    def wait(self, timeout: float = 180):
        if not self.state.event.wait(timeout):
            self.state.error = "OAuth authorization timed out"
        return self.state

    def close(self):
        server = self._server
        if server is None:
            return
        server.shutdown()
        server.server_close()
        self._server = None
        thread = self._thread
        self._thread = None
        if thread and thread.is_alive():
            thread.join(timeout=2)


def _validate_metadata_url(value: str) -> str:
    parsed = urllib.parse.urlparse(str(value or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("OAuth metadata contains an invalid URL")
    return str(value)


def discover_mcp_oauth(resource_url: str) -> dict[str, Any]:
    """Discover OAuth metadata for an MCP protected resource."""
    import httpx

    resource_url = _validate_metadata_url(resource_url)
    parsed = urllib.parse.urlparse(resource_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    resource_path = parsed.path.rstrip("/")
    candidates = [
        origin + "/.well-known/oauth-protected-resource" + resource_path,
        origin + "/.well-known/oauth-protected-resource",
    ]
    resource_metadata = None
    with httpx.Client(timeout=10, follow_redirects=False) as client:
        for url in dict.fromkeys(candidates):
            try:
                response = client.get(url)
                if response.status_code == 200:
                    resource_metadata = response.json()
                    if isinstance(resource_metadata, dict):
                        break
            except (httpx.HTTPError, ValueError):
                continue
        if not isinstance(resource_metadata, dict):
            raise RuntimeError("MCP server does not expose OAuth protected-resource metadata")
        servers = resource_metadata.get("authorization_servers") or []
        if not servers:
            raise RuntimeError("OAuth resource metadata did not advertise an authorization server")
        issuer = _validate_metadata_url(servers[0])
        issuer_parsed = urllib.parse.urlparse(issuer)
        issuer_origin = f"{issuer_parsed.scheme}://{issuer_parsed.netloc}"
        issuer_path = issuer_parsed.path.rstrip("/")
        metadata_urls = [
            issuer_origin + "/.well-known/oauth-authorization-server" + issuer_path,
            issuer_origin + "/.well-known/openid-configuration" + issuer_path,
            issuer.rstrip("/") + "/.well-known/oauth-authorization-server",
            issuer.rstrip("/") + "/.well-known/openid-configuration",
        ]
        authorization_metadata = None
        for url in dict.fromkeys(metadata_urls):
            try:
                response = client.get(url)
                if response.status_code == 200:
                    payload = response.json()
                    if isinstance(payload, dict) and payload.get("authorization_endpoint") and payload.get("token_endpoint"):
                        authorization_metadata = payload
                        break
            except (httpx.HTTPError, ValueError):
                continue
        if not isinstance(authorization_metadata, dict):
            raise RuntimeError("Could not discover OAuth authorization-server metadata")
    return {
        "resource": resource_metadata,
        "issuer": issuer,
        "authorization_server": authorization_metadata,
    }


def exchange_code(config: OAuthConfig, code: str, verifier: str, redirect_uri: str) -> dict[str, Any]:
    """Exchange code for tokens using a lazily imported HTTP client."""
    import httpx

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": config.client_id,
        "code_verifier": verifier,
    }
    if config.client_secret:
        payload["client_secret"] = config.client_secret
    response = httpx.post(
        config.token_url,
        data=payload,
        headers={"Accept": "application/json"},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or not data.get("access_token"):
        raise RuntimeError("OAuth token response did not contain an access_token")
    return data


def refresh_tokens(config: OAuthConfig, refresh_token: str) -> dict[str, Any]:
    """Refresh OAuth tokens when the provider supports refresh_token grants."""
    import httpx

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": config.client_id,
    }
    if config.client_secret:
        payload["client_secret"] = config.client_secret
    response = httpx.post(
        config.token_url,
        data=payload,
        headers={"Accept": "application/json"},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or not data.get("access_token"):
        raise RuntimeError("OAuth refresh response did not contain an access_token")
    return data


def authorize(config: OAuthConfig, *, timeout: float = 180, open_browser: bool = True) -> dict[str, Any]:
    """Run PKCE authorization in the user's browser and return token response."""
    import webbrowser

    callback = OAuthCallbackServer()
    state = callback.start()
    verifier, challenge = create_pkce()
    state.verifier = verifier
    config.redirect_uri = state.redirect_uri
    url = build_authorization_url(config, state, challenge)
    try:
        if open_browser and not webbrowser.open(url):
            raise RuntimeError("Could not open the authorization browser")
        result = callback.wait(timeout)
        if result.error:
            raise RuntimeError(result.error)
        return exchange_code(config, result.code, result.verifier, result.redirect_uri)
    finally:
        callback.close()
