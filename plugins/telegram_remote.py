"""
telegram_remote.py — drive JARVIS from anywhere, over Telegram.

WHY THIS EXISTS
    The Remote Dashboard already relays typed commands into the Live session
    (dashboard/server.py -> main.py _process_dashboard_commands), but it is a
    LAN thing: it binds a local IP, generates a self-signed certificate and even
    opens a firewall rule. Outside the house it is unreachable.

    Telegram solves exactly the missing half. This plugin LONG-POLLS Telegram's
    servers (getUpdates) — so there is no inbound port, no port forwarding, no
    NAT traversal and no certificate warning. The only socket ever opened is an
    outbound HTTPS connection to api.telegram.org.

    No new dependency: `requests` is already in requirements.txt.

HOW A MESSAGE BECOMES A COMMAND
    Telegram message -> allowlist + freshness + rate checks -> player.on_text_command()
    -> main.py::_on_text_command -> session.send_client_content(). That is the
    same door the desk text box uses, so a remote command behaves identically to
    one typed at the machine — including every tool, guard and undo entry.

NO KEYWORD, IN ANY LANGUAGE, DECIDES ANYTHING HERE
    This project is used in dozens of languages, so the dispatch path contains
    no natural-language matching at all. There are exactly three ways in, and
    the third is really the first with a microphone in front of it:

      1. Free text. Anything that is not a slash command goes STRAIGHT to the
         model, in whatever language it was written. The model reads the English
         JSON schema below, decides what was asked for, and fills in `action`.
         "ekranı gönder", "schick mir den Bildschirm" and "screenshot please"
         all arrive at action="screenshot" without this file knowing a word of
         any of them.

      2. Slash commands. Language-neutral protocol tokens (/screen, /sys) that
         skip the model entirely when you want the answer in one second rather
         than one round trip. They are not words; they are buttons that happen
         to be spelled.

      3. A voice note. Turned into words first and then treated exactly as if
         they had been typed — including being shown back to you before anything
         acts on them, because recognition is never perfect and a remote command
         heard wrong and run in silence is the one failure this could not
         survive. Which recogniser does it is NOT a new setting: it follows the
         one already made in ENGINE & PROVIDERS, so a person who moved to a
         local pipeline to keep their audio on the machine does not have it
         posted to Google by a plugin they added for something else.

    The first two are generated from ONE table — _OPS, below — which is also
    what builds the tool declaration, the Telegram command menu, the phone
    keyboard and /help. Four consumers, one declaration, nothing written twice.

IT ONLY LISTENS WHEN YOU SAY SO
    Plugins have no startup hook, and a remote-control channel is the last thing
    that should open because a file was in a folder. "Start the Telegram remote"
    starts it, the same way water_reminder and whatsapp_guard start their loops.

    There is a switch that changes this — START LISTENING WHEN JARVIS LAUNCHES —
    and it is OFF until somebody at the computer turns it on. That distinction
    is the whole of it: the default is still that downloading this file gives
    nobody a listening bot, and turning it on is a deliberate act performed at
    the machine by the person who owns it. Nothing reachable from Telegram can
    flip it, so a phone still cannot arrange for its own way back in.

SECURITY — WHAT ACTUALLY PROTECTS YOU
    1. Allowlist.       Only chat IDs you approved are obeyed. Everything else is
                        dropped in silence — a stranger who guesses the bot never
                        even learns it is alive. The bot's command menu is
                        registered per approved chat, so an unapproved one sees
                        no menu either: the bot looks like it does nothing.
    2. Pairing window.  Pairing is not permanently open. It arms for 10 minutes,
                        either at start when no chat is approved yet, or when you
                        ask for it at the desk — and it closes on the first
                        success, on the clock, or after 10 wrong codes anywhere.
                        The budget is GLOBAL: a limit counted per chat is no
                        limit at all when the attacker picks a new chat each try.
    3. Pairing code.    An unknown chat becomes allowed ONLY by sending the code
                        you set, compared in constant time on the UTF-8 bytes and
                        consumed on first use. Case and accents are reduced first
                        — phone keyboards capitalise, and a silent mismatch on a
                        setup step is unfixable by the person hitting it.
    4. Rate limit.      20 messages per minute per chat, applied BEFORE the
                        allowlist is consulted, so pairing attempts are counted
                        too. (They were not, in the first version, which made the
                        pairing code the one wall with no limiter behind it.)
    5. Private only.    Groups, channels, bots and edited messages are refused.
                        An edited old message can never re-fire a command.
    6. Start floor.     Nothing sent before the bridge started is ever executed —
                        compared against Telegram's own timestamps, so it holds
                        even when the backlog drop below fails. That mattered:
                        the drop is one HTTP call, and a failed call used to
                        leave the whole queue eligible without saying so.
    7. Backlog drop.    Belt to the floor's braces: the webhook is deleted with
                        drop_pending_updates and the update offset jumps past
                        everything already queued.
    8. Freshness.       A message older than 2 minutes is refused — measured
                        against TELEGRAM's clock, learned from the Date header of
                        short requests, because a computer whose clock is wrong
                        would otherwise reject everything.
    9. Turn-bound replies. Answers go back only to the chat that asked, and only
                        for the turn it triggered — the window closes on quiet,
                        not on a stopwatch. Your desk conversation, a morning
                        briefing or a background alert that happens to land a
                        minute later is NOT mirrored to your phone.
   10. The phone cannot widen its own permissions. The screen, the camera, the
                        folder files may be written to and the folder files may
                        be read from are each shut until somebody at the COMPUTER
                        opens them, and no message from Telegram can open any of
                        them — not even by asking the model, which is checked
                        separately on that path. Turning things OFF works from
                        anywhere: /stop and /panic need no permission at all.
                        The asymmetry is the whole design.
   10b. Files stay in their folder. An incoming name is reduced to its last
                        component and stripped, so nothing can be written
                        outside the inbox; an outgoing one is resolved and its
                        parent compared against the shared folder, so no
                        traversal and no symlink can read out of it. Neither
                        folder has a default: unnamed means the feature does
                        not exist.
   11. Token hygiene.   The bot token lives in config/api_keys.json and is NEVER
                        printed: requests puts the URL in its exception text, so
                        every error string is scrubbed before it is logged.
   12. Visible at desk. Every accepted remote command, every picture sent, every
                        file saved or handed out and every refused stranger is
                        written to the activity log. Nothing happens invisibly.
   13. Kill switches.   /stop from an approved chat, /panic to stop AND erase
                        every approved phone in one message, "stop the telegram
                        remote" by voice, or action="unpair" at the desk.

WHAT IT DELIBERATELY DOES NOT DO
    It does not weaken the confirmation gate. Shutdown, restart and WiFi still
    put a banner on the HUD and wait for a button pressed BY A HAND. Asking for
    them from Telegram tells you the banner is up — and it stays up until someone
    is physically there. That is the correct answer, not a limitation: a remote
    channel must not be able to confirm an irreversible action on its own.

    And it makes no promise it cannot keep: once text reaches the Live session it
    is indistinguishable from text typed at the desk, so this file cannot give
    remote commands a smaller tool set than local ones. What it CAN gate, it
    gates here — at the door, before the model ever sees the request.
"""
from __future__ import annotations

import hmac
import queue
import threading
import time
import unicodedata

# One file, dropped into whatever Mark the person is running. The per-plugin
# settings store is load-bearing here — the bot token and the allowlist live in
# it — but an ImportError would have the loader reject this file and tell the
# user to install a package that has nothing to do with the project. So it
# loads either way and explains itself in a sentence instead.
try:
    from memory.config_manager import (
        get_assistant_name,
        get_plugin_config,
        save_plugin_config,
    )
    _HAS_CONFIG = True
except ImportError:
    _HAS_CONFIG = False

    def get_assistant_name():                        # type: ignore[misc]
        return "JARVIS"

    def get_plugin_config(namespace):                # type: ignore[misc]
        return {}

    def save_plugin_config(namespace, values):       # type: ignore[misc]
        raise RuntimeError("this Mark has no plugin settings store")

# The extras (screen, hardware readout) live in a sibling helper so this file
# stays the size of the thing it is: a security boundary. Plugins are shared one
# file at a time, so a missing helper must not take the bridge down with it —
# the ops it declares simply never appear in the schema, the menu or /help.
try:
    from plugins import _telegram_ops as _ops
except Exception:                                     # noqa: BLE001 — see above
    _ops = None                                       # type: ignore[assignment]

_NS = "telegram_remote"

_API           = "https://api.telegram.org/bot{token}/{method}"
_FILE_API      = "https://api.telegram.org/file/bot{token}/{path}"
_POLL_TIMEOUT  = 20      # long-poll seconds; also the worst-case stop latency
_HTTP_TIMEOUT  = 35      # must exceed _POLL_TIMEOUT
_MAX_AGE       = 120     # refuse messages older than this (seconds)
_FUTURE_SKEW   = 60      # tolerate this much clock skew the other way
_START_GRACE   = 2       # seconds of slack on the start floor
_MAX_TEXT      = 1000    # refuse absurdly long messages
_RATE_MAX      = 20      # messages per window, per chat
_RATE_WINDOW   = 60
_RATE_KEYS     = 512     # prune the rate table past this many chats
_REPLY_WINDOW  = 90      # hard ceiling on how long one turn may keep replying
_TURN_FIRST    = 25      # wait this long for the turn's first sentence
_TURN_QUIET    = 12      # ...then this long after each one, before closing
_PAIR_WINDOW   = 600     # pairing stays armed this long
_PAIR_TRIES    = 10      # wrong codes, in total, before pairing closes itself
_REFUSE_NOTICE = 600     # seconds between "someone tried" notices at the desk
_SKEW_REFRESH  = 900     # re-measure the clock at least this often
_DRAIN_SECS    = 3       # how long the sender keeps posting after a stop
_TG_CHUNK      = 3800    # Telegram's own limit is 4096; leave room
_REPEAT_WINDOW = 60      # a confirm-op must be sent twice inside this
_WATCH_TICK    = 10      # how often the watcher looks at the desk
_LEASE_GRACE   = 15      # keep relaying this long after a banner clears
_LEASE_MAX     = 150     # ...and never longer than this in total
_AWAY_IDLE     = 600     # no desk activity for this long means nobody is there
_VOICE_SECS    = 90      # refuse a voice note longer than this
_VOICE_BYTES   = 5_000_000
_SPEAK_CHARS   = 700     # never synthesise more than this in one go
_SPEAK_MAX     = 3       # ...nor more than this many answers in one turn
_FILE_BYTES    = 20_000_000   # refuse an incoming file bigger than this
_FILE_HOLD     = 900     # a file the phone sent stays attached to the next command
_AUTOSTART_WAIT   = 300  # how long to wait for the interface to appear
_AUTOSTART_POLL   = 2.0
_AUTOSTART_SETTLE = 3.0  # ...then this, so the first line is not stepped on


