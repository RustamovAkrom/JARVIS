"""
Plugin — WhatsApp incoming-call guard.

whatsapp_call.py rings other people. This one handles the other direction: the
phone rings, and JARVIS tells you who it is and does what you say.

    "Yusuf is calling."   → "answer it"                → answered
                          → "don't answer"              → declined
                          → "decline, say I'm busy"     → declined, and the
                                                          message you spoke
                                                          is sent
                          → "answer and send my address" → answered, and the
                                                          message you spoke
                                                          is sent
                          → "pick up, say I'm busy"     → answered, JARVIS
                                                          says it to the
                                                          caller, hung up

    Nothing above is a keyword, and nothing here is tied to English. The model
    reads what you said, in whatever language you said it in, and fills in a
    decision and a `message` field — so a request phrased in Turkish, Arabic or
    Japanese reaches this file as exactly the same two values.

    The examples are written in English on purpose. A tool description is sent
    to the model on every connection, and worked examples in one particular
    language pull its REPLIES toward that language even when the user is
    speaking another — so the rules here state the shape of a request rather
    than demonstrating it in somebody's mother tongue.

NEEDS  _whatsapp_core.py  NEXT TO IT
    Same shared driver whatsapp_call.py uses, and it works on all three systems:
    the desktop app through UI Automation on Windows and the Accessibility API
    on macOS, and WhatsApp Web through Playwright everywhere — which is the only
    option on Linux, where WhatsApp has no official desktop client.

MODES
    announce     say who is calling and wait for your voice. Nothing is pressed
                 until you decide. This is the default, because a call answered
                 without you is worse than a call missed.
    auto_reply   decline immediately and send the sentence you wrote in
                 settings. For meetings — the caller gets a real answer rather
                 than a ring that dies.
    auto_decline decline immediately and send nothing.
    off          watch nothing.

    There is no mode that answers EVERY call with the assistant's voice, on
    purpose: a caller would be talking to an AI nobody chose to put on the
    line. Speaking to a caller happens only for the call ringing now, because
    the user said so (accept_speak), and the assistant then opens by saying it
    is an AI assistant answering for the user (_SPEAK) — people have a right
    to know when they are talking to a machine (EU AI Act, art. 50(1)).

    A per-contact list overrides the mode both ways: people you always want
    announced, and people who are always auto-declined.

IT ONLY WATCHES WHEN YOU SAY SO
    Plugins have no startup hook and this one deliberately does not want one:
    a background thread that reads your WhatsApp window should start because you
    asked, not because a file was in a folder. "Turn on the call guard" starts
    it, the same way water_reminder starts its loop.

SPEAKING TO THE CALLER
    A plain "answer it" puts the call on your speakers and microphone exactly as
    if you had clicked it, and JARVIS steps back. "Pick up and tell them I'm
    busy" is different: the call is answered, JARVIS says that to the caller in
    its own voice, and the call is hung up.

    The voice reaches the caller the plain way — out of the speakers and into
    the microphone WhatsApp is already listening to. No virtual audio cable, no
    driver, nothing to install and nothing different per operating system, and
    it speaks whatever language the message is in because the assistant's own
    voice already does. The price is stated rather than hidden: with headphones
    on, the caller hears nothing.

    WhatsApp also has to be listening on a working microphone, and on Windows
    that is not the one JARVIS was given: calls open the "default communication
    device". Measured on a real call — a dead input there, and the caller heard
    nothing at all while every other check passed. The button on this plugin's
    own settings card (⚙ → WHATSAPP CALL GUARD → USE JARVIS MIC FOR CALLS) sets
    it to the microphone chosen for JARVIS.

    NOTHING IN THE APP WAS CHANGED FOR THIS, ON PURPOSE.
    A plugin that needs a line in main.py is no longer a plugin — it is core
    that everybody carries whether they installed it or not. So all three
    things this needs come from what the player already has:
      muted          the HUD's own microphone switch, flipped on the GUI thread
                     for the length of the call — otherwise the assistant hears
                     the caller and starts answering them — and put back after
      push_visemes   the HUD's mouth is fed every batch of the assistant's
                     speech together with the moment it starts to SOUND, so
                     listening to it for one call says when the sentence has
                     left the speakers; the original is put back afterwards
      request_say    the sentence itself
    A build without them refuses the spoken forms and says why, rather than
    answering a call it has no way to speak to.

    One message, then the call ends. It never holds a conversation: improvising
    with a real person on the user's behalf is a different product with
    different risks.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

from core import outcome
from memory.config_manager import get_plugin_config
from plugins import _whatsapp_core as wa
from memory.config_manager import get_assistant_name

_NS = "whatsapp_guard"

# How often the transport is asked whether anything is ringing.
#
# This is half of the delay a person actually feels — a poll that has just run
# when the phone starts ringing does not look again for a full interval. It was
# 1.2s, which averaged 0.6s of dead time for nothing: with the main window
# skipped, a look costs about 3ms when nothing is ringing (measured: five polls
# in 15ms). Spending it four times as often is free and the call is seen almost
# as soon as it appears.
_POLL_SECONDS = 0.35
# How long a ringing call stays answerable after it is announced. Longer than
# the ring itself on purpose: it is better to tell the user "that call already
# ended" than to have forgotten which call they meant.
_PENDING_SECONDS = 45.0
# After the transport fails, how long before trying to attach again. Stops a
# closed WhatsApp from being relaunched over and over in the background.
_REATTACH_SECONDS = 30.0
# How long a ringing call may stay unidentified before it is handled anyway.
#
# The very first call in a given WhatsApp language arrives before anyone has
# worked out which button answers and which refuses — that answer is fetched in
# the background the instant the call is seen, and cached for every call after.
# So the loop simply looks again a third of a second later, and again, until the
# answer lands. That is what makes it feel instant on every call but the first,
# and still act on the first.
#
# It must be well under the ~30s a WhatsApp call rings for: whatever happens,
# something has to happen while the phone is still ringing.
_RESOLVE_GRACE = 5.0

# Speaking to a caller (action 'accept_speak').
#
# How long after the call panel appears before a word is said. The audio path
# to the caller opens a moment after the panel does, and a sentence started
# inside that moment reaches them without its first word.
_CONNECT_SETTLE = 1.2
# How long the assistant's voice may take to START once it has been asked to
# speak — one model round trip. Past this, nothing is going to be said.
_SPEECH_START = 8.0
# Between the last sound leaving the speakers and the call being cut. The
# push_visemes timestamps already include the device buffer; this is the room
# and the call's own delay, so the caller hears the last word and not a click.
_AFTER_SPEECH = 0.8
# How long without a new batch of speech before the voice counts as finished.
# The model's audio arrives in bursts, and a pause of a few hundred milliseconds
# inside one sentence is normal — hanging up on it cuts the sentence in half.
_VOICE_GAP = 0.7

# What the assistant is told once the call is live. English, like every other
# instruction here; the message itself is spoken VERBATIM, in its own language.
#
# NO NAMES AND NO REWRITES, AND THAT IS A FIX.
#     The first version named the caller here and allowed one rewrite — turn
#     "I'm busy" into "<the user's name> is busy". Given "<user> is not
#     available", the model reached for the only name in this instruction and
#     told the caller that the CALLER was not available. Person is settled once,
#     when the message is extracted (the 'message' parameter says to write it
#     about the user), and nothing here offers a second name to swap in.
_SPEAK = (
    "A WhatsApp call has just been answered for the user and is live on the "
    "speakers. The CALLER hears what you say now, not the user. First say "
    "ONE short sentence, in the language the message is written in, telling "
    "the caller that they are hearing an AI assistant answering on the user's "
    "behalf; name nobody in it. Then say the message below EXACTLY as written, "
    "word for word, in the language it is written in, then stop. Do not "
    "change, translate or replace any word or name in the message. Add nothing "
    "else: no greeting, no form of address such as 'sir', no question, no tool "
    "call.\n\nMessage: {note}"
)


PLUGIN = {
    # For the plugin manager (core/plugin_loader): never sent to a model.
    "level": "advanced",
    "label": "WhatsApp call guard",
    "summary": "Tells you who is calling on WhatsApp and answers or declines as you say.",
    "risk": "It operates WhatsApp by program, which WhatsApp does not allow; your WhatsApp account could be restricted.",
    # It watches WhatsApp in the background and acts on calls by itself: the
    # product leaves that out (core/plugin_loader.in_this_edition).
    "product": False,
    "setup": [
        "Needs WhatsApp Desktop installed and signed in.",
        "In these settings: who is always announced, who is always declined, and the message callers get.",
        "Turn it on by voice: \"Guard my WhatsApp calls.\"",
        "WhatsApp does not allow its app to be used by a program: use this only for your own calls, now and then. Heavy use can get your WhatsApp account restricted.",
    ],
    "name": "whatsapp_guard",
    "description": (
        "Watches for INCOMING WhatsApp calls and answers, declines or "
        "auto-replies to them. Use when the user wants the assistant to handle calls "
        "that come IN — e.g. 'watch my WhatsApp calls', 'turn on the call "
        "guard', 'answer it', 'don't answer', 'decline and tell them I'm "
        "busy', 'stop watching my calls', 'did I miss any calls'. The user may "
        "say any of this in ANY language; match on what they mean, not on "
        "these words. "
        "When a call is ringing and the user says to answer it use "
        "action='accept'; to refuse it and say nothing use action='decline'; "
        "to refuse it AND text the caller use action='decline_message'; to "
        "answer it AND text the caller use action='accept_message'; to answer "
        "it AND have YOU speak to the caller use action='accept_speak'. "
        "ANY request that decides the call AND says something to pass on "
        "carries a message: put it into 'message', in the user's own "
        "language, and use a _message or _speak form of the action. The test "
        "is the request's SHAPE, not its wording, and it holds in every "
        "language. A declined caller cannot hear anything, so decline plus "
        "something to tell them is always a text: 'decline and tell them I'm "
        "busy' and 'refuse and send a message that I'm not available' are "
        "action='decline_message'. An ANSWERED caller can hear you, so answer "
        "plus something to tell or say to them is spoken: 'pick up and tell "
        "them I'm busy' is action='accept_speak'. Only when the user says to "
        "write, text or send it is it action='accept_message': 'answer it and "
        "send them the address'. Never plain 'decline' or plain 'accept' when "
        "the user asked for something to be passed on. "
        "The same shapes set a standing rule with action='on': refuse every "
        "call and text the caller is mode='auto_reply'. There is no mode that "
        "answers every call and speaks to the caller: that is only done for the "
        "call ringing now (action='accept_speak'). "
        "If the tool reports that the message was NOT sent, say so plainly and "
        "repeat the reason it gives; never tell the user a message was sent "
        "unless the tool said it was. "
        "Do NOT use 'whatsapp_call' for any of this — that one PLACES a call to "
        "somebody, it never answers one."
    ),
    # Used in place of `description` only when a small model is on a tool
    # budget - Gemini still gets the full text. See providers/schema.py.
    "brief": (
        "Handles INCOMING WhatsApp calls: action 'on'/'off'/'status', or 'accept'/'decline' for the call ringing now. Add '_message' to text the caller, or use 'accept_speak' to answer and SAY 'message' to them yourself (only for the call ringing now)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "'on' start watching (default), 'off' stop watching, "
                    "'status' report what is happening and list missed calls, "
                    "'accept' answer the call ringing now, 'decline' refuse it, "
                    "'decline_message' refuse it and text the caller, "
                    "'accept_message' answer it and text the caller, "
                    "'accept_speak' answer it, say 'message' to the caller "
                    "yourself, then hang up, "
                    "'probe' watch for 30 seconds and report every control "
                    "WhatsApp shows (for setting the guard up — use only when "
                    "the user explicitly asks to test or debug call detection)."
                ),
                "enum": ["on", "off", "status", "accept", "decline",
                         "decline_message", "accept_message", "accept_speak",
                         "probe"],
            },
            "message": {
                "type": "STRING",
                "description": (
                    "The text to send the caller, in the USER'S OWN LANGUAGE, "
                    "taken from what they just said. Use it with "
                    "action='decline_message', 'accept_message' or "
                    "'accept_speak' for the call ringing now, AND with "
                    "action='on' + mode='auto_reply' to "
                    "set what every caller is told for the rest of the "
                    "session. For accept_speak YOU say it to "
                    "the caller on the user's behalf, so write it the way you "
                    "will say it — about the user, not as the user: 'tell "
                    "them I'm busy' becomes '<the user's name> is busy right "
                    "now'. "
                    "'If anyone calls, decline it and say I'm not available' "
                    "already contains it — pass 'I'm not available right now.' "
                    "Leave it out only when the user gave no hint of what to "
                    "say; their saved message is then used."
                ),
            },
            "mode": {
                "type": "STRING",
                "description": (
                    "Only with action='on'. 'announce' (default) says who is "
                    "calling and waits for the user. 'auto_reply' declines "
                    "every call AND texts the caller. 'auto_decline' declines "
                    "every call and sends NOTHING — use this whenever the user "
                    "asks to refuse calls without mentioning telling anyone, "
                    "or says not to message them: 'just decline "
                    "them', 'don't send any message'. Recognise the same "
                    "intent in any language."
                ),
                "enum": ["announce", "auto_reply", "auto_decline"],
            },
        },
        "required": [],
    },
}

PLUGIN_SETTINGS = {
    # Its own namespace on purpose. The settings UI shows one section per
    # namespace, so sharing whatsapp_call's would hide these fields behind that
    # plugin's card. Two cards: one for reaching WhatsApp, one for what to do
    # when it rings.
    "namespace": _NS,
    "title": "🛡  WHATSAPP CALL GUARD",
    "fields": [
        {"key": "busy_message",
         "label": "Message for callers (texted on auto-reply)",
         "type": "text",
         "placeholder": "I can't take your call right now."},
        {"key": "always_announce",
         "label": "Always announce these (comma-separated names)",
         "type": "text", "placeholder": "Anne, Yusuf"},
        {"key": "always_decline",
         "label": "Always decline these (comma-separated names)",
         "type": "text", "placeholder": "Spam, Unknown"},
        {"key": "decline_unknown",
         "label": "Auto-decline callers whose name cannot be read",
         "type": "toggle", "default": False},
    ],
}


def _use_jarvis_mic_for_calls(_values: dict):
    """Settings button: make JARVIS's microphone the one CALLS use. -> (ok, sentence)

    Windows keeps two default microphones, and call applications open the
    "default communication device" — not the one chosen for JARVIS. Measured on
    a real call: that device was a dead Realtek input, WhatsApp opened it every
    time, and the caller heard nothing while every other check passed.

    It lives here rather than in AUDIO DEVICES because this plugin is what needs
    it: somebody without the plugin gets no button and no code. It changes a
    setting other applications share, so it happens only when pressed, and the
    result is read back from Windows rather than assumed.
    """
    try:
        from memory.config_manager import get_input_device
        name = get_input_device()
    except Exception:
        name = ""
    try:
        # The settings card runs this on a worker thread, and comtypes only
        # initialises the thread that imported it. Never uninitialised: an
        # interface released after CoUninitialize is an access violation.
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass
    try:
        from pycaw.constants import ERole
        from pycaw.pycaw import AudioUtilities
    except Exception:
        return False, "pycaw is not installed, so the call microphone cannot be set here."
    if not hasattr(AudioUtilities, "SetDefaultDevice"):
        return False, "This pycaw is too old to set it (pip install -U pycaw)."

    try:
        enum = AudioUtilities.GetDeviceEnumerator()
        if name:
            # Active endpoints only — Windows keeps unplugged ones under the
            # same name — exact first, a prefix only for MME's 31-character cut.
            wanted, exact, partial = wa.fold(name), None, None
            for dev in AudioUtilities.GetAllDevices():
                try:
                    if str(getattr(dev, "state", "")) != "AudioDeviceState.Active":
                        continue
                    if "{0.0.1." not in str(dev.id):          # capture endpoints
                        continue
                    shown = wa.fold(dev.FriendlyName or "")
                except Exception:
                    continue
                if not shown:
                    continue
                if shown == wanted:
                    exact = dev
                    break
                if partial is None and (shown.startswith(wanted[:24])
                                        or wanted.startswith(shown[:24])):
                    partial = dev
            target = exact or partial
            if target is None:
                return False, f"The assistant's microphone '{name}' is not connected right now."
            target_id, target_name = target.id, target.FriendlyName
        else:
            # JARVIS on "System default": make the two defaults agree.
            raw = enum.GetDefaultAudioEndpoint(1, 0)
            target_id = raw.GetId()
            target_name = AudioUtilities.CreateDevice(raw).FriendlyName

        if enum.GetDefaultAudioEndpoint(1, 2).GetId() == target_id:
            return True, f"Calls already use '{target_name}'."
        AudioUtilities.SetDefaultDevice(target_id, roles=[ERole.eCommunications])
        if enum.GetDefaultAudioEndpoint(1, 2).GetId() == target_id:
            return True, f"Done — calls now use '{target_name}'."
        return False, "Windows accepted it but still reports the previous call microphone."
    except Exception as e:
        return False, f"Windows refused the change: {e}"


# Only where there is a separate call microphone to set. macOS and Linux have
# one default input, so the button would be a question with no answer there.
if wa.IS_WINDOWS:
    PLUGIN_SETTINGS["action"] = {"label": "USE THE ASSISTANT'S MIC FOR CALLS",
                                 "run": _use_jarvis_mic_for_calls}


# ─────────────────────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────────────────────
def _guard_cfg() -> dict:
    stored = get_plugin_config(_NS) or {}

    def _list(key: str) -> list[str]:
        raw = str(stored.get(key) or "")
        return [wa.fold(p) for p in raw.split(",") if p.strip()]

    decline_unknown = stored.get("decline_unknown")
    if not isinstance(decline_unknown, bool):
        decline_unknown = str(decline_unknown or "").strip().lower() in (
            "1", "true", "on", "yes")

    return {
        "busy_message": str(stored.get("busy_message") or "").strip(),
        "always_announce": _list("always_announce"),
        "always_decline": _list("always_decline"),
        "decline_unknown": decline_unknown,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Live state — one watcher per process
# ─────────────────────────────────────────────────────────────────────────────
_lock = threading.Lock()
_state: dict = {
    "running": False,
    "thread": None,
    "stop": None,
    "mode": "announce",
    "pending": None,        # the Incoming currently being decided on
    "pending_at": 0.0,
    "seen": [],             # recent Incoming.key values, so one ring = one announce
    "missed": [],           # [(when, caller, what-happened), ...]
    "transport": None,
    "message": "",          # what the user said to tell callers, this session
    "speaking": False,      # a call is being answered and spoken to right now
}


# ─────────────────────────────────────────────────────────────────────────────
# HUD helpers (no-ops when player is None)
# ─────────────────────────────────────────────────────────────────────────────
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


def _panel(player) -> None:
    if not player:
        return
    with _lock:
        mode = _state["mode"]
        running = _state["running"]
        missed = list(_state["missed"])[-8:]
    head = f"{'🟢' if running else '⚪'}  Guard {'on' if running else 'off'}"
    if running:
        head += f"  ·  {mode.replace('_', ' ')}"
    body = "\n".join(f"{when}  {who or 'unknown'} — {what}"
                     for when, who, what in reversed(missed)) or "No calls yet."
    try:
        player.show_content("🛡 WHATSAPP CALLS", f"{head}\n\n{body}")
    except Exception:
        pass


def _record(caller: str, what: str) -> None:
    with _lock:
        _state["missed"].append((datetime.now().strftime("%H:%M"), caller, what))
        del _state["missed"][:-40]


# ─────────────────────────────────────────────────────────────────────────────
# Deciding what to do with one ring
# ─────────────────────────────────────────────────────────────────────────────
def _listed(caller: str, names: list[str]) -> bool:
    """Is this caller on one of the user's lists? Folded on both sides, so
    'anne' matches 'Anne ❤' and case and accents never matter."""
    if not names:
        return False
    folded = wa.fold(caller)
    if not folded:
        return False
    return any(n and (n in folded or folded in n) for n in names)


def _handle(player, call, mode: str, gcfg: dict, transport) -> None:
    """One ringing call, decided and acted on."""
    caller = (call.caller or "").strip()
    kind = "video call" if call.call_type == "video" else "call"

    # NOTHING above the announcement is allowed to wait on anything.
    #
    # The first version worked out which button answers and which refuses before
    # saying a word, and that reads the labels over the network the first time a
    # given WhatsApp language is seen. Measured on a real call: ten seconds of
    # silence while the phone rang, because the first model in the list was
    # timing out. The user is watching a ringing phone — the assistant has to be
    # ahead of them, not behind.
    #
    # Both facts the announcement needs are already in hand: who is calling
    # comes from the window title, and that it IS a call comes from the shape.
    # Which button does what is only needed once somebody has said "answer it",
    # and by then the background resolve started at first sighting has finished.
    if not caller:
        # Everything downstream needs a name — the message is sent by opening
        # that person's conversation — so when it cannot be read, put what WAS
        # on the window into the log. The alternative is asking the user to run
        # a probe and ring themselves again, which is a lot to ask twice.
        _log(player, f"{get_assistant_name()}: Could not read the caller's name. The call window "
                     "published: " + (", ".join(call.controls[:12]) or "nothing"))

    policy_decline = (_listed(caller, gcfg["always_decline"])
                      or (gcfg["decline_unknown"] and not caller))
    auto = (mode in ("auto_reply", "auto_decline")
            and not _listed(caller, gcfg["always_announce"]))

    # Silence is a choice the user is allowed to make, and it is honoured
    # absolutely: in auto_decline nothing is typed to anybody, whatever is
    # saved in the settings.
    quiet = mode == "auto_decline"

    if policy_decline or auto:
        # This path presses a button, so it is the one place that may wait.
        _decline_now(player, call, transport, gcfg, with_message=not quiet,
                     reason="on your always-decline list" if policy_decline and caller
                     else "the caller could not be identified" if policy_decline
                     else "auto-reply is on" if not quiet
                     else "you asked for calls to be refused silently")
        return

    with _lock:
        _state["pending"] = call
        _state["pending_at"] = time.monotonic()
    who = caller or "Someone whose name I can't read"
    _log(player, f"{get_assistant_name()}: Incoming WhatsApp {kind} — {who}.")
    _say(player,
         f"A WhatsApp {kind} is coming in right now from: {who}. Tell the user "
         f"who is calling and ask whether to answer it. One short sentence, in "
         f"their own language. Do not call any tool yet — wait for their answer.")
    _panel(player)


def _note_for(message: str, gcfg: dict) -> str:
    """What actually gets sent, in order of who said it most recently.

    What the user just SAID wins over what they once configured.

    "Decline and tell them I'm not available" already contains the message, in
    their own language, and the assistant transcribing it is the same one that
    will phrase it. Requiring them to have filled in a settings field first is
    why the last real call was declined in silence. The saved message stays as
    the fallback, and for auto-reply mode — where nobody is speaking — it is
    the only source.
    """
    with _lock:
        session_note = _state.get("message") or ""
    return (message or "").strip() or session_note or gcfg["busy_message"]


def _send_to_caller(transport, caller: str, note: str) -> tuple[bool, str]:
    """Text the caller. -> (sent, why-not, in words the model can pass on).

    Both call decisions that carry a message come through here, and the
    sequence itself lives one level further down in the transport's
    send_message_to(): open the conversation, prove it is the right one, type,
    confirm the box emptied. Nothing in this file assembles that by hand any
    more — it was assembled twice, checked differently each time, and the
    difference is how a message that never went got reported as sent.
    """
    if not note:
        return False, ("nothing was ever said about what to send, so NOTHING "
                       "was sent - ask the user what they want the caller told")
    if not caller:
        return False, ("the caller could not be identified, so there was no "
                       "conversation to send it to and NOTHING was sent")
    try:
        return transport.send_message_to(caller, note, wa.cfg())
    except wa.Unsupported as e:
        return False, f"{e}, so NOTHING was sent"
    except Exception as e:
        print(f"[WhatsAppGuard] sending to {caller!r} failed: "
              f"{type(e).__name__}: {e}")
        return False, f"sending the message failed ({e}), so NOTHING was sent"


def _decline_now(player, call, transport, gcfg: dict, with_message: bool,
                 reason: str = "", message: str = "") -> str:
    """Decline, and optionally send the user's own busy message afterwards."""
    caller = (call.caller or "").strip()

    # This is a pressing path, so unlike the announcement it is allowed to wait
    # for the control mapping. Only ever reached on the automatic routes — an
    # always-decline contact or auto-reply mode — where nobody is waiting on a
    # sentence from JARVIS.
    call, why = _identified_call(player, call, transport)
    if call is None:
        return why

    try:
        declined = transport.decline(call)
    except wa.Unsupported as e:
        _record(caller, f"could not decline ({e})")
        return f"I couldn't decline that call: {e}."
    except Exception as e:
        _record(caller, f"could not decline ({e})")
        return f"I couldn't decline that call: {e}."

    if not declined:
        _record(caller, "decline failed")
        return "I found the call but couldn't press decline."

    # LET THE CALL WINDOW GO BEFORE TYPING ANYTHING.
    #
    # Declining does not close the panel instantly, and while it is closing it
    # is the window with the keyboard. It belongs to WhatsApp, so every check
    # that asks "does WhatsApp have the keyboard" answers yes — correctly, and
    # uselessly, because a call panel has no text box: the reply was typed into
    # it and vanished, and the user was told the message was not sent.
    #
    # Waiting for it is cheap and needs no new way of recognising anything —
    # incoming() already reports whether a call window is on screen, and a look
    # costs under a millisecond when there is nothing ringing.
    gone = False
    for _ in range(24):                      # ~3 seconds, in eighths
        try:
            if transport.incoming() is None:
                gone = True
                break
        except Exception:
            gone = True                      # cannot see one; do not wait on it
            break
        time.sleep(0.125)
    if not gone:
        print("[WhatsAppGuard] the call window is still up after three seconds "
              "— sending anyway.")

    sent = False
    failure = ""                      # why the message did not go, in the model's words

    note = _note_for(message, gcfg)
    if with_message:
        sent, failure = _send_to_caller(transport, caller, note)

    what = "declined"
    if sent:
        what += " + message sent"
    elif with_message:
        what += " (message NOT sent)"
    _record(caller, what)
    _log(player, f"{get_assistant_name()}: WhatsApp call from {caller or 'unknown'} — {what}.")
    _panel(player)

    # Say what did NOT happen, explicitly.
    #
    # The first version returned "Declined the call from X." and left the rest
    # unsaid — and the model, having been asked to decline AND send a message,
    # filled the gap for itself: "Call declined, and I've sent the message that
    # you're unavailable, sir." Nothing had been sent. A tool that reports only
    # its successes is an invitation to invent the rest, so a failure is now
    # stated in the result rather than left out of it.
    who = caller or "an unknown caller"
    because = f" ({reason})" if reason else ""
    if not with_message:
        return f"Declined the call from {who}{because}."
    if sent:
        return f"Declined the call from {who} and sent your message{because}."
    return (f"Declined the call from {who}{because}, but {failure}. Tell the "
            f"user plainly that the message was NOT sent and why - do not say "
            f"it was sent.")


