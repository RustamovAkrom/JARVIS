"""
plugins/_google_core.py — shared Google OAuth + API layer for the JARVIS
Google plugin suite (gmail_control, calendar_control, …).

The leading underscore matters: core/plugin_loader.py SKIPS files starting with
"_", so this is a shared helper, never loaded as a plugin. The real plugins
import from it:

    from plugins._google_core import build_settings, get_service, explain, GoogleError

Design goals — this ships to a WHOLE COMMUNITY, so:
  • NOTHING is hard-coded. Every user connects THEIR OWN Google account with
    THEIR OWN OAuth "Desktop app" client id/secret, entered in the ⚙ plugin-
    settings tab (the token screen). No shared secret is baked into the code.
  • Credentials + the resulting token live ONLY in config/api_keys.json under
    plugin_config[<namespace>]  (via memory.config_manager). Never in code.
  • Cross-OS: the OAuth consent uses a loopback local-server flow, which works
    the same on Windows, macOS and Linux (it just opens the user's browser).
  • Language-neutral: this file returns English status/errors; the plugins hand
    English directives to JARVIS, which re-speaks them in the user's language.
  • Every way the setup goes wrong gets its own plain answer naming the one
    missing step, and the Cloud Console page for it is opened when the user is
    there to see it. Google's own error pages never reach the app (a blocked
    sign-in simply never comes back), so the sign-in wait has an end and says
    what usually caused it.

Third-party deps (installed by requirements.txt, imported LAZILY so a missing
one can never stop the module importing or the plugin loading):
    google-auth-oauthlib   google-api-python-client   google-auth

How a user sets one up (put this in each plugin's field placeholders / docs):
  1. console.cloud.google.com → create a project → enable the Gmail / Calendar API
  2. OAuth consent screen → External → add yourself as a test user
  3. Credentials → Create → OAuth client ID → "Desktop app"
  4. Paste the Client ID + Client secret into the plugin settings, press AUTHORIZE
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import webbrowser

from memory.config_manager import get_plugin_config, save_plugin_config


# ── error taxonomy (plugins translate these into calm spoken guidance) ───────
class GoogleError(Exception):
    pass


class GoogleConfigError(GoogleError):
    """No client id/secret entered yet."""


class GoogleAuthError(GoogleError):
    """Not authorized yet, or the stored token is unusable."""


class GoogleDependencyError(GoogleError):
    """A required Google library is not installed."""


_TOKEN_KEY = "token"          # where the serialized OAuth credentials live, per namespace
_SINCE_KEY = "authorized_at"  # when that token was granted (epoch seconds)
_TOKEN_URI = "https://oauth2.googleapis.com/token"
_ID_SUFFIX = ".apps.googleusercontent.com"   # every Google OAuth client id ends so
_CONSOLE = "https://console.cloud.google.com"
_WAIT = 180                   # seconds the sign-in may take before we say why it didn't come back
# While a consent screen is in "Testing", Google ends its grants after 7 days.
_TESTING_LIFE = 7 * 86400

# One sign-in at a time per namespace; what the last background one ended with.
_running: dict[str, threading.Thread] = {}
_last_failure: dict[str, str] = {}
_lock = threading.Lock()


# ── reading what the user pasted ─────────────────────────────────────────────
def _clean(s) -> str:
    return str(s or "").strip().strip("\"'").strip()


def _project_number(client_id: str) -> str:
    """The project number Google puts in front of every client id."""
    m = re.match(r"(\d+)-", client_id or "")
    return m.group(1) if m else ""


def console_url(path: str, client_id: str = "") -> str:
    """A Cloud Console page, opened on the user's own project when we know it."""
    num = _project_number(client_id)
    return f"{_CONSOLE}/{path}" + (f"?project={num}" if num else "")


def read_client(values: dict) -> tuple[str, str, str]:
    """(client_id, client_secret, problem) from what was typed. Accepts the
    downloaded client JSON pasted into either field, and the two fields typed
    the wrong way round; `problem` is a plain English sentence or ""."""
    cid = _clean(values.get("client_id"))
    sec = _clean(values.get("client_secret"))
    for raw in (cid, sec):
        if raw.startswith("{"):
            try:
                blob = json.loads(raw)
            except ValueError:
                return cid, sec, ("The text pasted into the settings looks like the downloaded "
                                  "client file but isn't complete. Paste the whole file, or just "
                                  "the Client ID and Client Secret.")
            kind = next((k for k in ("installed", "web") if isinstance(blob.get(k), dict)), "")
            inner = blob.get(kind) or blob
            cid = _clean(inner.get("client_id"))
            sec = _clean(inner.get("client_secret"))
            if kind == "web":
                return cid, sec, _WRONG_TYPE
            break
    if not cid.endswith(_ID_SUFFIX) and sec.endswith(_ID_SUFFIX):
        cid, sec = sec, cid
    if not cid or not sec:
        return cid, sec, "Enter your OAuth Client ID and Client Secret first."
    if not cid.endswith(_ID_SUFFIX) or " " in cid:
        return cid, sec, ("That Client ID doesn't look like one from Google — it ends in "
                          f"'{_ID_SUFFIX}'. Copy it again from Google Cloud → Credentials.")
    return cid, sec, ""