# ── the capability table ─────────────────────────────────────────────────────
# One row per thing this bridge can do. See _telegram_ops.py for the field
# meanings — they are identical there, because the two tuples are merged and
# every consumer below reads the merged result.
#
# `desc` is written for the MODEL first: it is what a small local model reads to
# decide, from a sentence in any language, which action value to write. Telegram
# gets the same line in its command menu, which keeps the two from drifting.

_CORE_OPS = (
    {"action": "start",   "cmd": None,     "icon": "▶", "button": False,
     "desc": "begin listening for commands from the paired phone",
     "where": "desk",   "gate": None, "arg": None},
    {"action": "stop",    "cmd": "stop",   "icon": "⏹", "button": False,
     "desc": "stop listening, so nothing outside can reach this computer",
     "where": "both",   "gate": None, "arg": None},
    {"action": "status",  "cmd": "status", "icon": "ℹ", "button": True,
     "desc": "report whether the bridge is listening and how many phones are approved",
     "where": "both",   "gate": None, "arg": None},
    {"action": "pair",    "cmd": None,     "icon": "🔑", "button": False,
     "desc": "open the pairing window for ten minutes so one more phone can be approved",
     "where": "desk",   "gate": None, "arg": None},
    {"action": "unpair",  "cmd": None,     "icon": "🚫", "button": False,
     "desc": "erase every approved phone, for one that has been lost",
     "where": "desk",   "gate": None, "arg": None},
    {"action": "panic",   "cmd": "panic",  "icon": "🚨", "button": False,
     "desc": "stop the bridge AND erase every approved phone in one step",
     "where": "both",   "gate": None, "arg": None},
    {"action": None,      "cmd": "help",   "icon": "❓", "button": True,
     "desc": "list what this bot can do",
     "where": "remote", "gate": None, "arg": None},
    {"action": None,      "cmd": "id",     "icon": "🆔", "button": False,
     "desc": "show this chat's numeric ID",
     "where": "remote", "gate": None, "arg": None},
)

_OPS = _CORE_OPS + (tuple(getattr(_ops, "EXTRA_OPS", ()) or ()) if _ops else ())


def _gate_ok(op: dict, cfg: dict) -> bool:
    """A gated op is invisible and inert until the switch is thrown at the desk."""
    key = op.get("gate")
    return True if not key else bool(cfg.get(key))


def _gate_notice(op: dict | None) -> str:
    """One sentence naming the switch, built from the op rather than typed out.

    It has to say WHERE the switch is. "Not allowed" sends somebody hunting
    through a settings drawer for a toggle whose name they are guessing at.
    """
    what = f"/{op.get('cmd')}" if op and op.get("cmd") else "That"
    return (f"{what} is switched off. It can only be turned on at the computer: "
            f"settings → TELEGRAM REMOTE.")


def _ops_for(where: str, cfg: dict) -> list[dict]:
    return [o for o in _OPS
            if o.get("where") in (where, "both") and _gate_ok(o, cfg)]


def _remote_ops(cfg: dict) -> list[dict]:
    """The phone's view of the table, with the things it reaches for first.

    Ops that send something back are put ahead of ops that change the bridge:
    /screen and /sys are pressed daily, /stop and /panic are pressed once. The
    order is derived from which tuple an op came from, so it stays right when a
    new one is added.
    """
    ops = [o for o in _ops_for("remote", cfg) if o.get("cmd")]
    ops.sort(key=lambda o: 0 if o.get("action") in _EXTRA_ACTIONS else 1)
    return ops


def _op_by_command(token: str, cfg: dict) -> dict | None:
    token = (token or "").lower()
    for o in _ops_for("remote", cfg):
        if o.get("cmd") == token:
            return o
    return None


def _split_command(text: str) -> tuple[str, str]:
    """Find a slash token in the first two words; return (name, argument).

    Two words rather than one because the phone keyboard's buttons are labelled
    with an icon in front of the token — pressing one sends "\U0001F4F8 /screen",
    and a parser that only looked at the first word would see the icon and give
    up. Anything further in than that is prose, and prose belongs to the model.
    """
    parts = text.split()
    for i, tok in enumerate(parts[:2]):
        if tok.startswith("/") and len(tok) > 1:
            name = tok[1:].split("@", 1)[0].strip().lower()
            if name:
                return name, " ".join(parts[i + 1:]).strip()
    return "", ""


# The model side of the same table. Ops with no `action` are Telegram-only
# conveniences and are never offered to the model; ops the desk cannot trigger
# are left out too, so the schema describes exactly what a tool call can do.
_MODEL_OPS = [o for o in _OPS if o.get("action") and o.get("where") in ("desk", "both")]

# The ops whose answer is a THING sent to the phone rather than a change to the
# bridge itself. Derived from what the helper declares, so adding one there needs
# no edit here — and removing the helper removes them from the dispatch too.
# Everything the helper contributes, whoever may trigger it. Used only for
# ordering: the things a phone reaches for are the things this file did not
# already have, so they go at the front of the menu and the keyboard.
_EXTRA_ACTIONS = {o["action"] for o in (getattr(_ops, "EXTRA_OPS", ()) or ())
                  if o.get("action")} if _ops else set()

_PUSH_ACTIONS = {o["action"] for o in (getattr(_ops, "EXTRA_OPS", ()) or ())
                 if o.get("action") and o.get("where") in ("desk", "both")} \
    if _ops else set()


# ── shared state ─────────────────────────────────────────────────────────────

_lock = threading.Lock()
_state: dict = {
    "running":     False,
    "stopping":    False,  # stop asked for, poller not yet wound down
    "stop":        None,   # threading.Event
    "thread":      None,
    "sender":      None,
    "outbox":      None,   # queue.Queue of job dicts
    "orig_log":    None,   # the player's real write_log, while relaying
    "wrapped":     None,   # the wrapper we installed, for identity on removal
    "player":      None,
    "turn_chat":   None,   # chat_id whose turn is currently open
    "turn_open":   False,
    "turn_end":    0.0,    # hard ceiling for this turn
    "turn_quiet":  0.0,    # closes early once the answer has gone quiet
    "bot":         "",
    "started":     0.0,
    "floor_ts":    0.0,    # Telegram-clock moment the bridge began listening
    "pair_until":  0.0,    # monotonic; pairing is armed until then
    "pair_tries":  0,      # wrong codes during the current armed window
    "refused_at":  0.0,    # last time the desk was told about a refused chat
    "stale_at":    0.0,    # last time the desk was told about a clock-dropped message
    "skew":        0.0,    # this machine's clock minus Telegram's, in seconds
    "skew_seen":   [],     # recent samples; the median is used
    "skew_at":     0.0,    # monotonic, last measurement
    "seen":        0,      # accepted commands this run
    "shots":       0,      # pictures sent this run
    "watch":       None,   # the desk watcher thread
    "repeat":      None,   # (chat_id, cmd, expires) — a confirm-op awaiting its second press
    "lease_chat":  None,   # chat still hearing system lines about a pending confirmation
    "lease_until": 0.0,
    "confirm_at":  "",     # the banner title the watcher last saw
    "desk_seen":   0.0,    # last activity-log line that was not ours
    "alerts":      0,      # away alerts pushed this run
    "alert_last":  "",     # the last one, so it is never repeated back to back
    "turn_voice":  False,  # this turn arrived as speech, so answer in kind
    "spoken":      0,      # answers synthesised during this turn
    "heard":       0,      # voice notes understood this run
    "last_file":   None,   # (chat_id, path, expires) — sent from a phone, not yet used
    "files":       0,      # files received this run
}
_rate: dict[int, list[float]] = {}


# ── helpers ──────────────────────────────────────────────────────────────────

def _http():
    """`requests` is imported on first use, never at module scope.

    Nothing else in the app imports it at startup — measured, it costs 173 ms —
    and plugin discovery executes every plugin file on the launch path. A remote
    channel that is off by default must not be charged to every boot. The same
    mistake the 2.1-second openwakeword import made on the settings drawer.
    """
    import requests                       # noqa: PLC0415 — deliberate, see above
    return requests


def _session():
    """One keep-alive Session per thread.

    Without it every poll pays a fresh TCP connection and TLS handshake — one
    every twenty seconds, for as long as the bridge is up, to the same host. A
    Session is not documented as thread-safe, so the poller and the sender get
    one each rather than sharing.
    """
    try:
        return _http().Session()
    except Exception:
        return None


def _api(sess, token: str, method: str, **kw):
    """One place where a URL is built, so the token appears in one line of code."""
    url = _API.format(token=token, method=method)
    kw.setdefault("timeout", 20)
    if sess is not None:
        return sess.post(url, **kw) if ("json" in kw or "data" in kw or "files" in kw) \
            else sess.get(url, **kw)
    http = _http()
    return http.post(url, **kw) if ("json" in kw or "data" in kw or "files" in kw) \
        else http.get(url, **kw)


def _scrub(text, token: str) -> str:
    """requests puts the full URL — token included — in its exception text."""
    s = str(text)
    if token:
        s = s.replace(token, "<token>")
        # Telegram tokens are "<digits>:<secret>"; scrub the halves too.
        if ":" in token:
            head, _, tail = token.partition(":")
            if len(tail) > 6:
                s = s.replace(tail, "<token>")
            if len(head) > 4:
                s = s.replace(head, "<botid>")
    return s


def _code_matches(sent: str, code: str) -> bool:
    """Constant-time pairing-code comparison that a human can actually pass.

    Two traps, both of which fail as total silence rather than as a wrong answer:

    `hmac.compare_digest` RAISES TypeError on str arguments holding non-ASCII —
    so a pairing code with a Turkish letter in it, or an emoji the phone's
    autocorrect slipped in, threw inside the handler and was swallowed by the
    poll loop's except. Comparing the UTF-8 bytes has no such restriction and is
    still constant-time.

    And phone keyboards capitalise the first letter of a message. A code typed
    in lower case on the desktop arrives capitalised from the phone and never
    matches. Case alone is not enough either: Turkish "İ" casefolds to i plus a
    combining dot, so "GİZLİ" never equals "gizli". Both sides are reduced the
    way the viseme code reduces script — NFKD, drop the combining marks, then
    casefold — which also lets ş/s and ğ/g stand in for each other on a keyboard
    that may not have them.

    That costs some entropy, which is why the walls around it now hold: the code
    is one-time, consumed on first use, armed only inside a ten-minute window,
    rate-limited like every other message, and cancelled outright after ten wrong
    guesses from anywhere at all. The allowlist, not this string, is what keeps
    the channel shut afterwards.
    """
    def _reduce(v: str) -> bytes:
        flat = " ".join(str(v or "").split())
        decomposed = unicodedata.normalize("NFKD", flat)
        stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
        return stripped.casefold().encode("utf-8")

    return hmac.compare_digest(_reduce(sent), _reduce(code))


