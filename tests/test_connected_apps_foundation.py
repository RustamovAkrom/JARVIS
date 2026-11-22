from __future__ import annotations
import json
from core.connected_apps.models import AppDescriptor, TransportType
from core.connected_apps.registry import AppRegistry
from core.connected_apps.store import ConnectedAppsStore
from core.connected_apps.oauth import create_pkce, OAuthConfig, OAuthState, build_authorization_url

def test_registry_search_and_duplicate(tmp_path):
    registry=AppRegistry()
    app=AppDescriptor(id="demo",name="Demo App",description="Example MCP",transport=TransportType.STDIO)
    registry.register(app)
    assert registry.get("demo") is app
    assert registry.search("example")==[app]
    try:
        registry.register(app)
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate descriptor must fail")

def test_store_roundtrip_and_remove(tmp_path,monkeypatch):
    store=ConnectedAppsStore(tmp_path/"apps.json")
    monkeypatch.setattr("core.connected_apps.store.secretstore.protect",lambda value,name:"protected:"+value)
    monkeypatch.setattr("core.connected_apps.store.secretstore.reveal",lambda stored,name:stored.removeprefix("protected:"))
    row={"id":"c1","app_id":"demo","display_name":"Demo","status":"connected","metadata":{}}
    store.upsert_connection(row)
    assert store.get_connection("c1")["display_name"]=="Demo"
    store.set_credential("c1","access_token","secret")
    assert store.get_credential("c1","access_token")=="secret"
    assert store.remove_connection("c1") is True
    assert store.get_connection("c1") is None
    assert store.credential_names("c1")==[]

def test_pkce_and_authorization_url():
    verifier,challenge=create_pkce()
    assert verifier and challenge and verifier!=challenge
    config=OAuthConfig("https://example.com/authorize","https://example.com/token","client","http://127.0.0.1/callback",["read"])
    state=OAuthState("state-value",verifier,config.redirect_uri)
    url=build_authorization_url(config,state,challenge)
    assert "code_challenge_method=S256" in url
    assert "state=state-value" in url
    assert "scope=read" in url

def test_oauth_extra_parameters_cannot_override_pkce_security_fields():
    config = OAuthConfig(
        "https://example.com/authorize",
        "https://example.com/token",
        "client",
        "http://127.0.0.1/callback",
        ["read"],
        extra_authorize_params={
            "state": "attacker-state",
            "redirect_uri": "https://attacker.example/callback",
            "code_challenge": "attacker-challenge",
            "code_challenge_method": "plain",
        },
    )
    state = OAuthState("expected-state", "verifier", config.redirect_uri)
    url = build_authorization_url(config, state, "expected-challenge")
    from urllib.parse import parse_qs, urlparse

    params = parse_qs(urlparse(url).query)
    assert params["state"] == ["expected-state"]
    assert params["redirect_uri"] == [config.redirect_uri]
    assert params["code_challenge"] == ["expected-challenge"]
    assert params["code_challenge_method"] == ["S256"]

def test_mcp_catalog_search_and_templates_are_reviewable():
    from core.connected_apps.catalog import list_templates, get_template
    assert get_template("filesystem") is not None
    assert list_templates("browser")[0].id == "playwright"
    assert list_templates("not-a-template") == []