def _identified_call(player, call, transport):
    """-> (call, "") ready to press, or (None, what to tell the user).

    A cached re-read only. The watch loop already gave the background lookup
    its grace period, so if it has landed this picks it up for free; if it has
    not, blocking here would just spend the rest of the ring waiting instead of
    telling the user something useful.
    """
    if call.identified:
        return call, ""
    caller = (call.caller or "").strip()
    try:
        fresh = transport.incoming()
    except Exception as e:
        print(f"[WhatsAppGuard] re-reading the call failed: {e}")
        fresh = None
    if fresh is None:
        _record(caller, "stopped ringing")
        return None, f"The call from {caller or 'an unknown caller'} stopped ringing."
    if not fresh.identified:
        _record(caller, "controls unresolved — left alone")
        _log(player, f"{get_assistant_name()}: Could not identify the call controls "
                     f"({', '.join(fresh.controls[:6]) or 'none published'}) "
                     f"— left the call alone.")
        return None, ("A call came in but I couldn't tell which button answers "
                      "it and which declines it, so I left it alone rather "
                      "than guess.")
    return fresh, ""


# ─────────────────────────────────────────────────────────────────────────────
# Answering, and speaking to the caller
# ─────────────────────────────────────────────────────────────────────────────
def _wait(check, limit: float, step: float = 0.15) -> bool:
    """Poll `check` until it is true or `limit` seconds pass. Never raises: a
    check that throws is a check that has not come true yet."""
    deadline = time.monotonic() + limit
    while True:
        try:
            if check():
                return True
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(step)


