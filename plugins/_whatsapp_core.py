"""
Shared WhatsApp access layer for the JARVIS WhatsApp plugins.

NOT A PLUGIN. The leading underscore keeps the loader from treating it as one
(core/plugin_loader.py skips `_*.py`), exactly like _google_core.py serves the
Gmail/Calendar pair and _printer_core.py serves the printer trio. Ship it
alongside whatsapp_call.py and whatsapp_guard.py; if a user downloads a plugin
without it the loader already names the missing file for them.

WHY THIS FILE EXISTS
    whatsapp_call.py grew a complete WhatsApp driver inside itself, and every
    line of it was Windows UI Automation. A second plugin that answers incoming
    calls needs the same driver, and a Linux user has no WhatsApp desktop app to
    drive at all. So the driver moved here, behind an interface, with more than
    one implementation behind it.

THE INTERFACE
    A Transport is "some way to reach WhatsApp". It can find the app, open a
    conversation, read back who is actually on screen, press the call buttons,
    send a message, and see an incoming call. What it CANNOT do it reports as
    unsupported rather than pretending.

    Three of them:

      WindowsDesktop  UI Automation against the WhatsApp desktop app. The
                      original implementation, moved here unchanged — same
                      Invoke-the-control approach, same header geometry, same
                      confirm-before-dialling contract.

      MacDesktop      The macOS Accessibility API (AXUIElement / AXPress),
                      which is the exact structural equivalent of UIA. Needs
                      pyobjc and the user granting Accessibility permission.

      Web             Playwright against web.whatsapp.com in a persistent
                      profile. Works identically on all three systems, and is
                      the only option on Linux, where there is no official
                      WhatsApp desktop application at all.

CHOOSING ONE
    The `transport` setting is 'auto', 'desktop' or 'web'. 'auto' prefers the
    desktop app — that is what most people actually run, and a call placed there
    rings in the real application with the real audio devices — and falls back
    to the web transport when this system has no desktop path.

EVERY LANGUAGE, STILL
    The folding rules, the ~20-language call-button vocabulary and the learned
    header-hint stripping all moved here untouched, because they are not
    Windows-specific: the web transport reads the same words out of aria-labels.
"""
from __future__ import annotations

import platform
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from memory.config_manager import get_plugin_config

try:
    from core.paths import data_dir as _data_dir
except ImportError:          # a core older than core/paths.py
    _data_dir = lambda: Path(__file__).resolve().parent.parent

BASE_DIR = _data_dir()

# The settings namespace is deliberately the one whatsapp_call.py already used,
# so nobody's stored app name or timeouts are lost when they update. It is also
# what makes the settings UI show ONE "WHATSAPP" card for the whole family:
# plugin_loader.settings_schemas() dedupes sections by namespace.
NS = "whatsapp_call"

_SYSTEM = platform.system().lower()
IS_WINDOWS = _SYSTEM.startswith("win")
IS_MACOS = _SYSTEM == "darwin"
IS_LINUX = _SYSTEM.startswith("linux")


# ─────────────────────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────────────────────
DEFAULTS = {
    "transport": "auto",          # auto | desktop | web
    "app_name": "WhatsApp",
    "verify_contact": True,
    "launch_wait": 8.0,
    "search_wait": 4.0,
}

_TRANSPORT_CHOICES = ("auto", "desktop", "web")


def cfg() -> dict:
    """Stored settings merged over the defaults, with every value coerced to the
    type the code expects. A blank field means 'unset', not 'empty string'."""
    stored = get_plugin_config(NS)
    out = dict(DEFAULTS)
    for key, default in DEFAULTS.items():
        raw = stored.get(key)
        if raw in (None, ""):
            continue
        if isinstance(default, bool):
            out[key] = raw if isinstance(raw, bool) else \
                str(raw).strip().lower() in ("1", "true", "on", "yes")
        elif isinstance(default, float):
            try:
                out[key] = max(0.5, min(60.0, float(str(raw).strip())))
            except (TypeError, ValueError):
                pass
        else:
            out[key] = str(raw).strip()
    if out["transport"].lower() not in _TRANSPORT_CHOICES:
        out["transport"] = "auto"
    else:
        out["transport"] = out["transport"].lower()
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Language-neutral text folding — accents stripped by Unicode decomposition,
# spacing and punctuation dropped, the rest casefolded. Same rule set the
# screen agent uses, so "Görüntülü arama" and "GORUNTULU ARAMA" are one string.
# ─────────────────────────────────────────────────────────────────────────────
_PUNCT = re.compile(r"[\s&_\.… :\-–—()\[\]'’\"/]+")


def _invisible(ch: str) -> bool:
    """Characters that carry no appearance of their own.

    Text that has been through a clipboard, a WebView and an accessibility
    property is not always byte-for-byte the text that went in: joiners and
    direction marks get added and dropped, and an emoji arrives with or without
    the selector that asks for the colour version. None of it is visible, none
    of it is what anybody typed, and all of it would make two identical
    sentences compare as different.

    Two rules, both from the Unicode data rather than from a list: the format
    category (Cf) covers the joiners, the left-to-right and right-to-left
    marks, and the soft hyphen; the variation selectors are named by range
    because Unicode files them under a mark category alongside real diacritics
    that must NOT be dropped — a Devanagari vowel sign changes the word, a
    variation selector changes nothing but the shade of an emoji.
    """
    if unicodedata.category(ch) == "Cf":
        return True
    cp = ord(ch)
    return 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(ch for ch in s
                if not unicodedata.combining(ch) and not _invisible(ch))
    return _PUNCT.sub("", s).casefold()


def fold_words(s: str) -> str:
    """fold(), but the gaps between words survive as single spaces.

    fold() throws separators away, which is right when comparing two names to
    each other and wrong when looking for a name INSIDE a sentence: without the
    gaps there is no way to tell a whole name from the middle of a longer one.
    """
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(ch for ch in s
                if not unicodedata.combining(ch) and not _invisible(ch))
    return _PUNCT.sub(" ", s).strip().casefold()


def name_in_phrase(name: str, phrase: str) -> bool:
    """Is `name` in `phrase` as a whole name, rather than as a fragment of a
    longer one?

    THIS EXISTS BECAUSE THE LOOSE VERSION OPENED THE WRONG PERSON'S CHAT.

    The composer's placeholder is a sentence with the contact's name somewhere
    inside it — "Type a message to A Annem" — and a plain substring test
    against a sentence is far looser than the same test against a header, which
    is a name with a hint stuck on the end. Observed on the real window: asked
    for 'A Annem', it accepted the conversation whose box read "Type a message
    to a annemm". A different contact, one letter longer, and the message would
    have gone to them.

    So the match has to begin and end where a word does. A letter or a digit
    immediately beside it means the name was cut out of the middle of a longer
    one; a space, a heart, a comma, or the end of the string means it was not
    — which is what keeps "mum" matching "Mum ❤" while "Ali" stops matching
    "Alican". Decided by Unicode category rather than by alphabet, and it makes
    no assumption about WHERE in the sentence the name sits: WhatsApp puts it
    last in English and first in Turkish, and neither is written down here.
    """
    n, phrase_folded = fold_words(name), fold_words(phrase)
    if not n or not phrase_folded:
        return False
    at = phrase_folded.find(n)
    while at >= 0:
        before = phrase_folded[at - 1] if at else ""
        after = phrase_folded[at + len(n):at + len(n) + 1]
        if not (before and before.isalnum()) and not (after and after.isalnum()):
            return True
        at = phrase_folded.find(n, at + 1)

    # A phone number typed with spaces or dashes against one saved without
    # them. The word rule cannot see through the grouping, and the grouping is
    # not part of the number — the same allowance names_match makes, and for
    # the same reason.
    digits_n = re.sub(r"\D", "", n)
    if len(digits_n) >= 7:
        return digits_n in re.sub(r"\D", "", phrase_folded)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# What each control DOES — asked, not tabulated
#
# WhatsApp publishes its controls under whatever name the user's WhatsApp is
# set to: "Sesli arama", "Voice call", "音声通話", "Прием", "ተቀበል". The obvious
# implementation is a table of translations, and it is the wrong one — a table
# is finite, it is stale the day a language is added, and the person it fails is
# always somebody the author never thought about.
#
# There is a model in this process that reads every language already. So the
# labels are handed to it and it says what each one is for. No vocabulary, no
# per-language rules, nothing to extend when WhatsApp ships a new locale.
#
# IT IS ASKED ONCE, NOT ONCE PER CALL.
# The answer depends only on the set of labels, which changes when the user
# changes their WhatsApp language and at no other time. So it is cached on disk
# and keyed by the labels themselves: one request per WhatsApp language for the
# lifetime of the install, and every ring after that is a dictionary lookup.
#
# WHEN IT CANNOT BE ASKED
# No API key, no network, or a model that is unsure: nothing is invented. The
# outgoing call buttons fall back to layout, which is safe because video and
# voice sit in a fixed order and pressing either still calls the person the user
# asked for. Answer and decline get no fallback at all — see the note on
# Incoming.identified for why a coin toss there is not a fallback.
# ─────────────────────────────────────────────────────────────────────────────
_ROLES = ("video", "voice", "end", "accept", "decline")
_BULLET = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s*")
_CONTROL_CACHE = BASE_DIR / "memory" / "whatsapp_controls.json"
_resolve_lock = threading.Lock()
_resolve_memo: dict[str, dict] = {}
# The model ladder, the request deadline and the key all live in
# core/gemini.py now — this file only says what it needs asking.

_RESOLVE_PROMPT = (
    "These are the accessible names of the on-screen controls of a WhatsApp "
    "window, written in whatever language that WhatsApp is set to. Say what "
    "each one is for.\n\n"
    "{listing}\n\n"
    "Return ONLY minified JSON, no markdown fences and no prose, with exactly "
    "these keys:\n"
    '  "video"   : the control that STARTS a video call, else null\n'
    '  "voice"   : the control that STARTS a voice/audio call, else null\n'
    '  "end"     : the control that ENDS/hangs up a call in progress, else null\n'
    '  "accept"  : the control that ANSWERS an incoming call, else null\n'
    '  "decline" : the control that REJECTS an incoming call, else null\n\n'
    "Each value must be one control name from the list above, copied EXACTLY "
    "and on its own — no bullet, no number, no quotes around it, no "
    "translation — or null.\n"
    "Not every control is a call button. The list can also contain a contact's "
    "name, a search box, a menu, an attachment control, or the mute and camera "
    "toggles that appear during a call. None of those start, end, answer or "
    "reject anything, so they are null for every key.\n"
    "In particular, silencing a ringtone, muting a microphone or turning off a "
    "camera is NOT ending, answering or rejecting a call — the call carries on "
    "either way, so those are null too.\n"
    "Use null whenever no control clearly has that purpose, and whenever you "
    "are not sure. A wrong answer here calls, answers or hangs up on a real "
    "person, so null is always the better guess. Never give the same control "
    "two different purposes."
)


def _api_key() -> str:
    from core import gemini
    return gemini.api_key()


def _cache_key(labels: tuple[str, ...]) -> str:
    import hashlib
    return hashlib.sha1("\n".join(labels).encode("utf-8")).hexdigest()[:16]