_WRONG_TYPE = ("This OAuth client is a 'Web application'. Torvis needs a 'Desktop app' client: "
               "in Google Cloud → Credentials press Create credentials → OAuth client ID → "
               "Desktop app, and paste that one's Client ID and Secret.")


def check_client(client_id: str, client_secret: str) -> str:
    """Ask Google whether this id/secret pair exists, before any browser opens.
    A made-up code is exchanged: a real client gets 'bad code', an unknown one
    'invalid_client'. Returns a plain sentence, or "" when fine or unsure."""
    try:
        import requests
        r = requests.post(_TOKEN_URI, timeout=10, data={
            "grant_type": "authorization_code", "code": "check",
            "client_id": client_id, "client_secret": client_secret,
            "redirect_uri": "http://localhost"})
        err = (r.json() or {}).get("error", "")
    except Exception:
        return ""                       # offline or odd answer: the sign-in will tell
    if err == "invalid_client":
        return ("Google doesn't accept this Client ID and Secret together. Copy both again from "
                "the same 'Desktop app' client in Google Cloud → Credentials (the secret is "
                "shown when you open the client).")
    return ""


# ── settings-schema builder (identical fields for every Google plugin) ───────
def build_settings(namespace: str, title: str, scopes: list[str]) -> dict:
    """Return a ready PLUGIN_SETTINGS dict: client id/secret fields + an
    AUTHORIZE button that runs the browser consent flow for `scopes` and stores
    the token under `namespace`. Each plugin just assigns the result to its
    module-level PLUGIN_SETTINGS constant."""
    def _authorize(values: dict):
        return authorize(namespace, scopes, values or {})

    return {
        "namespace": namespace,
        "title": title,
        "fields": [
            {"key": "client_id", "label": "OAuth Client ID", "type": "text",
             "placeholder": "…apps.googleusercontent.com  (Google Cloud → Desktop app)"},
            {"key": "client_secret", "label": "OAuth Client Secret", "type": "password",
             "placeholder": "GOCSPX-…  (from the same OAuth client)"},
        ],
        "action": {"label": "AUTHORIZE (opens your browser)", "run": _authorize},
    }


# ── OAuth: the AUTHORIZE button ──────────────────────────────────────────────
def _blocked_advice(client_id: str) -> str:
    return ("Google didn't send the sign-in back. If the browser said 'Access blocked' or "
            "'Error 403: access_denied': open Google Auth Platform → Audience and add the Google "
            "address you signed in with under Test users (or press Publish app), then try again. "
            "If it said 'redirect_uri_mismatch', the client must be a 'Desktop app' one. "
            f"The page is: {console_url('auth/audience', client_id)}")


def _missing_scopes(token: dict, scopes: list[str]) -> list[str]:
    granted = token.get("scope") or []
    if isinstance(granted, str):
        granted = granted.split()
    return [s for s in scopes if s not in granted] if granted else []