def _voice_ready(player) -> str:
    """'' when this app can speak into a call, otherwise why it cannot.

    Nothing asked for here was added for this plugin — request_say, the HUD's
    push_visemes and its microphone switch are what every player already has.
    A build without them must refuse rather than improvise: a call answered by
    an assistant that cannot tell when its sentence ended, or that is still
    listening to the room, is worse than a phone left to ring.
    """
    missing = [n for n in ("request_say", "push_visemes")
               if not callable(getattr(player, n, None))]
    if player is None or not hasattr(player, "muted"):
        missing.append("muted")
    if not missing:
        return ""
    return ("this version of the app cannot speak into a call ("
            + ", ".join(missing) + " not available) - update the app")


class _VoiceWatch:
    """When the assistant's own voice is audible — for the length of one call.

    main.py hands every batch of the assistant's speech to
    player.push_visemes(frames, hop, at), with `at` the wall-clock moment the
    batch starts to SOUND. That already answers "is it still talking", device
    buffer included, so this listens to it instead of asking main.py for a new
    hook — and puts the original back on the way out, leaving nothing behind.
    """

    def __init__(self, player):
        self._player = player
        self._lock = threading.Lock()
        self._until = 0.0            # time.time() the latest batch stops sounding
        self._original = None
        self._own_attr = False

    def __enter__(self):
        player = self._player
        self._own_attr = "push_visemes" in getattr(player, "__dict__", {})
        original = self._original = player.push_visemes

        def tap(frames, hop, at, *args, **kwargs):
            try:
                end = float(at) + len(frames) * float(hop)
                with self._lock:
                    self._until = max(self._until, end)
            except Exception:
                pass
            return original(frames, hop, at, *args, **kwargs)

        player.push_visemes = tap
        return self

    def __exit__(self, *_exc):
        try:
            if self._own_attr:
                self._player.push_visemes = self._original
            else:
                del self._player.push_visemes     # back to the class's own method
        except Exception:
            pass
        return False

    def speaking(self) -> bool:
        with self._lock:
            return time.time() < self._until + _VOICE_GAP