def _note_skew(response) -> None:
    """Learn how far this computer's clock is from Telegram's, from the Date header.

    The freshness check compares a message's timestamp — which Telegram stamps
    with ITS clock — against this machine's. That silently assumed the local
    clock was right. Measured on the machine this was written for, Windows was
    188 seconds behind, so every message looked like it came from the future and
    was dropped before it ever reached the allowlist. Nothing was wrong with the
    token, the code or the bridge; the wall was made of the wrong clock.

    Only SHORT requests are sampled. A long poll can sit open for twenty seconds
    and any layer that flushes headers when the connection opens rather than when
    the body is produced would report a Date that old — poisoning the correction
    with the very wait it is supposed to measure across. And a single sample can
    be an outlier, so the median of the last few is used rather than the latest.
    """
    # email.utils costs a measured 50 ms to import and is reached only when the
    # bridge is actually talking to Telegram, so it is not paid by a launch that
    # never opens the remote — the same reasoning `requests` gets above.
    try:
        from email.utils import parsedate_to_datetime   # noqa: PLC0415

        server = parsedate_to_datetime(response.headers["Date"]).timestamp()
    except Exception:
        return
    seen = _state.get("skew_seen") or []
    seen.append(time.time() - server)
    del seen[:-5]
    _state["skew_seen"] = seen
    # The middle of the last few samples. `statistics.median` would be the
    # obvious call and costs 27 ms of import — paid on every launch, on the
    # plugin discovery path, for five numbers.
    ordered = sorted(seen)
    _state["skew"] = ordered[len(ordered) // 2]
    _state["skew_at"] = time.monotonic()


def _now() -> float:
    """This moment on Telegram's clock, as best we can tell."""
    return time.time() - _state.get("skew", 0.0)


def _cfg() -> dict:
    return get_plugin_config(_NS)


def _clean_token(raw) -> str:
    """A Telegram token is '<digits>:<secret>' and contains no whitespace.

    People retype it as "123456 : ABC..." or copy it out of a chat bubble with a
    line break in the middle, and every one of those fails as a flat 401 with
    nothing to say why. Stripping the ends is not enough — the space that breaks
    it is the one in the middle.
    """
    return "".join(str(raw or "").split())


def _parse_ids(raw) -> list[int]:
    out: list[int] = []
    for part in str(raw or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return out


def _log(player, text: str) -> None:
    if not player:
        return
    try:
        # Deliberately the ORIGINAL logger while relaying is installed, so our
        # own notices are never echoed back out to Telegram.
        orig = _state.get("orig_log")
        (orig or player.write_log)(text)
    except Exception:
        pass


def _rate_ok(chat_id: int) -> bool:
    """Applied to EVERY message, approved or not.

    In the first version this sat behind the allowlist, which meant the one wall
    an unapproved stranger can actually push against — the pairing code — was the
    only one with no limiter in front of it.
    """
    now = time.monotonic()
    if len(_rate) > _RATE_KEYS:
        for key in [k for k, v in _rate.items()
                    if not v or now - v[-1] > _RATE_WINDOW]:
            _rate.pop(key, None)
    hits = [t for t in _rate.get(chat_id, []) if now - t < _RATE_WINDOW]
    if len(hits) >= _RATE_MAX:
        _rate[chat_id] = hits
        return False
    hits.append(now)
    _rate[chat_id] = hits
    return True


# ── settings (⚙ → PLUGIN SETTINGS) ───────────────────────────────────────────

def _test_connection(values: dict) -> tuple[bool, str]:
    """TEST button: prove the token works and name the bot, without saving state.

    It deliberately does NOT start the bridge, and every message it returns has
    to say so. The first version said "send the pairing code to @bot" the moment
    the token checked out, which reads like an instruction you can follow right
    then — and it silently cannot work: nothing is polling yet, so the code sits
    in Telegram's queue, and the backlog drop discards it on the next start. The
    order is not optional, so the wording states it.
    """
    token = _clean_token(values.get("bot_token"))
    if not token:
        return False, "No bot token. Get one from @BotFather."
    try:
        r = _http().get(_API.format(token=token, method="getMe"), timeout=15)
        data = r.json()
    except Exception as e:
        return False, f"Could not reach Telegram: {_scrub(e, token)}"
    if not data.get("ok"):
        return False, f"Telegram refused the token ({data.get('description', 'unknown error')})."
    who = "@" + (data.get("result", {}).get("username") or "bot")
    ids = _parse_ids(values.get("allowed_chat_ids", ""))
    if ids:
        return True, (f"Connected as {who}. {len(ids)} approved chat(s). "
                      f"Say \"start the telegram remote\" to begin listening.")
    if (values.get("pairing_code") or "").strip():
        return True, (f"Connected as {who}. No approved chats yet. "
                      f"SAVE, then say \"start the telegram remote\" — and ONLY THEN "
                      f"send your pairing code to {who}. A code sent before it is "
                      f"listening is discarded, and pairing closes 10 minutes "
                      f"after it starts.")
    return True, f"Connected as {who}. Set a pairing code, or nothing will be accepted."


PLUGIN_SETTINGS = {
    "namespace": _NS,
    "title": "✈️  TELEGRAM REMOTE",
    "fields": [
        {"key": "bot_token", "label": "Bot Token", "type": "password",
         "placeholder": "Telegram → @BotFather → /newbot → the token it gives you"},
        {"key": "pairing_code", "label": "Pairing Code (one-time)", "type": "password",
         "placeholder": "Any secret phrase. Send it to the bot once to approve your phone."},
        {"key": "allowed_chat_ids", "label": "Approved Chat IDs", "type": "text",
         "placeholder": "Filled in by pairing. Comma-separated. Empty = nothing is accepted."},
        {"key": "relay_replies", "label": "Send JARVIS's answers back to Telegram",
         "type": "toggle", "default": True},
        # Off by default, and deliberately so. Updating a plugin must never hand
        # a remote channel a view of your screen because you were not asked.
        # Once this is on there is no cooldown and no second question — that is
        # the whole point of it — so the decision belongs here, at the machine,
        # once.
        {"key": "allow_screenshot",
         "label": "Allow /screen — send screenshots to approved phones",
         "type": "toggle", "default": False},
        # main.py's own monitor speaks its alerts out loud, and only while
        # JARVIS is awake — so a machine in trouble while the house is empty
        # says nothing at all. This is the half that was missing, and it is
        # off by default because an unasked-for push notification is somebody
        # else's phone buzzing at three in the morning.
        # A camera is a stronger thing to hand out than a screen, so it gets its
        # own switch rather than riding on that one.
        {"key": "allow_camera",
         "label": "Allow /cam — send webcam pictures to approved phones",
         "type": "toggle", "default": False},
        # These two are folders, not switches, and empty means the feature does
        # not exist. Naming a folder is the whole permission: it is the only
        # place files may be written to, and the only place they may be read
        # from. Nothing arriving from Telegram can change either one.
        {"key": "inbox_folder", "label": "Folder for files sent from the phone",
         "type": "text",
         "placeholder": "Empty = files from the phone are ignored"},
        {"key": "share_folder", "label": "Folder /get may send files out of",
         "type": "text",
         "placeholder": "Empty = /get does not exist"},
        # Off by default, and it has to be: a plugin that is merely PRESENT must
        # never open a way in. On, it is the owner of the machine saying they
        # would rather not repeat themselves every morning — which is a
        # different thing entirely, and theirs to decide.
        {"key": "start_on_launch",
         "label": "Start listening when JARVIS launches (no need to ask each time)",
         "type": "toggle", "default": False},
        {"key": "voice_replies",
         "label": "Speak answers to typed commands too (spoken ones always are)",
         "type": "toggle", "default": False},
        {"key": "away_alerts",
         "label": "Alert my phone when this computer is in trouble and nobody is here",
         "type": "toggle", "default": False},
    ],
    "action": {"label": "TEST CONNECTION", "run": lambda values: _test_connection(values or {})},
}

PLUGIN = {
    "name": "telegram_remote",
    "description": (
        "Opens, closes and drives the Telegram remote-control bridge, which lets the "
        "user command this computer from their phone over Telegram while away from "
        "home, and lets this computer push things back to that phone — a screenshot "
        "of what is on screen right now, or a hardware readout. Call this for phrases "
        "like 'start the telegram remote', 'let me control the computer from my "
        "phone', 'send a screenshot to my phone', 'what does my screen look like on "
        "telegram', 'send the system status to my phone', 'stop the telegram bridge'. "
        "Do NOT use this to send a message to a person — sending a WhatsApp or "
        "Telegram message to a contact is the send_message tool. This tool only "
        "drives the remote-control channel; it never messages anyone."
    ),
    # Used in place of `description` only when a small model is on a tool
    # budget - Gemini still gets the full text. See providers/schema.py.
    "brief": (
        "Sends and reads Telegram messages remotely."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": [o["action"] for o in _MODEL_OPS],
                "description": "; ".join(f"{o['action']} = {o['desc']}" for o in _MODEL_OPS),
            },
            "target": {
                "type": "STRING",
                "description": ("Optional detail for the action. For screenshot: "
                                "a monitor number such as '2', or 'all' for every "
                                "monitor at once. Leave empty for the main screen."),
            },
        },
        "required": [],
    },
}


# ── outbound: a queue, because write_log must never block on the network ─────

def _put(job: dict) -> None:
    q = _state.get("outbox")
    if q is None:
        return
    try:
        q.put_nowait(job)
    except Exception:
        pass