def _cache_load() -> dict:
    try:
        import json
        return json.loads(_CONTROL_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _cache_save(store: dict) -> None:
    try:
        import json
        _CONTROL_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CONTROL_CACHE.write_text(json.dumps(store, ensure_ascii=False),
                                  encoding="utf-8")
    except Exception:
        pass


def _ask_model(labels: tuple[str, ...]) -> dict | None:
    from core import gemini

    listing = "\n".join(name for name in labels if name)
    raw = gemini.as_json(_RESOLVE_PROMPT.format(listing=listing), tier=gemini.FAST)
    if not isinstance(raw, dict):
        return None

    # The model answers with the label text, not a position, and the position is
    # worked out here. Asking for an index looked tidier and was measurably
    # worse: on a Turkish header it came back one place out, naming the
    # conversation title as the video-call button and the video-call button as
    # the voice one. Copying a string back is a much easier thing to get right
    # than counting, and a string that does not appear in the list is obviously
    # wrong and can simply be dropped — a wrong number cannot be spotted at all.
    lookup = {fold(name): i for i, name in enumerate(labels) if name}
    out: dict[str, int] = {}
    used: set[int] = set()
    for role in _ROLES:
        val = raw.get(role)
        if not isinstance(val, str):
            continue
        idx = lookup.get(fold(val))
        if idx is None:
            # A model handed a list will sometimes hand back a list ITEM —
            # "1. Video call", "- Video call" — rather than the bare name. That
            # is a formatting slip rather than a wrong answer, so it is stripped
            # and retried instead of thrown away.
            idx = lookup.get(fold(_BULLET.sub("", val)))
        if idx is None or idx in used:
            continue
        out[role] = idx
        used.add(idx)
    return out


def resolve_controls(labels: list[str], cached_only: bool = False) -> dict:
    """{role: index} for the controls that were recognised. Never raises.

    `cached_only` is what keeps a ringing phone fast. Asking the model is a
    network round trip, and on the very first call in a given WhatsApp language
    it is the slowest thing in the whole plugin — measured at ten seconds when
    the first model in the list was timing out. Announcing a call does not need
    the answer: who is ringing comes from the window title and THAT it is
    ringing comes from the shape. Only pressing a button needs it, and that
    happens later, after a human has said something.

    So the ring path reads the cache and moves on, `resolve_async` fills it in
    behind the announcement, and by the time the user has said "answer it" the
    mapping is there.
    """
    clean = tuple((s or "").strip() for s in labels)
    if not any(clean):
        return {}
    key = _cache_key(clean)

    with _resolve_lock:
        if key in _resolve_memo:
            return dict(_resolve_memo[key])
        store = _cache_load()
        if key in store:
            _resolve_memo[key] = store[key]
            return dict(store[key])

    if cached_only:
        return {}

    answer = _ask_model(clean)
    if answer is None:
        return {}

    with _resolve_lock:
        _resolve_memo[key] = answer
        store = _cache_load()
        store[key] = answer
        # Keyed by label set, so this only grows when the user changes their
        # WhatsApp language. A cap keeps a pathological case bounded anyway.
        if len(store) > 40:
            for stale in list(store)[:-40]:
                store.pop(stale, None)
        _cache_save(store)
    return dict(answer)


_resolving: set[str] = set()


def resolve_async(labels: list[str]) -> None:
    """Warm the cache for these labels without making anybody wait.

    Started the instant a ring is seen, so the request runs alongside the
    announcement rather than in front of it. One thread per distinct label set:
    a call rings for thirty seconds and the loop polls several times a second,
    so without the guard below the same request would be fired fifty times.
    """
    clean = tuple((s or "").strip() for s in labels)
    if not any(clean):
        return
    key = _cache_key(clean)
    with _resolve_lock:
        if key in _resolve_memo or key in _resolving:
            return
        _resolving.add(key)

    def work():
        try:
            resolve_controls(list(clean))
        finally:
            with _resolve_lock:
                _resolving.discard(key)

    threading.Thread(target=work, daemon=True, name="whatsapp-resolve").start()


def role_of(labels: list[str], index: int) -> str | None:
    """What this one control is for, if the model recognised it."""
    for role, idx in resolve_controls(labels).items():
        if idx == index:
            return role
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Cleaning the header title
#
# WhatsApp does not publish the contact's name on its own. The desktop header
# button is named "<contact> click here for contact info" — an instruction to a
# screen reader, not part of anybody's name — and the self-chat reads "<number>
# (You) Message yourself". Repeating that back at the user looks like a bug,
# because it is one.
#
# There is no cleaner property to read instead, so the trailing hint has to be
# removed, and the only rules that work in every language are the two below —
# never a table of translated phrases.
# ─────────────────────────────────────────────────────────────────────────────
_seen_titles: list[str] = []
_MAX_SEEN = 12


def note_title(raw: str) -> None:
    """Remember distinct header titles. The hint is appended to every chat, so
    once two different conversations have been seen, whatever they end with in
    common IS the hint — learned rather than translated."""
    raw = (raw or "").strip()
    if raw and raw not in _seen_titles:
        _seen_titles.append(raw)
        del _seen_titles[:-_MAX_SEEN]


def learned_hint() -> str:
    """Longest suffix shared by every remembered title — but only if it is
    clearly a phrase rather than a coincidence.

    Two contacts who share a surname also share a suffix, and a naive longest
    match on "Ahmet Yilmaz" / "Mehmet Yilmaz" is "hmet Yilmaz", which would
    display Ahmet as "A". So a candidate has to survive three checks: it must
    begin where a word begins, it must be a phrase of several words (or, for
    scripts that do not space their words, a decent run of characters), and it
    must leave a real name behind in every title seen.
    """
    if len(_seen_titles) < 2:
        return ""
    shortest = min(_seen_titles, key=len)
    best = ""
    for size in range(1, len(shortest)):
        tail = shortest[-size:]
        if all(t.endswith(tail) for t in _seen_titles):
            best = tail
        else:
            break
    best = best.strip()
    if len(best) < 10:
        return ""

    spaced = " " in best
    if spaced:
        # It has to start where a word starts, in every title, or it is a
        # fragment of somebody's name rather than an appended phrase.
        if any(not t[: -len(best)].endswith((" ", "\t", " "))
               for t in _seen_titles):
            return ""
        # And a hint is a sentence ("click here for contact info"); a shared
        # surname is two words at most.
        if len(best.split()) < 3:
            return ""
    elif len(best) < 6:
        # Scripts that do not space their words get a length rule instead.
        return ""

    # Never strip so much that some chat is left without a usable name.
    if any(len(t[: -len(best)].strip(" ,.-–—")) < 2 for t in _seen_titles):
        return ""
    return best


def display_name(raw: str, wanted: str = "") -> str:
    """The contact's name, without the screen-reader hint glued to it."""
    raw = (raw or "").strip()
    if not raw:
        return raw

    hint = learned_hint()
    if hint and raw.endswith(hint):
        cut = raw[: -len(hint)].strip(" ,.-–—")
        if cut:
            return cut

    # Nothing learned yet — but on the calling path we know who was asked for,
    # so cut the string where that name ends. Exact in the common case, and it
    # needs no knowledge of the language the hint is written in.
    w = fold(wanted)
    if w:
        for end in range(1, len(raw) + 1):
            if w in fold(raw[:end]):
                cut = raw[:end].strip(" ,.-–—")
                if cut:
                    return cut
    return raw


def names_match(wanted: str, shown: str) -> bool:
    """Did we land on the right conversation? Deliberately generous about
    partial names ("mum" vs "Mum ❤"), strict about landing somewhere else."""
    w, s = fold(wanted), fold(shown)
    if not w or not s:
        return False
    if w == s or w in s or s in w:
        return True
    # A phone number typed with spaces/dashes against one saved without them.
    wd, sd = re.sub(r"\D", "", w), re.sub(r"\D", "", s)
    if len(wd) >= 7 and (wd in sd or sd in wd):
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# What a transport hands back
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Chat:
    """One open conversation, as the transport currently sees it."""
    raw_title: str = ""                       # exactly what the tree published
    controls: list = field(default_factory=list)   # [(name, handle), ...] in visual order
    handle: object = None                     # transport-private (hwnd, page, AXRef…)
    # What the message box called itself when this conversation was opened.
    # Kept so the typing step can tell "the conversation changed under me" from
    # "the box never named the contact in the first place" — which it often
    # does not: see _message_chat.
    placeholder: str = ""

    def name(self, wanted: str = "") -> str:
        return display_name(self.raw_title, wanted)


@dataclass
class Incoming:
    """An incoming call the transport can see right now.

    `identified` is the important field, and the asymmetry behind it is
    deliberate.

    Seeing THAT a call is ringing needs no words at all: it is a panel, or a
    window, that was not there a second ago and carries a small cluster of
    controls. That works in Khmer and Amharic exactly as well as in English.

    Knowing WHICH of those controls answers and which refuses is a different
    question, and the layout trick the outgoing call buttons rely on must not be
    reused here. Video and voice sit in a fixed order and pressing the wrong one
    still places a call the user wanted; accept and decline do not, and pressing
    the wrong one either answers a call somebody wanted refused or hangs up on
    somebody they were waiting for. Both are done to a real person and neither
    can be taken back.

    So when the vocabulary recognises the controls, `identified` is True and the
    plugin may press them. When it does not, the call is still reported — the
    user is told who is ringing, which is most of the value — and every button
    press is refused with an explanation instead of guessed at.
    """
    caller: str = ""
    call_type: str = "voice"                  # 'voice' | 'video'
    handle: object = None                     # transport-private; None when unidentified
    key: str = ""                             # stable id, so one ring is announced once
    identified: bool = False                  # were accept/decline actually recognised?
    controls: list = field(default_factory=list)   # what WAS published, for the probe hint


class Unsupported(RuntimeError):
    """This transport cannot do that here — raised with a sentence the user can
    act on, never a stack trace they cannot."""


# ─────────────────────────────────────────────────────────────────────────────
# The transport contract
#
# Every method returns data or raises Unsupported with a sentence. Nothing here
# speaks, logs or decides policy: that belongs to the plugins, which know what
# the user asked for and what language to answer in.
# ─────────────────────────────────────────────────────────────────────────────
class Transport:
    name = "abstract"
    label = "abstract"

    # -- availability -------------------------------------------------------
    @classmethod
    def usable(cls) -> tuple[bool, str]:
        """Can this transport run on this machine at all? (ok, why-not)."""
        return False, "not implemented"

    # -- lifecycle ----------------------------------------------------------
    def ensure_ready(self, conf: dict) -> tuple[bool, str]:
        """Attach to (or start) WhatsApp. (ok, message-when-not)."""
        raise Unsupported("this transport cannot start WhatsApp")

    def close(self) -> None:
        """Release whatever was held. Must be safe to call twice."""

    # -- conversations ------------------------------------------------------
    def current_chat(self) -> Chat | None:
        """The conversation on screen right now, or None."""
        raise Unsupported("this transport cannot read the open conversation")

    def open_chat(self, contact: str, conf: dict, before: str = "") -> Chat | None:
        """Search for `contact` and return the conversation that opened."""
        raise Unsupported("this transport cannot open a conversation")
    def open_chat_for_message(self, contact: str, conf: dict, before: str = ""):
        """Open `contact`'s conversation so a message can be TYPED into it.

        Separate from open_chat() because the two are reached from different
        places. open_chat() runs when the user is watching: WhatsApp is in
        front, the keyboard is ours, and typing into the search box is the
        plainest thing that works.

        This one runs seconds after a call was declined — WhatsApp has just
        hidden its conversation window, the keyboard is wherever the call panel
        left it, and a transport that insists on typing gets nothing. A
        transport that has a route needing neither the foreground nor the
        keyboard should override this and offer it first.

        The default is the search route, so a transport that has nothing better
        still works.
        """
        return self.open_chat(contact, conf, before=before)

    # -- outgoing -----------------------------------------------------------
    def press_call(self, chat: Chat, call_type: str) -> tuple[bool, str]:
        """Press voice/video on an open chat. (ok, how) where how is
        'name' | 'position', or (False, reason)."""
        raise Unsupported("this transport cannot place calls")

    def hangup(self) -> bool:
        raise Unsupported("this transport cannot end a call")

    def send_text(self, chat: Chat, text: str) -> bool:
        raise Unsupported("this transport cannot send messages")

    def send_message_to(self, contact: str, text: str,
                        conf: dict | None = None) -> tuple[bool, str]:
        """Open `contact`'s conversation and send `text`. -> (sent, why-not).

        THE ONE PLACE THAT DECIDES WHAT "SENT" MEANS.

        Every caller of this family used to assemble the sequence for itself —
        open a chat, check the title, type — and each one checked a slightly
        different subset, so the same failure was reported three different ways
        and one of them reported it as success. A message that did not go is
        the failure that matters most here, because the user acts on it: they
        stop worrying about the call.

        So the sequence lives here once, per transport it is the same sequence,
        and the second half of the answer is a phrase that says what did NOT
        happen. Callers pass it on rather than inventing one.

        Nothing in here is language-specific. The contact is matched by
        identity (names_match), never by vocabulary, and `text` is whatever the
        user said, in whatever language they said it in.
        """
        contact = (contact or "").strip()
        text = (text or "").strip()
        if not contact:
            return False, ("there was nobody to send it to, so NOTHING was sent")
        if not text:
            return False, ("there was no message to send, so NOTHING was sent")
        conf = conf or cfg()

        try:
            chat = self.open_chat_for_message(contact, conf)
        except Unsupported as e:
            return False, f"{e}, so NOTHING was sent"
        except Exception as e:
            print(f"[WhatsApp] opening {contact!r} failed: {type(e).__name__}: {e}")
            return False, (f"{contact}'s conversation could not be opened "
                           f"({e}), so NOTHING was sent")

        if not chat:
            return False, (f"{contact}'s conversation could not be opened, so "
                           f"NOTHING was sent")

        # The same refusal the calling path makes, for the same reason: a
        # message in the wrong conversation is worse than no message at all,
        # and it cannot be taken back.
        #
        # name_in_phrase rather than names_match because what comes back here
        # may be a sentence — the composer's placeholder — rather than a bare
        # title, and 'A Annem' is a substring of 'a annemm'. It is the stricter
        # of the two on a sentence and identical to it on a title.
        if not name_in_phrase(contact, chat.raw_title):
            return False, (f"the conversation that opened was "
                           f"'{chat.name(contact)}' rather than '{contact}', "
                           f"so NOTHING was sent")

        try:
            ok = self.send_text(chat, text)
        except Unsupported as e:
            return False, f"{e}, so NOTHING was sent"
        except Exception as e:
            print(f"[WhatsApp] send_text to {contact!r} failed: "
                  f"{type(e).__name__}: {e}")
            return False, f"typing the message failed ({e}), so NOTHING was sent"

        if not ok:
            return False, (f"the message could not be typed into {contact}'s "
                           f"chat, so NOTHING was sent")
        return True, ""

    # -- incoming -----------------------------------------------------------
    def incoming(self, blocking: bool = False) -> Incoming | None:
        """Poll for a ringing call. None when nothing is ringing.

        `blocking` decides whether it may wait on the network to work out which
        control answers and which refuses. The watch loop calls it without,
        so an announcement is never held up; the moment a button actually has
        to be pressed it is called with, by which time the background resolve
        started at the first sighting has almost always finished anyway.
        """
        raise Unsupported("this transport cannot see incoming calls")

    def accept(self, call: Incoming) -> bool:
        raise Unsupported("this transport cannot answer calls")

    def decline(self, call: Incoming) -> bool:
        raise Unsupported("this transport cannot decline calls")

    def in_call(self) -> bool | None:
        """Is a call IN PROGRESS on screen? None when that cannot be read.

        The opposite question to incoming(), asked of the same controls: a
        panel that offers a way to hang up and none to answer. It is what lets
        a plugin say that a call it answered actually connected, and that a
        call it hung up actually ended, instead of assuming both from a button
        press having returned.

        Unlike incoming() it may ask the model for the control mapping —
        nothing is ringing while it is asked, and a live call's controls are a
        different label set from a ringing one, so the first call in a given
        WhatsApp language pays one lookup and every later one reads the cache.
        """
        raise Unsupported("this transport cannot see a call in progress")

    # -- the ⚙ CHECK button -------------------------------------------------
    def diagnose(self, conf: dict) -> tuple[bool, list[str]]:
        """Everything that is wrong, not the first thing. (ok, lines)."""
        return False, ["✗ this transport has no self-check"]

    # -- discovery ----------------------------------------------------------
    def probe(self) -> list[str]:
        """Every control this transport can see right now, by accessible name.

        This exists because a ringing call cannot be read from documentation.
        The accept and decline controls are matched by a vocabulary and a
        layout, and both were written without ever having observed a real
        incoming call — on any of the three systems. So rather than guess and
        fail silently, the guard can be asked to watch for thirty seconds while
        somebody actually calls, and report what appeared. Whatever comes back
        is the truth, and the vocabulary is corrected from it.
        """
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Picking one
#
# 'auto' prefers the desktop app because that is what most people run, and a
# call placed there rings in the real application on the real audio devices. It
# falls through to the web transport when this system has no desktop path —
# which is every Linux machine, since WhatsApp ships no official Linux client.
# ─────────────────────────────────────────────────────────────────────────────
_registry_lock = threading.Lock()
_live: dict[str, Transport] = {}


def _desktop_class() -> type[Transport] | None:
    if IS_WINDOWS:
        return WindowsDesktop
    if IS_MACOS:
        return MacDesktop
    return None


def _web_class() -> type[Transport]:
    return WebTransport


def choose(conf: dict | None = None) -> tuple[type[Transport] | None, str]:
    """(transport class, why-not). Never raises — an unusable system returns a
    sentence explaining what to install or switch on."""
    conf = conf or cfg()
    want = conf.get("transport", "auto")

    def _try(getter):
        try:
            cls = getter()
        except Exception as e:                    # a missing helper file, say
            return None, f"could not load that transport: {e}"
        if cls is None:
            return None, ""
        ok, why = cls.usable()
        return (cls, "") if ok else (None, why)

    if want == "web":
        cls, why = _try(_web_class)
        return cls, why or "the web transport is unavailable"

    if want == "desktop":
        if _desktop_class() is None:
            return None, ("there is no WhatsApp desktop app for this system — "
                          "switch the WhatsApp transport to 'web' in settings")
        cls, why = _try(_desktop_class)
        return cls, why or "the desktop transport is unavailable"

    # auto: desktop first, web second, and report BOTH reasons when neither works
    cls, desk_why = _try(_desktop_class)
    if cls:
        return cls, ""
    cls, web_why = _try(_web_class)
    if cls:
        return cls, ""
    parts = [p for p in (desk_why, web_why) if p]
    return None, "; ".join(parts) or "no way to reach WhatsApp on this system"


def get(conf: dict | None = None) -> tuple[Transport | None, str]:
    """A live, ready transport instance — one per kind, reused for the life of
    the process so the web transport does not relaunch a browser per command."""
    conf = conf or cfg()
    cls, why = choose(conf)
    if cls is None:
        return None, why
    with _registry_lock:
        inst = _live.get(cls.name)
        if inst is None:
            inst = cls()
            _live[cls.name] = inst
    ok, msg = inst.ensure_ready(conf)
    if not ok:
        return None, msg
    return inst, ""


def shutdown() -> None:
    """Close every live transport. Safe to call at any time."""
    with _registry_lock:
        items = list(_live.items())
        _live.clear()
    for _name, inst in items:
        try:
            inst.close()
        except Exception:
            pass


def transports_for_diagnosis(conf: dict) -> list[type[Transport]]:
    """Which transports the CHECK button should report on: the one that would
    actually be used, plus anything else available, so a user who picked the
    wrong one can see that the other works."""
    out: list[type[Transport]] = []
    for getter in (_desktop_class, _web_class):
        try:
            cls = getter()
        except Exception:
            continue
        if cls is not None and cls not in out:
            out.append(cls)
    return out


# ═════════════════════════════════════════════════════════════════════════════
#  WINDOWS DESKTOP — UI Automation
#
#  Moved here from whatsapp_call.py essentially unchanged. The approach and
#  every measured constant are the original ones: find the WebView host, read
#  the header by geometry, press the control through its Invoke pattern, and
#  refuse to dial when the conversation on screen is not the one that was asked
#  for. Only the packaging is new.
# ═════════════════════════════════════════════════════════════════════════════
import concurrent.futures    # noqa: E402  (kept beside the code that uses it)
import ctypes                # noqa: E402
import os                    # noqa: E402
import queue                 # noqa: E402
import subprocess            # noqa: E402
import time                  # noqa: E402

from core.lazyimport import LazyModule, installed   # noqa: E402

# Imported on first USE, not at launch — see core/lazyimport.py. This driver is
# loaded by plugin discovery at every start, and pyautogui alone is 394 ms of
# it; nobody who never opens WhatsApp should pay that. The twenty-eight call
# sites below are unchanged.
HAVE_PYAUTOGUI = installed("pyautogui")
HAVE_PYPERCLIP = installed("pyperclip")


def _configure_pyautogui(module) -> None:
    module.FAILSAFE = True     # mouse to the top-left corner = hard abort
    module.PAUSE = 0.0         # we do our own settling


pyautogui = LazyModule("pyautogui", _configure_pyautogui)
pyperclip = LazyModule("pyperclip")

if IS_WINDOWS:
    from ctypes import wintypes
    _user32 = ctypes.windll.user32
else:
    wintypes = None
    _user32 = None

# The main window is titled exactly "WhatsApp" (sometimes with an unread count).
# A call runs in its own window, which may be titled after the person instead,
# so hanging up searches on the looser pattern.
_WA_TITLE = re.compile(r"^\s*(\(\d+\)\s*)?whatsapp\b", re.I)
_WA_ANY = re.compile(r"whatsapp", re.I)

_CT_PROP = 30003          # UIA_ControlTypePropertyId
_CT_BUTTON = 50000        # UIA_ButtonControlTypeId
_CT_EDIT = 50004          # UIA_EditControlTypeId
_CT_TEXT = 50020          # UIA_TextControlTypeId
_TREE_SUBTREE = 7         # TreeScope_Subtree
_SCAN_BUDGET = 8.0


def _visible_windows() -> list[dict]:
    """Every visible titled top-level window, with the process that owns it."""
    out: list[dict] = []
    if not IS_WINDOWS:
        return out
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _lparam):
        try:
            if not _user32.IsWindowVisible(hwnd):
                return True
            n = _user32.GetWindowTextLengthW(hwnd)
            if not n:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            _user32.GetWindowTextW(hwnd, buf, n + 1)
            cls = ctypes.create_unicode_buffer(256)
            _user32.GetClassNameW(hwnd, cls, 256)
            rect = wintypes.RECT()
            _user32.GetWindowRect(hwnd, ctypes.byref(rect))
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            out.append({"hwnd": int(hwnd), "title": buf.value, "class": cls.value,
                        "pid": int(pid.value),
                        "rect": (rect.left, rect.top, rect.right, rect.bottom)})
        except Exception:
            pass
        return True

    try:
        _user32.EnumWindows(proc(cb), 0)
    except Exception:
        pass
    return out


def _enum_windows(pattern: re.Pattern = _WA_TITLE) -> list[dict]:
    return [w for w in _visible_windows() if pattern.search(w["title"] or "")]


_pid_cache: dict = {"pids": set(), "at": 0.0}
_PID_TTL = 10.0


def _whatsapp_pids() -> set[int]:
    """Every process that IS WhatsApp — the app AND everything it started.

    THE NAME IS NOT ENOUGH, and believing it was is what broke typing whenever
    WhatsApp was already open.

    WhatsApp desktop is a WinUI shell hosting a WebView, and the WebView is a
    separate process running a separate executable: measured on this machine,
    `WhatsApp.Root.exe` (pid 6012) with `msedgewebview2.exe` (pid 16916) as its
    child. The search box and the message box both live in the WebView, so the
    moment the caret lands in either of them, the window holding the keyboard
    belongs to a process with no "whatsapp" anywhere in its name.

    Everything that asks "does WhatsApp have the keyboard" then answered no
    about a WhatsApp that plainly did, and the answer to that question is what
    decides whether a keystroke is allowed to be sent. So the reply was never
    typed — and only in the state where the WebView had focus, which is any
    state a user has actually clicked around in. Launch WhatsApp fresh and the
    shell holds focus and it works; find it already open and it does not.

    The relationship is in the process table, so it is read from there rather
    than guessed at from names: whatever WhatsApp itself started is WhatsApp,
    however Microsoft chose to name the executable this month.
    """
    now = time.monotonic()
    if _pid_cache["pids"] and now - _pid_cache["at"] < _PID_TTL:
        return set(_pid_cache["pids"])
    pids: set[int] = set()
    try:
        import psutil
        children: dict[int, list[int]] = {}
        roots: set[int] = set()
        for proc in psutil.process_iter(["name", "ppid"]):
            info = proc.info
            children.setdefault(info.get("ppid") or 0, []).append(proc.pid)
            if "whatsapp" in (info.get("name") or "").lower():
                roots.add(proc.pid)
        # Every descendant, not just the immediate children: the WebView starts
        # renderer processes of its own and any of them can own the window that
        # ends up with the keyboard.
        stack = list(roots)
        while stack:
            pid = stack.pop()
            if pid in pids:
                continue
            pids.add(pid)
            stack.extend(children.get(pid, ()))
    except Exception as e:
        print(f"[WhatsApp] could not list processes: {e}")
    if pids:
        _pid_cache.update({"pids": set(pids), "at": now})
    return pids


def _whatsapp_family(known_pids: set | None = None) -> list[dict]:
    """Every window WhatsApp has on screen.

    THE PROCESS IS THE ANCHOR, NOT THE TITLE — and that is the whole fix.

    This used to find WhatsApp by looking for a visible window whose TITLE says
    "WhatsApp", then take every other window sharing that process. It worked
    right up until the moment it mattered: when a call comes in, WhatsApp hides
    the conversation window and shows the call panel, and the call panel's Win32
    title is not "WhatsApp" — the word you can see on it is painted content, not
    the window title. So no visible window matched, the process was never
    identified, and the function returned an empty list. The guard was not
    failing to recognise the call; it was never being shown it.

    Now the process is found three ways, cheapest first: the titles (free, and
    right whenever the main window is up), the handles this transport attached
    to earlier (free, and survives the main window being hidden), and failing
    both, the process table by executable name.
    """
    # PROCESS ONLY. A window title is never evidence of anything.
    #
    # This used to start from "every visible window whose title contains
    # whatsapp", and that is a trap with teeth: a VS Code window showing a file
    # called whatsapp_controls.json matched, joined the family, and was scanned
    # sixty controls deep on every poll — and its process was one step away from
    # being treated as WhatsApp for the purposes of "does WhatsApp have the
    # keyboard", which is the question that decides where a message gets typed.
    #
    # It also explains an intermittency that looked like magic: whether that
    # file happened to be open in an editor changed what the guard believed
    # WhatsApp was, from one call to the next.
    #
    # The process table knows which programs are WhatsApp. Nothing else needs a
    # say, and a title cannot lie its way in.
    pids = set(known_pids or ()) | _whatsapp_pids()
    if not pids:
        return []
    return [w for w in _visible_windows() if w["pid"] in pids]


# SM_XVIRTUALSCREEN / SM_YVIRTUALSCREEN / SM_CXVIRTUALSCREEN / SM_CYVIRTUALSCREEN
_SM_VIRTUAL = (76, 77, 78, 79)


def _virtual_screen() -> tuple | None:
    """The rectangle every monitor together covers, asked of the system.

    Nothing here assumes a resolution, a monitor count or an origin: a second
    display above or to the left of the first one has negative coordinates and
    is inside this rectangle, and a laptop that is docked and undocked reports
    a different one each time without anybody having to be told.
    """
    if not IS_WINDOWS:
        return None
    try:
        x, y = (_user32.GetSystemMetrics(_SM_VIRTUAL[0]),
                _user32.GetSystemMetrics(_SM_VIRTUAL[1]))
        w, h = (_user32.GetSystemMetrics(_SM_VIRTUAL[2]),
                _user32.GetSystemMetrics(_SM_VIRTUAL[3]))
        if w <= 0 or h <= 0:
            return None
        return (x, y, x + w, y + h)
    except Exception:
        return None


def _on_screen(rect: tuple | None) -> bool:
    """Is this rectangle somewhere a person could actually see it?

    THE PROVIDER CANNOT BE ASKED THIS. The obvious test is the element's own
    IsOffscreen property, and on WhatsApp it is wrong: with the window
    minimised, its search box and composer both reported IsOffscreen = False
    while sitting at x = -31834. Measured, on the real window.

    A minimised window is parked far outside the desktop — that is how Windows
    has always hidden one — so where a rectangle IS answers the question that
    what it CLAIMS does not. And it answers it without a single constant: the
    desktop's own bounds are read from the system, so this is the same rule on
    one 1366x768 laptop screen and on three 4K monitors in a row.
    """
    if not rect:
        return False
    screen = _virtual_screen()
    if not screen:
        return True          # cannot tell — do not invent a reason to refuse
    return _overlap(rect, screen) > 0.0


def _overlap(a: tuple, b: tuple) -> float:
    """Fraction of the smaller rectangle covered by the intersection."""
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    if not (ix and iy):
        return 0.0
    areas = [max(1, (r[2] - r[0]) * (r[3] - r[1])) for r in (a, b)]
    return (ix * iy) / min(areas)


def _pick_windows() -> tuple[int, int] | tuple[None, None]:
    """(focus_hwnd, scan_hwnd). The WebView host is preferred for scanning, but
    only when it really belongs to the WhatsApp shell sitting under it — a
    browser tab that happens to be titled "WhatsApp" must not be mistaken for
    the app."""
    # Windows belonging to the WhatsApp PROCESS, with the title match kept only
    # as a fallback for a machine where the process list cannot be read.
    #
    # The title alone is not enough on its own account either: "WhatsApp -
    # Google Chrome" matches it, and a browser tab is a different tree and a far
    # slower one. Asking which program owns the window settles both that and the
    # editor-window problem in one rule.
    pids = _whatsapp_pids()
    found = [w for w in _visible_windows() if w["pid"] in pids] if pids else []
    if not found:
        found = _enum_windows()
    if not found:
        return None, None
    shells = [w for w in found if "chrome_widgetwin" not in w["class"].lower()]
    views = [w for w in found if "chrome_widgetwin" in w["class"].lower()]

    if shells:
        # The BIGGEST one, not the first one.
        #
        # EnumWindows walks the z-order from the top down, and an incoming call
        # opens its own window — titled "WhatsApp", like the app — right at the
        # top of it. Taking shells[0] therefore returned the call popup as
        # though it were the main window the moment somebody rang, and the guard
        # then skipped that window as "the main one" and never saw the call it
        # was watching for. Size settles it without a constant: the conversation
        # window is the whole app, a call popup is a panel.
        shell = max(shells, key=lambda w: (w["rect"][2] - w["rect"][0])
                    * (w["rect"][3] - w["rect"][1]))
        for v in views:
            if _overlap(shell["rect"], v["rect"]) > 0.8:
                return shell["hwnd"], v["hwnd"]
        return shell["hwnd"], shell["hwnd"]
    # No shell: WhatsApp Web in a browser. Same controls, slower scan.
    return views[0]["hwnd"], views[0]["hwnd"]