_bridge = None
_bridge_lock = threading.Lock()


def _gui_bridge(app):
    """A QObject living on the GUI thread, so a worker can flip the mute there.

    The HUD's mute switch restyles widgets and writes the log, and a widget
    touched from any thread but the GUI thread is a crash waiting for a bad
    moment. A signal emitted here is queued onto the thread the receiver lives
    on — the one pattern that is safe from anywhere. (QTimer.singleShot from a
    worker is not: ui.py records it silently never firing.)
    """
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            from core.qt.QtCore import QObject, Signal, Slot

            class _Bridge(QObject):
                set_muted = Signal(object, bool)

                @Slot(object, bool)
                def apply(self, player, muted):
                    try:
                        player.muted = muted
                    except Exception as e:
                        print(f"[WhatsAppGuard] could not switch the microphone: {e}")

            bridge = _Bridge()
            bridge.moveToThread(app.thread())
            bridge.set_muted.connect(bridge.apply)
            _bridge = bridge
        return _bridge


def _set_muted(player, muted: bool) -> bool:
    """Set the HUD's microphone switch, on the GUI thread. -> did it take."""
    try:
        from core.qt.QtCore import QThread
        from core.qt.QtWidgets import QApplication
        app = QApplication.instance()
    except Exception:
        app = None
    if app is None or QThread.currentThread() is app.thread():
        player.muted = muted
    else:
        _gui_bridge(app).set_muted.emit(player, bool(muted))
    return _wait(lambda: bool(player.muted) == bool(muted), 2.0, 0.05)


