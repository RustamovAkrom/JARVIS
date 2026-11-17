"""
Plugin — WhatsApp voice & video calling.

`send_message` can already open WhatsApp and find a person. What it cannot do
is the thing you actually want half the time: *ring* them. This plugin finishes
that journey — "call Yusuf on WhatsApp", "görüntülü ara Yusuf'u", "video call
mum" — and hangs up again when you ask.

NEEDS  _whatsapp_core.py  NEXT TO IT
    The driver — finding WhatsApp, opening a conversation, reading back who is
    on screen, pressing the control — lives in that shared file, because the
    incoming-call plugin needs exactly the same driver. Download both.

THREE SYSTEMS, ONE BEHAVIOUR
    The core offers three ways to reach WhatsApp and this plugin does not care
    which one it gets:

      Windows   the desktop app, through UI Automation
      macOS     the desktop app, through the Accessibility API (beta)
      Linux     WhatsApp Web in a Playwright-driven browser — there is no
                official WhatsApp desktop client for Linux at all

    ⚙ → WHATSAPP → Transport is 'auto' by default, which prefers the desktop
    app because that is what most people run and a call placed there rings in
    the real application on the real audio devices. Set it to 'web' and every
    system uses the browser instead.

IT PRESSES THE CONTROL, NOT A PIXEL
    On every transport the call button is *invoked* — Invoke on Windows, AXPress
    on macOS, a click on the element's own reported rectangle on the web. No
    cursor offsets, no DPI arithmetic, nothing that breaks when the window moves
    to a second monitor or is partly covered.

IT CHECKS WHO IT IS ABOUT TO RING
    This is the difference between a convenience and an embarrassment. After
    searching, it reads back *who is actually on screen* and compares it with
    who you asked for. A mismatch does not dial — it returns a question, and
    only calls once you have agreed.

    A wrong tap sends a real phone call to a real person. Refusing to guess is
    the whole point.

FOR EVERY LANGUAGE
    There is not one hardcoded UI string on the critical path. The call buttons
    are found by name across ~20 languages, and when the name is unfamiliar the
    plugin falls back to *layout*: the conversation title is by far the widest
    element in the header band, and the action buttons sit to the right of it in
    a fixed order. Contact names are typed through the clipboard, which is the
    only way ş, ü, я and 漢 survive.
"""
from __future__ import annotations

import threading

from plugins import _whatsapp_core as wa
from memory.config_manager import get_assistant_name


# ─────────────────────────────────────────────────────────────────────────────
# Tool declaration
# ─────────────────────────────────────────────────────────────────────────────
PLUGIN = {
    # For the plugin manager (core/plugin_loader): never sent to a model.
    "level": "basic",
    "label": "WhatsApp calls",
    "summary": "Places WhatsApp calls by voice.",
    "setup": [
        "Needs WhatsApp Desktop installed and signed in.",
        "Say: \"Call Mom on WhatsApp\" or \"Video call Ali.\"",
    ],
    "name": "whatsapp_call",
    "description": (
        "Places a WHATSAPP CALL to one of the user's contacts, and ends a call "
        "in progress. Use whenever the user wants to CALL, ring, phone or video "
        "call somebody — e.g. 'call Yusuf on WhatsApp', 'ring mum', 'video call "
        "my brother', 'start a video call with Ahmet', 'hang up', 'end the "
        "call'. Put the contact's name exactly as it is saved in their phone "
        "into 'contact'. If the user does not say which kind of call they want, "
        "leave 'call_type' as 'voice' — a plain voice call is the default. "
        "Do NOT use 'send_message' for this: that only types a text message and "
        "never rings anyone. "
        "User might say 'my mom', 'my dude', do not directly attain contact:'my dude' because 'my' doesn't mean a name. 'Call my dude' = contact: 'dude' "
        "CONFIRMATION RULE: if the tool answers that it opened a different "
        "conversation than the one asked for, do NOT retry silently — relay its "
        "question to the user, and only call again with confirm=true once they "
        "have clearly agreed to call that person."
    ),
    # Used in place of `description` only when a small model is on a tool
    # budget - Gemini still gets the full text. See providers/schema.py.
    "brief": (
        "Places a WhatsApp voice or video call to a contact, or hangs one up. Put the contact in 'contact'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "contact": {
                "type": "STRING",
                "description": (
                    "Who to call, spelled as the contact is saved in WhatsApp. "
                    "A phone number works too. Not needed for action='hangup'."
                ),
            },
            "call_type": {
                "type": "STRING",
                "description": (
                    "'voice' for a normal audio call, 'video' for a video call. "
                    "Defaults to 'voice' when the user did not specify."
                ),
                "enum": ["voice", "video"],
            },
            "action": {
                "type": "STRING",
                "description": "'call' to ring the contact (default), 'hangup' to end the call in progress.",
                "enum": ["call", "hangup"],
            },
            "confirm": {
                "type": "BOOLEAN",
                "description": (
                    "Only set true after the tool reported a different contact "
                    "than requested AND the user agreed to call that person."
                ),
            },
        },
        # 'contact' is deliberately not required, so action='hangup' does not
        # force a name to be invented; run() asks for one when it is missing.
        "required": [],
    },
    # NON_BLOCKING is deliberately NOT set. The loaders still support it and it
    # is the API's own answer to a slow tool, but measured against the full tool
    # set the model fell silent after issuing the call and produced nothing at
    # all — twice, reproducibly. That is what made a real call vanish. run()
    # returns immediately instead and dials on a thread, which needs nothing
    # from the API to be true.
}