def _is_foreground(hwnd: int) -> bool:
    """True when this window — or a window of the same process, which is how the
    shell and its WebView host relate — already has the keyboard."""
    if not (IS_WINDOWS and hwnd):
        return False
    try:
        fg = _user32.GetForegroundWindow()
        if fg == hwnd:
            return True
        a, b = wintypes.DWORD(), wintypes.DWORD()
        _user32.GetWindowThreadProcessId(fg, ctypes.byref(a))
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(b))
        return bool(a.value) and a.value == b.value
    except Exception:
        return False


_ASFW_ANY = -1
_HWND_TOPMOST = -1
_HWND_NOTOPMOST = -2
_SWP_NOMOVE_NOSIZE_NOACTIVATE = 0x0002 | 0x0001 | 0x0010
_SW_RESTORE = 9
_SW_MINIMIZE = 6


def _yield_foreground() -> None:
    """Give away the foreground rights this process is holding.

    THIS IS THE ONE THING ONLY THE ASSISTANT CAN DO, AND IT IS WHY THE
    FULLSCREEN CASE FAILED.

    Windows refuses SetForegroundWindow to a process that does not already own
    the foreground — that is the rule that stops background programs stealing
    focus mid-sentence, and it is a good rule. But when the assistant is on
    screen, the process that owns the foreground IS this one, and Windows
    provides the documented way to hand that right to somebody else. So it is
    not a trick to get around the rule: it is the rule being used the way it
    was designed, by the only process entitled to.

    Nothing happens when the assistant is not in front, which is exactly right
    — there is then nothing of ours to give away.
    """
    try:
        _user32.AllowSetForegroundWindow(_ASFW_ANY)
    except Exception:
        pass


def _raise_above_fullscreen(hwnd: int) -> None:
    """Put a window above one that is covering the whole screen.

    Winning the foreground and being VISIBLE are two different things. A
    maximised or fullscreen window can still be painted over the top, and then
    WhatsApp is in front by every measure the API reports while the user is
    looking at the assistant. Promoting it to topmost and immediately back
    lifts it above whatever is covering it, in the z-order and on the screen,
    and leaves it unpinned afterwards so it does not sit over everything else
    for the rest of the session.
    """
    for flag in (_HWND_TOPMOST, _HWND_NOTOPMOST):
        try:
            _user32.SetWindowPos(hwnd, flag, 0, 0, 0, 0,
                                 _SWP_NOMOVE_NOSIZE_NOACTIVATE)
        except Exception:
            return


def _focus(hwnd: int) -> bool:
    """Bring a window to the front, and keep trying harder until it is there.

    SetForegroundWindow is a REQUEST, and Windows refuses it silently. The old
    version asked once, slept a third of a second and returned True whether or
    not anything had happened — so "WhatsApp is in front" was an assumption,
    and with the assistant running fullscreen it was a wrong one. Everything
    downstream then waited for a foreground that was never coming.

    Now every step is followed by looking, and the next step is only paid for
    when the last one did not work:

      1. restore it if it is minimised, and give away our own foreground
         rights — which is what makes the ordinary request succeed;
      2. borrow the input queue of the window that has the keyboard, then ask;
      3. lift it above whatever is covering the screen;
      4. SwitchToThisWindow — what Alt+Tab uses, and it is granted in cases
         where the plain request is not;
      5. minimise and restore. A window coming back from minimised is treated
         as a user-initiated activation, which is the last thing that works
         when a fullscreen window will not let go.

    The return value now means what it says, so a caller that gets False can
    tell the user the truth instead of typing into whatever is actually in
    front.
    """
    if not IS_WINDOWS or not hwnd:
        return False

    def there() -> bool:
        # "Is WhatsApp in front", not "is this handle in front".
        #
        # WhatsApp desktop is two processes — a WinUI shell and a WebView host
        # — and the keyboard usually ends up in the WebView while the handle
        # being raised is the shell's. Comparing against one handle answers no
        # about a WhatsApp that is perfectly well in front, and the escalation
        # below would then keep climbing, all the way to minimising and
        # restoring a window that never needed touching.
        if _user32.IsIconic(hwnd):
            return False
        return _is_foreground(hwnd) or _whatsapp_has_keyboard()

    if there():
        return True

    def attach_and_ask():
        me = ctypes.windll.kernel32.GetCurrentThreadId()
        fg = _user32.GetForegroundWindow()
        threads = {_user32.GetWindowThreadProcessId(fg, None),
                   _user32.GetWindowThreadProcessId(hwnd, None)} - {0, me}
        for t in threads:
            _user32.AttachThreadInput(me, t, True)
        try:
            _user32.SetForegroundWindow(hwnd)
            _user32.BringWindowToTop(hwnd)
        finally:
            for t in threads:
                _user32.AttachThreadInput(me, t, False)

    def restore():
        if _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, _SW_RESTORE)

    # There WAS a last resort here that minimised the window and restored it,
    # because a window coming back from minimised is granted the foreground
    # where a plain request is refused. It is removed, and the reason is worth
    # keeping: a minimised WhatsApp is a WhatsApp with no usable boxes, so on
    # the runs where it did not immediately win the foreground it left the
    # window in the one state the rest of this file correctly refuses to work
    # with — and then tried again, and minimised it again. A trick that can
    # make things worse than not trying is not a last resort.
    steps = (
        lambda: (restore(), _yield_foreground()),
        attach_and_ask,
        lambda: _raise_above_fullscreen(hwnd),
        lambda: _user32.SwitchToThisWindow(hwnd, True),
    )
    for step in steps:
        try:
            step()
        except Exception:
            continue
        # Give the window manager a moment to act on it, then look. The wait is
        # short because it is paid once per step, and only while failing.
        if _wait_until(there, 0.45, 0.05):
            return True
    return there()


def _foreground_pid() -> int:
    """Which process owns the window the keyboard is pointing at."""
    if not IS_WINDOWS:
        return 0
    try:
        fg = _user32.GetForegroundWindow()
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


def _whatsapp_has_keyboard(pids: set | None = None) -> bool:
    """Is WhatsApp — ANY of its windows — in front?

    Asking about one window is not the same question, and getting them confused
    is what stopped the message being typed. WhatsApp desktop runs as two
    processes: a WinUI shell and a WebView host, with different PIDs. The check
    before this one compared the foreground process against the process of the
    single handle it had attached to, so the moment the app restored and its
    WebView came to the front, "is WhatsApp in front?" answered no — of the
    wrong window, correctly, and uselessly. The name had already been typed into
    the search box by then; only the Enter that would have opened the chat was
    withheld.

    The right question is whether the keyboard is anywhere in WhatsApp at all.
    """
    fg = _foreground_pid()
    if not fg:
        return False
    if fg in set(pids or ()):
        return True
    # Not in the set we remembered — which proves nothing, because that set is
    # built from the windows that happened to be visible when this transport
    # attached. WhatsApp runs as two processes and shows one of them at a time:
    # attaching while it sat minimised as a 199x34 stub remembered the shell
    # and never saw the WebView, so the moment the WebView came to the front
    # the answer was "that is not WhatsApp" about WhatsApp. The process table
    # is the authority, and it is cached, so asking it costs nothing.
    return fg in _whatsapp_pids()


def _await_foreground(hwnd: int, limit: float = 6.0, pids: set | None = None) -> bool:
    """Is WhatsApp holding the keyboard? Ask for it if not, and keep looking.

    SetForegroundWindow is a request, not a command — Windows refuses it for a
    process that does not already own the foreground, and it refuses silently.
    The old code asked once, slept a fixed 0.35s and carried on typing, which is
    how a contact's name ended up in another application's text box.

    The asking, and the escalation behind it, now lives in _focus, which
    verifies its own work. This is the deadline around it: while the window is
    still restoring, or the app is still starting, the refusal is transient and
    worth another go. When the deadline passes the answer is the truth, and the
    caller says so to the user rather than typing into whatever is in front.
    """
    if not (IS_WINDOWS and hwnd):
        return False
    deadline = time.monotonic() + limit
    while True:
        if _whatsapp_has_keyboard(pids) and not _user32.IsIconic(hwnd):
            return True
        if time.monotonic() >= deadline:
            return _whatsapp_has_keyboard(pids) and not _user32.IsIconic(hwnd)
        _focus(hwnd)


def _await_window(seconds: float) -> tuple[int, int] | tuple[None, None]:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _pick_windows()[0]:
            time.sleep(1.5)                       # let the interface paint
            return _pick_windows()
        time.sleep(0.4)
    return None, None


def _launch(app_name: str, wait: float) -> tuple[int, int] | tuple[None, None]:
    """Two ways in: the whatsapp: protocol handler the app registers, and — if
    that is not registered — the Start menu, the way send_message opens apps."""
    try:
        subprocess.Popen(["cmd", "/c", "start", "", "whatsapp:"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=0x08000000)
        found = _await_window(wait)
        if found[0]:
            return found
    except Exception:
        pass

    if not HAVE_PYAUTOGUI:
        return None, None
    try:
        pyautogui.press("win")
        time.sleep(0.6)
        _type(app_name)
        time.sleep(0.9)
        pyautogui.press("enter")
    except Exception:
        return None, None
    return _await_window(wait)


# One thread, for the life of the process, and every UI Automation call goes
# through it.
#
# This is not tidiness. UIA hands back COM objects that belong to the apartment
# of the thread that fetched them, and pywinauto keeps its IUIAutomation as a
# process-wide singleton. Giving each call its own short-lived thread — then
# abandoning that thread without closing COM — means objects created in one
# apartment are used and released from another. That is undefined behaviour, and
# undefined behaviour here is a segmentation fault: not an exception the plugin
# loader can isolate, but the whole assistant disappearing. One long-lived
# apartment makes the question moot.
_uia_queue: "queue.Queue | None" = None
_uia_thread: threading.Thread | None = None
_uia_lock = threading.Lock()


def _com_ready() -> None:
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass


def _uia_loop(jobs: "queue.Queue") -> None:
    _com_ready()
    while True:
        job = jobs.get()
        if job is None:
            break
        fn, fut = job
        if not fut.set_running_or_notify_cancel():
            continue
        try:
            fut.set_result(fn())
        except BaseException as exc:       # noqa: BLE001 - carried to the caller
            fut.set_exception(exc)


def _submit(fn) -> concurrent.futures.Future:
    global _uia_queue, _uia_thread
    with _uia_lock:
        if _uia_thread is None or not _uia_thread.is_alive():
            _uia_queue = queue.Queue()
            _uia_thread = threading.Thread(target=_uia_loop, args=(_uia_queue,),
                                           daemon=True, name="whatsapp-uia")
            _uia_thread.start()
        fut: concurrent.futures.Future = concurrent.futures.Future()
        _uia_queue.put((fn, fut))
        return fut


def _budget(fn, seconds: float, default=None):
    """Run `fn` on the UI Automation thread, giving up after `seconds`."""
    try:
        return _submit(fn).result(timeout=seconds)
    except concurrent.futures.TimeoutError:
        print("[WhatsApp] UI Automation exceeded its time budget.")
        return default
    except Exception as e:
        print(f"[WhatsApp] UI Automation failed: {e}")
        return default


# ──────────────────────────────────────────────────────────────────────────────
# What a look at this window actually costs
#
# MEASURED, on WhatsApp's own window on this machine — a WinUI shell with about
# twelve thousand elements in its automation tree:
#
#     FindAll(subtree, Button)        3.40 s      3682 matches
#     FindAll(subtree, Text)          2.61 s      1229 matches
#     FindAll(subtree, Edit)          2.47 s       189 matches
#     FindFirst(subtree, Edit)        0.015 s     stops at the first hit
#     re-reading an element in hand   0.00025 s
#
# Two things fall out of those numbers, and between them they decide everything
# in this section.
#
# THE COST IS THE WALK, NOT THE ANSWER. Asking for Edits is no cheaper than
# asking for Buttons although there are twenty times fewer of them, and
# FindFirst is a hundred and sixty times faster than FindAll for no reason
# except that it stops early. UI Automation charges for elements VISITED, and
# TreeScope_Subtree visits all of them. Fetching the properties is small change
# next to that — and free outright with a cache request: 0.93 s of per-element
# property calls on the button scan became 0.02 s in a single round trip.
#
# AN ELEMENT SURVIVES THE WALK THAT FOUND IT, and re-reading one costs a
# quarter of a millisecond. So the expensive part need not be repeated.
#
# That is what was wrong with sending a message. Nothing in the sequence was
# slow; the sequence simply walked this tree about ten times — once to find the
# window, once to read the header, once to find the row, once to press it, once
# to check the right conversation opened, then five or six more while polling
# for the caret and for the text to appear in the box. Ten walks is fifteen to
# twenty seconds, and that is exactly what it took.
#
# Now the boxes are found once and HELD, and every question after that is put
# to the element itself. A walk happens when the elements are gone, and even
# that is answered by asking them rather than by walking.
# ──────────────────────────────────────────────────────────────────────────────
_P_RECT = 30001           # UIA_BoundingRectanglePropertyId
_P_NAME = 30005           # UIA_NamePropertyId
_P_FOCUS = 30008          # UIA_HasKeyboardFocusPropertyId
_P_OFFSCREEN = 30022      # UIA_IsOffscreenPropertyId

_no_cache_warned = {"done": False}


def _uia_root(hwnd: int):
    """(iuia, window element), or (None, None). Must run on the UIA thread.

    The handle is checked deliberately hard. Asked about a window that has gone
    away, UIAElementInfo quietly hands back the DESKTOP instead — and then every
    window on the machine is inside the search. A header read would walk
    thousands of elements belonging to other applications, pick some wide button
    as the "conversation title", and a call could end up pressing a control in
    an unrelated program. Refusing an invalid handle is the only thing standing
    between a missing window and a click somewhere it should never happen.
    """
    if not hwnd or not _user32.IsWindow(hwnd):
        return None, None
    from pywinauto.uia_defines import IUIA
    from pywinauto.uia_element_info import UIAElementInfo
    iuia = IUIA().iuia
    root = UIAElementInfo(hwnd).element
    if int(getattr(root, "CurrentNativeWindowHandle", 0) or 0) != int(hwnd):
        return None, None
    return iuia, root


def _find_all(iuia, root, *control_types):
    """[(element, control_type, name, rect, has_focus)] for every on-screen
    control of those types — ONE walk, and the properties in ONE round trip.

    Several types at once on purpose: the walk is the cost, so asking for
    buttons and text separately pays it twice for the same journey.

    The cache request is what makes the properties free, and it is wrapped
    because not every provider will build one. When that happens it says so
    once and reads them the slow way, which is what this did before.
    """
    conds = [iuia.CreatePropertyCondition(_CT_PROP, ct) for ct in control_types]
    cond = conds[0]
    for extra in conds[1:]:
        cond = iuia.CreateOrCondition(cond, extra)

    cached = True
    try:
        req = iuia.CreateCacheRequest()
        for pid in (_P_RECT, _P_NAME, _P_FOCUS, _P_OFFSCREEN, _CT_PROP):
            req.AddProperty(pid)
        found = root.FindAllBuildCache(_TREE_SUBTREE, cond, req)
    except Exception as e:
        if not _no_cache_warned["done"]:
            _no_cache_warned["done"] = True
            print(f"[WhatsApp] this window will not cache its properties ({e}) "
                  f"— reading them one at a time instead.")
        found, cached = root.FindAll(_TREE_SUBTREE, cond), False

    out = []
    for i in range(found.Length):
        try:
            el = found.GetElement(i)
            if cached:
                if el.CachedIsOffscreen:
                    continue
                rect = el.CachedBoundingRectangle
                name = el.CachedName or ""
                ctype = el.CachedControlType
                focus = bool(el.CachedHasKeyboardFocus)
            else:
                if el.CurrentIsOffscreen:
                    continue
                rect = el.CurrentBoundingRectangle
                name = el.CurrentName or ""
                ctype = el.CurrentControlType
                focus = bool(el.CurrentHasKeyboardFocus)
            if rect.right <= rect.left or rect.bottom <= rect.top:
                continue
            out.append((el, int(ctype), name,
                        (rect.left, rect.top, rect.right, rect.bottom), focus))
        except Exception:
            continue
    return out


def _each_button(hwnd: int):
    """Yields (element, name, rect) for every on-screen button in the window,
    the window's own rectangle first."""
    if not hwnd or not _user32.IsWindow(hwnd):
        raise RuntimeError("the WhatsApp window is gone")
    iuia, root = _uia_root(hwnd)
    if root is None:
        raise RuntimeError("UI Automation resolved something other than that window")
    rr = root.CurrentBoundingRectangle
    if rr.right <= rr.left or rr.bottom <= rr.top:
        raise RuntimeError("the WhatsApp window has no usable rectangle")
    yield (rr.left, rr.top, rr.right, rr.bottom)                # first item: the window rect
    for el, _ct, name, rect, _focus in _find_all(iuia, root, _CT_BUTTON):
        yield el, name, rect


def _scan_call_window(hwnd: int):
    """(win_rect, buttons, texts) for one window, in a SINGLE UI Automation job.

    One job, not three, and that is a bug fix rather than tidiness.

    The ring path used to submit a button scan, then a text scan for the
    caller's name, then a second text scan to record what was on screen — three
    jobs, every 0.35 seconds, onto the one UI Automation apartment this process
    is allowed to have. They queued behind each other until they started timing
    out, and a timed-out text scan returns nothing, and nothing meant the caller
    fell back to the window title, which reads "Voice call". So the message went
    looking for a contact by that name and there is no such person.

    Reading both control types in one pass is what keeps the queue empty — and
    it is now literally one pass. Two FindAll calls are two complete walks of
    the same tree for the same journey, which measured 2.4 s + 2.6 s on the app
    window; one OR condition asks for both and pays the walk once.
    """
    def work():
        if not hwnd or not _user32.IsWindow(hwnd):
            raise RuntimeError("the WhatsApp window is gone")
        iuia, root = _uia_root(hwnd)
        if root is None:
            raise RuntimeError("UI Automation resolved something other than that window")
        rr = root.CurrentBoundingRectangle
        if rr.right <= rr.left or rr.bottom <= rr.top:
            raise RuntimeError("the window has no usable rectangle")
        win_rect = (rr.left, rr.top, rr.right, rr.bottom)

        buttons, texts = [], []
        for _el, ctype, name, rect, _focus in _find_all(
                iuia, root, _CT_BUTTON, _CT_TEXT):
            (buttons if ctype == _CT_BUTTON else texts).append((name.strip(), rect))
        texts = sorted(((r[1], n) for n, r in texts if n))
        return win_rect, buttons, texts

    return _budget(work, 6.0)


def _caller_name(texts: list, win_rect: tuple, buttons: list, app_title: str) -> str:
    """Who is ringing, read off the call window itself.

    Two wrong answers came before this one, and both were the same mistake in
    different clothes: reading a string that is on the window without checking
    what part of the window it is on.

    First the window TITLE, which said "Voice call" — the kind of call, not who
    is making it. Then the topmost TEXT element, which said "WhatsApp", because
    the title bar is text too and it is above everything else.

    The panel is laid out caller, then call type, then the action buttons, all
    under a title bar. So the caller is the first text that is BELOW the title
    bar and ABOVE the buttons — anchored to the buttons, which the scan already
    found, rather than to a pixel constant. And the app naming itself is never a
    person, whatever height it sits at.

    Every part of that is position or identity, not vocabulary, so it holds in
    any language. When it does not hold the name comes back empty and the guard
    says so, instead of inventing somebody to send a message to.
    """
    if not texts:
        return ""

    app = fold(app_title or "")

    # Everything a control is CALLED is not the caller.
    #
    # A real ringing window published these:
    #     buttons  Close, Mute microphone, Device settings, Accept call, Decline call
    #     texts    WhatsApp, A Annem, Voice call, Accept
    # The name is plainly "A Annem", and the only things standing in front of it
    # are the app naming itself and a caption that merely repeats a button.
    #
    # The version before this one anchored on button POSITION — "the caller sits
    # above the controls" — and that is exactly what returned nothing here: the
    # topmost button is `Close`, the title-bar X, so there was no room left
    # "above the controls" and every text was discarded, including the name.
    # Which control a caption belongs to is knowable; where a window chose to
    # put its close button is not worth guessing about.
    button_names = {fold(n) for n, _r in buttons if n}
    for _top, name in texts:
        folded = fold(name)
        if not folded:
            continue
        if folded in button_names:      # a caption repeating a control
            continue
        if (app and folded == app) or _WA_ANY.search(name):
            continue                    # the app naming itself, not a person
        return name
    return ""


def _scan(hwnd: int):
    """-> (window_rect, [(name, rect), ...]) — pure data, thread-safe."""
    it = _each_button(hwnd)
    win_rect = next(it)
    return win_rect, [(name, rect) for _el, name, rect in it]


def _header(win_rect, buttons) -> dict | None:
    """The chat header, read from geometry alone.

    The band is the strip just under the title bar. Inside it the conversation
    title is unmistakable — it is hundreds of pixels wide where every control is
    forty — and everything to its right is the action cluster, in WhatsApp's
    fixed order: video, voice, search, menu.
    """
    _wl, wt, _wr, _wb = win_rect
    band: list[tuple[str, tuple]] = []
    for name, r in buttons:
        cy = (r[1] + r[3]) // 2
        if not (wt + 25 <= cy <= wt + 115):
            continue
        if (r[3] - r[1]) > 200:            # the full-height panel resize handle
            continue
        band.append((name, r))
    if not band:
        return None

    title_name, title_rect = max(band, key=lambda nr: nr[1][2] - nr[1][0])
    if (title_rect[2] - title_rect[0]) < 120:
        return None                        # no conversation open yet

    cluster = sorted([nr for nr in band if nr[1][0] >= title_rect[2]],
                     key=lambda nr: nr[1][0])
    return {"title": title_name, "cluster": cluster}


def _read_header(scan_hwnd: int) -> dict | None:
    def work():
        return _header(*_scan(scan_hwnd))
    return _budget(work, _SCAN_BUDGET)


def _ringing_shape(buttons: list) -> bool:
    """Does this collection of controls look like a call that is ringing, with
    no reference to what any of them is called?

    A ringing call is a small cluster: WhatsApp offers answer and decline, and
    on some builds a third control to reply with a message. The conversation
    view, by contrast, publishes dozens — a chat list, a header, a composer, an
    emoji strip. So a container carrying between two and six named controls, and
    none of the wide title element a conversation always has, is a call.

    Counting is not proof, which is why nothing is pressed on the strength of
    it. It is enough to say "something is ringing", and being able to say that
    in a language nobody wrote a word list for is the entire point.
    """
    named = [(n, r) for n, r in buttons if n]
    return 2 <= len(named) <= 6
    # The count IS the rule, and an earlier version had a second one that had to
    # be removed: "no control wider than 200px, because a conversation header
    # always has a wide title". On a real ringing call the green Accept button
    # measured about 225px — the rule threw away exactly the window it was
    # written to find. A call panel is a handful of buttons and a conversation
    # window is dozens of them, and that difference needs no pixels.


def _in_call(roles: dict) -> bool:
    """Is this a call already in progress rather than one that is ringing?

    A call in progress and a call that is ringing look the same to the geometry
    rule above: both are a handful of action buttons with no wide title. What
    separates them is what the buttons DO — a live call offers a way to hang up
    and no way to answer, because there is nothing left to answer.

    This matters because the panel of a live call is mostly mute and camera
    toggles, and those read plausibly enough as "voice" and "video" that a
    ringing-call reading of them is not obviously wrong. Checking the roles
    rather than the shape settles it without another rule about pixels.
    """
    return roles.get("end") is not None and roles.get("accept") is None


def _find_role(controls: list, role: str, cached_only: bool = False):
    """The control that does `role`, as ((name, rect), index), or (None, None).

    `controls` is [(name, rect), ...] in visual order — the same shape every
    transport produces. The mapping comes from the model, cached per label set,
    so this costs one dictionary lookup after the first time a WhatsApp language
    is seen.
    """
    labels = [n for n, _r in controls]
    idx = resolve_controls(labels, cached_only=cached_only).get(role)
    if idx is None or not (0 <= idx < len(controls)):
        return None, None
    return controls[idx], idx


def _pick_call_button(cluster: list, call_type: str):
    """-> ((name, rect), how) where how is 'name' | 'position', or (None, why)."""
    found, _idx = _find_role(cluster, call_type)
    if found:
        return found, "name"
    # The model could not be reached, or did not recognise the control. Fall
    # back to the layout: WhatsApp puts video, then voice, then search, then
    # menu, and pressing the wrong one of the first two still places a call to
    # the person the user asked for. Only when the cluster is the full four
    # controls, so a chat that offers no calling (a channel, an archived view)
    # is refused instead of guessed at.
    if len(cluster) >= 4:
        return cluster[0 if call_type == "video" else 1], "position"
    return None, "position"


def _invoke(scan_hwnd: int, want_name: str, want_rect: tuple) -> bool:
    """Press the control itself.

    The element has to be found again here rather than carried in from the scan,
    because a UIA element belongs to the thread that fetched it. Matching is
    exact first and by rectangle second — a header can repaint between reading
    it and pressing it, and the position is the more stable half.
    """
    def work():
        from pywinauto.uia_defines import get_elem_interface
        iuia, root = _uia_root(scan_hwnd)
        if root is None:
            return False

        def press(el, rect):
            try:
                get_elem_interface(el, "Invoke").Invoke()
                return True
            except Exception as e:
                print(f"[WhatsApp] Invoke unavailable ({e}) — clicking instead.")
                return _click(rect)

        # ASK FOR IT BY NAME BEFORE WALKING FOR IT.
        #
        # FindFirst stops at its first match: 0.015 s against 3.4 s for the
        # full walk, measured on this window. It is only a shortcut, never a
        # decision — the rectangle is checked against the one the scan reported
        # before anything is pressed, so a name that belongs to two controls
        # (a contact appears both in the chat list and in the header) either
        # lands on the right one or falls through to the walk below.
        if want_name:
            try:
                hit = root.FindFirst(_TREE_SUBTREE, iuia.CreateAndCondition(
                    iuia.CreatePropertyCondition(_CT_PROP, _CT_BUTTON),
                    iuia.CreatePropertyCondition(_P_NAME, want_name)))
                if hit is not None:
                    r = hit.CurrentBoundingRectangle
                    if (r.left, r.top, r.right, r.bottom) == want_rect:
                        return press(hit, want_rect)
            except Exception:
                pass

        found = [(el, name, rect) for el, _ct, name, rect, _f
                 in _find_all(iuia, root, _CT_BUTTON)]
        for match in (lambda n, r: r == want_rect and n == want_name,
                      lambda n, r: r == want_rect,
                      lambda n, r: bool(want_name) and n == want_name):
            for el, name, rect in found:
                if match(name, rect):
                    return press(el, rect)
        return False
    return bool(_budget(work, _SCAN_BUDGET, default=False))


def _scale() -> float:
    """Physical (what UI Automation reports) -> logical (what pyautogui clicks).
    Measured, never assumed, so 125% and 150% displays land on the right pixel."""
    try:
        dc = _user32.GetDC(0)
        try:
            physical = ctypes.windll.gdi32.GetDeviceCaps(dc, 118)   # DESKTOPHORZRES
        finally:
            _user32.ReleaseDC(0, dc)
        logical = pyautogui.size()[0] if HAVE_PYAUTOGUI else physical
        if physical:
            return float(logical) / float(physical)
    except Exception:
        pass
    return 1.0


def _click(rect: tuple) -> bool:
    """Click the middle of a rectangle — but only if that is a real place.

    A click is the one thing here aimed at a COORDINATE rather than at a
    control, so it is the one thing that can land somewhere nobody asked for:
    on another application, on a desktop icon, on whatever happens to be at
    that point. It gets a bounds check for the same reason a name is verified
    before a call is dialled.

    The bounds are the desktop's own, read from the system, so this neither
    knows nor cares how many monitors there are or what they are set to.
    """
    if not HAVE_PYAUTOGUI:
        return False
    try:
        s = _scale()
        x = int(round((rect[0] + rect[2]) / 2 * s))
        y = int(round((rect[1] + rect[3]) / 2 * s))
        screen = _virtual_screen()
        if screen and not (screen[0] <= x < screen[2] and screen[1] <= y < screen[3]):
            print(f"[WhatsApp] refusing to click ({x}, {y}) — that is not on "
                  f"any screen, so the window it belongs to is not visible.")
            return False
        pyautogui.click(x, y)
        return True
    except Exception as e:
        print(f"[WhatsApp] click failed: {e}")
        return False


# The shape Windows wants for a synthetic keystroke. Declared once, and only
# on the system that has the API.
if IS_WINDOWS:
    _ULONG_PTR = (ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8
                  else ctypes.c_ulong)

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", _ULONG_PTR)]

    class _INPUT_UNION(ctypes.Union):
        # Padded to the size of the largest member (MOUSEINPUT) so the struct
        # matches what SendInput expects on both 32- and 64-bit.
        _fields_ = [("ki", _KEYBDINPUT), ("_pad", ctypes.c_byte * 32)]

    class _INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]