def _call_live(transport) -> bool | None:
    """Is a call in progress on screen? None when this transport cannot tell."""
    try:
        return transport.in_call()
    except wa.Unsupported:
        return None
    except Exception as e:
        print(f"[WhatsAppGuard] in_call() raised {type(e).__name__}: {e}")
        return None


def _answer_and_speak(player, call, transport, note: str,
                      after_tool_call: bool) -> str:
    """Answer `call`, say `note` to the caller, hang up. -> an outcome line.

    Blocks for as long as the sentence takes, so it must never run inside a
    tool call: main.py answers tools from inside its receive loop, and the
    voice this waits for could not arrive until the tool had returned.

    `after_tool_call` means the user has just asked for this out loud. The
    model may still be replying to that request, and anything it says once the
    call is live is said to the caller — so that reply is let finish first.
    """
    caller = (call.caller or "").strip()
    who = caller or "unknown"
    action = "answer_and_speak"
    live = spoke = pressed = False
    final = None
    # Muted through the HUD's own switch, so the user can SEE the microphone is
    # off while a call is on the speakers — and switched back on only if it was
    # on to begin with.
    mute_asked = not bool(getattr(player, "muted", False))

    try:
        with _VoiceWatch(player) as voice:
            if mute_asked:
                _set_muted(player, True)
            if after_tool_call:
                _wait(voice.speaking, 1.5, 0.05)
            _wait(lambda: not voice.speaking(), 10.0, 0.1)

            try:
                answered = transport.accept(call)
            except wa.Unsupported as e:
                _record(caller, f"could not answer ({e})")
                return outcome.failed(action, str(e), caller=who)
            if not answered:
                _record(caller, "answer failed")
                _panel(player)
                return outcome.failed(action, "the answer button could not be "
                                      "pressed", caller=who)

            # The ringing panel goes, the call panel comes, and the audio path
            # to the caller opens a moment after that. The settle is paid even
            # when the call panel cannot be read, because the audio does not care.
            _wait(lambda: transport.incoming() is None, 4.0, 0.125)
            live = _wait(lambda: _call_live(transport) is True, 6.0, 0.3)
            time.sleep(_CONNECT_SETTLE)
            _log(player, f"{get_assistant_name()}: Answered the WhatsApp call from {who} — "
                         f"speaking to them.")

            player.request_say(_SPEAK.format(note=note))
            spoke = _wait(voice.speaking, _SPEECH_START, 0.05)
            if spoke:
                _wait(lambda: not voice.speaking(),
                      min(90.0, 10.0 + len(note) / 8), 0.1)
                time.sleep(_AFTER_SPEECH)

            # Hung up whether or not anything was said: a silent line held open
            # on somebody is the one outcome worse than a failed message.
            try:
                pressed = bool(transport.hangup())
            except wa.Unsupported:
                pressed = False
            ended = _wait(lambda: _call_live(transport) is False, 4.0, 0.3)
            final = False if ended else _call_live(transport)
    except Exception as e:
        print(f"[WhatsAppGuard] answering and speaking failed: "
              f"{type(e).__name__}: {e}")
        _record(caller, f"answer + speak failed ({e})")
        _panel(player)
        return outcome.failed(action, f"it stopped part-way ({e}); the call may "
                              f"still be open", caller=who)
    finally:
        if mute_asked:
            try:
                _set_muted(player, False)
            except Exception:
                pass

    # "Ended" is only claimed when the call was SEEN live and then seen gone.
    # A panel this transport cannot read proves nothing either way.
    ended_seen = live and final is False
    if final is True:
        what = ("answered, " + ("spoke" if spoke else "did NOT speak")
                + ", call still OPEN")
        res = outcome.failed("hang_up", "the call is still open - the hang-up "
                             "control could not be pressed, so it has to be "
                             "ended by hand", caller=who, spoke=spoke)
    elif not spoke:
        what = "answered, did NOT speak" + (", hung up" if ended_seen else "")
        res = outcome.failed(action, "the assistant's voice never started, so "
                             "nothing was said to the caller", caller=who,
                             call_ended=True if ended_seen else "unverified")
    elif ended_seen:
        what = "answered + spoke + hung up"
        res = outcome.done(action, caller=who, said=note, call_ended=True)
    else:
        what = "answered + spoke + hang-up not confirmed"
        res = outcome.unverified(action, "the message was said and hang-up was "
                                 "pressed, but the call window could not be "
                                 "read to confirm the call ended", caller=who,
                                 said=note, hangup_pressed=pressed)
    _record(caller, what)
    _log(player, f"{get_assistant_name()}: WhatsApp call from {who} — {what}.")
    _panel(player)
    return res