def _enqueue(chat_id: int, text: str) -> None:
    """Queue a text reply, split rather than truncated.

    Telegram rejects anything over 4096 characters. The first version cut at
    3500 and sent the front of the answer as though it were the whole of it,
    which is the kind of failure you only notice when the missing half mattered.
    """
    if not chat_id or not text:
        return
    text = str(text)
    while text:
        chunk, text = text[:_TG_CHUNK], text[_TG_CHUNK:]
        if text:
            # Prefer a line break, then a space, so a word is not cut in half.
            cut = max(chunk.rfind("\n"), chunk.rfind(" "))
            if cut > _TG_CHUNK // 2:
                chunk, text = chunk[:cut], chunk[cut:] + text
        _put({"kind": "text", "chat": chat_id, "text": chunk})


def _enqueue_job(chat_id: int, job: dict) -> None:
    """Queue whatever _telegram_ops.produce() handed back."""
    if not chat_id or not isinstance(job, dict):
        return
    if job.get("kind") == "text":
        _enqueue(chat_id, job.get("text") or "")
        return
    job = dict(job)
    job["chat"] = chat_id
    _put(job)


def _keyboard(cfg: dict) -> dict:
    """A persistent button row on the phone, built from the same table.

    Reply-keyboard buttons, not inline ones: pressing a reply button SENDS the
    text, so a press walks through the allowlist, the rate limit and the
    freshness wall exactly like a typed message. Inline buttons would arrive as
    callback_query updates instead — a whole second class of update to admit and
    to guard. Buttons that are just typing are strictly safer, and they behave
    identically on every Telegram client.

    Only the ops marked `button` get one. /stop and /panic are reachable by
    typing and from the command menu, but they are not put under a thumb that is
    aiming at /screen — the cost of a mis-tap is losing remote control, or the
    allowlist, from the one place you cannot walk over and fix it.
    """
    labels = [f"{o['icon']} /{o['cmd']}" for o in _remote_ops(cfg) if o.get("button")]
    rows = [labels[i:i + 2] for i in range(0, len(labels), 2)]
    return {"keyboard": rows, "resize_keyboard": True, "is_persistent": True}


def _menu(cfg: dict) -> list[dict]:
    """setMyCommands payload — same table, Telegram's field names."""
    return [{"command": o["cmd"], "description": (o["desc"] or "")[:250]}
            for o in _remote_ops(cfg)]


def _help_text(cfg: dict) -> str:
    lines = [f"{o['icon']} /{o['cmd']}" + (f" [{o['arg']}]" if o.get("arg") else "")
             + f" — {o['desc']}"
             for o in _remote_ops(cfg)]
    lines.append("")
    lines.append("Anything else you write is passed straight to the assistant, "
                 "in your own language.")
    # Only mentioned when there is actually a way to hear it. A help screen that
    # offers something the machine cannot do is worse than one that is short.
    if _ops is not None:
        try:
            hearing, _by = _ops.stt_ready()
        except Exception:
            hearing = False
        if hearing:
            lines.append("Or hold the microphone button and just say it — when "
                         "you do, the answer comes back out loud as well.")
    return "\n".join(lines)


def _send_job(sess, token: str, job: dict, stop: threading.Event) -> None:
    """Post one job, honouring Telegram's own back-pressure.

    Telegram answers a chat that is being written to too quickly with 429 and a
    `retry_after`. Ignoring it does not slow anything down — it loses the
    message, which for a relayed answer means JARVIS looks like it said nothing.
    """
    kind = job.get("kind")
    for attempt in range(3):
        try:
            if kind == "photo":
                r = _api(sess, token, "sendPhoto",
                         data={"chat_id": job["chat"],
                               "caption": (job.get("caption") or "")[:1024]},
                         files={"photo": (job.get("name") or "photo.jpg",
                                          job.get("data") or b"",
                                          job.get("mime") or "image/jpeg")},
                         timeout=60)
            elif kind == "document":
                r = _api(sess, token, "sendDocument",
                         data={"chat_id": job["chat"],
                               "caption": (job.get("caption") or "")[:1024]},
                         files={"document": (job.get("name") or "file.bin",
                                             job.get("data") or b"",
                                             job.get("mime") or "application/octet-stream")},
                         timeout=180)
            elif kind in ("voice", "audio"):
                # sendVoice draws the round waveform bubble and needs OGG/Opus;
                # sendAudio takes anything and draws a file row. Which one this
                # is was decided where the sound was made, by whether ffmpeg was
                # there to encode it.
                field = "voice" if kind == "voice" else "audio"
                r = _api(sess, token, "sendVoice" if kind == "voice" else "sendAudio",
                         data={"chat_id": job["chat"],
                               "duration": int(job.get("seconds") or 0)},
                         files={field: (job.get("name") or f"{field}.ogg",
                                        job.get("data") or b"",
                                        job.get("mime") or "audio/ogg")},
                         timeout=90)
            elif kind == "menu":
                r = _api(sess, token, "setMyCommands",
                         json={"commands": job.get("commands") or [],
                               "scope": {"type": "chat", "chat_id": job["chat"]}})
            elif kind == "menu_clear":
                r = _api(sess, token, "deleteMyCommands",
                         json={"scope": {"type": "chat", "chat_id": job["chat"]}})
            else:
                payload = {"chat_id": job["chat"], "text": job.get("text") or "",
                           "disable_web_page_preview": True}
                # No parse_mode on purpose: JARVIS's answers are arbitrary text
                # and Markdown parsing would reject or mangle them.
                if job.get("markup"):
                    payload["reply_markup"] = job["markup"]
                r = _api(sess, token, "sendMessage", json=payload)

            if r is not None and r.status_code == 429:
                wait = 3.0
                try:
                    wait = float((r.json().get("parameters") or {}).get("retry_after", 3))
                except Exception:
                    pass
                if stop.wait(min(max(wait, 1.0), 30.0)):
                    return
                continue
            if r is not None and r.status_code < 400:
                _note_skew(r)
            return
        except Exception as e:
            print(f"[TelegramRemote] send failed: {_scrub(e, token)}")
            if attempt == 2 or stop.wait(1.0):
                return


def _sender_loop(token: str, stop: threading.Event) -> None:
    q = _state["outbox"]
    sess = _session()
    deadline: float | None = None
    try:
        while True:
            if stop.is_set() and deadline is None:
                # Do not abandon what is already queued. The last thing a person
                # sends is often /stop, and answering "closing the bridge" only
                # to vanish mid-sentence reads like a crash.
                deadline = time.monotonic() + _DRAIN_SECS
            if deadline is not None and time.monotonic() > deadline:
                return
            try:
                job = q.get(timeout=0.3)
            except queue.Empty:
                if stop.is_set():
                    return
                continue
            _send_job(sess, token, job, stop)
    finally:
        try:
            if sess is not None:
                sess.close()
        except Exception:
            pass


# ── reply relay: mirror JARVIS's answers back, bound to the turn that asked ──

_name_cache: list = ["", 0.0]


def _name_prefix() -> str:
    """The assistant's name can be changed from the UI mid-session, so it is
    re-read rather than frozen at install — but cached, because the caller is on
    the path of every log line."""
    now = time.monotonic()
    if now - _name_cache[1] > 60:
        try:
            _name_cache[0] = f"{get_assistant_name()}:"
        except Exception:
            pass
        _name_cache[1] = now
    return _name_cache[0] or "JARVIS:"


def _open_turn(chat_id: int, spoken: bool = False) -> None:
    now = time.time()
    _state["turn_chat"]  = chat_id
    _state["turn_open"]  = True
    _state["turn_end"]   = now + _REPLY_WINDOW
    _state["turn_quiet"] = now + _TURN_FIRST
    # A question asked out loud is answered out loud. That is the whole rule —
    # no setting to find, and it is right for the case this exists for, which is
    # somebody with their hands on a steering wheel.
    _state["turn_voice"] = bool(spoken)
    _state["spoken"]     = 0


def _turn_active() -> bool:
    if not _state.get("turn_open"):
        return False
    now = time.time()
    if now > _state.get("turn_end", 0) or now > _state.get("turn_quiet", 0):
        _state["turn_open"] = False
        return False
    return True


def _maybe_relay(line: str) -> None:
    """Forward one activity-log line, if it belongs to the turn a phone started.

    The first version relayed for ninety seconds flat, which meant anything the
    assistant said in that window went to the phone — a sentence spoken to
    somebody standing at the desk, a morning briefing, a GPU alert. The window
    is bound to the exchange now: it opens when a remote command is delivered,
    stays open while sentences keep arriving, and closes on quiet. The ninety
    seconds survive only as a ceiling.
    """
    turn = _turn_active()
    if not turn:
        # Outside a turn, this line is somebody at the desk. That is what makes
        # the machine look occupied to the away-alert watcher.
        _state["desk_seen"] = time.monotonic()

    if not _cfg().get("relay_replies", True):
        return

    if turn:
        chat_id = _state.get("turn_chat")
        if not chat_id:
            return
        prefix = _name_prefix()
        if line.startswith(prefix):
            answer = line[len(prefix):].strip()
            _enqueue(chat_id, answer)
            _maybe_speak(chat_id, answer)
        elif line.startswith("SYS:"):
            # Inside the turn, a system line is part of the answer. Note that
            # core/confirm.py's own lines are NOT among them — see the watcher
            # for why, and for what carries the confirmation instead.
            _enqueue(chat_id, line.strip())
        else:
            return
        _state["turn_quiet"] = time.time() + _TURN_QUIET
        return

    # Outside the turn, only the confirmation lease can still speak, and only
    # about the machine — never the assistant's own sentences to whoever is
    # standing at the desk.
    if _lease_active() and (line.startswith("SYS:") or line.startswith("ERR:")):
        _enqueue(_state["lease_chat"], line.strip())


def _maybe_speak(chat_id: int, answer: str) -> None:
    """Send the answer as audio too, when it was asked for out loud.

    Capped twice. A long answer is not worth waiting on a synthesiser for, and
    a turn that produces sentence after sentence would otherwise arrive as a
    stack of voice bubbles nobody wants to tap through.
    """
    if _ops is None or not answer:
        return
    if not (_state.get("turn_voice") or _cfg().get("voice_replies")):
        return
    if _state.get("spoken", 0) >= _SPEAK_MAX or len(answer) > _SPEAK_CHARS:
        return
    _state["spoken"] = _state.get("spoken", 0) + 1
    _spawn(_speak_reply, _state.get("player"), chat_id, answer)