def authorize(namespace: str, scopes: list[str], values: dict, open_help: bool = False):
    """Run Google's loopback consent flow and store the token. Returns
    (ok: bool, message: str) — shown verbatim in the settings panel.
    `open_help` opens the Cloud Console page that fixes a blocked sign-in
    (used when nobody is looking at the settings panel)."""
    cfg = {**get_plugin_config(namespace), **(values or {})}
    client_id, client_secret, problem = read_client(cfg)
    if problem:
        return False, problem
    if (client_id, client_secret) != (_clean(cfg.get("client_id")), _clean(cfg.get("client_secret"))):
        save_plugin_config(namespace, {"client_id": client_id, "client_secret": client_secret})

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow, WSGITimeoutError
    except ImportError:
        return False, ("Missing library. Run:  pip install google-auth-oauthlib "
                       "google-api-python-client")

    problem = check_client(client_id, client_secret)
    if problem:
        return False, problem

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": _TOKEN_URI,
            "redirect_uris": ["http://localhost"],
        }
    }
    # A user may untick a permission on Google's page; we check what was
    # granted ourselves instead of oauthlib raising a bare "Scope has changed".
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
    try:
        flow = InstalledAppFlow.from_client_config(client_config, scopes=scopes)
        # port=0 → pick any free port; opens the browser, waits for consent.
        creds = flow.run_local_server(port=0, prompt="consent", timeout_seconds=_WAIT,
                                      authorization_prompt_message="")
    except WSGITimeoutError:
        print(f"[Google] {namespace}: sign-in never came back")
        if open_help:
            _open(console_url("auth/audience", client_id))
        return False, _blocked_advice(client_id)
    except Exception as e:
        code = str(getattr(e, "error", "") or "")
        print(f"[Google] {namespace}: sign-in failed ({type(e).__name__}: {code or e})")
        if code == "access_denied":
            return False, ("Google's page was cancelled or access wasn't allowed. Press Authorize "
                           "again and press Continue / Allow on every step.")
        if code == "invalid_client":
            return False, check_client(client_id, client_secret) or _WRONG_TYPE
        return False, f"Authorization failed or was cancelled: {e}"

    missing = _missing_scopes(getattr(flow.oauth2session, "token", None) or {}, scopes)
    if missing:
        return False, ("Google connected, but not every permission was ticked. Press Authorize "
                       "again and tick every box on Google's page.")

    try:
        save_plugin_config(namespace, {_TOKEN_KEY: creds.to_json(), _SINCE_KEY: int(time.time())})
    except Exception as e:
        return False, f"Authorized, but couldn't save the token: {e}"
    _last_failure.pop(namespace, None)
    return True, "Authorized ✓ — this Google account is now connected."


def _open(url: str) -> None:
    try:
        webbrowser.open(url, new=2)
    except Exception:
        pass


def reconnect(namespace: str, scopes: list[str]) -> bool:
    """Start the browser sign-in in the background (a voice request can't wait
    for it). False when one is already running."""
    with _lock:
        t = _running.get(namespace)
        if t and t.is_alive():
            return False

        def work():
            ok, msg = authorize(namespace, scopes, {}, open_help=True)
            if not ok:
                _last_failure[namespace] = msg

        t = threading.Thread(target=work, daemon=True, name=f"google-signin-{namespace}")
        _running[namespace] = t
        t.start()
        return True