_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
# How many key events to hand over at once. One call for a whole sentence works
# here, but a WebView is entitled to drop a flood, so it goes in mouthfuls.
_INPUT_CHUNK = 120


def _send_unicode(text: str) -> bool:
    """Type `text` as literal characters, whatever they are.

    KEYEVENTF_UNICODE tells Windows to deliver a CHARACTER rather than a key:
    no layout is consulted, so ş, ğ, я, 漢 and emoji all arrive on a keyboard
    that has no such keys. Characters outside the basic plane are sent as the
    surrogate pair they are made of, which is what the API asks for.
    """
    if not (IS_WINDOWS and text):
        return False
    events = []
    for ch in text:
        cp = ord(ch)
        units = ([cp] if cp < 0x10000 else
                 [0xD800 + ((cp - 0x10000) >> 10), 0xDC00 + ((cp - 0x10000) & 0x3FF)])
        for unit in units:
            for up in (0, _KEYEVENTF_KEYUP):
                item = _INPUT(type=_INPUT_KEYBOARD)
                item.ki = _KEYBDINPUT(0, unit, _KEYEVENTF_UNICODE | up, 0, 0)
                events.append(item)
    try:
        for start in range(0, len(events), _INPUT_CHUNK):
            batch = events[start:start + _INPUT_CHUNK]
            arr = (_INPUT * len(batch))(*batch)
            sent = _user32.SendInput(len(batch), arr, ctypes.sizeof(_INPUT))
            if sent != len(batch):
                return False
            if start + _INPUT_CHUNK < len(events):
                time.sleep(0.005)
        return True
    except Exception as e:
        print(f"[WhatsApp] could not synthesise keystrokes: {e}")
        return False


def _type(text: str) -> bool:
    """Put `text` into whatever holds the caret.

    THE CLIPBOARD IS SHARED, AND SOMETHING IS ALWAYS HOLDING IT.

    This used to copy the text and press Ctrl+V, which is the usual way to type
    ş, ü, я and 漢 through pyautogui — and it is a race with every other
    program on the machine, this assistant included. Its own window subscribes
    to clipboard changes and reads the clipboard on each one, Windows notifies
    every other listener too, and while they are all reading it a copy can be
    clobbered or land late.

    Measured from a real session: three auto-replies in four went nowhere, and
    both boxes read back empty afterwards — "did not go into the search box (it
    holds \'\')". The console named the culprit itself: "qt.qpa.mime: Retrying
    to obtain clipboard".

    So the characters are delivered as characters. Nothing is copied, nothing is
    shared, and there is nothing left in the clipboard afterwards for the user
    to find their own text replaced by. The clipboard route stays as a fallback
    for a system where the input API refuses.
    """
    if _send_unicode(text):
        # A moment for the application to process a burst of characters before
        # anything reads the box back.
        time.sleep(0.05)
        return True

    if HAVE_PYPERCLIP:
        try:
            pyperclip.copy(text)
            time.sleep(0.12)
            # Do not paste what the clipboard does not hold: under contention
            # the copy silently loses, and pasting then types somebody else\'s
            # text into a conversation.
            if pyperclip.paste() == text:
                pyautogui.hotkey("ctrl", "v")
                time.sleep(0.1)
                return True
            print("[WhatsApp] the clipboard did not keep what was copied.")
        except Exception as e:
            print(f"[WhatsApp] clipboard typing failed: {e}")

    if HAVE_PYAUTOGUI and text.isascii():
        pyautogui.write(text, interval=0.02)
        return True
    return False


def _search_focused(hwnd: int) -> bool:
    """Has the chat-search box taken the keyboard?

    Told from the composer by which half of the window it is in, not by a pair
    of pixel offsets measured on one machine. The offsets were the bug: on a
    2578x1408 window they pointed at a corner the search box does not occupy,
    so a box that HAD taken the keyboard was reported as not having taken it.
    """
    search, _composer_box, focused = _text_boxes(hwnd)
    if not (search and focused):
        return False
    # Overlap, not equality. WhatsApp grows its search field the moment it takes
    # focus — a back arrow appears beside it — so the rectangle read back is
    # never the rectangle that was aimed at, and an exact comparison reported
    # failure for a box that had focused perfectly well.
    return focused == search or _overlap(focused, search) > 0.15


# ──────────────────────────────────────────────────────────────────────────────
# The two boxes, found once and kept
#
# Every question this file asks about typing — where is the search box, does
# the composer have the caret, what is in it, whose conversation is it — used
# to begin by walking the whole window again. Four functions, each with its own
# copy of the same walk, several of them inside polling loops. On the measured
# tree that is 2.5 seconds a question.
#
# They are all questions about two specific elements, and an element outlives
# the walk that found it. So the walk happens once and the elements are kept;
# asking one of them something costs a quarter of a millisecond.
#
# WHEN THE ANSWER GOES STALE. A held element dies when WhatsApp rebuilds that
# part of its interface, and it says so: the rectangle read throws, or comes
# back empty. That is the check — asking the element, not walking the tree —
# and failing it is what triggers a fresh walk. The other trigger is wanting a
# composer when the last look did not find one, which is exactly what happens
# the moment a conversation opens.
# ──────────────────────────────────────────────────────────────────────────────
# How long a look is trusted without re-checking it. Short enough that waiting
# for a conversation to open still works — those loops run for seconds — and
# long enough that the handful of questions asked back-to-back in one step
# share a single walk instead of paying for one each.
_BOXES_FRESH = 0.5


@dataclass
class _Boxes:
    """The chat-search box and the message composer of one window, held as live
    UI Automation elements. Either may be None: a window with no conversation
    open has no composer at all, which is the ordinary state right after a
    call."""
    hwnd: int
    search: object = None
    composer: object = None
    at: float = 0.0


_boxes_held: dict[int, _Boxes] = {}


def _el_rect(el):
    """An element's rectangle, or None when the answer is not usable.

    Three ways it is not usable, and the third is the one that bit:

      * the element will not answer at all — how a dead element announces
        itself, and the cheapest liveness check there is;
      * the rectangle is empty;
    A held element survives its window being minimised and goes on answering,
    with coordinates out at -31834 and IsOffscreen still reporting False — so
    that state has to be caught, and it is, but ONE LEVEL UP. Whether the
    WINDOW is minimised or hidden is a single cheap call about the window, and
    _boxes asks it before any of this is reached.

    Testing each element against the desktop instead was overreach, and it cost
    a real send: WhatsApp was sitting at y=601 with a height of 1111 on a
    1440-tall screen, so its message box was a few dozen pixels below the
    bottom edge — a perfectly live box in a perfectly ordinary window, refused,
    and then looked for again every two seconds for twenty seconds. Where a box
    is matters when a POINTER is about to be sent to it, which is _click's
    business, and not before.
    """
    if el is None:
        return None
    try:
        r = el.CurrentBoundingRectangle
    except Exception:
        return None
    if r.right <= r.left or r.bottom <= r.top:
        return None
    return (r.left, r.top, r.right, r.bottom)


def _window_usable(hwnd: int) -> bool:
    """Is this window in a state where reading it means anything?

    Minimised and hidden are the two states whose elements keep answering with
    the coordinates the window had when it was neither. Both are one cheap call
    about the window, and asking here means nothing further down has to wonder.
    """
    if not (hwnd and _user32.IsWindow(hwnd) and _user32.IsWindowVisible(hwnd)):
        return False
    if _user32.IsIconic(hwnd):
        return False
    return _on_screen(_win_rect(hwnd))


def _read_boxes(hwnd: int):
    """ONE walk, and which box is which decided by position in the window.

    Two Edits matter here and they are told apart by WHERE they are: the chat
    search sits near the top of the sidebar, the composer runs along the bottom
    of the conversation. So the search box is the highest Edit in the upper half
    and the composer is the lowest Edit in the lower half.

    The version before this one tested `r.left <= rr.left + 520 and r.top <=
    rr.top + 300`. Those numbers were measured on somebody's laptop, and on a
    2578x1408 window at high DPI they describe a corner the search box is not
    in — so the box was never recognised, focus was never confirmed, and the
    message was never sent. Halves of the actual window hold everywhere.
    """
    if not _window_usable(hwnd):
        return None

    def work():
        iuia, root = _uia_root(hwnd)
        if root is None:
            return None
        wr = root.CurrentBoundingRectangle
        if wr.right <= wr.left or wr.bottom <= wr.top:
            return None
        window = (wr.left, wr.top, wr.right, wr.bottom)
        midline = wr.top + (wr.bottom - wr.top) * 0.5

        # A BOX HAS TO BE INSIDE ITS OWN WINDOW.
        #
        # WhatsApp is two windows and they do not come back together. Restoring
        # it puts the shell on screen immediately while the WebView — which is
        # where both boxes actually live — stays parked at -32000 for a moment
        # longer. The shell's tree still lists those boxes, at those
        # coordinates, so a read taken in that instant returns a search box at
        # x = -31834 belonging to a window at x = 645.
        #
        # Neither reading is wrong by itself; together they are impossible, and
        # the pair is what says "not ready yet". Refusing it here means the
        # caller simply looks again in a moment, by which time the WebView has
        # caught up — instead of clicking into nowhere and reporting that the
        # conversation could not be opened.
        #
        # Note this is about the box and its OWN window, not about the desktop:
        # a window hanging over the bottom edge of the screen is unusual but
        # not broken, and its boxes are still its boxes.
        edits = [(el, rect) for el, _ct, _n, rect, _f
                 in _find_all(iuia, root, _CT_EDIT)
                 if _overlap(rect, window) > 0.0]
        above = [(el, rect) for el, rect in edits if rect[1] <= midline]
        below = [(el, rect) for el, rect in edits if rect[1] > midline]
        return _Boxes(
            hwnd,
            search=min(above, key=lambda er: er[1][1])[0] if above else None,
            composer=max(below, key=lambda er: er[1][1])[0] if below else None,
            at=time.monotonic(),
        )
    return _budget(work, _SCAN_BUDGET)


def _boxes(hwnd: int, need_composer: bool = False, fresh: bool = False):
    """The window's text boxes, walking for them only when it has to."""
    held = None if fresh else _boxes_held.get(hwnd)
    if held is not None:
        # The half-second of trust is for questions asked back-to-back about a
        # window that is sitting still. It is NOT a licence to keep answering
        # about a window that has just been minimised or destroyed, which is a
        # state change and which costs nothing to notice.
        if time.monotonic() - held.at < _BOXES_FRESH and _window_usable(hwnd):
            return held

        def still_there():
            # The window is checked first and by itself. Its state is what the
            # held elements lie about, and it is a handful of cheap calls
            # rather than an inference from coordinates.
            if not _window_usable(hwnd):
                return False

            # AND THE BOXES HAVE TO BE IN IT.
            #
            # A held element outlives its window being minimised and keeps
            # answering with the coordinates it had while parked off the
            # desktop — and it goes on doing so for a moment after the window
            # comes back. The window then says "I am on screen" while the box
            # says "I am at -31684", and everything downstream believed the
            # window: the click was refused, the caret never arrived, and the
            # conversation "could not be opened".
            #
            # Neither reading is wrong on its own; it is the pair that is
            # impossible. A box belongs to its window, so a box outside it is
            # a stale answer and the tree is walked again.
            win = _win_rect(hwnd)
            for el, needed in ((held.search, True),
                               (held.composer, need_composer)):
                if not needed:
                    continue
                rect = _el_rect(el)
                if rect is None:
                    return False
                if win and _overlap(rect, win) <= 0.0:
                    return False
            return True

        if _budget(still_there, 4.0, default=False):
            held.at = time.monotonic()
            return held

    got = _read_boxes(hwnd)
    if got is None:
        _boxes_held.pop(hwnd, None)
    else:
        _boxes_held[hwnd] = got
    return got


def _forget_boxes(hwnd: int | None = None) -> None:
    """Drop what is held, for a window or for all of them. Cheap insurance
    when a window is about to be replaced rather than merely repainted."""
    if hwnd is None:
        _boxes_held.clear()
    else:
        _boxes_held.pop(hwnd, None)


def _text_boxes(hwnd: int, need_composer: bool = False) -> tuple:
    """(search_box, composer, focused_rect) — every rectangle read off the two
    elements this window is already holding, never from a pixel constant.

    `focused_rect` is the rectangle of whichever of the two has the keyboard,
    or None. It used to be the rectangle of any focused Edit in the window;
    every caller compares it against the search box or the composer, so
    restricting it to those two says the same thing and costs two questions
    instead of a journey.

    `need_composer` HAS TO BE PASSED ON, and forgetting to was a deadlock.
    What is held is only re-examined against what the caller says it needs, so
    a look taken while no conversation was open — no composer, correctly — was
    handed back for ever to a caller that was specifically waiting for one. The
    conversation opened, the composer appeared, and nothing ever went and
    looked: WhatsApp was brought up again and again, each time to be told there
    was no message box, until the twenty-second budget ran out.
    """
    b = _boxes(hwnd, need_composer=need_composer)
    if b is None:
        return None, None, None

    def work():
        search, composer = _el_rect(b.search), _el_rect(b.composer)
        focused = None
        for el, rect in ((b.search, search), (b.composer, composer)):
            if el is None or rect is None:
                continue
            try:
                if el.CurrentHasKeyboardFocus:
                    focused = rect
                    break
            except Exception:
                continue
        return search, composer, focused

    return _budget(work, 4.0, default=(None, None, None)) or (None, None, None)


def _composer(hwnd: int) -> tuple | None:
    """The message box of the open conversation, or None."""
    return _text_boxes(hwnd, need_composer=True)[1]


def _win_rect(hwnd: int) -> tuple | None:
    """This window's rectangle, straight from the window list. No UIA, no scan."""
    for w in _visible_windows():
        if w["hwnd"] == hwnd:
            return w["rect"]
    return None