def _install_relay(player) -> None:
    """Wrap the player's write_log so answers can be forwarded.

    There is no transcript callback in core, and every reply already passes
    through write_log as "<assistant name>: ..." (main.py's turn_complete
    handler), so this is the one seam that needs no core edit.

    It is installed for as long as the bridge runs, whatever the relay setting
    says, and the setting is read inside. Installing it conditionally meant
    turning the setting on mid-session did nothing at all, with no way to tell
    why.
    """
    # A wrapper of ours that is already in place is adopted, never stacked on
    # top of. Two layers would relay every answer twice, and the guard below is
    # not enough on its own: _remove_relay leaves ours installed when something
    # else has wrapped write_log after us, and the next start would find the
    # bookkeeping empty but the wrapper still there.
    current = getattr(player, "write_log", None)
    if getattr(current, "_telegram_relay", False):
        with _lock:
            _state["orig_log"] = getattr(current, "_telegram_orig", None) \
                or _state.get("orig_log")
            _state["wrapped"] = current
        return

    with _lock:
        if _state.get("orig_log"):
            return
        orig = player.write_log
        _state["orig_log"] = orig

    def wrapped(text):
        try:
            orig(text)
        finally:
            try:
                _maybe_relay(str(text))
            except Exception:
                pass

    wrapped._telegram_relay = True      # type: ignore[attr-defined]
    wrapped._telegram_orig  = orig      # type: ignore[attr-defined]
    _state["wrapped"] = wrapped
    player.write_log = wrapped


def _remove_relay(player) -> None:
    with _lock:
        orig    = _state.get("orig_log")
        wrapped = _state.get("wrapped")
        _state["orig_log"] = None
        _state["wrapped"]  = None
    if not orig or not player:
        return
    # Only unwrap what is still ours. If something else wrapped write_log after
    # we did, deleting the attribute would silently take that wrapper down with
    # this one — and it would look like the other feature had simply stopped.
    try:
        if getattr(player, "write_log", None) is not wrapped:
            return
        del player.write_log          # unshadow the class method
    except Exception:
        try:
            player.write_log = orig
        except Exception:
            pass


# ── inbound ──────────────────────────────────────────────────────────────────

def _drop_backlog(sess, token: str):
    """Return an offset past everything already queued, so nothing sent while
    JARVIS was off is executed on start.

    The webhook is deleted first. A bot that ever had one set answers getUpdates
    with 409 for ever, which reads as "another poller is running" and sends the
    reader looking for a second copy of JARVIS that does not exist — and
    drop_pending_updates throws the queue away at the server, which is a better
    place to throw it away than here.

    Note that this returning None no longer means "everything is eligible": the
    start floor in _handle stands whether this call worked or not.
    """
    try:
        _api(sess, token, "deleteWebhook", json={"drop_pending_updates": True},
             timeout=15)
    except Exception:
        pass
    try:
        r = _api(sess, token, "getUpdates", params={"offset": -1, "timeout": 0},
                 timeout=15)
        _note_skew(r)
        result = r.json().get("result") or []
    except Exception:
        return None
    if not result:
        return None
    return result[-1]["update_id"] + 1


def _deliver(player, text: str) -> bool:
    """Hand a command to the Live session exactly as the desk text box does."""
    fn = getattr(player, "on_text_command", None)
    if not callable(fn):
        return False
    # A remote command is deliberate control and there is no WAKE button within
    # reach — so wake first if asleep, the same reasoning main.py applies to a
    # dashboard command. State is checked before toggling, because on_wake_manual
    # is a toggle and would otherwise put an awake JARVIS to sleep.
    try:
        get_state = getattr(player, "wake_get_state", None)
        wake_now  = getattr(player, "on_wake_manual", None)
        if callable(get_state) and callable(wake_now):
            st = get_state() or {}
            if st.get("enabled") and not st.get("awake"):
                wake_now()
                time.sleep(0.3)
    except Exception:
        pass
    fn(text)
    return True