# ─────────────────────────────────────────────────────────────────────────────
# The watcher
# ─────────────────────────────────────────────────────────────────────────────
def _report_windows(player, transport) -> None:
    """Say what WhatsApp just put on screen, and what the guard made of it.

    This runs the moment the set of WhatsApp windows changes — which is the
    moment a call starts ringing — and nothing else. It exists because three
    separate rounds of "it does not trigger" were each debugged by guessing,
    changing something, and asking for another call. The window that appears is
    the whole story, so the guard tells it once, by itself, instead of needing a
    probe run alongside.
    """
    try:
        fam = wa._whatsapp_family(getattr(transport, "_pids", None))
        main = {h for h in (getattr(transport, "_focus_hwnd", None),
                            getattr(transport, "_scan_hwnd", None)) if h}
        lines = [f"[WhatsAppGuard] windows changed: {len(fam)} | "
                 f"main handles: {sorted(main) or 'NONE'}"]
        for w in fam:
            r = w["rect"]
            tag = "MAIN-skipped" if w["hwnd"] in main else "candidate"
            lines.append(f"  hwnd={w['hwnd']} [{tag}] {r[2]-r[0]}x{r[3]-r[1]} "
                         f"class={w['class']} title={w['title']!r}")
            if w["hwnd"] in main:
                continue
            data = wa._scan_call_window(w["hwnd"])
            if data is None:
                lines.append("    scan -> None (timed out or the window went away)")
                continue
            _wr, btns, txts = data
            named = [n for n, _r in btns if n]
            lines.append(f"    buttons: {named or 'none named'}")
            lines.append(f"    texts:   {[t for _y, t in txts] or 'none'}")
            lines.append(f"    ringing_shape -> {wa._ringing_shape(btns)} "
                         f"(needs 2-6 named buttons)")
        for ln in lines:
            print(ln)
        head = fam and [w for w in fam if w["hwnd"] not in main]
        if head:
            _log(player, f"{get_assistant_name()}: WhatsApp opened a new window "
                         f"({head[0]['title'] or 'untitled'}) — see the console "
                         f"if nothing was announced.")
    except Exception as e:
        print(f"[WhatsAppGuard] window report failed: {e}")


try:
    from core import sensors as _sensors          # what watches, shown on the window
except ImportError:                               # an older core: nothing to show
    _sensors = None


def _worker(player, stop: threading.Event, conf: dict) -> None:
    transport, why = wa.get(conf)
    if transport is None:
        _say(player, "Tell the user, in one short sentence in their own "
                     f"language: the WhatsApp call guard could not start - {why}")
        with _lock:
            _state["running"] = False
        if _sensors is not None:
            _sensors.stop("whatsapp_guard")
        return

    with _lock:
        _state["transport"] = transport
    _log(player, f"{get_assistant_name()}: Watching for incoming WhatsApp calls.")
    _panel(player)

    next_attach = 0.0
    unresolved_since = 0.0
    last_windows: tuple = ()
    while not stop.wait(_POLL_SECONDS):
        # Enumerating windows costs about 0.6ms and only reports when the set
        # actually changes, so this is a permanent aid rather than a debug flag
        # somebody has to remember to switch on.
        try:
            now_windows = tuple(sorted(
                w["hwnd"] for w in wa._whatsapp_family(
                    getattr(transport, "_pids", None))))
        except Exception:
            now_windows = ()
        if now_windows != last_windows:
            last_windows = now_windows
            _report_windows(player, transport)

        with _lock:
            mode = _state["mode"]
            pending = _state["pending"]
            pending_at = _state["pending_at"]

        # A ring the user never answered stops being answerable.
        if pending and time.monotonic() - pending_at > _PENDING_SECONDS:
            with _lock:
                _state["pending"] = None
            _record(pending.caller, "missed")
            _panel(player)

        try:
            call = transport.incoming()
        except wa.Unsupported:
            _say(player, "Tell the user, in one short sentence in their own "
                         "language: this way of reaching WhatsApp cannot see "
                         "incoming calls, so the guard has stopped")
            break
        except Exception as e:
            # WhatsApp was closed, or the window went away. Back off rather than
            # relaunching it in the background every second. Printed EVERY time
            # rather than once per backoff window: an exception here silently
            # swallowed is how "it just does not trigger" looked from outside.
            print(f"[WhatsAppGuard] incoming() raised "
                  f"{type(e).__name__}: {e} — will retry.")
            if time.monotonic() >= next_attach:
                next_attach = time.monotonic() + _REATTACH_SECONDS
            continue

        if call is None:
            unresolved_since = 0.0
            continue

        with _lock:
            already = call.key in _state["seen"]
        if already:
            continue

        # Not yet known which control answers and which refuses. Do NOT wait for
        # it here — waiting is what made the first call of a session fail: the
        # request took seconds, the caller rang off, and by the time the answer
        # arrived there was no call left to decline. Come back in a third of a
        # second instead; the lookup was started in the background the moment
        # this call was first seen.
        if not call.identified:
            if not unresolved_since:
                unresolved_since = time.monotonic()
                print("[WhatsAppGuard] call seen, controls not resolved yet — "
                      "waiting for the lookup that just started.")
            if time.monotonic() - unresolved_since < _RESOLVE_GRACE:
                continue
            print("[WhatsAppGuard] controls still unresolved after "
                  f"{_RESOLVE_GRACE:.0f}s — handling it anyway.")

        unresolved_since = 0.0
        with _lock:
            _state["seen"].append(call.key)
            del _state["seen"][:-20]

        try:
            _handle(player, call, mode, _guard_cfg(), transport)
        except Exception as e:
            print(f"[WhatsAppGuard] handling a call failed: {e}")

    with _lock:
        _state["running"] = False
        _state["pending"] = None
    if _sensors is not None:
        _sensors.stop("whatsapp_guard")
    _log(player, f"{get_assistant_name()}: Stopped watching WhatsApp calls.")
    _panel(player)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