def _wait_until(check, limit: float, step: float = 0.08):
    """Poll `check` until it returns something truthy, or give up.

    Every fixed pause in this file has now been replaced by one of these. A
    sleep encodes a guess about somebody else's machine — how fast their disk
    is, how loaded their CPU is, how big their chat list is — and the guess is
    either too short, which is the bug, or too long, which is the delay. Waiting
    for the thing that actually has to be true is neither, and it returns the
    moment it happens rather than at the end of a constant.
    """
    deadline = time.monotonic() + limit
    while True:
        try:
            got = check()
        except Exception:
            got = None
        if got:
            return got
        if time.monotonic() >= deadline:
            return None
        time.sleep(step)


def _edit_state(hwnd: int, which: str):
    """(text, placeholder) for the search box or the composer.

    Reading is the point. WRITING through this same pattern was tried and it is
    a trap: ValuePattern.SetValue on WhatsApp's composer returns without error
    and changes nothing, because the box is a contenteditable inside a WebView
    and the web app never sees an input event. The value read back before and
    after a SetValue was identical — an empty box both times — and the code
    above it reported a message sent that had never been written.

    Read back it is honest, and it gives two things worth having: what is
    actually in the box, and the placeholder, which on a real window reads
    "Type a message to A Annem" and so says which conversation is open.
    """
    b = _boxes(hwnd, need_composer=(which == "composer"))
    if b is None:
        return None
    el = b.composer if which == "composer" else b.search
    if el is None:
        return None

    def work():
        from pywinauto.uia_defines import get_elem_interface
        try:
            text = get_elem_interface(el, "Value").CurrentValue or ""
        except Exception:
            text = ""
        try:
            name = el.CurrentName or ""
        except Exception:
            name = ""
        return text.strip(), name

    return _budget(work, 4.0, default=None)


def _composer_state(hwnd: int):
    """(text_in_the_box, placeholder_name) for the message composer."""
    return _edit_state(hwnd, "composer")


def _await_named(hwnd: int, matches, limit: float = 4.0, exact=None) -> str:
    """Wait for a button matching `matches` to appear, then press it.

    The waiting is the point, and it is not padding. WhatsApp only publishes
    chat-list rows that are actually rendered, so a contact further down the
    list has no button at all — measured on the real window, 'A Annem' was
    there twice and 'Dude' was not there once. That is exactly the difference
    between the call this worked for and the call it did not.

    A declined call fixes it by itself: the missed call bumps that conversation
    to the top of the list, so the row appears within a moment. Looking once,
    immediately after pressing decline, is looking too early.
    """
    deadline = time.monotonic() + limit
    while True:
        started = time.monotonic()
        pressed = _invoke_named(hwnd, matches, exact=exact)
        if pressed:
            return pressed
        took = time.monotonic() - started
        # Do not start a look there is no time to finish.
        #
        # One look is a 2.5-second walk of the whole window, and the deadline
        # used to be checked only after starting another one — so a four-second
        # budget reliably spent five and a half seconds, the second walk being
        # thrown away the moment it completed. The walk is also the waiting: by
        # the time one finishes, a row that was going to appear has appeared.
        if time.monotonic() + took + 0.3 >= deadline:
            return ""
        time.sleep(0.3)


def _invoke_named(hwnd: int, matches, exact=None) -> str:
    """Find the first on-screen button whose name satisfies `matches`, and
    press it. Returns the name pressed, or "".

    Finding and pressing are ONE job, which is the whole speed of this route.
    It used to scan the window to find the row and then hand the name and the
    rectangle to _invoke(), which walked the same tree a second time to find
    the same element again — two 3.4-second walks to press one row. The element
    from the first walk is perfectly good: every UI Automation call in this file
    runs on the same apartment thread, so it is still the thread that fetched
    it.

    This is also the one mechanism here that has never needed the keyboard or
    the foreground, which is why the message path reaches for it first.
    """
    def work():
        from pywinauto.uia_defines import get_elem_interface
        iuia, root = _uia_root(hwnd)
        if root is None:
            return ""
        hits = [(el, name, rect) for el, _ct, name, rect, _f
                in _find_all(iuia, root, _CT_BUTTON) if name and matches(name)]
        if not hits:
            return ""

        # Leftmost wins. A contact's name appears twice in this window — once as
        # the row in the chat list and once in the header of the conversation
        # already open — and they are not interchangeable: pressing the header
        # one opens contact details, not the chat. The list is the left pane and
        # the header is the right, so the smaller x is the one that opens a
        # conversation. Relative to the window, so it holds at any size.
        #
        # `exact` is the other half of the same problem, from the other end.
        # Matching is deliberately generous, because "mum" has to find
        # "Mum ❤" — and generous means "Ali" also matches "Alican". When one
        # row IS the name and the others merely contain it, that row wins
        # whatever the x order says, and only then does position decide.
        hits.sort(key=lambda enr: (0 if (exact and exact(enr[1])) else 1,
                                   enr[2][0]))
        el, name, rect = hits[0]
        try:
            get_elem_interface(el, "Invoke").Invoke()
            return name
        except Exception as e:
            print(f"[WhatsApp] Invoke unavailable ({e}) — clicking instead.")
            return name if _click(rect) else ""

    return _budget(work, _SCAN_BUDGET, default="") or ""


def _set_edit_value(hwnd: int, which: str, text: str) -> bool:
    """Write straight into a box through UI Automation, bypassing the keyboard.

    Measured on the real window: this WORKS on the chat search (set 'x', read
    back 'x') and does NOT work on the message composer (set 'x', read back
    ','), which is why typing is done with keystrokes everywhere else. It is
    used here for one job only — EMPTYING the search box — where the value
    written is "" and there is nothing for the web app to have missed.

    That job matters because the alternative failed for real: a search box left
    holding an old query filters the chat list to nobody, Ctrl+A and Delete go
    to whichever of WhatsApp's two windows has the keyboard rather than to the
    box, and every conversation afterwards "could not be opened".
    """
    box = _boxes(hwnd, need_composer=(which == "composer"))
    el = None if box is None else (box.composer if which == "composer" else box.search)
    if el is None:
        return False

    def work():
        from pywinauto.uia_defines import get_elem_interface
        try:
            get_elem_interface(el, "Value").SetValue(text)
            return True
        except Exception as e:
            print(f"[WhatsApp] could not write the {which} box directly: {e}")
            return False

    return bool(_budget(work, 6.0, default=False))


def _focus_edit(hwnd: int, which: str, tries: int = 3) -> bool:
    """Put the caret in the search box or the composer, and confirm it arrived.

    ASK THE CONTROL, DO NOT AIM AT IT.
    Three approaches have now failed here and all three failed the same way —
    by addressing the box indirectly. Ctrl+F went wherever the keyboard already
    was. Clicking sent the mouse to a screen coordinate, which depends on the
    window's position, the display scaling and the pointer arriving before the
    layout shifts. And verifying by exact rectangle failed even when the click
    worked, because WhatsApp resizes its search field the instant it takes
    focus, so the rectangle read back was never the rectangle clicked.

    UI Automation exposes SetFocus() on the element itself. No pointer, no
    coordinates, no DPI arithmetic, nothing that changes with the size of
    somebody's monitor or where they dragged the window — the same call on a
    1366x768 laptop and a 4K display. That is what makes this work on machines
    that are not the one it was written on.

    `which` is 'search' or 'composer', and which is which is decided by
    position within the window, never by a fixed number of pixels — that
    decision was made once, by the walk that found them, and is not made again
    here.
    """
    b = _boxes(hwnd, need_composer=(which == "composer"))
    el = None if b is None else (b.composer if which == "composer" else b.search)
    if el is None:
        return False

    def work():
        for _attempt in range(tries):
            try:
                el.SetFocus()
            except Exception as e:
                print(f"[WhatsApp] SetFocus on the {which} box failed: {e}")
                return False
            time.sleep(0.15)
            try:
                if el.CurrentHasKeyboardFocus:
                    return True
            except Exception:
                return False
        return False

    return bool(_budget(work, 6.0, default=False))