# Rendered by the ⚙ plugin-settings tab, under the WhatsApp core's namespace.
PLUGIN_SETTINGS = {
    "namespace": wa.NS,
    "title": "📞  WHATSAPP",
    "fields": [
        {"key": "transport", "label": "How to reach WhatsApp", "type": "choice",
         "options": ["auto", "desktop", "web"], "default": "auto"},
        {"key": "app_name", "label": "Desktop app name (how the OS finds it)",
         "type": "text", "placeholder": "WhatsApp"},
        {"key": "verify_contact", "label": "Refuse to dial if the wrong chat opened",
         "type": "toggle", "default": True},
        {"key": "launch_wait", "label": "Seconds to wait after launching WhatsApp",
         "type": "text", "placeholder": "8"},
        {"key": "search_wait", "label": "Seconds to wait for the chat to open",
         "type": "text", "placeholder": "4"},
    ],
    "action": {"label": "CHECK WHATSAPP", "run": lambda values: _self_check(values or {})},
}


# ─────────────────────────────────────────────────────────────────────────────
# ⚙ settings-tab CHECK button — read-only, rings nobody
# ─────────────────────────────────────────────────────────────────────────────
def _self_check(_values: dict):
    """Report on every transport this system could use, not just the chosen one.

    Somebody whose desktop path is broken needs to see that the web path works,
    and somebody who set 'web' by mistake needs to see that their desktop app
    was fine. One press, the whole picture, each failing line carrying the
    command or the action that resolves it.
    """
    conf = wa.cfg()
    lines: list[str] = []
    any_ok = False

    cls, why = wa.choose(conf)
    lines.append(f"Transport setting: {conf['transport']}"
                 + (f"  →  {cls.label}" if cls else "  →  nothing usable"))
    if not cls and why:
        lines.append(f"  ✗ {why}")
    lines.append("")

    for candidate in wa.transports_for_diagnosis(conf):
        lines.append(f"{candidate.label}")
        usable, unusable_why = candidate.usable()
        if not usable:
            lines.append(f"  ✗ {unusable_why}")
            lines.append("")
            continue
        try:
            ok, detail = candidate().diagnose(conf)
        except Exception as e:
            ok, detail = False, [f"  ✗ the check itself failed: {e}"]
        lines.extend(detail)
        any_ok = any_ok or ok
        lines.append("")

    lines.append("Ready." if any_ok else "Fix the ✗ lines above, then press CHECK again.")
    return any_ok, "\n".join(lines).strip()


# ─────────────────────────────────────────────────────────────────────────────
# HUD helpers (no-ops when player is None)
# ─────────────────────────────────────────────────────────────────────────────
def _panel(player, line: str) -> None:
    if not player:
        return
    try:
        player.show_content("📞 WHATSAPP", line)
    except Exception:
        pass


def _log(player, msg: str) -> None:
    if player:
        try:
            player.write_log(msg)
        except Exception:
            pass


def _say(player, instruction: str) -> None:
    if player and hasattr(player, "request_say"):
        try:
            player.request_say(instruction)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
_lock = threading.Lock()
_busy = {"running": False}


# Gated by a person, never by the model (core/confirm): the button on screen,
# or on the phone during a phone turn. A step the phone already confirmed runs
# straight through (core/phonegate.confirmed_run), so nothing is asked twice.
def run(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "call").strip().lower()
    contact = str(p.get("contact") or "").strip()
    if action in ("hangup", "hang_up", "end", "end_call") or not contact:
        return _run_unconfirmed(parameters, player, session_memory)
    from core import confirm
    from core.i18n import t
    return confirm.request(
        key="whatsapp_call", title=t("confirm.call", to=contact), detail="",
        run=lambda: _run_unconfirmed(parameters, player, session_memory), on_behalf=True)