# Gated by a person, never by the model (core/confirm): the button on screen,
# or on the phone during a phone turn. A step the phone already confirmed runs
# straight through (core/phonegate.confirmed_run), so nothing is asked twice.
_TALKS_TO_CALLER = ("accept_message", "answer_message", "accept_speak", "answer_speak",
                    "decline_message")
_NOT_ON = ("accept", "answer", "decline", "reject", "probe", "debug", "test", "status",
           "off", "stop", "disable") + _TALKS_TO_CALLER


def run(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "on").strip().lower()
    mode = str(p.get("mode") or "announce").strip().lower()
    if action in _TALKS_TO_CALLER:
        key = "confirm.call_message"
    elif action not in _NOT_ON and mode == "auto_reply":
        key = f"confirm.guard_{mode}"
    else:
        return _run_unconfirmed(parameters, player, session_memory)
    from core import confirm
    from core.i18n import t
    message = str(p.get("message") or "").strip() or _guard_cfg().get("busy_message") or ""
    return confirm.request(
        key="whatsapp_guard", title=t(key), detail=str(message)[:400],
        run=lambda: _run_unconfirmed(parameters, player, session_memory))


def _run_unconfirmed(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    action = (params.get("action") or "on").strip().lower()

    conf = wa.cfg()
    transport_cls, why = wa.choose(conf)
    if transport_cls is None:
        return f"I can't reach WhatsApp on this system: {why}."

    # -------- decisions about the call ringing right now --------
    if action in ("accept", "answer", "accept_message", "answer_message",
                  "accept_speak", "answer_speak",
                  "decline", "reject", "decline_message"):
        return _decide(player, action, conf, str(params.get("message") or ""))

    # -------- probe --------
    if action in ("probe", "debug", "test"):
        threading.Thread(target=_probe, args=(player, conf), daemon=True,
                         name="whatsapp-probe").start()
        return ("status=probing seconds=30 — the user should have somebody call "
                "them on WhatsApp now; results appear in the panel")

    # -------- status --------
    if action == "status":
        with _lock:
            running, mode = _state["running"], _state["mode"]
            missed = list(_state["missed"])
        _panel(player)
        if not running:
            return ("The WhatsApp call guard is off. "
                    + (f"{len(missed)} call(s) in this session's log."
                       if missed else "Nothing logged yet."))
        recent = "; ".join(f"{who or 'unknown'} at {when} ({what})"
                           for when, who, what in missed[-4:])
        return (f"The call guard is on in {mode.replace('_', ' ')} mode via "
                f"{transport_cls.label}."
                + (f" Recent: {recent}." if recent else " No calls yet."))

    # -------- off --------
    if action in ("off", "stop", "disable"):
        with _lock:
            running, stop = _state["running"], _state["stop"]
        if not running or stop is None:
            return "The WhatsApp call guard isn't running."
        stop.set()
        return "I've stopped watching your WhatsApp calls."

    # -------- on --------
    mode = (params.get("mode") or "announce").strip().lower()
    if mode not in ("announce", "auto_reply", "auto_decline"):
        mode = "announce"

    # "If anyone calls, decline it and say I'm not available" is a complete
    # instruction: the sentence to send is IN it. Demanding the user go and fill
    # in a settings field first — having just been told what to say — is the
    # assistant refusing to listen. What they said wins; the saved setting is
    # the fallback for when they said nothing.
    gcfg = _guard_cfg()
    spoken = str(params.get("message") or "").strip()
    session_note = spoken or gcfg["busy_message"]
    if mode == "auto_decline":
        _state["message"] = ""          # nothing is ever sent in this mode
    if mode == "auto_reply" and not session_note:
        return ("I'll need to know what to tell them. What should I say to "
                "people who call — something like 'I'm not available right "
                "now'?")

    with _lock:
        if _state["running"]:
            _state["mode"] = mode
            if session_note:
                _state["message"] = session_note
            _panel(player)
            return (f"The call guard was already running — switched it to "
                    f"{mode.replace('_', ' ')} mode.")
        stop = threading.Event()
        _state.update({"running": True, "stop": stop, "mode": mode,
                       "pending": None, "seen": [], "message": session_note})

    thread = threading.Thread(target=_worker, args=(player, stop, conf),
                              daemon=True, name="whatsapp-guard")
    with _lock:
        _state["thread"] = thread
    thread.start()
    if _sensors is not None:
        _sensors.start("whatsapp_guard", "screen", stop=stop.set)

    if mode == "auto_decline":
        return ("status=guarding mode=auto_decline messages=none — This is "
                "DONE. Every call will be refused and NOTHING will be sent to "
                "anyone. Confirm that in one short sentence, in the user's own "
                "language.")
    if mode == "auto_reply":
        # Say that it is SETTLED, not that it is quotable.
        #
        # This used to end with "quote this message back so they can correct
        # it", and the model read that as permission to ask — it had already
        # been given the sentence, had already stored it, and still came back
        # with "what would you like me to tell them?". A tool result that
        # describes an accomplished fact has to sound like one.
        return (f"status=guarding mode=auto_reply message_is_set=true "
                f"message=\"{session_note}\" — This is DONE. The message is "
                f"already stored and will be sent to every declined caller. Do "
                f"NOT ask the user what to say; they have already said it. "
                f"Confirm in one short sentence, in their own language, that "
                f"calls will be declined and tell them what the caller will be "
                f"sent.")
    return ("status=guarding mode=announce — This is DONE. Confirm in one "
            "short sentence, in the user's own language, that you will tell "
            "them who is calling and wait for their decision. Do not ask any "
            "further questions.")


def _probe(player, conf: dict) -> None:
    """Watch for thirty seconds and report every control that appears.

    The accept and decline controls are matched by a ~20-language vocabulary
    and, failing that, by layout — and both rules were written without ever
    having observed a real incoming call, because a ringing phone cannot be read
    out of documentation. This is how that gets fixed: somebody calls, and the
    tree says what it actually publishes. Only what is NEW since the last look
    is reported, so the steady-state interface does not bury the two lines that
    matter.
    """
    transport, why = wa.get(conf)
    if transport is None:
        _say(player, "Tell the user, in one short sentence in their own "
                     f"language: the probe could not reach WhatsApp - {why}")
        return

    seen: set[str] = set()
    fresh: list[str] = []
    deadline = time.monotonic() + 30.0
    _log(player, f"{get_assistant_name()}: Probing WhatsApp for 30 seconds — call me now.")

    while time.monotonic() < deadline:
        try:
            for line in transport.probe():
                if line not in seen:
                    seen.add(line)
                    fresh.append(line)
                    print(f"[WhatsAppGuard][probe] {line}")
        except Exception as e:
            print(f"[WhatsAppGuard][probe] {e}")
        time.sleep(1.0)

    body = "\n\n".join(fresh[-12:]) or "Nothing was published — WhatsApp may " \
                                       "have been closed the whole time."
    if player:
        try:
            player.show_content("🔎 WHATSAPP PROBE", body)
        except Exception:
            pass
    _log(player, f"{get_assistant_name()}: Probe finished — {len(fresh)} distinct view(s) seen. "
                 f"The full list is in the console.")
    _say(player, "Tell the user, in one short sentence in their own language: "
                 "the WhatsApp probe has finished and the results are on screen "
                 "and in the console")


def _decide(player, action: str, conf: dict, message: str = "") -> str:
    """Answer or refuse the call that is ringing right now."""
    with _lock:
        call = _state["pending"]
        pending_at = _state["pending_at"]
        transport = _state["transport"]

    if call is None or transport is None:
        return "There's no WhatsApp call waiting for an answer right now."
    if time.monotonic() - pending_at > _PENDING_SECONDS:
        with _lock:
            _state["pending"] = None
        return "That call has already stopped ringing."

    # Everything that would make answering-and-speaking impossible is checked
    # while the phone is still ringing and nothing has been pressed, so the
    # user can still decide something else.
    speak = action in ("accept_speak", "answer_speak")
    note = ""
    if speak:
        who = (call.caller or "").strip() or "unknown"
        why = _voice_ready(player)
        if why:
            return outcome.failed("answer_and_speak", why, caller=who)
        note = _note_for(message, _guard_cfg())
        if not note:
            return outcome.ask("answer_and_speak",
                               "what should be said to the caller?", caller=who)
        with _lock:
            if _state["speaking"]:
                return ("status=busy action=answer_and_speak -- a call is "
                        "already being answered and spoken to. Do not call "
                        "this tool again.")

    # Re-read the live window rather than trusting the snapshot taken when it
    # started ringing, and this time allow the wait: the user has just spoken,
    # a button is about to be pressed, and the mapping must be right rather than
    # merely fast. In practice it is already cached — the background resolve
    # started the moment the call was first seen.
    caller = (call.caller or "").strip() or "an unknown caller"
    try:
        # The user has just spoken, so a moment's wait is affordable here in a
        # way it never is on the ring path.
        fresh = transport.incoming(blocking=True)
    except Exception as e:
        print(f"[WhatsAppGuard] re-reading the call failed: {e}")
        fresh = None

    with _lock:
        _state["pending"] = None

    if fresh is None:
        _record(call.caller, "stopped ringing")
        _panel(player)
        return f"The call from {caller} has already stopped ringing."
    if not fresh.identified:
        _record(call.caller, "controls unresolved")
        _panel(player)
        return (f"{caller} is still ringing, but I couldn't work out which "
                f"button answers and which refuses, so I haven't touched it — "
                f"you'll have to pick it up yourself. The controls it shows "
                f"are: {', '.join(fresh.controls[:6]) or 'none I can read'}.")
    call = fresh

    if speak:
        # Off the tool call, on a thread of its own. The tool has to return
        # before the model can say anything at all, and saying the message is
        # the whole point.
        with _lock:
            _state["speaking"] = True

        def work():
            try:
                res = _answer_and_speak(player, call, transport, note,
                                        after_tool_call=True)
            finally:
                with _lock:
                    _state["speaking"] = False
            print(f"[WhatsAppGuard] {res}")
            _say(player, "The WhatsApp call the user asked you to answer and "
                         "speak to is over. Tell the user, in one short "
                         f"sentence in their own language, how it went: {res}")

        threading.Thread(target=work, daemon=True, name="whatsapp-speak").start()
        return (f"status=started action=answer_and_speak caller=\"{caller}\" -- "
                f"UNDER WAY. Say NOTHING now: the call is being answered, and "
                f"anything you say will be heard by the caller. How it went is "
                f"reported to you when the call has ended. Do not call this "
                f"tool again.")

    if action in ("accept", "answer", "accept_message", "answer_message"):
        with_message = action in ("accept_message", "answer_message")
        try:
            ok = transport.accept(call)
        except wa.Unsupported as e:
            return f"I can't answer calls through this transport — {e}."
        except Exception as e:
            return f"I couldn't answer that call: {e}."
        if not ok:
            _record(call.caller, "answer failed")
            _panel(player)
            return "I found the call but couldn't press answer."

        if not with_message:
            _record(call.caller, "answered")
            _log(player, f"{get_assistant_name()}: Answered the WhatsApp call from {caller}.")
            _panel(player)
            # Nothing more is said. The call is live in the user's ear now, and
            # talking over it is exactly the noise this plugin exists to avoid.
            return f"status=answered caller={caller}"

        # Answering AND writing. The call is live from here on, so the sending
        # runs against a WhatsApp that has just swapped its conversation window
        # for a call window — which is precisely the case the message path is
        # built for, and it reports honestly when the window never comes back.
        sent, failure = _send_to_caller(transport, call.caller,
                                        _note_for(message, _guard_cfg()))
        what = "answered" + (" + message sent" if sent
                             else " (message NOT sent)")
        _record(call.caller, what)
        _log(player, f"{get_assistant_name()}: WhatsApp call from {caller} — {what}.")
        _panel(player)
        if sent:
            return (f"status=answered+message_sent caller={caller} — the call "
                    f"is live and the message went to them. Say so in one "
                    f"short sentence, in the user's own language.")
        return (f"status=answered message_sent=false caller={caller} — the "
                f"call was answered, but {failure}. Tell the user plainly "
                f"that the message was NOT sent and why - do not say it was.")

    return _decline_now(player, call, transport, _guard_cfg(),
                        with_message=(action == "decline_message"),
                        message=message)