class WindowsDesktop(Transport):
    """UI Automation against the WhatsApp desktop app on Windows."""

    name = "desktop-windows"
    label = "WhatsApp desktop app (Windows)"

    def __init__(self):
        self._focus_hwnd: int | None = None
        self._scan_hwnd: int | None = None
        # The processes behind those handles, remembered so the call window can
        # still be found after WhatsApp hides the window they came from.
        self._pids: set[int] = set()

    # -- availability -------------------------------------------------------
    @classmethod
    def usable(cls) -> tuple[bool, str]:
        if not IS_WINDOWS:
            return False, "this is not Windows"
        missing = []
        if not HAVE_PYAUTOGUI:
            missing.append("pyautogui")
        if not HAVE_PYPERCLIP:
            missing.append("pyperclip")
        for mod in ("pywinauto", "comtypes"):
            try:
                __import__(mod)
            except Exception:
                missing.append(mod)
        if missing:
            return False, ("missing " + ", ".join(missing) +
                           " — run: pip install " + " ".join(missing))
        return True, ""

    # -- lifecycle ----------------------------------------------------------
    def ensure_ready(self, conf: dict) -> tuple[bool, str]:
        self._focus_hwnd, self._scan_hwnd = _pick_windows()
        if not self._scan_hwnd:
            self._focus_hwnd, self._scan_hwnd = _launch(conf["app_name"],
                                                        conf["launch_wait"])
        if not self._scan_hwnd:
            return False, ("WhatsApp would not open — it should be started by "
                           "hand once and then asked again")
        self._remember_pids()
        return True, ""

    def _activate(self, conf: dict) -> None:
        """Bring WhatsApp up. Unconditionally, and by both routes at once.

        Asking is cheap and idempotent — the app is already running, so the
        protocol handler restores it rather than starting a second copy — and
        the old code only asked after waiting several seconds to see whether it
        would come back on its own. It does not always come back: WhatsApp hides
        its conversation window for the duration of a call, and if the user had
        it minimised beforehand it stays hidden afterwards.
        """
        # WHAT IS HELD IS DELIBERATELY KEPT.
        #
        # This used to drop every held element here, on the reasoning that
        # they describe the window as it was before. They do — while it is
        # hidden they answer with the coordinates it had when it was not, which
        # is a real hazard and is why _el_rect refuses a rectangle that is not
        # on any screen.
        #
        # But refusing a stale answer and throwing the element away are not the
        # same thing, and only one of them costs 2.9 seconds. Measured across a
        # minimise and a restore of the real window: the same two elements come
        # back with the right coordinates in 0.8 ms. WhatsApp is being brought
        # forward here, not rebuilt, so its boxes are the same boxes; and the
        # check that stands between a hidden window and a click into nowhere is
        # the on-screen test, which runs on every read either way.
        #
        # So the elements are kept and re-examined. When WhatsApp really has
        # been restarted they fail that examination and the walk happens then.

        # The app is about to be asked to come forward, so hand it the
        # foreground rights this process is holding — see _yield_foreground.
        _yield_foreground()
        try:
            # os.startfile rather than a shell: "cmd /c start" creates a
            # console process to do one thing that Windows will do directly,
            # and that is a few hundred milliseconds on the one path where the
            # user is waiting for WhatsApp to appear.
            os.startfile("whatsapp:")            # noqa: S606 - a URI, not a path
        except Exception as e:
            print(f"[WhatsApp] could not ask Windows to open WhatsApp: {e}")
        # And raise whatever window it already has, which is what restores a
        # minimised one.
        for w in _whatsapp_family(self._pids):
            if _user32.IsIconic(w["hwnd"]):
                try:
                    _user32.ShowWindow(w["hwnd"], 9)      # SW_RESTORE
                except Exception:
                    pass
        fh, _sh = _pick_windows()
        if fh:
            _focus(fh)

    def _usable_window(self, need_composer: bool):
        """(focus_hwnd, scan_hwnd) of the WhatsApp window publishing the boxes
        the next step actually needs, or None.

        EVERY window is checked, not just the one _pick_windows chose.
        WhatsApp desktop is a WinUI shell hosting a WebView, and the text boxes
        live in the WebView — a different window, in a different process, which
        WhatsApp hides while a call is ringing. Looking only at the shell found
        no boxes and concluded there was no conversation window, while a
        perfectly good 2578x1408 WhatsApp sat on screen.

        WHICH BOXES, THOUGH, DEPENDS ON WHAT IS ABOUT TO HAPPEN — and getting
        that wrong is what stopped every message after a declined call.

        The composer only exists while a conversation is open, and WhatsApp
        closes the conversation to show a call. So a moment after declining
        one there is no composer anywhere on screen. Requiring one before we
        are allowed to go LOOKING for the conversation is circular: it waited
        the full twenty seconds, found nothing and gave up — which from the
        outside looked exactly like "it opened WhatsApp and just left it
        sitting there", because that is precisely what it did.

        The search box is always in the sidebar, open chat or not. So the
        search box is what qualifies a window to be worked in, and the
        composer is required only by the step that types into it.
        """
        # THE WINDOW WE ARE ALREADY HOLDING, FIRST OF ALL.
        #
        # Finding the boxes costs one 2.3-second walk and everything after it
        # costs microseconds, so the whole speed of this file rests on that
        # walk happening ONCE. It stops happening once the moment anything
        # asks about a different handle: WhatsApp is two windows, _pick_windows
        # can name either as the one to scan depending on what is on screen,
        # and a handle with nothing held against it means a fresh walk for an
        # answer we already had.
        if self._scan_hwnd:
            search, composer, _f = _text_boxes(self._scan_hwnd, need_composer)
            if search and (composer or not need_composer):
                return (self._focus_hwnd or self._scan_hwnd), self._scan_hwnd

        # THE WINDOW LIST NEXT. It already knows which window is the app
        # and which is its WebView, and asking it costs about a millisecond.
        # The loop below pays a full tree walk for every window it rejects, so
        # in the ordinary case — where the pair it names is the answer — this
        # skips seconds of work rather than a millisecond of it.
        pair = _pick_windows()
        if pair[1]:
            search, composer, _f = _text_boxes(pair[1], need_composer)
            if search and (composer or not need_composer):
                return (pair[0] or pair[1]), pair[1]

        fam = _whatsapp_family(self._pids)
        # The WebView first: it is where the boxes are and it scans in a
        # fraction of the time the shell takes.
        fam.sort(key=lambda w: 0 if "chrome_widgetwin" in w["class"].lower() else 1)
        shells = [w for w in fam if "chrome_widgetwin" not in w["class"].lower()]
        for w in fam:
            search, composer, _f = _text_boxes(w["hwnd"], need_composer)
            if not search:
                continue
            if need_composer and not composer:
                continue
            # Keystrokes go to the top-level window, so focus the shell that
            # owns this one when there is one.
            owner = next((sh["hwnd"] for sh in shells
                          if sh["pid"] == w["pid"] or _overlap(sh["rect"], w["rect"]) > 0.8),
                         w["hwnd"])
            return owner, w["hwnd"]
        return None

    def _window_up(self) -> bool:
        """Is there a WhatsApp window on screen at all?

        The window list answers this in about a millisecond. A real look costs
        a 2.4-second tree walk, and while WhatsApp is still starting up every
        one of those walks is both expensive and fruitless — which is what made
        "open WhatsApp and send a message" feel like it hung: it was not
        waiting, it was walking, over and over, a tree that had nothing in it
        yet.

        Not iconic and somewhere on the desktop, both read from the system. No
        size threshold, so it holds for a window on a 1366x768 laptop and one
        spanning two 4K monitors alike.
        """
        for w in _whatsapp_family(self._pids):
            if not _user32.IsIconic(w["hwnd"]) and _on_screen(w["rect"]):
                return True
        return False

    def _conversation_window(self):
        """A window with a conversation actually open — search box AND
        composer. What send_text needs, because it types into the composer."""
        return self._usable_window(need_composer=True)

    def _chat_list_window(self):
        """A window with the chat list on it; the search box is enough. What
        every step BEFORE typing needs — including the one a call that has
        just been declined leaves behind."""
        return self._usable_window(need_composer=False)

    def _await_conversation(self, conf: dict, limit: float = 20.0,
                            need_composer: bool = True):
        """A WhatsApp window we can work in, opening WhatsApp if there is not
        one. `need_composer` says whether a conversation has to be open."""
        look = self._conversation_window if need_composer else self._chat_list_window
        got = look()
        if got:
            return got

        print("[WhatsApp] bringing WhatsApp up.")
        self._activate(conf)

        # Cheap question first, expensive question second.
        #
        # This used to be one _wait_until(look) polling five times a second,
        # and `look` is a tree walk. So while WhatsApp was starting — the whole
        # of the wait, in the case the user actually complained about — it
        # spent every one of those seconds walking a tree that was not ready,
        # and the window it was waiting for could not even be noticed until a
        # walk finished. Asking the window list whether anything is up costs a
        # millisecond, so that is what the waiting is done with; a walk is only
        # paid for once there is something to walk.
        deadline = time.monotonic() + limit
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            if _wait_until(self._window_up, min(1.5, left), 0.1):
                got = look()
                if got:
                    return got
                # Up but not ready — still painting, or still on the call
                # panel. Let it get on with it rather than walking again
                # immediately.
                time.sleep(0.3)
            elif not self._window_up():
                time.sleep(0.2)

        print("[WhatsApp] WhatsApp never presented a window with "
              + ("a message box" if need_composer else "its chat list")
              + " — nothing could be done there.")
        return None

    def _message_chat(self, contact: str, allow_header: bool = True):
        """`contact`'s conversation, if that is the one the message box belongs
        to right now — otherwise None. No tree walk.

        WhatsApp names the composer after the conversation it will post to:
        "Type a message to A Annem". That is the one string on screen that is
        guaranteed to describe where the text is about to GO, rather than
        merely what is on display near it — and it is read off an element
        already in hand, so asking costs a quarter of a millisecond where
        reading the header costs a 3.4-second walk.

        It is deliberately the same string send_text() re-reads immediately
        before it types, which is what makes this cheap check safe: if the
        conversation changes in between, that second reading catches it with
        the caret already in the box.
        """
        state = _composer_state(self._scan_hwnd)
        if not state:
            return None                      # no conversation open at all
        _text, placeholder = state
        # name_in_phrase, not names_match: the placeholder is a SENTENCE, and
        # a substring of a sentence is not an identity. See its docstring for
        # the conversation this opened by mistake.
        if placeholder and name_in_phrase(contact, placeholder):
            return Chat(raw_title=placeholder, placeholder=placeholder,
                        handle=self._scan_hwnd)
        if not allow_header:
            return None

        # THE MESSAGE BOX DOES NOT ALWAYS NAME THE CONTACT.
        #
        # Measured on a real call, and it is why an auto-reply opened the right
        # conversation and then sent nothing: a contact saved as 'Zeki zeki'
        # — the chat-list row says so, the header says so, the ringing call
        # panel says so — has a message box that reads "Type a message to
        # +90 544 134 35 72". WhatsApp uses the number there. No amount of
        # comparing a NAME against that string will ever match, so the right
        # conversation was refused over and over.
        #
        # So when the cheap reading does not recognise the contact, the header
        # is read before the conversation is dismissed. That costs one tree
        # walk, which on the WebView window is a quarter of a second rather
        # than the three seconds the shell used to take.
        header = _read_header(self._scan_hwnd)
        if header and name_in_phrase(contact, header["title"]):
            note_title(header["title"])
            return Chat(raw_title=header["title"], controls=header["cluster"],
                        placeholder=placeholder, handle=self._scan_hwnd)
        return None

    def _await_message_chat(self, contact: str, limit: float):
        """Wait for the message box to belong to `contact`.

        The cheap reading drives the waiting, and the header — which costs a
        tree walk — is read at most ONCE for any given message box. That is
        enough, because the answer cannot change while the box does not: if the
        header did not name this contact a moment ago, it does not now either.

        There WAS a further condition here, skipping the header while the box
        still said what it said before anything was pressed — on the reasoning
        that an unchanged box means nothing has opened. It is wrong whenever the
        conversation that was wanted is the one already open: nothing changes,
        nothing is ever verified, and a chat sitting right there in front of us
        times out as "could not be opened".
        """
        deadline = time.monotonic() + limit
        asked_about = None
        while True:
            fh, sh = _pick_windows()
            if sh and sh != self._scan_hwnd:
                self._focus_hwnd, self._scan_hwnd = fh, sh

            state = _composer_state(self._scan_hwnd)
            placeholder = state[1] if state else ""
            if placeholder and name_in_phrase(contact, placeholder):
                return Chat(raw_title=placeholder, placeholder=placeholder,
                            handle=self._scan_hwnd)
            if placeholder and placeholder != asked_about:
                asked_about = placeholder
                got = self._message_chat(contact)
                if got:
                    return got

            if time.monotonic() >= deadline:
                return None
            time.sleep(0.15)

    def _await_chat(self, contact: str, limit: float):
        """Wait for `contact`'s conversation to be the one on screen, and hand
        back the header — which is the one thing that carries the call buttons.

        WAITING AND READING ARE DIFFERENT QUESTIONS, and they used to be the
        same call. Reading the header is a 2.9-second tree walk, and this
        polled with it five times a second: so the wait was not spent waiting,
        it was spent walking, and a four-second budget bought one and a half
        looks at a conversation that had opened in the first hundred
        milliseconds.

        The composer's placeholder answers "has the right chat opened" for a
        quarter of a millisecond, so the WAITING is done with that. The header
        is read once, at the end, when the answer is already known.

        A build that publishes no composer still works: the cheap wait simply
        never succeeds, and the single header read at the end is exactly what
        this function used to do on its first poll.
        """
        def opened():
            fh, sh = _pick_windows()
            if sh and sh != self._scan_hwnd:
                self._focus_hwnd, self._scan_hwnd = fh, sh
            return self._message_chat(contact)

        _wait_until(opened, limit, 0.15)

        chat = self.current_chat()
        return chat if chat and names_match(contact, chat.raw_title) else None

    def _remember_pids(self) -> None:
        """Which processes own the windows we attached to. Taken now, while the
        conversation window is definitely up — a call hides it, and that is
        exactly when the process is needed."""
        wanted = {self._focus_hwnd, self._scan_hwnd} - {None}
        for w in _visible_windows():
            if w["hwnd"] in wanted:
                self._pids.add(w["pid"])
        # Always union, never only-when-empty. WhatsApp is at least two
        # processes and only shows some of them at a time, so a set built from
        # the windows on screen right now is a subset, not the answer.
        self._pids |= _whatsapp_pids()

    def close(self) -> None:
        _forget_boxes()
        self._focus_hwnd = self._scan_hwnd = None
        self._pids = set()

    # -- conversations ------------------------------------------------------
    def current_chat(self) -> Chat | None:
        header = _read_header(self._scan_hwnd) if self._scan_hwnd else None
        if not header:
            return None
        note_title(header["title"])
        return Chat(raw_title=header["title"], controls=header["cluster"],
                    handle=self._scan_hwnd)

    def open_chat(self, contact: str, conf: dict, before: str = "") -> Chat | None:
        """Open WhatsApp, Ctrl+F, type the name, open the chat. That is all.

        This was a four-rung ladder that pressed chat-list rows, focused the
        search field through the accessibility tree, and clicked coordinates.
        Each rung was added to work around the previous one failing, and the
        result was a lot of machinery that still failed — and failed differently
        each time, which is worse. So it does the plain thing a person does.

        The one check that stays is that WhatsApp actually has the keyboard
        before anything is typed. That is not caution for its own sake: without
        it a contact's name went into JARVIS's own command box once, and the
        Enter that followed placed a real call to a real person. Every keystroke
        below is global — it goes to whatever window is in front — so knowing
        which window that is, is the difference between typing and firing blind.
        """
        if not HAVE_PYAUTOGUI:
            return None

        # Make sure WhatsApp is up and showing its chat list before
        # anything else — it may be minimised to its stub, and after a
        # call it may have no conversation open at all. Only the search
        # box is needed to search; the composer comes with the chat.
        got = self._await_conversation(conf, need_composer=False)
        if not got:
            return None
        self._focus_hwnd, self._scan_hwnd = got
        self._remember_pids()

        already = self.current_chat()
        if already and names_match(contact, already.raw_title):
            return already

        if not _await_foreground(self._focus_hwnd, pids=self._pids):
            print("[WhatsApp] WhatsApp would not come to the front — refusing "
                  "to type, the keystrokes would land somewhere else.")
            return None

        pyautogui.hotkey("ctrl", "f")
        # Wait for the caret to actually be in the search box, not for a number
        # of milliseconds somebody measured once.
        if not _wait_until(lambda: _search_focused(self._scan_hwnd), 2.0):
            # One nudge: put the caret in the box through the tree, which needs
            # no pointer and no shortcut, then carry on with the same typing.
            _focus_edit(self._scan_hwnd, "search")

        # Ask for the foreground again rather than reading it once and giving
        # up: putting the caret in the box can itself hand the keyboard to
        # WhatsApp's WebView, and that lands a moment after SetFocus returns.
        if not _await_foreground(self._focus_hwnd, limit=2.0, pids=self._pids):
            print("[WhatsApp] the keyboard is not in WhatsApp — not typing.")
            return None

        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("delete")
        # Wait for it to BE empty rather than assuming the delete landed.
        _wait_until(lambda: (_edit_state(self._scan_hwnd, "search") or ("x",))[0] == "",
                    1.5)

        _type(contact)
        # Wait for the box to hold what was typed. This is the "let the result
        # list settle" pause, except it settles when it settles: on a fast
        # machine that is immediate, and on a slow one it is not cut short.
        if not _wait_until(
                lambda: fold(contact) in fold(
                    (_edit_state(self._scan_hwnd, "search") or ("",))[0]), 3.0):
            got = (_edit_state(self._scan_hwnd, "search") or ("",))[0]
            print(f"[WhatsApp] {contact!r} did not go into the search box "
                  f"(it holds {got!r}) — not pressing Enter.")
            return None

        if not _whatsapp_has_keyboard(self._pids):
            print("[WhatsApp] focus left WhatsApp while searching — not "
                  "pressing Enter.")
            return None
        pyautogui.press("enter")

        chat = self._await_chat(contact, max(2.5, conf.get("search_wait", 4.0)))
        if chat:
            return chat
        now = self.current_chat()
        print(f"[WhatsApp] searched for {contact!r} but the conversation on "
              f"screen is {(now.raw_title if now else None)!r}.")
        return None


    def open_chat_for_message(self, contact: str, conf: dict,
                              before: str = "") -> Chat | None:
        """Open a conversation to TYPE in it, without needing the keyboard.

        WHY THIS IS NOT open_chat().

        open_chat() searches, and searching means typing: Ctrl+F, the name,
        Enter. Every one of those keystrokes is global — it goes to whichever
        window Windows currently considers the foreground — so the whole
        sequence is guarded by "does WhatsApp have the keyboard", and when the
        answer is no it correctly refuses to type. That guard is not optional;
        without it a contact's name once went into JARVIS's own input box and
        the Enter behind it placed a real call.

        The trouble is that the moment this runs is the worst possible moment
        to be asking for the keyboard. A call has just been declined: WhatsApp
        tore down its call panel, the foreground is being handed around between
        windows that are closing, and the conversation window may not be back
        yet. So the honest answer to "does WhatsApp have the keyboard" is often
        no — and a message that is never typed is exactly what the user saw.

        A declined call, though, hands us something better than a search box.
        It bumps that conversation to the top of the chat list, and a chat-list
        row is a UI Automation element with an Invoke pattern: pressing it
        needs no pointer, no foreground and no keystroke at all. That is the
        same mechanism that pressed decline a second earlier, and it is the one
        thing here that has never depended on who owns the foreground.

        So: press the row if the row is there, and fall back to typing when it
        is not. Which route ran is invisible from outside — both return the
        conversation, and send_message_to() verifies it either way.

        Row matching is by identity (names_match/fold), never by vocabulary, so
        it behaves the same whatever language WhatsApp is in.
        """
        # The chat list is enough to press a row, and insisting on a composer
        # here is what used to fail: a call leaves no conversation open, so
        # there is no composer to find, so it waited twenty seconds and gave
        # up with WhatsApp open and nothing typed.
        got = self._await_conversation(conf, need_composer=False)
        if not got:
            return None
        self._focus_hwnd, self._scan_hwnd = got
        self._remember_pids()

        # Already the right conversation? Asked of the composer, which costs
        # nothing, instead of of the header, which costs a walk — and when
        # there is no composer at all there is no conversation open, so the
        # question answers itself without asking anybody.
        already = self._message_chat(contact)
        if already:
            return already


        wait = max(2.5, float(conf.get("search_wait", 4.0) or 4.0))

        wait = max(2.5, float(conf.get("search_wait", 4.0) or 4.0))

        # -- route 1: the chat search. ---------------------------------------
        #
        # THIS IS THE ROUTE THAT ALWAYS EXISTS, which is why it now goes first.
        #
        # Pressing a chat-list row is lovely when there IS a chat-list row, and
        # there is not always one. Measured on this machine with WhatsApp open
        # on its start screen — no conversation selected — the whole list is
        # absent from the accessibility tree: seventeen named buttons, every
        # one of them navigation, not a single conversation among them. The
        # search box was there, as it always is.
        #
        # That is exactly the difference the user could see. Launching WhatsApp
        # lands it on the last conversation, where rows exist and the row route
        # works; finding it ALREADY open on the start screen left nothing to
        # press, and two routes then spent five seconds proving that before the
        # search ever ran.
        #
        # It needs the keyboard, which the row route does not — so the
        # foreground is asked for first and the row route is kept for when the
        # answer is no.
        if _await_foreground(self._focus_hwnd, pids=self._pids):
            chat = self._search_for_message(contact, conf)
            if chat:
                return chat
        else:
            print("[WhatsApp] WhatsApp would not come to the front — trying the "
                  "route that needs no keyboard instead.")

        # -- route 2: press the row in the chat list. -------------------------
        #
        # No keyboard, no foreground and no pointer: the row is invoked through
        # the accessibility tree, the same mechanism that presses decline. It
        # is second because it depends on the list being rendered, and it is
        # kept because when the search cannot be typed it is the only way left.
        folded = fold(contact)
        # name_in_phrase, matching the verification. With names_match here it
        # pressed the row called 'a annemm' when asked for 'A Annem' — a real
        # and different contact — and only the check afterwards stopped it.
        # A route that reliably opens the wrong person and is then overruled is
        # not a route, it is a delay.
        pressed = _await_named(
            self._scan_hwnd,
            lambda n: name_in_phrase(contact, n),
            limit=wait,
            exact=lambda n: fold(n) == folded,
        )
        if pressed:
            chat = self._await_message_chat(contact, wait)
            if chat:
                return chat
            print(f"[WhatsApp] pressed the row {pressed!r} but the message box "
                  f"does not belong to {contact!r}.")
        return None

    def _search_for_message(self, contact: str, conf: dict):
        """Type the name into the chat search and open the result. No walk.

        Every step here reads or drives an element that is already held, so on
        a warm window this costs a few property calls and the clipboard paste
        — no 2.4-second tree walk anywhere in it. It also asks the SEARCH BOX
        for the caret rather than aiming Ctrl+F at whatever has the keyboard,
        which is the same reason the composer is focused the way it is: a
        shortcut goes where the keyboard already is, SetFocus goes to the
        control, at any window size and on any monitor.

        Verification is the composer's placeholder, never the header: it names
        the conversation the text will actually go to, and it costs nothing.
        """
        if not HAVE_PYAUTOGUI:
            return None
        box = _boxes(self._scan_hwnd)
        if box is None or box.search is None:
            print("[WhatsApp] this window publishes no chat search.")
            return None

        # THE WINDOW FIRST, THE CARET SECOND. In that order, and not the other
        # one.
        #
        # Focusing the box while the window is still coming forward reports
        # success and then loses: the window arrives, the WebView takes the
        # keyboard for itself, and the caret is no longer where it was put.
        # Every keystroke after that goes to whatever is in front. Observed
        # from a minimised WhatsApp: the name was never typed and the search
        # box still held the previous search.
        if not _await_foreground(self._focus_hwnd, limit=4.0, pids=self._pids):
            print("[WhatsApp] the keyboard is not in WhatsApp — not typing.")
            return None

        # CLICK IT, the same way the message box is reached.
        #
        # SetFocus reports success and the keystrokes still go elsewhere: both
        # boxes live in WhatsApp's WebView, which is a SEPARATE WINDOW from the
        # shell that was just brought to the front, and a browser only moves its
        # own caret for a real pointer or a real key. Measured: focus said yes,
        # then Ctrl+A and Delete left an old query sitting in the box, and every
        # search after that filtered the chat list to nobody.
        search_rect = _text_boxes(self._scan_hwnd)[0]
        if not _search_focused(self._scan_hwnd):
            if search_rect:
                _click(search_rect)
            if not _wait_until(lambda: _search_focused(self._scan_hwnd), 1.5):
                _focus_edit(self._scan_hwnd, "search")
                if not _wait_until(lambda: _search_focused(self._scan_hwnd), 1.0):
                    print("[WhatsApp] the chat search would not take the caret.")
                    return None

        # EMPTY IT, AND MEAN IT. Typing onto what is already there searches for
        # both strings at once and finds nobody — and the failure then reads as
        # "the name did not go into the box", which is not what happened.
        def empty() -> bool:
            return not (_edit_state(self._scan_hwnd, "search") or ("x",))[0]

        if not empty():
            pyautogui.hotkey("ctrl", "a")
            pyautogui.press("delete")
            if not _wait_until(empty, 1.0):
                # The keystrokes did not reach it. The box itself will take a
                # value even when the keyboard cannot find it.
                _set_edit_value(self._scan_hwnd, "search", "")
                if not _wait_until(empty, 1.0):
                    still = (_edit_state(self._scan_hwnd, "search") or ("",))[0]
                    print(f"[WhatsApp] the chat search would not empty (it "
                          f"holds {still!r}) — not typing on top of it.")
                    return None

        if not _type(contact):
            print(f"[WhatsApp] {contact!r} could not be typed.")
            return None
        # Wait for the box to hold what was typed rather than for a constant:
        # on a fast machine that is immediate, on a slow one it is not cut
        # short, and either way nothing is sent to a half-typed search.
        if not _wait_until(
                lambda: fold(contact) in fold(
                    (_edit_state(self._scan_hwnd, "search") or ("",))[0]), 3.0):
            got = (_edit_state(self._scan_hwnd, "search") or ("",))[0]
            print(f"[WhatsApp] {contact!r} did not go into the search box "
                  f"(it holds {got!r}) — not pressing Enter.")
            return None

        if not _whatsapp_has_keyboard(self._pids):
            print("[WhatsApp] focus left WhatsApp while searching — not "
                  "pressing Enter.")
            return None
        pyautogui.press("enter")

        return self._await_message_chat(
            contact, max(2.5, float(conf.get("search_wait", 4.0) or 4.0)))

    def focus(self) -> None:
        _focus(self._focus_hwnd)

    # -- outgoing -----------------------------------------------------------
    def press_call(self, chat: Chat, call_type: str) -> tuple[bool, str]:
        target, how = _pick_call_button(chat.controls, call_type)
        if not target:
            return False, "no-button"
        if how == "position":
            print(f"[WhatsApp] '{target[0]}' matched by layout, not by name.")
        ok = _invoke(self._scan_hwnd, target[0], target[1])
        return (ok, how) if ok else (False, "press-failed")

    def hangup(self) -> bool:
        """Best effort: the call runs in its own window, whose end-call control
        cannot be observed without placing a real call. Look for it by name
        across the whole WhatsApp window family and report honestly."""
        for win in _whatsapp_family(self._pids):
            scan = win["hwnd"]
            data = _budget(lambda: _scan(scan), _SCAN_BUDGET)
            if not data:
                continue
            found, _i = _find_role(data[1], "end")
            if found and _invoke(scan, found[0], found[1]):
                return True
        return False

    def send_text(self, chat: Chat, text: str) -> bool:
        """Type the message into the open conversation and confirm it went.

        Deliberately the plain way — bring WhatsApp up, put the caret in the
        box, type, press Enter — because every cleverer route was tried here
        and failed silently. ValuePattern.SetValue writes nothing into a
        WebView contenteditable and reports success anyway: measured on the
        real window, the box read '\n' before the call and '\n' after it,
        and the code above happily said "message sent". Clicking a coordinate
        depends on the pointer, the scaling and the layout not moving. Ctrl+F
        depends on a binding. Typing into a focused box is what WhatsApp is
        actually built to receive.

        What makes it trustworthy is not the method, it is that every step is
        read back:
          * the placeholder names the conversation ("Type a message to A
            Annem"), so the message cannot go to the wrong person;
          * the box is read after typing, so "typed" means the characters are
            really in it;
          * the box is read after Enter, so "sent" means it emptied.
        Any of those failing returns False, and the guard then tells the user
        the message was not sent instead of claiming it was.
        """
        if not HAVE_PYAUTOGUI:
            return False
        try:
            got = self._await_conversation(cfg())
            if not got:
                return False
            self._focus_hwnd, self._scan_hwnd = got
            self._remember_pids()

            if not _await_foreground(self._focus_hwnd, pids=self._pids):
                print("[WhatsApp] WhatsApp would not come to the front — the "
                      "message was not typed anywhere.")
                return False

            state = _composer_state(self._scan_hwnd)
            if state is None:
                print("[WhatsApp] no message box in the open conversation — "
                      "nothing was typed.")
                return False
            _before, placeholder = state

            # DID THE CONVERSATION CHANGE SINCE IT WAS OPENED?
            #
            # That is the only question this step needs to answer, and asking
            # it as "does the box name the contact" was wrong: the box does not
            # always name anybody. A chat with 'Zeki zeki' has a message box
            # that reads "Type a message to +90 544 134 35 72", so a check that
            # demanded the name refused to send into the very conversation the
            # step before had just verified and opened.
            #
            # What identifies the box is the string the box itself gave when
            # the conversation was opened. If it still says that, this is still
            # that conversation, whatever it happens to be made of — a name, a
            # number, or a phrase in a language nobody here reads. If the
            # conversation was opened by some other route that never read a
            # placeholder, fall back to comparing against the title.
            was = (chat.placeholder or "").strip()
            if was:
                # fold, not fold_words: this compares one string with itself,
                # so the grouping inside it is noise. WhatsApp re-renders
                # "+90 544 134 35 72" as "+905441343572" and back, and a
                # refusal over that is a message not sent for no reason.
                if placeholder and fold(placeholder) != fold(was):
                    print(f"[WhatsApp] the message box now says {placeholder!r} "
                          f"and it said {was!r} when the conversation was "
                          f"opened — refusing to send.")
                    return False
            else:
                wanted = (chat.raw_title or "").strip()
                if wanted and placeholder and not name_in_phrase(wanted, placeholder):
                    print(f"[WhatsApp] the open conversation is {placeholder!r}, "
                          f"not {wanted!r} — refusing to send.")
                    return False

            # CLICK the box. A real click is what puts the caret in it.
            #
            # SetFocus() through the accessibility tree reports success and sets
            # the focus flag, and the keystrokes still go nowhere: the composer
            # is a contenteditable inside a WebView, and the browser only moves
            # the DOM caret for a real pointer or a real key. Measured: focus
            # said yes, the paste happened, and the box read back empty.
            #
            # So it is clicked, like a person would, and then the box is read to
            # confirm the caret is really there before anything is typed.
            _, composer, _f = _text_boxes(self._scan_hwnd, need_composer=True)
            if composer is None:
                print("[WhatsApp] no message box to click — nothing was typed.")
                return False

            def caret_in_box():
                _s, _c, focused = _text_boxes(self._scan_hwnd)
                return bool(focused and (focused == composer
                                         or _overlap(focused, composer) > 0.15))

            def put_caret_in() -> bool:
                if caret_in_box():
                    return True
                _click(composer)
                if _wait_until(caret_in_box, 1.5):
                    return True
                _focus_edit(self._scan_hwnd, "composer")      # last nudge
                return bool(_wait_until(caret_in_box, 1.0))

            def empty_the_box() -> bool:
                """Whatever is in there is an unsent DRAFT — something the user
                typed into this conversation and did not send. Typing a reply on
                top of it and pressing Enter sends BOTH, glued together, to a
                real person: observed exactly once, and once is plenty, when a
                declined call went out as "<leftover text>I'm busy right now."

                The draft is named in the log rather than dropped quietly. It is
                the user's own words and they are entitled to know they are
                gone; what they are not entitled to is having them posted to
                somebody else in the middle of an automatic reply.
                """
                leftover = (_composer_state(self._scan_hwnd) or ("",))[0]
                if not leftover:
                    return True
                print(f"[WhatsApp] the message box already held an unsent draft "
                      f"({leftover!r}) — clearing it so it is not sent along "
                      f"with the reply.")
                pyautogui.hotkey("ctrl", "a")
                pyautogui.press("delete")

                # EMPTY TWICE, NOT ONCE.
                #
                # A box read as empty in the middle of a deletion is empty for
                # an instant and not for the next one, and typing into that gap
                # produced "eI'm busy right now." — one surviving character of
                # the old draft welded to the front of the reply. Two readings
                # in a row cost a millisecond and describe a box that has
                # settled rather than one caught mid-erase.
                def empty_now():
                    return not (_composer_state(self._scan_hwnd) or ("",))[0]

                if not _wait_until(empty_now, 1.5):
                    return False
                time.sleep(0.08)
                return empty_now()

            def box_holds_exactly() -> bool:
                """The box must hold the message AND NOTHING ELSE.

                This used to ask whether the box CONTAINED the message, and a
                containment test cannot tell "I'm busy right now." from
                "<leftover>I'm busy right now." — so the one check standing
                between a draft and somebody else's phone waved it through.
                """
                return fold((_composer_state(self._scan_hwnd) or ("",))[0]) == fold(text)

            # TYPE IT, AND IF IT DID NOT LAND, TRY AGAIN.
            #
            # A single attempt was right when the only thing that could go wrong
            # was a wrong window. It is wrong at the moment this actually runs:
            # a call has just been declined, WhatsApp is tearing down the call
            # panel, and for a moment that panel is the window with the
            # keyboard. It belongs to WhatsApp, so "does WhatsApp have the
            # keyboard" answers yes — correctly, and uselessly, because the
            # characters go to a window with no text box in it. The message box
            # then reads back empty and a perfectly recoverable half-second of
            # transition was reported to the user as "the message was NOT sent".
            #
            # Nothing about that state needs detecting. It needs waiting out,
            # and the check that already exists says exactly when it is over:
            # the box holds the message. So the whole thing — foreground, caret,
            # empty, type, verify — is attempted again, with the boxes re-read
            # in case the window was rebuilt underneath us.
            landed = False
            for attempt in range(3):
                if attempt:
                    print(f"[WhatsApp] the message did not reach the box — "
                          f"attempt {attempt + 1} of 3.")
                    time.sleep(0.4)
                    _await_foreground(self._focus_hwnd, limit=3.0, pids=self._pids)
                    _forget_boxes(self._scan_hwnd)
                    _s2, composer, _f2 = _text_boxes(self._scan_hwnd,
                                                     need_composer=True)
                    if composer is None:
                        break

                if not put_caret_in():
                    continue
                if not empty_the_box():
                    # Refusing here rather than retrying: a box that will not
                    # empty still holds the user's draft, and another attempt
                    # would type on top of it.
                    print("[WhatsApp] the box would not empty — refusing to "
                          "type, because the draft would go out with it.")
                    return False
                if not _type(text):
                    continue
                if _wait_until(box_holds_exactly, 3.0):
                    landed = True
                    break

            if not landed:
                after = _composer_state(self._scan_hwnd)
                got = after[0] if after else None
                print(f"[WhatsApp] the box does not hold the message and only "
                      f"the message (it holds {got!r}) — not pressing Enter.")
                return False

            if not _await_foreground(self._focus_hwnd, limit=1.5,
                                     pids=self._pids):
                print("[WhatsApp] the keyboard left WhatsApp with the message "
                      "typed but unsent.")
                return False
            pyautogui.press("enter")
            # Sent means the box emptied. Wait for that, do not assume it.
            sent = _wait_until(
                lambda: fold(text)[:40] not in fold(
                    (_composer_state(self._scan_hwnd) or ("",))[0]), 3.0)
            if not sent:
                print("[WhatsApp] Enter did not send it — the message is "
                      "still sitting in the box.")
                return False
            return True
        except Exception as e:
            print(f"[WhatsApp] send_text failed: {e}")
            return False


    # -- incoming -----------------------------------------------------------
    def incoming(self, blocking: bool = False) -> Incoming | None:
        """An incoming call opens its own small window in the WhatsApp process,
        carrying accept and decline controls.

        This one is found by vocabulary and then by shape, the same two-step
        every other control here uses. When neither step is confident it returns
        None — announcing a call that is not happening is worse than missing one.

        THE MAIN WINDOW IS SKIPPED, AND THAT IS THE WHOLE PERFORMANCE STORY.
        A ringing call is always a *different* window from the conversation
        list, so scanning the main pair is pure waste — and not cheap waste: the
        WinUI shell walks ~6000 elements and takes around four seconds. Paying
        that once a second in a background loop would queue jobs behind each
        other on the single UI Automation apartment until every other WhatsApp
        command timed out. Skipping the two handles the app already told us
        about leaves only the windows that could actually be a call, and in the
        common case — nothing ringing — that is no scan at all.
        """
        # Exclude the handles captured when the transport attached, NOT a fresh
        # _pick_windows(). At attach time there was no call ringing, so those
        # two really are the conversation window; recomputing them here would
        # ask the question at the one moment an extra WhatsApp window exists.
        #
        # They do go stale — WhatsApp restarts, and the user is not going to
        # restart JARVIS to match. A dead handle would leave `main` empty and
        # every poll would then walk the WinUI shell, which is ~6000 elements
        # and about four seconds, once every third of a second. So they are
        # re-picked when they die, and _pick_windows takes the LARGEST window,
        # which a call popup never is.
        if not (self._scan_hwnd and _user32.IsWindow(self._scan_hwnd)):
            self._focus_hwnd, self._scan_hwnd = _pick_windows()
            self._remember_pids()
        if not self._pids:
            self._remember_pids()
        main = {h for h in (self._focus_hwnd, self._scan_hwnd) if h}
        if not main:
            return None          # nothing attached: scanning blind is worse
        for win in _whatsapp_family(self._pids):
            scan = win["hwnd"]
            if scan in main:
                continue
            # There is deliberately NO size test here any more.
            #
            # There was one — "a window bigger than the app is the app" — added
            # as a second defence against scanning the conversation window while
            # the handles above were stale. It rejected real calls twice, and
            # the reason is in the window list: when a call arrives WhatsApp
            # shrinks its main window to a 199x34 stub. The app is then smaller
            # than the call panel, every threshold measured against it is
            # nonsense, and a 562x562 ringing call gets thrown away for being
            # "too big".
            #
            # Nothing is lost by removing it. Its whole job was covering for
            # stale handles, and stale handles are now handled properly: the
            # pair is re-picked when it dies and the owning processes are
            # remembered so the call window is found even with no titled window
            # left on screen.
            data = _scan_call_window(scan)
            if not data:
                continue
            win_rect, buttons, texts = data
            if not _ringing_shape(buttons):
                continue

            width = win_rect[2] - win_rect[0]
            # The window title is the KIND of call ("Voice call"), not the
            # person making it, so the name comes off the panel itself. The
            # title is kept only as a last resort, and never when it is just the
            # app naming itself.
            # No fallback to the window title. It is never the person: it read
            # "WhatsApp" on one build and "Voice call" on another, and feeding
            # either of them into a contact search is how a message ends up sent
            # to nobody. An unknown caller is reported as unknown.
            caller = _caller_name(texts, win_rect, buttons, win["title"] or "")
            names = [n for n, _r in buttons if n]
            if not blocking:
                resolve_async(names)          # fills the cache behind us
            roles = resolve_controls(names, cached_only=not blocking)
            if _in_call(roles):
                continue
            ctype = "video" if roles.get("video") is not None else "voice"

            accept, _ai = _find_role(buttons, "accept", cached_only=True)
            decline, _di = _find_role(buttons, "decline", cached_only=True)
            # (cached_only is safe here: when blocking was asked for, the call
            #  to resolve_controls just above has already filled the cache.)
            identified = bool(accept and decline)
            return Incoming(
                caller=display_name(caller),
                call_type=ctype,
                handle=(scan, accept, decline) if identified else None,
                key=f"{scan}:{caller}:{width}",
                identified=identified,
                # Buttons AND text, because when the caller comes out wrong the
                # question is always "what else was on that window?" and the
                # answer has to be in the log rather than in another test call.
                controls=names + [f"text:{t}" for _y, t in texts],
            )
        return None

    def accept(self, call: Incoming) -> bool:
        if not call.identified or not call.handle:
            return False
        scan, accept_ctrl, _decline = call.handle
        return _invoke(scan, accept_ctrl[0], accept_ctrl[1])

    def decline(self, call: Incoming) -> bool:
        if not call.identified or not call.handle:
            return False
        scan, _accept, decline_ctrl = call.handle
        return _invoke(scan, decline_ctrl[0], decline_ctrl[1])

    def in_call(self) -> bool | None:
        # The windows incoming() looks at, with the main pair skipped for the
        # same four-second reason. A scan that timed out is not evidence that
        # there is no call, so it turns the answer into None rather than False
        # — "the call ended" is exactly the claim that must not rest on a
        # window that could not be read.
        if not (self._scan_hwnd and _user32.IsWindow(self._scan_hwnd)):
            self._focus_hwnd, self._scan_hwnd = _pick_windows()
            self._remember_pids()
        main = {h for h in (self._focus_hwnd, self._scan_hwnd) if h}
        if not main:
            return None
        unread = False
        for win in _whatsapp_family(self._pids):
            if win["hwnd"] in main:
                continue
            data = _scan_call_window(win["hwnd"])
            if not data:
                unread = True
                continue
            names = [n for n, _r in data[1] if n]
            if names and _in_call(resolve_controls(names)):
                return True
        return None if unread else False

    # -- discovery ----------------------------------------------------------
    def probe(self) -> list[str]:
        """Every button in every WhatsApp window, main one included this time.

        diagnose() and incoming() both skip the main window for speed; here the
        cost does not matter and completeness does, because the whole point is
        to find out what a ringing call publishes.
        """
        out: list[str] = []
        for win in _whatsapp_family(self._pids):
            data = _scan_call_window(win["hwnd"])
            if not data:
                continue
            win_rect, buttons, texts = data
            names = [n for n, _r in buttons if n]
            out.append(
                f'[{win["title"] or "untitled"}] top={win_rect[1]} '
                + "buttons: " + (", ".join(f"'{n}'" for n in names[:30]) or "none")
                + "  |  texts(y): " + (", ".join(f"{y}:'{t}'" for y, t in texts[:20])
                                       or "none")
                + "  |  caller-> "
                + repr(_caller_name(texts, win_rect, buttons, win["title"] or "")))
        return out

    # -- the ⚙ CHECK button -------------------------------------------------
    def diagnose(self, conf: dict) -> tuple[bool, list[str]]:
        """Everything at once, not the first thing that is wrong.

        This is what somebody has when the plugin does nothing on their machine
        and they cannot see a console. Reporting one problem at a time makes
        them fix, restart, fix, restart; the whole checklist tells them in one
        press. Each failing line carries the command or the action that
        resolves it.
        """
        lines: list[str] = []
        ok = True

        def good(msg):
            lines.append(f"  ✓ {msg}")

        def bad(msg):
            nonlocal ok
            ok = False
            lines.append(f"  ✗ {msg}")

        good(f"Windows ({platform.release()})")

        usable, why = self.usable()
        if usable:
            good("pyautogui, pyperclip, pywinauto, comtypes")
        else:
            bad(why)

        windows = _enum_windows()
        shells = [w for w in windows if "chrome_widgetwin" not in w["class"].lower()]
        views = [w for w in windows if "chrome_widgetwin" in w["class"].lower()]
        if not windows:
            bad("WhatsApp is not running — open the desktop app, click a chat, "
                "then press CHECK again")
            return ok, lines
        if not shells and views:
            # A browser tab titled "WhatsApp" looks exactly like this. It may
            # work, but it is a different tree and a much slower one.
            bad("only a browser window called WhatsApp was found. Install the "
                "WhatsApp DESKTOP app, or set the transport to 'web'")
        else:
            good("WhatsApp desktop app found")

        self._focus_hwnd, self._scan_hwnd = _pick_windows()
        if not self._scan_hwnd:
            bad("could not attach to the WhatsApp window")
            return ok, lines

        chat = self.current_chat()
        if not chat:
            bad("no conversation is open (or the header could not be read) — "
                "click any normal chat and press CHECK again")
            return ok, lines
        good(f"chat open: {chat.name()}")

        voice, how_v = _pick_call_button(chat.controls, "voice")
        video, _how = _pick_call_button(chat.controls, "video")
        if not (voice and video):
            found = ", ".join(n for n, _r in chat.controls) or "none"
            bad(f"no call buttons in this chat (header offers: {found}). "
                f"Channels and the notes-to-self chat look like this — try a "
                f"normal chat")
        else:
            how = "by name" if how_v == "name" else "by position — unfamiliar language"
            good(f"call buttons: '{video[0]}' / '{voice[0]}' ({how})")
        return ok, lines