# ── build an API client from the stored token (auto-refreshes) ───────────────
def get_service(namespace: str, api_name: str, api_version: str, scopes: list[str]):
    """Return an authenticated googleapiclient service, refreshing the token if
    needed. Raises GoogleConfigError / GoogleAuthError / GoogleDependencyError so
    the plugin can turn each into friendly spoken guidance (see explain())."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError as e:
        raise GoogleDependencyError(
            "google-api-python-client / google-auth not installed "
            "(pip install google-api-python-client google-auth-oauthlib)"
        ) from e

    cfg = get_plugin_config(namespace)
    client_id, client_secret, problem = read_client(cfg)
    token_json = cfg.get(_TOKEN_KEY)

    if not client_id and not client_secret:
        raise GoogleConfigError("no OAuth client configured")
    if problem:
        raise GoogleConfigError(problem)
    if not token_json:
        raise GoogleAuthError("not authorized yet")

    try:
        info = json.loads(token_json) if isinstance(token_json, str) else dict(token_json)
    except Exception as e:
        raise GoogleAuthError(f"stored token is unreadable: {e}") from e

    # make sure the token blob carries everything Credentials needs
    info.setdefault("client_id", client_id)
    info.setdefault("client_secret", client_secret)
    info.setdefault("token_uri", _TOKEN_URI)

    try:
        creds = Credentials.from_authorized_user_info(info, scopes)
    except Exception as e:
        raise GoogleAuthError(f"could not load credentials: {e}") from e

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                save_plugin_config(namespace, {_TOKEN_KEY: creds.to_json()})
            except Exception as e:
                print(f"[Google] {namespace}: refresh failed ({type(e).__name__}: {e})")
                raise GoogleAuthError(_expired_reason(cfg, e)) from e
        else:
            raise GoogleAuthError("not authorized (no valid token)")

    try:
        return build(api_name, api_version, credentials=creds, cache_discovery=False)
    except Exception as e:
        raise GoogleError(f"couldn't start the Google {api_name} client: {e}") from e


def _expired_reason(cfg: dict, e: Exception) -> str:
    if "invalid_grant" not in str(e):
        return f"session couldn't be renewed ({e})"
    try:
        age = time.time() - float(cfg.get(_SINCE_KEY) or 0)
    except (TypeError, ValueError):
        age = 0
    if cfg.get(_SINCE_KEY) and age >= _TESTING_LIFE - 3600:
        return "testing_expired"
    return "revoked"


def is_configured(namespace: str) -> bool:
    cfg = get_plugin_config(namespace)
    return bool(_clean(cfg.get("client_id")) and cfg.get(_TOKEN_KEY))


# ── turning any failure into one plain sentence ──────────────────────────────
def _http_parts(e) -> tuple[int, set[str], str, str]:
    """(status, reasons, message, fix_url) from a googleapiclient HttpError."""
    status = int(getattr(e, "status_code", 0) or getattr(getattr(e, "resp", None), "status", 0) or 0)
    reasons, message, url = set(), "", ""
    try:
        content = e.content.decode("utf-8", "replace") if isinstance(e.content, bytes) else str(e.content)
        err = (json.loads(content) or {}).get("error") or {}
    except Exception:
        err, content = {}, str(e)
    if isinstance(err, dict):
        message = str(err.get("message") or "")
        reasons.add(str(err.get("status") or ""))
        for item in err.get("errors") or []:
            reasons.add(str(item.get("reason") or ""))
        for d in err.get("details") or []:
            reasons.add(str(d.get("reason") or ""))
            meta = d.get("metadata") or {}
            url = url or str(meta.get("activationUrl") or "")
            for link in d.get("links") or []:
                url = url or str(link.get("url") or "")
    if not url:
        m = re.search(r"https://console\.(?:developers|cloud)\.google\.com/\S+", message or content)
        url = m.group(0).rstrip(".,)") if m else ""
    reasons.discard("")
    return status, reasons, message, url


def explain(e: Exception, namespace: str, scopes: list[str], what: str) -> str:
    """One plain English sentence for any failure of a Google plugin, `what`
    being the product the user knows ("calendar", "Gmail"). Opens the sign-in
    or the fixing page in the browser when that is the next step."""
    if isinstance(e, GoogleDependencyError):
        return ("I need the Google libraries first. Run: pip install "
                "google-api-python-client google-auth-oauthlib")
    if isinstance(e, GoogleConfigError):
        detail = str(e) if str(e).endswith(".") else ""
        return (f"Your {what} isn't set up yet. " + (detail + " " if detail else "")
                + "Open the plugin settings (the ? button shows the steps), add your Google "
                  "OAuth Client ID and Secret, then press Authorize.")
    if isinstance(e, GoogleAuthError):
        earlier = _last_failure.pop(namespace, "")
        if earlier:
            return f"Last time the Google sign-in for your {what} didn't finish. {earlier}"
        reason = str(e)
        if reason == "testing_expired":
            why = ("Google ends access every 7 days while your Google Cloud app is in Testing "
                   "(Google Auth Platform → Audience → Publish app stops that). ")
        elif reason == "revoked":
            why = "Google withdrew access (a password change or a removed permission does that). "
        else:
            why = ""
        if reconnect(namespace, scopes):
            return (f"{why}I opened Google's sign-in page in your browser to connect your {what} — "
                    "choose your account, allow access, then ask me again.")
        return (f"{why}Google's sign-in page is already open in your browser — finish it there, "
                "then ask me again.")
    if type(e).__name__ == "HttpError":
        status, reasons, message, url = _http_parts(e)
        print(f"[Google] {namespace}: HTTP {status} {sorted(reasons)} {message[:200]}")
        if reasons & {"accessNotConfigured", "SERVICE_DISABLED"}:
            if url:
                _open(url)
            return (f"The Google {what} API is switched off in your Google Cloud project. "
                    + ("I opened its page — press Enable, " if url else
                       "Open Google Cloud → APIs & Services → Library, enable it, ")
                    + "wait a minute, then ask me again.")
        if status == 401 or reasons & {"ACCESS_TOKEN_SCOPE_INSUFFICIENT", "insufficientPermissions",
                                       "authError"}:
            reconnect(namespace, scopes)
            return (f"Google didn't give me every permission for your {what}. I opened the sign-in "
                    "page — tick every box and allow it, then ask me again.")
        if status == 429 or reasons & {"rateLimitExceeded", "userRateLimitExceeded",
                                       "RATE_LIMIT_EXCEEDED", "quotaExceeded"}:
            return f"Google says your {what} had too many requests just now — try again in a minute."
        if status >= 500:
            return f"Google's {what} service had a problem on its side — try again in a moment."
        return f"Google refused that {what} request: {message or e}"
    try:
        from httplib2 import HttpLib2Error as _NetError
    except ImportError:
        _NetError = OSError
    if isinstance(e, (OSError, _NetError)):
        return f"I couldn't reach Google for your {what} — check the internet connection."
    return f"Sorry, the {what} request failed: {e}"