def _run_unconfirmed(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    action = (params.get("action") or "call").strip().lower()
    contact = (params.get("contact") or "").strip()
    call_type = (params.get("call_type") or "voice").strip().lower()
    if call_type not in ("voice", "video"):
        call_type = "voice"
    confirmed = bool(params.get("confirm"))

    conf = wa.cfg()
    transport_cls, why = wa.choose(conf)
    if transport_cls is None:
        return f"I can't reach WhatsApp on this system: {why}."

    if action in ("hangup", "hang_up", "end", "end_call"):
        return _hangup(player, conf)

    if not contact:
        return "Who should I call on WhatsApp?"

    spoken = "video call" if call_type == "video" else "voice call"

    # Hand the work to a thread and answer straight away.
    #
    # This is about WHEN the user hears something, and it was measured rather
    # than guessed. Doing the dialling inside run() means the reply cannot be
    # spoken until it finishes: on a real session the function call left at
    # 0.6 s, the tool answered at 2.6 s, and the first word only arrived at
    # 4.3 s — a second and a half AFTER the phone had started ringing. The
    # assistant was narrating something the user was already listening to.
    #
    # Neither of the obvious cures works here. Asking the model in the prompt to
    # speak before it calls does not change the order it chooses. NON_BLOCKING,
    # which is the API's own answer, made the model fall silent after issuing
    # the call and produce nothing at all. Injecting a sentence while the call
    # was pending cancelled the exchange outright.
    #
    # So the tool returns in milliseconds and the phone is dialled behind it —
    # the same shape screen_agent and water_reminder already use, with
    # request_say carrying anything that still needs saying. The user now hears
    # the sentence BEFORE the ringing starts, which is the whole point.
    with _lock:
        if _busy["running"]:
            return "I'm already placing a call — one moment."
        _busy["running"] = True

    threading.Thread(
        target=_place_call,
        args=(player, contact, call_type, spoken, confirmed, conf),
        daemon=True, name="whatsapp-call",
    ).start()
    # Deliberately a status line, not a sentence. Handing the model a finished
    # first-person sentence in English is an invitation to repeat it, and it
    # took one to prove it: a request made in Turkish came back in English,
    # padded with an offer nobody had made. Facts only — the model writes the
    # sentence, in the user's language.
    return f"status=dialling contact={contact} type={call_type}"


def _hangup(player, conf: dict) -> str:
    transport, why = wa.get(conf)
    if transport is None:
        return f"I couldn't reach WhatsApp: {why}."
    try:
        if transport.hangup():
            _log(player, f"{get_assistant_name()}: WhatsApp call ended.")
            return "Call ended."
    except wa.Unsupported as e:
        return f"I can't end the call here — {e}."
    return ("I couldn't find the hang-up button — the call window may not be "
            "open, or it doesn't publish that control. End it from the call "
            "window and I'll stay out of the way.")


def _place_call(player, contact: str, call_type: str, spoken: str,
                confirmed: bool, conf: dict) -> None:
    """The part that takes seconds. Speaks only when something needs saying:
    silence here means it went through, and the panel already shows it."""
    try:
        outcome = _do_call(player, contact, call_type, spoken, confirmed, conf)
    except Exception as e:
        print(f"[WhatsAppCall] {type(e).__name__}: {e}")
        outcome = f"the call to {contact} failed: {e}"
    finally:
        with _lock:
            _busy["running"] = False

    if outcome:
        _say(player, "Tell the user, in one short sentence in their own "
                     f"language: {outcome}")
        _log(player, f"{get_assistant_name()}: {outcome}")


def _do_call(player, contact: str, call_type: str, spoken: str,
             confirmed: bool, conf: dict) -> str:
    """Returns '' when the call was placed, or what to tell the user instead."""
    try:
        _log(player, f"{get_assistant_name()}: Opening WhatsApp…")
        transport, why = wa.get(conf)
        if transport is None:
            return f"{why}, so nobody was called"

        transport.focus()

        # Already on the right conversation? Then don't disturb it — this is the
        # fast path, and it skips the search entirely.
        chat = transport.current_chat()
        opened = chat.raw_title if chat else ""
        if not (chat and wa.names_match(contact, opened)):
            chat = transport.open_chat(contact, conf, before=opened)

        if not chat:
            return (f"the conversation header could not be read, so {contact} "
                    f"was NOT called and nothing was dialled")

        shown = chat.name(contact)
        if conf["verify_contact"] and not wa.names_match(contact, chat.raw_title) \
                and not confirmed:
            return (f"you searched for '{contact}' but the conversation that "
                    f"opened is '{shown}', so you have NOT dialled - ask whether "
                    f"to {spoken} '{shown}' instead")

        ok, how = transport.press_call(chat, call_type)
        if not ok:
            if how == "no-button":
                return (f"'{shown}' is open but offers no {spoken}, so nothing "
                        f"was dialled")
            return (f"the {spoken} button for '{shown}' could not be pressed, so "
                    f"nothing was dialled")

    except wa.Unsupported as e:
        return f"{e}, so nothing was dialled"
    except Exception as e:
        print(f"[WhatsAppCall] {type(e).__name__}: {e}")
        return f"the WhatsApp call failed: {e}"

    # Success says nothing. The user was told the call was being placed before
    # it was, the phone is now ringing in their ear, and a second announcement
    # over the top of it is noise. The panel and the log carry the record.
    _panel(player, f"{'📹' if call_type == 'video' else '📞'}  {spoken.title()}\n{shown}")
    _log(player, f"{get_assistant_name()}: Calling {shown} on WhatsApp — {spoken}.")
    return ""