# ═════════════════════════════════════════════════════════════════════════════
#  WEB — Playwright against web.whatsapp.com
#
#  The only transport that works on all three systems, and the only one that
#  works on Linux at all, since WhatsApp ships no official Linux client.
#
#  READ THE PAGE THE SAME WAY THE DESKTOP READS THE WINDOW
#      The temptation with a browser is to hardcode CSS selectors, and that is
#      exactly what rots: WhatsApp reissues its class names and data-testids
#      without warning. So the JavaScript below does not look for named
#      controls. It returns *every button in the header with its accessible
#      name and its rectangle* — the same shape _scan() returns on Windows —
#      and the Python side applies the same ~20-language vocabulary, then the
#      same left-to-right layout fallback. One rule set, two very different
#      trees underneath it.
#
#  A REAL BROWSER, NOT A HEADLESS ONE
#      Default is headed. A call needs a real audio device, the first login
#      needs a QR code on screen, and WhatsApp treats a headless browser with
#      suspicion. The profile is persistent, so the QR is scanned once.
#
#  ONE THREAD OWNS PLAYWRIGHT
#      The sync API is not thread-safe, so — exactly like the UIA apartment
#      above — a single long-lived worker owns the browser and every call is a
#      job submitted to it under a time budget.
# ═════════════════════════════════════════════════════════════════════════════
WEB_PROFILE = BASE_DIR / "config" / "whatsapp_web"
WEB_URL = "https://web.whatsapp.com/"

# Chromium-family browsers by the commands their packages install, the
# distribution's packages before a Snap: a Snap browser may not open a profile
# in a hidden folder (where WEB_PROFILE lives). Firefox is not one of them:
# Playwright drives only its own patched build of it.
_CHROMIUM_COMMANDS = ("google-chrome", "google-chrome-stable", "microsoft-edge", "microsoft-edge-stable",
                      "brave-browser", "brave", "vivaldi-stable", "vivaldi", "chromium", "chromium-browser")


def _installed_chromium() -> str | None:
    """The path of a Chromium-family browser installed here, or None."""
    import shutil
    found = [p for p in (shutil.which(c) for c in _CHROMIUM_COMMANDS) if p]
    unsnapped = [p for p in found if not _is_snap(p)]
    return (unsnapped or found or [None])[0]


def _is_snap(path: str) -> bool:
    """A command that runs a Snap (/snap/bin/x, or a link to snap itself)."""
    real = os.path.realpath(path)
    return real.startswith("/snap/") or os.path.basename(real) == "snap"
_WEB_BUDGET = 20.0

# Returns the header exactly as _scan()/_header() do on Windows: a title and a
# left-to-right cluster of (name, rect). Nothing is matched by class name or
# testid — only by role and accessible name, which is what WhatsApp must keep
# publishing for its own screen-reader support.
_JS_HEADER = r"""
() => {
  const header = document.querySelector('#main header') ||
                 document.querySelector('header[data-testid="conversation-header"]');
  if (!header) return null;
  const acc = (el) => (el.getAttribute('aria-label') ||
                       el.getAttribute('title') ||
                       el.getAttribute('data-testid') ||
                       (el.innerText || '').trim() || '');
  const r = (el) => { const b = el.getBoundingClientRect();
                      return [Math.round(b.left), Math.round(b.top),
                              Math.round(b.right), Math.round(b.bottom)]; };
  // The conversation title is the widest titled element in the header, the
  // same property the desktop header is read by.
  let title = '';
  const titled = header.querySelectorAll('span[title], [role="button"] span, h1, h2');
  let widest = 0;
  titled.forEach(el => { const b = el.getBoundingClientRect();
                         if (b.width > widest && (el.getAttribute('title') || el.innerText)) {
                           widest = b.width;
                           title = el.getAttribute('title') || el.innerText.trim(); } });
  const buttons = [];
  header.querySelectorAll('button, [role="button"]').forEach(el => {
    const b = el.getBoundingClientRect();
    if (b.width < 4 || b.height < 4) return;
    const name = acc(el);
    if (!name) return;
    buttons.push([name, r(el)]);
  });
  buttons.sort((a, b) => a[1][0] - b[1][0]);
  return {title: title, buttons: buttons};
}
"""

# An incoming call is a panel that is NOT the header and carries two controls
# whose accessible names read as accept and decline. Searching the whole
# document rather than a known container is deliberate: WhatsApp has moved this
# panel between a toast, a modal and a docked strip, and all three match this.
_JS_INCOMING = r"""
() => {
  const acc = (el) => (el.getAttribute('aria-label') ||
                       el.getAttribute('title') ||
                       el.getAttribute('data-testid') || '');
  const r = (el) => { const b = el.getBoundingClientRect();
                      return [Math.round(b.left), Math.round(b.top),
                              Math.round(b.right), Math.round(b.bottom)]; };
  const out = [];
  document.querySelectorAll('button, [role="button"]').forEach(el => {
    const b = el.getBoundingClientRect();
    if (b.width < 4 || b.height < 4) return;
    if (el.closest('#main header')) return;      // the chat header, not a call
    const name = acc(el);
    if (name) out.push([name, r(el)]);
  });
  // Whatever text the panel shows — the caller's name is in there somewhere.
  let text = '';
  const panel = document.querySelector('[data-testid*="call"], [class*="incoming"], [role="dialog"]');
  if (panel) text = (panel.innerText || '').trim().split('\n').filter(Boolean).slice(0, 4).join(' | ');
  return {buttons: out, text: text};
}
"""

_JS_TITLE_ONLY = r"""
() => {
  const h = document.querySelector('#main header');
  if (!h) return '';
  const el = h.querySelector('span[title]');
  return el ? (el.getAttribute('title') || el.innerText || '') : '';
}
"""


class _PlaywrightWorker:
    """One thread, one Playwright, one persistent browser context.

    Same reasoning as the UIA apartment: the sync Playwright API belongs to the
    thread that created it, so everything that touches the browser is a job
    submitted here and every job runs under a deadline. A hung page then fails
    one command instead of wedging the assistant.
    """

    def __init__(self):
        self._jobs: "queue.Queue" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._pw = None
        self._context = None
        self._page = None
        self._started = False
        self._error = ""

    # -- plumbing -----------------------------------------------------------
    def _loop(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                break
            fn, fut = job
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                fut.set_result(fn())
            except BaseException as exc:      # noqa: BLE001 - carried to caller
                fut.set_exception(exc)

    def submit(self, fn, seconds: float = _WEB_BUDGET, default=None):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, daemon=True,
                                                name="whatsapp-web")
                self._thread.start()
            fut: concurrent.futures.Future = concurrent.futures.Future()
            self._jobs.put((fn, fut))
        try:
            return fut.result(timeout=seconds)
        except concurrent.futures.TimeoutError:
            print("[WhatsAppWeb] the browser exceeded its time budget.")
            return default
        except Exception as e:
            print(f"[WhatsAppWeb] {type(e).__name__}: {e}")
            return default

    # -- browser ------------------------------------------------------------
    def _start(self, headless: bool) -> bool:
        """Runs ON the worker thread. Opens the persistent profile once."""
        if self._started and self._page is not None:
            try:
                if not self._page.is_closed():
                    return True
            except Exception:
                pass
            self._started = False

        from playwright.sync_api import sync_playwright

        WEB_PROFILE.mkdir(parents=True, exist_ok=True)
        if self._pw is None:
            self._pw = sync_playwright().start()

        # The computer's own Chromium-family browser: the installed program
        # carries no browser of Playwright's own (there is no "playwright
        # install" in it), so without one this never started.
        exe = _installed_chromium()
        if exe is None:
            raise RuntimeError("WhatsApp Web needs Google Chrome, Chromium, Microsoft Edge, "
                               "Brave or Vivaldi installed on this computer")
        # A real audio path matters here: this browser is where a call is
        # answered, so the microphone and camera are granted up front rather
        # than through a permission prompt nobody is watching for.
        self._context = self._pw.chromium.launch_persistent_context(
            str(WEB_PROFILE),
            executable_path=exe,
            headless=headless,
            viewport={"width": 1280, "height": 860},
            permissions=["microphone", "camera"],
            args=["--disable-blink-features=AutomationControlled",
                  "--autoplay-policy=no-user-gesture-required"],
        )
        self._page = self._context.pages[0] if self._context.pages \
            else self._context.new_page()
        if WEB_URL.rstrip("/") not in (self._page.url or ""):
            self._page.goto(WEB_URL, wait_until="domcontentloaded", timeout=60000)
        self._started = True
        return True

    def ensure(self, headless: bool) -> tuple[bool, str]:
        def work():
            try:
                self._start(headless)
                return True, ""
            except Exception as e:
                return False, f"the WhatsApp Web browser would not start: {e}"
        return self.submit(work, 90.0, default=(False, "the browser took too long to start"))

    def logged_in(self) -> bool:
        def work():
            if not self._page:
                return False
            try:
                return bool(self._page.query_selector("#pane-side"))
            except Exception:
                return False
        return bool(self.submit(work, 10.0, default=False))

    def eval(self, script: str, seconds: float = _WEB_BUDGET, default=None):
        def work():
            if not self._page:
                return default
            return self._page.evaluate(script)
        return self.submit(work, seconds, default=default)

    def click_rect(self, rect) -> bool:
        """Click the middle of a rectangle the page itself reported. Going
        through coordinates rather than a stored handle is deliberate: a handle
        goes stale when WhatsApp re-renders, a position does not."""
        def work():
            if not self._page:
                return False
            self._page.mouse.click((rect[0] + rect[2]) / 2,
                                   (rect[1] + rect[3]) / 2)
            return True
        return bool(self.submit(work, 10.0, default=False))

    def close(self) -> None:
        def work():
            for obj, name in ((self._context, "context"), (self._pw, "playwright")):
                try:
                    obj.close() if name == "context" else obj.stop()
                except Exception:
                    pass
            self._context = self._page = self._pw = None
            self._started = False
            return True
        try:
            self.submit(work, 15.0)
        except Exception:
            pass

    @property
    def page(self):
        return self._page


_web_worker = _PlaywrightWorker()


