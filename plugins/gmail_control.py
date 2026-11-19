"""
Plugin — Gmail control.

"Read me my new emails", "any unread from my boss?", "reply to Ayşe saying I'll
be there at five", "draft an email to the team about tomorrow's demo". JARVIS
checks, reads, searches, sends and drafts Gmail by voice.

FOR EVERYONE, not one inbox: there are no account details in this file. Each
user connects THEIR OWN Google account in the ⚙ plugin-settings tab (enter an
OAuth Client ID/Secret, press AUTHORIZE — the browser consent screen is where
they pick which Gmail account). Cross-OS and every-language: spoken lines are
English directives that JARVIS re-speaks in the user's language; email content
stays in whatever language it was written.

Requires:  pip install google-api-python-client google-auth-oauthlib
(the plugin still loads and explains this if they're missing).
"""
from __future__ import annotations

import base64
from email.message import EmailMessage

from plugins._google_core import (
    build_settings, get_service,
    explain,
)

_NS = "gmail"
_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",  # read + mark-as-read
    "https://www.googleapis.com/auth/gmail.send",    # send / draft
]

PLUGIN = {
    # For the plugin manager (core/plugin_loader): never sent to a model.
    "level": "advanced",
    "label": "Gmail",
    "summary": "Reads, searches and sends your Gmail; sending waits for your approval.",
    "setup": [
        "This one needs a free Google Cloud key of your own (about 10 minutes, once).",
        "Go to console.cloud.google.com and create a project (any name).",
        "APIs & Services → Library: search for the Gmail API and press Enable.",
        "APIs & Services → OAuth consent screen (Google Auth Platform): choose External and fill in the app name and your e-mail.",
        "Under Audience, add your own Google address as a Test user — or press Publish app, so Google doesn't ask you to sign in again every 7 days.",
        "APIs & Services → Credentials → Create credentials → OAuth client ID → Desktop app. Copy the Client ID and the Client Secret into these settings and save.",
        "Press Authorize, or just ask me: a browser window asks you to allow access — tick every box and allow it once. If Google says 'Access blocked', the Test user step is missing.",
        "Say: \"Do I have new e-mail?\" or \"Reply to Ali that I'll be late.\"",
    ],
    "name": "gmail_control",
    "description": (
        "Reads, searches, sends and drafts the user's GMAIL by voice. Use for: "
        "'check my email / any new emails', 'read my latest unread', 'do I have "
        "anything from <person>', 'search my mail for <topic>', 'send an email to "
        "<person> saying <message>', 'draft an email to <person> about <topic>', "
        "'mark it as read'. Extract recipients/subject/body/search terms into the "
        "parameters. Only SEND when the user clearly asks to send; otherwise "
        "prefer 'draft'."
    ),
    # Used in place of `description` only when a small model is on a tool
    # budget - Gemini still gets the full text. See providers/schema.py.
    "brief": (
        "Reads, searches, sends and drafts email in Gmail."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "'check' summarise unread, 'read' read one message, 'search' "
                    "find messages, 'send' send an email, 'draft' save a draft, "
                    "'mark_read' mark a message read. Default 'check'."
                ),
                "enum": ["check", "read", "search", "send", "draft", "mark_read"],
            },
            "query": {
                "type": "STRING",
                "description": ("Gmail search terms for read/search/mark_read, e.g. "
                                "'from:ayse', 'invoice', 'is:unread newer_than:2d'."),
            },
            "to": {"type": "STRING", "description": "Recipient email address(es) for send/draft."},
            "subject": {"type": "STRING", "description": "Subject line for send/draft."},
            "body": {"type": "STRING", "description": "Message body for send/draft, in the user's language."},
            "count": {"type": "INTEGER", "description": "How many messages to list for check/search (default 5)."},
        },
        "required": [],
    },
}

# Rendered by the ⚙ plugin-settings "token" tab — client id/secret + AUTHORIZE.
PLUGIN_SETTINGS = build_settings(_NS, "📧  GMAIL", _SCOPES)


# ── message parsing helpers (pure — unit-testable) ───────────────────────────