def _status_text() -> str:
    if not _state.get("running"):
        return "The Telegram remote is not running."
    mins = int((time.monotonic() - _state.get("started", 0)) // 60)
    cfg  = _cfg()
    ids  = _parse_ids(cfg.get("allowed_chat_ids", ""))
    bot  = _state.get("bot") or "the bot"
    bits = [f"Listening as {bot} for {mins} minute(s).",
            f"{len(ids)} approved chat(s), {_state.get('seen', 0)} command(s) "
            f"and {_state.get('shots', 0)} picture(s) this session."]
    left = _state.get("pair_until", 0.0) - time.monotonic()
    if left > 0:
        bits.append(f"Pairing is open for another {int(left // 60)} minute(s).")
    if not cfg.get("allow_screenshot"):
        bits.append("Screen sharing is off.")
    if cfg.get("away_alerts"):
        bits.append(f"Away alerts are on ({_state.get('alerts', 0)} sent"
                    f"{'; nobody at the desk right now' if _desk_idle() else ''}).")
    if _ops is not None and _state.get("heard"):
        bits.append(f"{_state['heard']} voice note(s) understood.")
    if _state.get("files"):
        bits.append(f"{_state['files']} file(s) received.")
    waiting = _ops.confirm_pending() if _ops else ""
    if waiting:
        bits.append(f"A confirmation is waiting at the computer: {waiting}.")
    skew = _state.get("skew", 0.0)
    if abs(skew) > _FUTURE_SKEW:
        bits.append(f"This computer's clock is {abs(skew):.0f}s "
                    f"{'behind' if skew < 0 else 'ahead of'} Telegram's.")
    return " ".join(bits)


def _register_menu(chat_id: int, cfg: dict) -> None:
    """Give one approved chat the bot's command menu and button keyboard.

    Scoped to the chat rather than set bot-wide: a stranger who finds the bot
    sees an empty menu and learns nothing about what it can do.
    """
    _put({"kind": "menu", "chat": chat_id, "commands": _menu(cfg)})


def _targets(cfg: dict | None = None) -> list[int]:
    """Where a push (a picture, a readout) should go.

    The chat holding an open turn, if there is one — it asked, so it gets the
    answer. Otherwise every approved chat, because "send my screen to my phone"
    said at the desk means the phones you approved, and there is no third party
    in that list by construction.
    """
    if _turn_active() and _state.get("turn_chat"):
        return [_state["turn_chat"]]
    return _parse_ids((cfg or _cfg()).get("allowed_chat_ids", ""))


def _run_op(player, op: dict, chats: list[int], arg: str) -> None:
    """Produce one op's payload and post it to every chat that should get it.

    Always off the poll thread: a screen grab plus a JPEG encode is a few hundred
    milliseconds, and the hardware readout blocks for 200 ms inside psutil. Doing
    either on the poll loop would stall every other message behind it.

    Produced ONCE, however many chats are listening. Two approved phones asking
    for the screen should see the same picture, not two grabs a second apart —
    and the second grab would cost as much as the first for no reason.
    """
    action = op.get("action") or ""
    try:
        if _ops is None:
            raise RuntimeError(
                "This needs the file '_telegram_ops.py' next to telegram_remote.py. "
                "It comes with the plugin — copy it into the plugins folder and "
                "restart."
            )
        job = _ops.produce(action, arg, _cfg())
    except Exception as e:
        for cid in chats:
            _enqueue(cid, str(e))
        _log(player, f"SYS: Telegram remote could not run {action} — {e}")
        return

    for cid in chats:
        _enqueue_job(cid, job)
    if job.get("kind") == "photo":
        _state["shots"] = _state.get("shots", 0) + len(chats)
    # Every picture that leaves this machine is named at the desk. A remote
    # channel that can look at your screen without leaving a trace is not one
    # anybody should install.
    where = ", ".join(str(c) for c in chats)
    _log(player, f"SYS: Telegram remote sent {action} to chat {where}.")


def _spawn(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True,
                     name="telegram-remote-op").start()


def _download(token: str, file_id: str) -> tuple[bytes, str]:
    """Fetch one Telegram file. Returns (bytes, mime-ish extension hint).

    The download URL carries the bot token in its path, which is a second place
    it can end up in an exception string — so this is the only function that
    builds one, and every error out of it goes through _scrub like the rest.
    """
    sess = _session()
    try:
        r = _api(sess, token, "getFile", params={"file_id": file_id}, timeout=20)
        info = r.json()
        if not info.get("ok"):
            raise RuntimeError(info.get("description", "getFile failed"))
        path = (info.get("result") or {}).get("file_path") or ""
        if not path:
            raise RuntimeError("Telegram returned no path for that file.")
        url = _FILE_API.format(token=token, path=path)
        got = (sess or _http()).get(url, timeout=60)
        if got.status_code >= 400:
            raise RuntimeError(f"download returned {got.status_code}")
        return got.content, path.rsplit(".", 1)[-1].lower() if "." in path else ""
    finally:
        try:
            if sess is not None:
                sess.close()
        except Exception:
            pass


def _run_voice(player, chat_id: int, voice: dict, token: str, who: str) -> None:
    """A voice note becomes a command by becoming words first.

    The transcript is sent back before anything acts on it. Recognition is
    never perfect, and a remote command that was heard wrong and executed
    silently is the one failure this whole feature could not survive — seeing
    "open the bank" when you said "open the bar" has to happen on the phone,
    not afterwards on the machine.
    """
    try:
        data, ext = _download(token, voice.get("file_id") or "")
        heard = _ops.transcribe(data, voice.get("mime_type") or "audio/ogg")
    except Exception as e:
        _state["turn_open"] = False
        _enqueue(chat_id, f"I could not make out that voice note: "
                          f"{_scrub(e, token)}")
        _log(player, f"SYS: Telegram remote could not transcribe a voice note "
                     f"— {_scrub(e, token)}")
        return

    heard = (heard or "").strip()
    if not heard:
        _state["turn_open"] = False
        _enqueue(chat_id, "I could not hear anything in that.")
        return
    if len(heard) > _MAX_TEXT:
        heard = heard[:_MAX_TEXT]

    _state["heard"] = _state.get("heard", 0) + 1
    _enqueue(chat_id, f"🎤 {heard}")
    _deliver_text(player, chat_id, who, heard, spoken=True)


def _run_file_in(player, chat_id: int, item: dict, name: str, caption: str,
                 token: str, who: str) -> None:
    """A file arrives from the phone, lands in the inbox folder, and waits.

    It is NOT handed to the assistant on its own. A file with no instruction is
    not a request, and inventing one here would mean writing a sentence in some
    language and hoping it is the user's. So it is saved, its path is confirmed,
    and it is attached to whatever that chat says next — which is how people use
    a chat window anyway: the photo, then "what is this?".

    A caption arrives with the file often enough that the two-step is skipped
    when there is one.
    """
    try:
        data, _ext = _download(token, item.get("file_id") or "")
        path = _ops.save_incoming(name, data, _cfg())
    except Exception as e:
        _enqueue(chat_id, f"I could not save that: {_scrub(e, token)}")
        _log(player, f"SYS: Telegram remote could not save a file — {_scrub(e, token)}")
        return

    _state["files"] = _state.get("files", 0) + 1
    _state["last_file"] = (chat_id, path, time.monotonic() + _FILE_HOLD)
    _log(player, f"SYS: Telegram remote saved a file from {who} — {path}")

    if caption:
        _open_turn(chat_id)
        _deliver_text(player, chat_id, who, caption)
    else:
        _enqueue(chat_id, f"Saved.\n{path}\n\nTell me what to do with it.")


def _attach_file(chat_id: int, text: str) -> str:
    """Put the path of a waiting file alongside the command that mentions it.

    main.py already fills in file_processor's path from the desk drop-zone
    (main.py:1372) when the model leaves it out — but that is the zone on the
    HUD, and a phone cannot put anything in it. Naming the path in the turn is
    the same idea through the door this bridge already uses, and it needs no
    word in any language: the model gets a marker and an absolute path.
    """
    pending = _state.get("last_file")
    if not pending:
        return text
    who, path, until = pending
    if who != chat_id or time.monotonic() > until:
        return text
    _state["last_file"] = None
    return f"{text}\n[FILE] {path}"


def _speak_reply(player, chat_id: int, line: str) -> None:
    """Send one answer as audio as well as text."""
    try:
        job = _ops.synthesize(line)
    except Exception as e:
        # Said once, then never again this turn: a voice that cannot be built
        # will not be buildable for the next sentence either, and a failure
        # repeated after every line is worse than the missing audio.
        _state["spoken"] = _SPEAK_MAX
        _enqueue(chat_id, f"(no voice for that one — {e})")
        _log(player, f"SYS: Telegram remote could not speak an answer — {e}")
        return
    _enqueue_job(chat_id, job)


def _repeat_ok(chat_id: int, cmd: str) -> bool:
    """Was this exact command already sent once, recently, by this chat?

    The confirmation for a destructive op is the op again — not the word "yes",
    which would have to be a word in some language, and would then be the wrong
    word for most of the people using this. Sending /undo twice means the same
    thing on every keyboard on earth, and the phone's own command menu makes the
    second press a tap.
    """
    pending = _state.get("repeat")
    if not pending:
        return False
    who, what, until = pending
    if who != chat_id or what != cmd or time.monotonic() > until:
        return False
    _state["repeat"] = None        # consumed either way
    return True


def _arm_repeat(chat_id: int, cmd: str) -> None:
    _state["repeat"] = (chat_id, cmd, time.monotonic() + _REPEAT_WINDOW)


# ── the desk watcher: two things a phone cannot see for itself ───────────────

def _open_lease(chat_id: int) -> None:
    """Let one chat keep hearing SYSTEM lines while a confirmation is pending.

    A remote user who asks for something irreversible is told the banner is up,
    and then — under the turn rule alone — goes deaf exactly when the answer
    arrives, because the person walking over to press the button takes longer
    than the turn lasts. The lease is narrow on purpose: only SYS/ERR lines,
    only the chat that asked, only while the banner is up plus a short grace,
    and never more than a hard ceiling. What leaks through it is the confirmation
    gate talking about the thing this user themselves set in motion.
    """
    if not chat_id:
        return
    _state["lease_chat"]  = chat_id
    _state["lease_until"] = time.time() + _LEASE_MAX


def _lease_active() -> bool:
    return bool(_state.get("lease_chat")) and time.time() < _state.get("lease_until", 0)


def _desk_idle() -> bool:
    """True when nothing has been written to the activity log for a while.

    Any line counts as somebody being there — their speech, the assistant's, a
    system notice. Lines produced by a remote turn do not, since the person
    causing those is the one holding the phone.
    """
    seen = _state.get("desk_seen", 0.0)
    return bool(seen) and (time.monotonic() - seen) > _AWAY_IDLE


def _watcher(player, stop: threading.Event) -> None:
    """Looks at two pieces of state a Telegram poll can never learn about."""
    while not stop.wait(_WATCH_TICK):
        try:
            cfg = _cfg()

            # -- a confirmation banner going up, and coming back down --------
            # This is read as state, not waited for as a log line, and it has to
            # be: main.py binds the gate's logger with `log=self.ui.write_log`
            # (main.py:2318), which captures the method BEFORE this plugin wraps
            # it. Every other write_log call in main.py is looked up at call time
            # and does reach the relay — but core/confirm.py's own lines never
            # will, whatever this file does. Polling the title sidesteps the
            # whole question, and works in every language besides.
            title = _ops.confirm_pending() if _ops else ""
            was   = _state.get("confirm_at", "")
            if title and title != was:
                # It went up. The assistant has already been told to say so in
                # the user's own language, and that sentence relays with the
                # turn — so nothing extra is sent here. The lease is opened so
                # anything that goes wrong while the banner waits still gets
                # through after the turn has gone quiet.
                chat = _state.get("turn_chat")
                if chat and (_turn_active() or _lease_active()):
                    _open_lease(chat)
            elif was and not title:
                # It came down — pressed, cancelled, or timed out; the gate does
                # not say which and this file will not guess. That it is over is
                # the part a person waiting on a train needs.
                chat = _state.get("lease_chat")
                if chat and _lease_active():
                    _enqueue(chat, f"The confirmation for “{was}” is no longer "
                                   f"waiting at the computer.")
                    _state["lease_until"] = min(_state.get("lease_until", 0),
                                                time.time() + _LEASE_GRACE)
            _state["confirm_at"] = title

            # -- the machine in trouble while nobody is at the desk ----------
            if cfg.get("away_alerts") and _ops is not None and _desk_idle():
                chats = _parse_ids(cfg.get("allowed_chat_ids", ""))
                if chats:
                    line = _ops.away_alert()
                    # SystemMonitor has a five-minute cooldown of its own, but
                    # a push notification is not a log line: nothing here should
                    # rely on another module's constant to avoid buzzing a phone
                    # in a loop. The same sentence is not sent twice running.
                    if line and line == _state.get("alert_last"):
                        line = None
                    if line:
                        _state["alert_last"] = line
                        for cid in chats:
                            _enqueue(cid, f"⚠ {line}")
                        _state["alerts"] = _state.get("alerts", 0) + 1
                        _log(player, f"SYS: Telegram remote pushed an away alert "
                                     f"to {len(chats)} chat(s).")
        except Exception as e:
            print(f"[TelegramRemote] watcher error: {e}")


def _handle(player, msg: dict, token: str) -> None:
    chat = msg.get("chat") or {}
    frm  = msg.get("from") or {}
    chat_id = chat.get("id")

    # -- shape: private human chats only -------------------------------------
    if chat.get("type") != "private" or frm.get("is_bot") or not isinstance(chat_id, int):
        return
    # In a private chat the sender and the chat are the same account. Anything
    # else is a shape this bridge was not designed around, so it is refused
    # rather than reasoned about.
    if frm.get("id") is not None and frm.get("id") != chat_id:
        return
    # Three shapes get in: something typed, something said, and something sent.
    # The last two are only considered when the helper is present to turn them
    # into words or into a saved file; without it they are not commands, they
    # are attachments, and they are ignored like anything else unrecognised.
    text  = msg.get("text")
    voice = msg.get("voice")
    doc   = msg.get("document")
    photo = msg.get("photo")
    text  = text.strip() if isinstance(text, str) else ""
    spoken = (not text) and isinstance(voice, dict) and _ops is not None
    filed  = (not text) and (not spoken) and _ops is not None and (
        isinstance(doc, dict) or (isinstance(photo, list) and photo))

    if not text and not spoken and not filed:
        return
    if text and len(text) > _MAX_TEXT:
        return

    # -- replay walls: the start floor first, then freshness ------------------
    ts = msg.get("date")
    if isinstance(ts, (int, float)):
        floor = _state.get("floor_ts", 0.0)
        if floor and ts < floor - _START_GRACE:
            return
        age = _now() - ts
        if age > _MAX_AGE or age < -_FUTURE_SKEW:
            # Never in silence. This is the one wall that can reject a perfectly
            # legitimate message because of something outside Telegram entirely,
            # so it has to be able to say so — throttled, and naming the clock
            # when that is what did it.
            now = time.monotonic()
            if now - _state.get("stale_at", 0.0) > _REFUSE_NOTICE:
                _state["stale_at"] = now
                skew = _state.get("skew", 0.0)
                if abs(skew) > _FUTURE_SKEW:
                    _log(player, f"SYS: Telegram remote dropped a message — this "
                                 f"computer's clock is {abs(skew):.0f}s "
                                 f"{'behind' if skew < 0 else 'ahead of'} Telegram's. "
                                 f"Fix the system clock.")
                else:
                    _log(player, f"SYS: Telegram remote dropped a message that was "
                                 f"{abs(age):.0f}s old.")
            return

    # -- rate limit, ahead of the allowlist so pairing is covered too ---------
    if not _rate_ok(chat_id):
        return

    cfg     = _cfg()
    allowed = _parse_ids(cfg.get("allowed_chat_ids", ""))

    # -- pairing: the ONLY thing an unapproved chat may do -------------------
    if chat_id not in allowed:
        # A pairing code is typed, never spoken — nothing here transcribes audio
        # for a chat that has not been approved yet, so an unapproved stranger
        # can never make this machine do work.
        _try_pair(player, chat_id, text, cfg, allowed)
        return

    who = str(frm.get("first_name") or frm.get("username") or chat_id)[:32]

    if filed:
        # Telegram sends a photo as a list of sizes, smallest first. The last is
        # the one the person actually took.
        item = doc if isinstance(doc, dict) else photo[-1]
        if not isinstance(item, dict):
            return
        # Checked HERE rather than where the file is written, so an install that
        # has never named a folder does not pay a download to find that out.
        # An approved chat still gets told, because a PDF sent into silence is
        # indistinguishable from a broken bridge.
        if not str(cfg.get("inbox_folder") or "").strip():
            _enqueue(chat_id, "Files from the phone are switched off. Name a "
                              "folder for them at the computer: settings → "
                              "TELEGRAM REMOTE.")
            return
        if (item.get("file_size") or 0) > _FILE_BYTES:
            _enqueue(chat_id, f"That file is over "
                              f"{_FILE_BYTES // 1_000_000} MB.")
            return
        name = item.get("file_name") or f"photo-{int(time.time())}.jpg"
        cap  = str(msg.get("caption") or "").strip()[:_MAX_TEXT]
        _spawn(_run_file_in, player, chat_id, item, name, cap, token, who)
        return

    if spoken:
        if (voice.get("duration") or 0) > _VOICE_SECS:
            _enqueue(chat_id, f"That is longer than {_VOICE_SECS} seconds. "
                              f"Say it in a shorter one.")
            return
        if (voice.get("file_size") or 0) > _VOICE_BYTES:
            _enqueue(chat_id, "That voice note is too big to fetch.")
            return
        _open_turn(chat_id, spoken=True)
        _spawn(_run_voice, player, chat_id, voice, token, who)
        return

    cmd, arg = _split_command(text)

    if cmd:
        # /start is Telegram's own first-run command; it is not in the table
        # because it is not a capability — it is the greeting, and it hands over
        # the keyboard that makes the rest reachable by thumb.
        if cmd == "start":
            _put({"kind": "text", "chat": chat_id, "markup": _keyboard(cfg),
                  "text": "Approved.\n\n" + _help_text(cfg)})
            _register_menu(chat_id, cfg)
            return

        op = _op_by_command(cmd, cfg)
        if op is None:
            # A slash token this bridge does not know is not prose — telling the
            # model to interpret "/scren" would just waste a turn. A token it
            # DOES know but has switched off is a different answer, and the
            # difference matters: one is a typo, the other is a switch.
            hidden = next((o for o in _OPS if o.get("cmd") == cmd), None)
            _enqueue(chat_id, _gate_notice(hidden) if hidden else
                     f"There is no /{cmd}. Send /help for the list.")
            return

        if op["cmd"] == "help":
            _put({"kind": "text", "chat": chat_id, "markup": _keyboard(cfg),
                  "text": _help_text(cfg)})
            return
        if op["cmd"] == "id":
            _enqueue(chat_id, f"This chat's ID is {chat_id}.")
            return
        if op["cmd"] == "status":
            _enqueue(chat_id, _status_text())
            return
        if op["cmd"] == "stop":
            _enqueue(chat_id, "Closing the remote bridge. Start it again from the computer.")
            _stop_now()
            return
        if op["cmd"] == "panic":
            _panic(player, chat_id)
            return

        # An op that cannot be taken back is shown first and done second.
        if op.get("confirm") and not _repeat_ok(chat_id, op["cmd"]):
            try:
                subject = _ops.preview(op["action"]) if _ops else ""
            except Exception as e:
                _enqueue(chat_id, str(e))
                return
            _arm_repeat(chat_id, op["cmd"])
            _enqueue(chat_id, f"{op['icon']} /{op['cmd']} → {subject}\n\n"
                              f"Send /{op['cmd']} again within {_REPEAT_WINDOW}s "
                              f"to confirm.")
            return

        # Everything else is payload, produced off this thread.
        _open_turn(chat_id)
        _spawn(_run_op, player, op, [chat_id], arg)
        return

    # -- a real command, in whatever language it was written -----------------
    _open_turn(chat_id)
    _deliver_text(player, chat_id, who, text)


def _deliver_text(player, chat_id: int, who: str, text: str,
                  spoken: bool = False) -> None:
    """The last few inches, shared by something typed and something said."""
    if spoken:
        _open_turn(chat_id, spoken=True)      # re-armed: transcription took time
    text = _attach_file(chat_id, text)
    if _deliver(player, text):
        _state["seen"] = _state.get("seen", 0) + 1
        _log(player, f"[Telegram {who}]{' 🎤' if spoken else ''}: {text}")
    else:
        _state["turn_open"] = False
        _enqueue(chat_id, "No live session right now — the command was not run.")


def _try_pair(player, chat_id: int, text: str, cfg: dict, allowed: list[int]) -> None:
    """The whole of what an unapproved chat is allowed to reach."""
    armed = time.monotonic() < _state.get("pair_until", 0.0)
    code  = str(cfg.get("pairing_code") or "").strip()

    if armed and code and _code_matches(text, code):
        allowed.append(chat_id)
        try:
            save_plugin_config(_NS, {
                "allowed_chat_ids": ",".join(str(i) for i in allowed),
                "pairing_code": "",           # consumed — one use only
            })
        except Exception as e:
            _log(player, f"SYS: Telegram remote could not save the pairing: {e}")
            return
        _state["pair_until"] = 0.0            # one success closes the window
        _state["pair_tries"] = 0
        cfg = _cfg()
        _put({"kind": "text", "chat": chat_id, "markup": _keyboard(cfg),
              "text": "Paired. This chat can now command the computer.\n\n"
                      + _help_text(cfg)})
        _register_menu(chat_id, cfg)
        _log(player, f"SYS: Telegram remote paired with chat {chat_id}.")
        return

    # Silence towards Telegram — answering would confirm to a stranger that the
    # bot is live. But somebody trying to reach your computer IS news, so the
    # desk hears about it, throttled so it cannot be used to flood the log.
    if armed and code:
        _state["pair_tries"] = _state.get("pair_tries", 0) + 1
        if _state["pair_tries"] >= _PAIR_TRIES:
            _state["pair_until"] = 0.0
            _log(player, "SYS: Telegram remote closed pairing — too many wrong "
                         "codes. Say \"pair my phone\" here to open it again.")
            return

    now = time.monotonic()
    if now - _state.get("refused_at", 0.0) > _REFUSE_NOTICE:
        _state["refused_at"] = now
        print(f"[TelegramRemote] refused chat {chat_id} (not approved)")
        _log(player, f"SYS: Telegram remote refused an unapproved chat ({chat_id}).")


def _stop_now() -> None:
    with _lock:
        ev = _state.get("stop")
        if _state.get("running"):
            _state["stopping"] = True
    if ev:
        ev.set()


def _panic(player, chat_id: int | None = None) -> str:
    """Stop and forget, in one step, from anywhere.

    Unpairing is a desk action because approving a phone should be — but the
    moment you need to revoke one is precisely the moment you are not at the
    desk. Widening permissions stays local; withdrawing them does not.
    """
    cfg = _cfg()
    for cid in _parse_ids(cfg.get("allowed_chat_ids", "")):
        _put({"kind": "menu_clear", "chat": cid})
    if chat_id:
        _enqueue(chat_id, "Bridge closed and every approved chat erased.")
    try:
        save_plugin_config(_NS, {"allowed_chat_ids": ""})
    except Exception as e:
        _log(player, f"SYS: Telegram remote could not clear the approved chats: {e}")
        return f"Sir, I could not clear the approved chats: {e}"
    _state["pair_until"] = 0.0
    _log(player, "SYS: Telegram remote — PANIC: bridge closed, every approved chat erased.")
    _stop_now()
    return ("The bridge is closed and every approved phone has been erased. "
            "Nothing can reach this computer from outside until you pair one again.")


def _worker(player, stop: threading.Event, token: str) -> None:
    sess   = _session()
    offset = _drop_backlog(sess, token)
    # The floor is set AFTER the backlog call, so it is measured on the clock we
    # have just corrected — and it is what actually holds if that call failed.
    _state["floor_ts"] = _now()
    fails = 0

    cfg = _cfg()
    # The desk gets the state, not just the event. "Listening" alone left people
    # who had not paired yet with nothing to act on — and the pairing code has to
    # be sent AFTER this line, because the backlog drop just threw away anything
    # sent before it.
    ids = _parse_ids(cfg.get("allowed_chat_ids", ""))
    if ids:
        _log(player, "SYS: Telegram remote is listening.")
        for cid in ids:
            _register_menu(cid, cfg)
    else:
        _log(player, f"SYS: Telegram remote is listening — no chat approved yet. "
                     f"Send your pairing code to {_state.get('bot') or 'the bot'} NOW; "
                     f"anything sent before this moment was discarded, and pairing "
                     f"closes in {_PAIR_WINDOW // 60} minutes.")
    try:
        while not stop.is_set():
            try:
                params = {"timeout": _POLL_TIMEOUT, "allowed_updates": '["message"]'}
                if offset is not None:
                    params["offset"] = offset
                r = _api(sess, token, "getUpdates", params=params,
                         timeout=_HTTP_TIMEOUT)
                if r.status_code == 401:
                    _log(player, "SYS: Telegram rejected the bot token — remote stopped.")
                    return
                if r.status_code == 409:
                    _log(player, "SYS: Another Telegram poller is using this bot — remote stopped.")
                    return
                data = r.json()
                if not data.get("ok"):
                    raise RuntimeError(data.get("description", "getUpdates failed"))
                fails = 0
                for upd in data.get("result") or []:
                    offset = upd["update_id"] + 1
                    if stop.is_set():
                        break
                    # Only "message". An edited_message must never re-fire a command.
                    msg = upd.get("message")
                    if isinstance(msg, dict):
                        try:
                            _handle(player, msg, token)
                        except Exception as e:
                            print(f"[TelegramRemote] handler error: {_scrub(e, token)}")

                # A clock that drifts, or is corrected while the bridge runs, is
                # followed rather than baked in at startup. The long poll itself
                # is not a safe sample (see _note_skew), so a short one is made
                # on purpose when the last measurement gets old.
                if time.monotonic() - _state.get("skew_at", 0.0) > _SKEW_REFRESH:
                    try:
                        _note_skew(_api(sess, token, "getMe", timeout=15))
                    except Exception:
                        pass
            except Exception as e:
                fails += 1
                print(f"[TelegramRemote] poll error: {_scrub(e, token)}")
                if fails == 5:
                    _log(player, "SYS: Telegram remote is having trouble reaching "
                                 "Telegram — still retrying.")
                stop.wait(min(60, 2 ** min(fails, 5)))
    finally:
        with _lock:
            _state["running"]  = False
            _state["stopping"] = False
        _remove_relay(player)
        _state["turn_open"] = False
        _state["turn_chat"] = None
        _state["pair_until"] = 0.0
        _state["lease_chat"] = None
        _state["repeat"] = None
        try:
            if sess is not None:
                sess.close()
        except Exception:
            pass
        _log(player, "SYS: Telegram remote stopped listening.")


# ── entry point ──────────────────────────────────────────────────────────────

def _push(player, action: str, target: str) -> str:
    """Model-side screenshot / readout: produce it and send it to the phone."""
    cfg = _cfg()
    op  = next((o for o in _MODEL_OPS if o["action"] == action), None)
    if op is None:
        return f"I can't do '{action}' over the Telegram remote."

    # The gate is checked HERE as well as at the slash command, because this is
    # the path a remote message can reach by asking the model instead. Free text
    # from a phone becomes an ordinary user turn upstream, so without this check
    # the toggle would guard the shortcut and not the request.
    if not _gate_ok(op, cfg):
        notice = _gate_notice(op)
        if _turn_active():
            return notice + " I won't turn it on from a remote request."
        return notice + " Turn it on there and ask me again."

    if not _state.get("running"):
        return ("The Telegram remote isn't running, so there's nowhere to send it. "
                "Ask me to start the telegram remote first.")

    chats = _targets(cfg)
    if not chats:
        return ("No phone is approved yet, so there's nobody to send it to. "
                "Pair one first.")

    _spawn(_run_op, player, op, chats, target or "")
    where = "your phone" if len(chats) == 1 else f"{len(chats)} approved chats"
    return f"On its way to {where}."


def run(parameters: dict, player=None, session_memory=None) -> str:
    if not _HAS_CONFIG:
        return ("This Mark is too old for the Telegram remote — it has no plugin "
                "settings store, so there's nowhere to keep the bot token or the "
                "approved chats. Update to Mark LII or newer and it will work as it is.")

    action = (parameters.get("action") or "start").strip().lower()
    target = str(parameters.get("target") or "").strip()

    # -------- PUSH (screenshot / hardware readout) --------
    if action in _PUSH_ACTIONS:
        return _push(player, action, target)

    # -------- STATUS --------
    if action in ("status", "state", "info"):
        return _status_text()

    # -------- PAIR --------
    if action in ("pair", "approve"):
        if not str(_cfg().get("pairing_code") or "").strip():
            return ("There's no pairing code set, so there is nothing for a phone "
                    "to send. Put one in settings → TELEGRAM REMOTE first.")
        _state["pair_until"] = time.monotonic() + _PAIR_WINDOW
        _state["pair_tries"] = 0
        _log(player, f"SYS: Telegram remote — pairing open for "
                     f"{_PAIR_WINDOW // 60} minutes.")
        tail = ("" if _state.get("running") else
                " The bridge isn't listening yet, so start it before you send the code.")
        return (f"Pairing is open for {_PAIR_WINDOW // 60} minutes. Send the "
                f"pairing code to {_state.get('bot') or 'the bot'} from the phone "
                f"you want to approve.{tail}")

    # -------- PANIC --------
    if action in ("panic", "lockdown"):
        return _panic(player)

    # -------- UNPAIR --------
    if action in ("unpair", "revoke", "forget"):
        for cid in _parse_ids(_cfg().get("allowed_chat_ids", "")):
            _put({"kind": "menu_clear", "chat": cid})
        try:
            save_plugin_config(_NS, {"allowed_chat_ids": ""})
        except Exception as e:
            return f"Sir, I could not clear the approved chats: {e}"
        _state["pair_until"] = 0.0
        _log(player, "SYS: Telegram remote — every approved chat revoked.")
        return ("Every approved chat has been revoked. No phone can command this "
                "computer until you pair one again with a new pairing code.")

    # -------- STOP --------
    if action in ("stop", "off", "disable", "close"):
        with _lock:
            ev = _state.get("stop")
            running = _state.get("running")
            if running and ev is not None:
                _state["stopping"] = True
        if not running or ev is None:
            return "The Telegram remote is not running."
        ev.set()
        return "Telegram remote closed. Nothing can reach the computer from outside now."

    # An action nobody above claimed is NOT a start. The parameter defaults to
    # "start" when it is absent, which is right, but letting an unrecognised
    # value fall through to the same place means a model that misspells its own
    # enum opens a remote-control channel instead of being corrected. The enum
    # is generated from the table, so this is only ever reached by a value the
    # schema did not offer.
    if action and action not in ("start", "on", "enable", "open", "listen"):
        return (f"I don't have a '{action}' action for the Telegram remote. "
                f"I can " + ", ".join(o["action"] for o in _MODEL_OPS) + ".")

    # -------- START --------
    # Claimed under the lock, not merely checked under it: two starts arriving
    # together would otherwise both pass and Telegram would answer the second
    # poller with a 409, killing the pair.
    with _lock:
        if _state.get("stopping"):
            return ("The Telegram remote is still closing the last connection — "
                    "give it a moment and ask again.")
        if _state.get("running"):
            return "The Telegram remote is already listening."
        _state["running"] = True

    def _release() -> None:
        with _lock:
            _state["running"] = False

    cfg   = _cfg()
    token = _clean_token(cfg.get("bot_token"))
    if not token:
        _release()
        return ("There's no bot token set. Open the settings, create a bot with "
                "Telegram's @BotFather, paste the token into TELEGRAM REMOTE and "
                "set a pairing code.")

    ids  = _parse_ids(cfg.get("allowed_chat_ids", ""))
    code = str(cfg.get("pairing_code") or "").strip()
    if not ids and not code:
        _release()
        return ("No approved chats and no pairing code, so nothing could ever be "
                "accepted. Set a pairing code in the settings first, then message "
                "that code to your bot once to approve your phone.")

    if player is None:
        _release()
        return "Sir, I have no interface to route remote commands into."

    try:
        r = _http().get(_API.format(token=token, method="getMe"), timeout=15)
        _note_skew(r)
        me = r.json()
        if not me.get("ok"):
            _release()
            return f"Telegram refused the token: {me.get('description', 'unknown error')}."
        bot_name = "@" + (me.get("result", {}).get("username") or "bot")
    except Exception as e:
        _release()
        return f"Sir, I couldn't reach Telegram: {_scrub(e, token)}"

    stop = threading.Event()
    _state.update({
        "running": True, "stopping": False,
        "stop": stop, "outbox": queue.Queue(maxsize=200),
        "bot": bot_name, "started": time.monotonic(), "seen": 0, "shots": 0,
        "player": player, "turn_chat": None, "turn_open": False,
        "turn_end": 0.0, "turn_quiet": 0.0, "floor_ts": 0.0,
        "repeat": None, "lease_chat": None, "lease_until": 0.0,
        "confirm_at": "", "desk_seen": time.monotonic(), "alerts": 0,
        "alert_last": "", "turn_voice": False, "spoken": 0, "heard": 0,
        "last_file": None, "files": 0,
        # Arm pairing only on a first run. Once a phone is approved the window
        # stays shut until it is asked for, so a bridge left running for a month
        # is not a month of open pairing.
        "pair_until": (time.monotonic() + _PAIR_WINDOW) if (not ids and code) else 0.0,
        "pair_tries": 0,
    })
    _rate.clear()

    _install_relay(player)

    sender = threading.Thread(target=_sender_loop, args=(token, stop),
                              daemon=True, name="telegram-remote-send")
    sender.start()
    thread = threading.Thread(target=_worker, args=(player, stop, token),
                              daemon=True, name="telegram-remote-poll")
    thread.start()
    watch = threading.Thread(target=_watcher, args=(player, stop),
                             daemon=True, name="telegram-remote-watch")
    watch.start()
    _state["sender"], _state["thread"], _state["watch"] = sender, thread, watch

    extras = "" if _ops else (" (the extras file '_telegram_ops.py' is missing, "
                              "so /screen and /sys are unavailable)")
    if ids:
        return (f"Telegram remote is live on {bot_name}. Message it from any of your "
                f"{len(ids)} approved chats and I'll act on it here.{extras}")
    return (f"Telegram remote is live on {bot_name}, but no chat is approved yet. "
            f"Send your pairing code to {bot_name} within {_PAIR_WINDOW // 60} "
            f"minutes and that chat becomes the only one I'll obey.{extras}")


# ── starting without being asked, when the desk has asked once ───────────────

def _player_candidates():
    """Objects that might be the running interface.

    A plugin is handed `player` when a TOOL CALL arrives; nothing hands one to a
    plugin at launch, because there is no launch hook to hand it through. What
    the app does do is bind interface methods into two shared modules while it
    starts — the confirmation gate's banner callbacks (main.py:2318) and the
    memory trimmer's notifier (main.py:2323) — and a bound method carries the
    object it belongs to. That is read here, never written.

    It is deliberately a guess that is then CHECKED, rather than a lookup that
    is trusted: whatever comes back is only used if it has the two methods this
    bridge actually needs. If the app ever binds a lambda instead, or moves
    these, nothing breaks — the waiter simply never finds anything and the
    auto-start quietly does not happen.
    """
    try:
        from core import confirm
        for cb in (confirm._show_cb, confirm._log_cb, confirm._hide_cb):
            owner = getattr(cb, "__self__", None)
            if owner is not None:
                yield owner
    except Exception:
        pass
    try:
        from memory import memory_manager
        owner = getattr(getattr(memory_manager, "_trim_notifier", None),
                        "__self__", None)
        if owner is not None:
            yield owner
    except Exception:
        pass


def _usable_player(obj) -> bool:
    """The contract _deliver needs, checked rather than assumed.

    `on_text_command` is a property on the interface that returns whatever
    main.py put there, so it is not callable until the app has wired itself up.
    That makes this both a type check and a readiness check, in one expression.
    """
    return (callable(getattr(obj, "write_log", None))
            and callable(getattr(obj, "on_text_command", None)))


def _autostart() -> None:
    """Wait for the app to finish coming up, then start the bridge once.

    The wait is long because the first launch of a fresh install blocks in
    `wait_for_api_key()` until somebody types a key — which can be minutes, and
    is not a failure. When the time runs out, this gives up in silence: the
    setting is a convenience, and a convenience that cannot be delivered must
    not turn into an error message on a HUD nobody was looking at.
    """
    deadline = time.monotonic() + _AUTOSTART_WAIT
    while time.monotonic() < deadline:
        for obj in _player_candidates():
            if not _usable_player(obj):
                continue
            # A moment more, so the first line lands after the app has finished
            # announcing itself rather than in the middle of it.
            time.sleep(_AUTOSTART_SETTLE)
            try:
                said = run({"action": "start"}, player=obj)
                # Neutral wording, because this same line carries the refusals:
                # no token, no approved chat, already running. "Started itself —
                # there is no bot token" would be a sentence that contradicts
                # itself halfway through.
                _log(obj, f"SYS: Telegram remote (auto-start) — {said}")
            except Exception as e:
                print(f"[TelegramRemote] auto-start failed: {e}")
            return
        time.sleep(_AUTOSTART_POLL)


# The only code in this file that runs at plugin-discovery time, and on a
# default install it is one config read that answers "no" — measured below the
# cost of the stat() the settings cache already pays. Nothing is imported,
# no thread is created and no socket is opened unless the switch is on.
if _HAS_CONFIG:
    try:
        if _cfg().get("start_on_launch"):
            threading.Thread(target=_autostart, daemon=True,
                             name="telegram-remote-autostart").start()
    except Exception:
        pass