class WebTransport(Transport):
    """WhatsApp Web driven through Playwright. Identical on all three systems."""

    name = "web"
    label = "WhatsApp Web (browser)"

    def __init__(self):
        self._w = _web_worker

    # -- availability -------------------------------------------------------
    @classmethod
    def usable(cls) -> tuple[bool, str]:
        try:
            import playwright  # noqa: F401
        except Exception:
            return False, "playwright is not installed — run: pip install playwright"
        return True, ""

    # -- lifecycle ----------------------------------------------------------
    def ensure_ready(self, conf: dict) -> tuple[bool, str]:
        ok, why = self._w.ensure(headless=False)
        if not ok:
            return False, why
        if not self._w.logged_in():
            return False, ("WhatsApp Web is not linked yet — a browser window is "
                           "open with a QR code; scan it from the phone "
                           "(WhatsApp → Settings → Linked devices) and ask again")
        return True, ""

    def close(self) -> None:
        self._w.close()

    # -- conversations ------------------------------------------------------
    def current_chat(self) -> Chat | None:
        data = self._w.eval(_JS_HEADER)
        if not data or not data.get("title"):
            return None
        note_title(data["title"])
        return Chat(raw_title=data["title"],
                    controls=[(n, tuple(r)) for n, r in data.get("buttons", [])],
                    handle="web")

    def open_chat(self, contact: str, conf: dict, before: str = "") -> Chat | None:
        """Type into the chat-list search box and open the first result.

        The search box is found by role rather than by testid, and the result is
        verified afterwards exactly as on Windows — WhatsApp Web's result list
        reorders while it filters, so pressing Enter early opens the wrong chat
        here too.
        """
        def work():
            page = self._w.page
            if not page:
                return False
            box = None
            for sel in ('div[contenteditable="true"][data-tab="3"]',
                        '#side div[contenteditable="true"]',
                        'div[role="textbox"][aria-label]'):
                box = page.query_selector(sel)
                if box:
                    break
            if not box:
                return False
            box.click()
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            # fill() rather than type(): it survives ş, ü, я and 漢, which is the
            # same reason the desktop transport goes through the clipboard.
            box.type(contact, delay=25)
            page.wait_for_timeout(700)
            page.keyboard.press("Enter")
            return True
        if not self._w.submit(work, 25.0, default=False):
            return None

        deadline = time.monotonic() + max(2.0, conf.get("search_wait", 4.0))
        chat = None
        while time.monotonic() < deadline:
            time.sleep(0.2)
            chat = self.current_chat()
            if chat and (names_match(contact, chat.raw_title)
                         or chat.raw_title != before):
                return chat
        return chat

    def focus(self) -> None:
        def work():
            if self._w.page:
                self._w.page.bring_to_front()
            return True
        self._w.submit(work, 8.0)

    # -- outgoing -----------------------------------------------------------
    def press_call(self, chat: Chat, call_type: str) -> tuple[bool, str]:
        target, how = _pick_call_button(chat.controls, call_type)
        if not target:
            return False, "no-button"
        if how == "position":
            print(f"[WhatsAppWeb] '{target[0]}' matched by layout, not by name.")
        return (True, how) if self._w.click_rect(target[1]) else (False, "press-failed")

    def hangup(self) -> bool:
        data = self._w.eval(_JS_INCOMING, 10.0)
        buttons = [(n, tuple(r)) for n, r in (data or {}).get("buttons", [])]
        found, _i = _find_role(buttons, "end")
        return self._w.click_rect(found[1]) if found else False

    def send_text(self, chat: Chat, text: str) -> bool:
        def work():
            page = self._w.page
            if not page:
                return False
            box = None
            for sel in ('#main div[contenteditable="true"][data-tab="10"]',
                        '#main footer div[contenteditable="true"]',
                        '#main div[role="textbox"]'):
                box = page.query_selector(sel)
                if box:
                    break
            if not box:
                return False
            box.click()
            box.type(text, delay=15)
            page.keyboard.press("Enter")
            return True
        return bool(self._w.submit(work, 25.0, default=False))

    # -- incoming -----------------------------------------------------------
    def incoming(self, blocking: bool = False) -> Incoming | None:
        data = self._w.eval(_JS_INCOMING, 8.0)
        if not data:
            return None
        buttons = [(n, tuple(r)) for n, r in data.get("buttons", [])]
        if not _ringing_shape(buttons):
            return None
        text = (data.get("text") or "").strip()
        caller = text.split(" | ")[0] if text else ""
        labels = [n for n, _r in buttons if n]
        if not blocking:
            resolve_async(labels)
        roles = resolve_controls(labels, cached_only=not blocking)
        if _in_call(roles):
            return None
        ctype = "video" if roles.get("video") is not None else "voice"

        accept, _ai = _find_role(buttons, "accept", cached_only=True)
        decline, _di = _find_role(buttons, "decline", cached_only=True)
        identified = bool(accept and decline)
        return Incoming(caller=caller, call_type=ctype,
                        handle=(accept, decline) if identified else None,
                        key=f"web:{caller}:{ctype}",
                        identified=identified,
                        controls=[n for n, _r in buttons if n])

    def accept(self, call: Incoming) -> bool:
        if not call.identified or not call.handle:
            return False
        accept_ctrl, _decline = call.handle
        return self._w.click_rect(accept_ctrl[1])

    def decline(self, call: Incoming) -> bool:
        if not call.identified or not call.handle:
            return False
        _accept, decline_ctrl = call.handle
        return self._w.click_rect(decline_ctrl[1])

    def in_call(self) -> bool | None:
        data = self._w.eval(_JS_INCOMING, 8.0)
        if data is None:
            return None
        buttons = [(n, tuple(r)) for n, r in data.get("buttons", [])]
        if not _ringing_shape(buttons):
            return False
        return _in_call(resolve_controls([n for n, _r in buttons if n]))

    # -- discovery ----------------------------------------------------------
    def probe(self) -> list[str]:
        data = self._w.eval(_JS_INCOMING, 8.0)
        if not data:
            return []
        names = [n for n, _r in data.get("buttons", []) if n]
        out = ["[page] " + (", ".join(f"'{n}'" for n in names[:40])
                            or "no named buttons")]
        if data.get("text"):
            out.append("[panel text] " + data["text"])
        return out

    # -- the ⚙ CHECK button -------------------------------------------------
    def diagnose(self, conf: dict) -> tuple[bool, list[str]]:
        lines: list[str] = []
        ok = True

        def good(msg):
            lines.append(f"  ✓ {msg}")

        def bad(msg):
            nonlocal ok
            ok = False
            lines.append(f"  ✗ {msg}")

        usable, why = self.usable()
        if not usable:
            bad(why)
            return ok, lines
        good("playwright installed")

        started, why = self._w.ensure(headless=False)
        if not started:
            bad(why)
            return ok, lines
        good(f"browser profile: {WEB_PROFILE}")

        if not self._w.logged_in():
            bad("not linked yet — scan the QR code in the browser window that "
                "just opened (phone → WhatsApp → Settings → Linked devices), "
                "then press CHECK again")
            return ok, lines
        good("linked to your WhatsApp account")

        chat = self.current_chat()
        if not chat:
            bad("no conversation is open — click any normal chat in that "
                "browser window and press CHECK again")
            return ok, lines
        good(f"chat open: {chat.name()}")

        voice, how_v = _pick_call_button(chat.controls, "voice")
        video, _how = _pick_call_button(chat.controls, "video")
        if not (voice and video):
            found = ", ".join(n for n, _r in chat.controls) or "none"
            bad(f"no call buttons found. The header published: {found}")
        else:
            how = "by name" if how_v == "name" else "by position — unfamiliar language"
            good(f"call buttons: '{video[0]}' / '{voice[0]}' ({how})")

        # The diagnostic dump. This is what makes an unfamiliar WhatsApp build
        # fixable without guessing: it prints what the page actually published,
        # so a user whose header reads differently can send that line back.
        lines.append("  · header controls: " +
                     (", ".join(f"'{n}'" for n, _r in chat.controls) or "none"))
        return ok, lines


# ═════════════════════════════════════════════════════════════════════════════
#  macOS DESKTOP — the Accessibility API
#
#  AXUIElement is the structural twin of Windows UI Automation: the same tree of
#  roles, the same accessible names, and AXPress is Invoke under another name.
#  So this backend is the Windows one with different verbs — it reads the header
#  band by geometry, applies the same vocabulary, and presses the control rather
#  than a pixel.
#
#  TWO THINGS THE USER MUST DO, AND THEY ARE TOLD BOTH
#      pip install pyobjc-framework-ApplicationServices pyobjc-framework-Cocoa
#      System Settings → Privacy & Security → Accessibility → allow whatever
#      runs JARVIS (Terminal, iTerm, the app bundle).
#  Without the permission the API returns empty trees rather than an error, and
#  a plugin that silently sees nothing is the worst possible failure. diagnose()
#  checks the permission explicitly and says which of the two is missing.
#
#  MARKED BETA ON PURPOSE. It was written against Apple's documented API but has
#  not been run on a Mac; the CHECK button prints what the tree actually
#  published so the first person to run it can report something useful.
# ═════════════════════════════════════════════════════════════════════════════
_AX_BUDGET = 8.0


def _ax_mods():
    """The pyobjc symbols, imported at the point of use so importing this file
    costs nothing on Windows and Linux."""
    from ApplicationServices import (              # type: ignore
        AXUIElementCreateApplication, AXUIElementCopyAttributeValue,
        AXUIElementPerformAction, AXIsProcessTrustedWithOptions,
        AXValueGetValue, kAXValueCGPointType, kAXValueCGSizeType,
    )
    from AppKit import NSWorkspace                 # type: ignore
    return dict(create=AXUIElementCreateApplication,
                attr=AXUIElementCopyAttributeValue,
                press=AXUIElementPerformAction,
                trusted=AXIsProcessTrustedWithOptions,
                value_get=AXValueGetValue,
                point_t=kAXValueCGPointType,
                size_t=kAXValueCGSizeType,
                workspace=NSWorkspace)


def _ax_get(ax, el, name):
    try:
        err, val = ax["attr"](el, name, None)
        return val if err == 0 else None
    except Exception:
        return None


def _ax_rect(ax, el) -> tuple | None:
    """(left, top, right, bottom) in screen points, or None."""
    pos = _ax_get(ax, el, "AXPosition")
    size = _ax_get(ax, el, "AXSize")
    if pos is None or size is None:
        return None
    try:
        import AppKit  # noqa: F401
        ok_p, p = ax["value_get"](pos, ax["point_t"], None)
        ok_s, s = ax["value_get"](size, ax["size_t"], None)
        if not (ok_p and ok_s):
            return None
        return (int(p.x), int(p.y), int(p.x + s.width), int(p.y + s.height))
    except Exception:
        return None


def _ax_name(ax, el) -> str:
    for attr in ("AXTitle", "AXDescription", "AXLabel", "AXHelp", "AXValue"):
        val = _ax_get(ax, el, attr)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _ax_walk(ax, el, out: list, depth: int = 0, limit: int = 4000) -> None:
    """Collect every AXButton in the subtree, with its name and rectangle. The
    node budget is the same defence the Windows scan has: a tree that grew
    unexpectedly costs a bounded amount of time, never an unbounded one."""
    if len(out) >= limit or depth > 40:
        return
    role = _ax_get(ax, el, "AXRole") or ""
    if role in ("AXButton", "AXMenuButton", "AXRadioButton"):
        rect = _ax_rect(ax, el)
        name = _ax_name(ax, el)
        if rect and name and rect[2] > rect[0] and rect[3] > rect[1]:
            out.append((el, name, rect))
    for child in (_ax_get(ax, el, "AXChildren") or []):
        _ax_walk(ax, child, out, depth + 1, limit)


class MacDesktop(Transport):
    """The WhatsApp desktop app on macOS, driven through the Accessibility API."""

    name = "desktop-macos"
    label = "WhatsApp desktop app (macOS — beta)"

    def __init__(self):
        self._pid: int | None = None

    # -- availability -------------------------------------------------------
    @classmethod
    def usable(cls) -> tuple[bool, str]:
        if not IS_MACOS:
            return False, "this is not macOS"
        try:
            _ax_mods()
        except Exception:
            return False, ("pyobjc is not installed — run: pip install "
                           "pyobjc-framework-ApplicationServices "
                           "pyobjc-framework-Cocoa")
        if not HAVE_PYAUTOGUI:
            return False, "missing pyautogui — run: pip install pyautogui"
        try:
            ax = _ax_mods()
            if not ax["trusted"]({}):
                return False, ("The assistant does not have Accessibility permission — "
                               "System Settings → Privacy & Security → "
                               "Accessibility, add the app that runs the assistant, "
                               "then restart it")
        except Exception:
            pass
        return True, ""

    # -- lifecycle ----------------------------------------------------------
    def _find_pid(self, app_name: str) -> int | None:
        try:
            ax = _ax_mods()
            wanted = fold(app_name or "WhatsApp")
            for app in ax["workspace"].sharedWorkspace().runningApplications():
                name = str(app.localizedName() or "")
                if wanted and wanted in fold(name):
                    return int(app.processIdentifier())
        except Exception as e:
            print(f"[WhatsAppMac] could not list applications: {e}")
        return None

    def ensure_ready(self, conf: dict) -> tuple[bool, str]:
        app_name = conf.get("app_name") or "WhatsApp"
        self._pid = self._find_pid(app_name)
        if self._pid:
            return True, ""
        try:
            subprocess.run(["open", "-a", app_name], capture_output=True,
                           timeout=15)
        except Exception as e:
            return False, f"could not open WhatsApp: {e}"
        deadline = time.monotonic() + conf.get("launch_wait", 8.0)
        while time.monotonic() < deadline:
            time.sleep(0.5)
            self._pid = self._find_pid(app_name)
            if self._pid:
                time.sleep(1.5)               # let the interface paint
                return True, ""
        return False, ("WhatsApp would not open — it should be started by hand "
                       "once and then asked again")

    def close(self) -> None:
        self._pid = None

    # -- tree ---------------------------------------------------------------
    def _buttons(self) -> tuple[tuple | None, list]:
        """(window_rect, [(element, name, rect), ...]) for the front window."""
        if not self._pid:
            return None, []
        try:
            ax = _ax_mods()
            app = ax["create"](self._pid)
            windows = _ax_get(ax, app, "AXWindows") or []
            if not windows:
                return None, []
            win = windows[0]
            rect = _ax_rect(ax, win)
            found: list = []
            _ax_walk(ax, win, found)
            return rect, found
        except Exception as e:
            print(f"[WhatsAppMac] tree read failed: {e}")
            return None, []

    def _press(self, el) -> bool:
        try:
            ax = _ax_mods()
            return ax["press"](el, "AXPress") == 0
        except Exception as e:
            print(f"[WhatsAppMac] AXPress failed: {e}")
            return False

    # -- conversations ------------------------------------------------------
    def current_chat(self) -> Chat | None:
        win_rect, found = self._buttons()
        if not win_rect or not found:
            return None
        header = _header(win_rect, [(n, r) for _el, n, r in found])
        if not header:
            return None
        note_title(header["title"])
        by_rect = {r: el for el, _n, r in found}
        controls = [(n, r) for n, r in header["cluster"]]
        return Chat(raw_title=header["title"], controls=controls,
                    handle=by_rect)

    def open_chat(self, contact: str, conf: dict, before: str = "") -> Chat | None:
        if not HAVE_PYAUTOGUI:
            return None
        try:
            subprocess.run(["open", "-a", conf.get("app_name") or "WhatsApp"],
                           capture_output=True, timeout=10)
            time.sleep(0.4)
            pyautogui.hotkey("command", "f")
            time.sleep(0.45)
            pyautogui.hotkey("command", "a")
            time.sleep(0.05)
            pyautogui.press("delete")
            time.sleep(0.08)
            if HAVE_PYPERCLIP:
                pyperclip.copy(contact)
                time.sleep(0.07)
                pyautogui.hotkey("command", "v")
            else:
                pyautogui.write(contact, interval=0.03)
            time.sleep(0.55)
            pyautogui.press("enter")
        except Exception as e:
            print(f"[WhatsAppMac] search failed: {e}")
            return None

        deadline = time.monotonic() + conf.get("search_wait", 4.0)
        chat = None
        while time.monotonic() < deadline:
            time.sleep(0.2)
            chat = self.current_chat()
            if chat and (names_match(contact, chat.raw_title)
                         or chat.raw_title != before):
                return chat
        return chat

    def focus(self) -> None:
        try:
            subprocess.run(["open", "-a", cfg().get("app_name") or "WhatsApp"],
                           capture_output=True, timeout=10)
        except Exception:
            pass

    # -- outgoing -----------------------------------------------------------
    def press_call(self, chat: Chat, call_type: str) -> tuple[bool, str]:
        target, how = _pick_call_button(chat.controls, call_type)
        if not target:
            return False, "no-button"
        el = (chat.handle or {}).get(target[1])
        if el is None:
            return False, "press-failed"
        return (True, how) if self._press(el) else (False, "press-failed")

    def hangup(self) -> bool:
        _rect, found = self._buttons()
        hit, idx = _find_role([(n, r) for _el, n, r in found], "end")
        return self._press(found[idx][0]) if hit else False

    def send_text(self, chat: Chat, text: str) -> bool:
        if not HAVE_PYAUTOGUI:
            return False
        try:
            pyautogui.press("escape")
            time.sleep(0.2)
            if HAVE_PYPERCLIP:
                pyperclip.copy(text)
                time.sleep(0.07)
                pyautogui.hotkey("command", "v")
            else:
                pyautogui.write(text, interval=0.03)
            time.sleep(0.2)
            pyautogui.press("enter")
            return True
        except Exception as e:
            print(f"[WhatsAppMac] send_text failed: {e}")
            return False

    # -- incoming -----------------------------------------------------------
    def incoming(self, blocking: bool = False) -> Incoming | None:
        _rect, found = self._buttons()
        if not _ringing_shape([(n, r) for _el, n, r in found]):
            return None
        pairs = [(n, r) for _el, n, r in found]
        labels = [n for n, _r in pairs if n]
        if not blocking:
            resolve_async(labels)
        roles = resolve_controls(labels, cached_only=not blocking)
        if _in_call(roles):
            return None
        _a, ai = _find_role(pairs, "accept", cached_only=True)
        _d, di = _find_role(pairs, "decline", cached_only=True)
        identified = ai is not None and di is not None
        ctype = "video" if roles.get("video") is not None else "voice"
        caller = ""
        try:
            ax = _ax_mods()
            app = ax["create"](self._pid)
            windows = _ax_get(ax, app, "AXWindows") or []
            for win in windows:
                title = _ax_get(ax, win, "AXTitle") or ""
                if title and not _WA_TITLE.search(str(title)):
                    caller = str(title)
                    break
        except Exception:
            pass
        return Incoming(caller=display_name(caller), call_type=ctype,
                        handle=(found[ai][0], found[di][0]) if identified else None,
                        key=f"mac:{caller}:{ctype}",
                        identified=identified,
                        controls=[n for _el, n, _r in found if n])

    def accept(self, call: Incoming) -> bool:
        if not call.identified or not call.handle:
            return False
        return self._press(call.handle[0])

    def decline(self, call: Incoming) -> bool:
        if not call.identified or not call.handle:
            return False
        return self._press(call.handle[1])

    def in_call(self) -> bool | None:
        rect, found = self._buttons()
        if rect is None:
            return None
        pairs = [(n, r) for _el, n, r in found]
        if not _ringing_shape(pairs):
            return False
        return _in_call(resolve_controls([n for n, _r in pairs if n]))

    # -- discovery ----------------------------------------------------------
    def probe(self) -> list[str]:
        _rect, found = self._buttons()
        names = [n for _el, n, _r in found if n]
        return ["[front window] " + (", ".join(f"'{n}'" for n in names[:40])
                                     or "no named buttons")]

    # -- the ⚙ CHECK button -------------------------------------------------
    def diagnose(self, conf: dict) -> tuple[bool, list[str]]:
        lines: list[str] = []
        ok = True

        def good(msg):
            lines.append(f"  ✓ {msg}")

        def bad(msg):
            nonlocal ok
            ok = False
            lines.append(f"  ✗ {msg}")

        good(f"macOS ({platform.mac_ver()[0] or platform.release()}) — beta")

        usable, why = self.usable()
        if not usable:
            bad(why)
            return ok, lines
        good("pyobjc present, Accessibility permission granted")

        started, why = self.ensure_ready(conf)
        if not started:
            bad(why)
            return ok, lines
        good("WhatsApp desktop app found")

        chat = self.current_chat()
        if not chat:
            _rect, found = self._buttons()
            bad("no conversation header could be read — click any normal chat "
                "and press CHECK again")
            # The dump matters more here than anywhere: this backend has never
            # been run on real hardware, so the first person to try it should be
            # able to send back exactly what their tree published.
            names = ", ".join(f"'{n}'" for _el, n, _r in found[:25]) or "none"
            lines.append(f"  · buttons the tree published: {names}")
            return ok, lines
        good(f"chat open: {chat.name()}")

        voice, how_v = _pick_call_button(chat.controls, "voice")
        video, _how = _pick_call_button(chat.controls, "video")
        if not (voice and video):
            found = ", ".join(n for n, _r in chat.controls) or "none"
            bad(f"no call buttons found. The header published: {found}")
        else:
            how = "by name" if how_v == "name" else "by position — unfamiliar language"
            good(f"call buttons: '{video[0]}' / '{voice[0]}' ({how})")
        lines.append("  · header controls: " +
                     (", ".join(f"'{n}'" for n, _r in chat.controls) or "none"))
        return ok, lines