def _header(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _decode_b64(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", "replace")
    except Exception:
        return ""


def _extract_body(payload: dict) -> str:
    """Depth-first walk for the first text/plain part; fall back to text/html
    stripped, then to nothing. Language-neutral."""
    mime = payload.get("mimeType", "")
    body = payload.get("body", {})
    if mime == "text/plain" and body.get("data"):
        return _decode_b64(body["data"]).strip()
    for part in payload.get("parts", []) or []:
        text = _extract_body(part)
        if text:
            return text
    if mime == "text/html" and body.get("data"):
        import re
        html = _decode_b64(body["data"])
        return re.sub(r"<[^>]+>", " ", html).replace("&nbsp;", " ").strip()
    return ""


def _build_raw(to: str, subject: str, body: str) -> str:
    """Build a base64url-encoded RFC-2822 message for the Gmail API."""
    msg = EmailMessage()
    msg["To"] = to
    if subject:
        msg["Subject"] = subject
    msg.set_content(body or "")
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")


def _short(text: str, limit: int = 2000) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


# ── HUD helper ────────────────────────────────────────────────────────────────

def _panel(player, title: str, text: str) -> None:
    if player:
        try:
            player.show_content(title, text)
        except Exception:
            pass



# ── entry point ───────────────────────────────────────────────────────────────

# Gated by a person, never by the model (core/confirm): the button on screen,
# or on the phone during a phone turn. A step the phone already confirmed runs
# straight through (core/phonegate.confirmed_run), so nothing is asked twice.
def run(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    to = str(p.get("to") or "").strip()
    subject, body = str(p.get("subject") or "").strip(), str(p.get("body") or "").strip()
    if str(p.get("action") or "").strip().lower() == "send" and to and (subject or body):
        from core import confirm
        from core.i18n import t
        return confirm.request(
            key="gmail_send", title=t("confirm.send_email", to=to),
            detail=(f"{subject}\n\n{body}" if subject else body)[:600],
            run=lambda: _run_unconfirmed(parameters, player, session_memory), on_behalf=True)
    result = _run_unconfirmed(parameters, player, session_memory)
    if str(p.get("action") or "check").strip().lower() in ("check", "search", "read"):
        # Mail is written by its senders, never by the user.
        from core import outcome
        return outcome.untrusted("email", result)
    return result


def _run_unconfirmed(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "check").strip().lower()

    try:
        service = get_service(_NS, "gmail", "v1", _SCOPES)
    except Exception as e:
        return explain(e, _NS, _SCOPES, "Gmail")

    try:
        # ---- SEND / DRAFT ----
        if action in ("send", "draft"):
            to = (parameters.get("to") or "").strip()
            body = (parameters.get("body") or "").strip()
            subject = (parameters.get("subject") or "").strip()
            if not to:
                return "Who should I send it to? I need a recipient email address."
            if not body and not subject:
                return "What should the email say?"
            raw = {"raw": _build_raw(to, subject, body)}
            if action == "draft":
                service.users().drafts().create(userId="me", body={"message": raw}).execute()
                _panel(player, "📧 GMAIL — DRAFT SAVED",
                       f"To: {to}\nSubject: {subject or '(none)'}\n\n{body}")
                return f"I've saved a draft to {to}. Want me to send it?"
            service.users().messages().send(userId="me", body=raw).execute()
            _panel(player, "📧 GMAIL — SENT",
                   f"To: {to}\nSubject: {subject or '(none)'}\n\n{body}")
            return f"Sent your email to {to}."

        # ---- CHECK (unread summary) / SEARCH ----
        if action in ("check", "search"):
            try:
                count = max(1, min(15, int(parameters.get("count") or 5)))
            except (TypeError, ValueError):
                count = 5
            q = (parameters.get("query") or "").strip() or ("is:unread" if action == "check" else "")
            resp = service.users().messages().list(userId="me", q=q, maxResults=count).execute()
            ids = [m["id"] for m in resp.get("messages", [])]
            if not ids:
                return ("You have no unread emails." if action == "check"
                        else "I couldn't find any emails matching that.")
            lines, spoken = [], []
            for i, mid in enumerate(ids, 1):
                m = service.users().messages().get(
                    userId="me", id=mid, format="metadata",
                    metadataHeaders=["From", "Subject", "Date"]).execute()
                frm = _header(m, "From")
                subj = _header(m, "Subject") or "(no subject)"
                lines.append(f"{i}. {frm}\n   {subj}\n   {m.get('snippet', '')[:120]}")
                spoken.append(f"{i}. from {frm.split('<')[0].strip()}: {subj}")
            _panel(player, f"📧 GMAIL — {'UNREAD' if action == 'check' else 'SEARCH'} ({len(ids)})",
                   "\n\n".join(lines))
            head = (f"You have {len(ids)} unread email{'s' if len(ids) != 1 else ''}"
                    if action == "check" else f"I found {len(ids)} matching email(s)")
            return head + ": " + "; ".join(spoken) + ". Want me to read one?"

        # ---- READ one message ----
        if action == "read":
            q = (parameters.get("query") or "").strip() or "is:unread"
            resp = service.users().messages().list(userId="me", q=q, maxResults=1).execute()
            ids = [m["id"] for m in resp.get("messages", [])]
            if not ids:
                # fall back to the most recent inbox message
                resp = service.users().messages().list(userId="me", q="in:inbox", maxResults=1).execute()
                ids = [m["id"] for m in resp.get("messages", [])]
            if not ids:
                return "I couldn't find an email to read."
            m = service.users().messages().get(userId="me", id=ids[0], format="full").execute()
            frm, subj = _header(m, "From"), _header(m, "Subject") or "(no subject)"
            date = _header(m, "Date")
            body = _short(_extract_body(m.get("payload", {})) or m.get("snippet", ""))
            _panel(player, "📧 GMAIL — MESSAGE",
                   f"From: {frm}\nSubject: {subj}\nDate: {date}\n\n{body}")
            return (f"Email from {frm.split('<')[0].strip()}, subject '{subj}'. "
                    f"It reads: {body}")

        # ---- MARK READ ----
        if action == "mark_read":
            q = (parameters.get("query") or "").strip() or "is:unread"
            resp = service.users().messages().list(userId="me", q=q, maxResults=1).execute()
            ids = [m["id"] for m in resp.get("messages", [])]
            if not ids:
                return "There's nothing matching to mark as read."
            service.users().messages().modify(
                userId="me", id=ids[0], body={"removeLabelIds": ["UNREAD"]}).execute()
            return "Marked as read."

        return "I can check, read, search, send, draft, or mark your email as read."

    except Exception as e:
        return explain(e, _NS, _SCOPES, "Gmail")
