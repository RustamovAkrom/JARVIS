"""
JARVIS plugin — Chat Takeover (Live-session screen vision + auto-reply).

"JARVIS, take over this conversation" — JARVIS watches the chat that is
open on the screen and, whenever the other person sends a new message,
reads it straight from a screenshot (no clicking, no selecting, no
copying), writes a reply in the user's own texting style and sends it
through the real input box.

Vision + reply generation run over a persistent Gemini LIVE session
(same native-audio model the rest of the app uses). Live models output
audio only these days, so the engine harvests the *output transcript*
as the text channel and discards the audio bytes — verified to be a
verbatim copy of the reply, Turkish characters and punctuation intact.
The session remembers the conversation, so the model itself tracks
which messages it has already answered. If Live can't connect, the
plugin silently falls back to stateless REST calls (gemini-2.5-flash)
so the feature never dies.

No clicking, no coordinates: the user clicks into the message input box
themselves before/when starting, and the plugin simply pastes into the
already-focused box (focus stays there after every send). A focus guard
remembers which window the takeover started on and holds replies if the
focus ever moves elsewhere, so a reply can never be pasted into the
wrong application. Messages are read purely by vision, so it works with
any messaging app: WhatsApp Web, Telegram, Instagram, Discord, Slack...

Ways to stop: say "take the conversation back" (action=stop), slam the
mouse into the top-left screen corner (pyautogui failsafe), or let it
time out after `duration_minutes` (default 10).
"""

import asyncio
import base64
import io
import json
import re
import sys
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

PLUGIN = {
    "name": "chat_takeover",
    "description": (
        "Takes over the chat/messaging conversation currently visible on the "
        "user's SCREEN and automatically replies to new incoming messages in "
        "the user's style until told to stop. Use action='start' when the user "
        "says things like 'konuşmayı devral', 'sen devam et', 'take over this "
        "chat', 'answer him for me'. Use action='stop' when the user says "
        "'konuşmayı geri al', 'devri bırak', 'stop replying', 'I'll take it "
        "from here'. Use action='status' when asked whether the takeover is "
        "running. This tool WATCHES the screen by itself every few seconds — "
        "NEVER use screen_process or send_message for an ongoing chat "
        "takeover. Pass the user's exact spoken words in 'query' (they may "
        "include instructions like 'tell him I'm in a meeting'). IMPORTANT: "
        "call this tool exactly ONCE per spoken request — never issue a "
        "second/duplicate start call for the same request."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["start", "stop", "status"],
                "description": "start = begin the takeover, stop = hand the "
                               "conversation back to the user, status = report "
                               "whether it is running.",
            },
            "query": {
                "type": "STRING",
                "description": "The user's exact request, verbatim, in their "
                               "own language (may contain extra instructions "
                               "for how to reply).",
            },
            "duration_minutes": {
                "type": "NUMBER",
                "description": "Optional safety time limit in minutes "
                               "(default 10).",
            },
        },
        "required": ["action"],
    },
}

# fallback if main.py isn't loaded (tests) — inside the app the model name is
# read live from main.LIVE_MODEL, so upgrading main.py upgrades this plugin too
_LIVE_MODEL       = "models/gemini-2.5-flash-native-audio-preview-12-2025"
_REST_MODEL       = "gemini-2.5-flash"   # fallback engine only
_POLL_SECONDS     = 10       # how often the screen is checked
_DEFAULT_MINUTES  = 10       # auto-stop safety limit
_PIXEL_DELTA      = 15       # per-pixel difference that counts as "changed"
_CHANGED_RATIO    = 0.002    # >0.2% of pixels changed = screen changed
                             # (a new chat bubble ≈ 1-2%; clock digits ≈ 0.03%)
_MAX_MISSES       = 4        # consecutive "no chat visible" checks before giving up
_MAX_ERRORS       = 5        # consecutive engine errors → Live falls back to REST,
                             # REST gives up
_MAX_REPLY_CHARS  = 500      # longer output = model chatter, never paste it
_MAX_BURST        = 3        # a reply may be split into at most this many messages
_BURST_GAP        = 2.0      # seconds between messages of one burst
_START_DEBOUNCE   = 20       # duplicate start calls within this window stay silent
_IMG_MAX_W        = 1280
_JPEG_Q           = 82
_PANEL_EXCHANGES  = 10       # last N exchanges shown in the HUD panel

# exactly one of these three outputs per turn — everything else gets pasted
_LIVE_SYSTEM = (
    "You are a silent chat-takeover engine answering on behalf of the user. "
    "Every turn you receive one screenshot of the user's screen.\n"
    "In the chat window: the user's OWN messages are aligned RIGHT "
    "(outgoing); the OTHER person's messages are aligned LEFT (incoming).\n"
    "Rules — produce exactly ONE of these three outputs per turn, and "
    "nothing else, ever:\n"
    "1. If NO chat conversation is visible in the screenshot, say exactly: "
    "NOSCREEN — a chat means a REAL messaging application conversation UI "
    "with message bubbles and an input field (WhatsApp, Telegram, Instagram, "
    "Discord...); terminals, code editors, documents and websites do NOT "
    "count, and text that merely QUOTES or logs chat messages (in a "
    "terminal, code, test output or article) is NOT a chat.\n"
    "2. If a chat is visible but the newest message in it is the user's own "
    "(outgoing/right side), or it is an incoming message you have ALREADY "
    "replied to earlier in this session, say exactly: STANDBY. IMPORTANT: "
    "some apps sometimes fail to display the user's own sent messages — if "
    "the newest incoming message is one you already answered this session, "
    "output STANDBY even though your reply is not visible in the screenshot; "
    "trust your own memory of what you answered over the screenshot.\n"
    "3. If the newest message in the conversation is an incoming (left-side) "
    "message you have NOT replied to yet, say ONLY the reply itself — "
    "written AS THE USER: same language as the conversation, mimicking the "
    "texting style and tone of the user's previous right-side messages. "
    "The reply is 1 to 3 chat messages: when more than one, separate them "
    "with the single word NEXTMSG (that word is never sent — it only splits "
    "the messages, which are then sent one after another). Each message is "
    "at most 2 sentences and typically 4-8 words — NEVER a bare one-or-two "
    "word reply unless the moment truly calls for it; go longer when the "
    "incoming message deserves it. Write like real casual texting between "
    "friends: relaxed wording, mirror the OTHER person's current mood and "
    "energy (playful if they joke, calm and warm if they're upset), and use "
    "almost no punctuation — no commas, no period at the end; a question or "
    "exclamation mark only when it really helps. Use 2-3 messages only when "
    "it fits the user's style (e.g. a reaction, then a follow-up question). "
    "No preamble, no explanation, no signatures, never mention being an "
    "AI.\n"
    "Whatever you say is pasted VERBATIM into the chat and sent to the other "
    "person — so never say anything that is not meant to be sent, except the "
    "exact single words NOSCREEN or STANDBY and the separator word NEXTMSG.\n"
    "LANGUAGE RULE: these instructions being in English is IRRELEVANT — the "
    "reply must ALWAYS be written in the language used in the conversation "
    "itself (the other person's messages). Never switch languages between "
    "replies unless the conversation itself switches."
)


# ── config helpers (same pattern as actions/screen_processor.py) ─────────────

def _config() -> dict:
    try:
        return json.loads(
            (BASE_DIR / "config" / "api_keys.json").read_text(encoding="utf-8")
        )
    except Exception:
        return {}


# ── module state (persists across run() calls — loader imports us once) ──────

_state = {
    "thread":    None,    # the takeover loop thread
    "stop":      None,    # threading.Event to end it
    "exchanges": [],      # list of (their_message_or_empty, our_reply)
    "engine":    "",      # "live" / "rest" — which engine is/was in use
    "ended":     "",      # why the last run ended (for status reports)
}
_state_lock = threading.Lock()


def _running() -> bool:
    t = _state["thread"]
    return bool(t and t.is_alive())


# ── screen capture ───────────────────────────────────────────────────────────

def _grab_screen():
    """Return (raw_rgb_np, jpeg_bytes) of the primary monitor."""
    import mss
    import numpy as np
    import PIL.Image

    with mss.mss() as sct:
        monitors = sct.monitors
        target = monitors[1] if len(monitors) > 1 else monitors[0]
        shot = sct.grab(target)
        raw = np.frombuffer(shot.rgb, dtype=np.uint8)

    img = PIL.Image.frombytes("RGB", shot.size, shot.rgb)
    img.thumbnail((_IMG_MAX_W, _IMG_MAX_W), PIL.Image.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_Q)
    return raw, buf.getvalue()


def _screen_changed(prev, cur) -> bool:
    import numpy as np
    if prev is None or prev.shape != cur.shape:
        return True
    delta = np.abs(prev.astype(np.int16) - cur.astype(np.int16))
    # a global mean would drown a new chat bubble (~1-2% of the screen), so
    # count clearly-changed pixels instead — small-but-real changes register
    # while clock digits and cursor blinks stay below the bar
    return bool(np.mean(delta > _PIXEL_DELTA) > _CHANGED_RATIO)


# ── engines ──────────────────────────────────────────────────────────────────
# Both expose: check(jpg, last_reply) -> "NOSCREEN" | "STANDBY" | reply text
# and close(). check() may raise — the loop counts errors and falls back.

class _LiveEngine:
    """Persistent Gemini Live session. Audio out is discarded; the output
    transcript is the text channel (verified verbatim, incl. Turkish)."""

    def __init__(self, api_key: str, system_prompt: str):
        self._api_key = api_key
        self._system  = system_prompt
        self._client  = None
        self._cm      = None
        self._session = None
        self._loop    = asyncio.new_event_loop()
        self._thread  = threading.Thread(target=self._loop.run_forever,
                                         daemon=True, name="chat-takeover-live")
        self._thread.start()

    def check(self, jpg: bytes, recent: list) -> str:
        fut = asyncio.run_coroutine_threadsafe(
            self._check_async(jpg, recent), self._loop)
        return fut.result(timeout=90)

    def close(self) -> None:
        try:
            asyncio.run_coroutine_threadsafe(
                self._close_session(), self._loop).result(timeout=5)
        except Exception:
            pass
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass

    async def _connect(self):
        if self._session is not None:
            return
        from google import genai
        from google.genai import types as gtypes

        if self._client is None:
            self._client = genai.Client(api_key=self._api_key,
                                        http_options={"api_version": "v1beta"})
        kwargs = dict(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            system_instruction=self._system,
        )
        try:  # keeps long sessions alive past the 15-min audio-session cap
            kwargs["context_window_compression"] = \
                gtypes.ContextWindowCompressionConfig(
                    sliding_window=gtypes.SlidingWindow())
        except AttributeError:
            pass
        model = (_config().get("chat_takeover_live_model")
                 or getattr(sys.modules.get("main"), "LIVE_MODEL", None)
                 or _LIVE_MODEL)
        self._cm = self._client.aio.live.connect(
            model=model, config=gtypes.LiveConnectConfig(**kwargs))
        self._session = await asyncio.wait_for(self._cm.__aenter__(), 25)

    async def _close_session(self):
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass
        self._cm = self._session = None

    async def _check_async(self, jpg: bytes, recent: list) -> str:
        try:
            return await self._turn(jpg, recent)
        except Exception:
            # connection dropped (10-min GoAway etc.) — reconnect once, retry
            await self._close_session()
            return await self._turn(jpg, recent)

    async def _turn(self, jpg: bytes, recent: list) -> str:
        await self._connect()
        note = _history_note(recent)
        await asyncio.wait_for(
            self._session.send_client_content(
                turns={"parts": [
                    {"inline_data": {
                        "mime_type": "image/jpeg",
                        "data": base64.b64encode(jpg).decode("ascii")}},
                    {"text": "Screenshot check — respond now with exactly "
                             "one of your three outputs (NOSCREEN / STANDBY "
                             "/ the reply), even if nothing changed since "
                             "the last screenshot. Reply language = the "
                             "conversation's own language, NOT English by "
                             "default." + note},
                ]},
                turn_complete=True),
            timeout=20)

        transcript: list[str] = []

        async def _drain():
            async for resp in self._session.receive():
                sc = getattr(resp, "server_content", None)
                if sc and sc.output_transcription and sc.output_transcription.text:
                    transcript.append(sc.output_transcription.text)

        await asyncio.wait_for(_drain(), timeout=60)

        # transcription can trail a beat behind the audio turn — grab any
        # late chunks so replies are never cut mid-word ("İyiyim, sağ...")
        try:
            await asyncio.wait_for(_drain(), timeout=1.5)
        except asyncio.TimeoutError:
            pass

        return "".join(transcript).strip()


class _RestEngine:
    """Stateless fallback: one generate_content call per check, strict JSON."""

    def __init__(self, api_key: str, user_instruction: str):
        self._api_key = api_key
        self._instruction = user_instruction

    def check(self, jpg: bytes, recent: list) -> str:
        from google import genai
        from google.genai import types as gtypes

        prompt = (
            "You are an AI that has temporarily taken over the USER's side of "
            "a chat conversation. The image is a screenshot of the user's "
            "screen showing a messaging app.\n"
            "The user's OWN messages are aligned RIGHT (outgoing); the OTHER "
            "person's messages are aligned LEFT (incoming).\n"
            + (f'Extra instruction from the user: "{self._instruction}"\n'
               if self._instruction else "")
            + (_history_note(recent).strip() + "\n" if recent else "")
            + "Return ONLY minified JSON — no markdown fences — with keys:\n"
            ' "chat_visible": true/false — true ONLY for a real messaging '
            "application conversation with message bubbles (WhatsApp, "
            "Telegram, Discord...); terminals, code editors, documents and "
            "websites do NOT count — text that merely LOOKS like dialogue "
            "(logs, test output, quoted strings) is NOT a chat; when not "
            "CERTAIN, use false,\n"
            ' "needs_reply": true ONLY if the newest message in the whole '
            "conversation is an incoming (left-side) message that has not "
            "been answered,\n"
            ' "reply": when needs_reply is true, the reply written AS THE '
            "USER — same language as the conversation, mimic the user's "
            "texting style; 1 to 3 chat messages separated by the single "
            "word NEXTMSG, each at most 2 sentences and typically 4-8 words "
            "(never a bare 1-2 word reply unless truly fitting), casual "
            "texting tone mirroring the other person's mood, almost no "
            "punctuation (no commas, no trailing period); no signatures, no "
            "AI mentions — else null."
        )
        client = genai.Client(api_key=self._api_key)
        resp = client.models.generate_content(
            model=_REST_MODEL,
            contents=[gtypes.Part.from_bytes(data=jpg, mime_type="image/jpeg"),
                      prompt],
        )
        text = (resp.text or "").strip()
        if "{" in text and "}" in text:
            text = text[text.find("{"): text.rfind("}") + 1]
        data = json.loads(text)
        if not data.get("chat_visible"):
            return "NOSCREEN"
        reply = (data.get("reply") or "").strip()
        if not data.get("needs_reply") or not reply:
            return "STANDBY"
        return reply

    def close(self) -> None:
        pass


# ── input-box calibration & sending ──────────────────────────────────────────

def _split_messages(verdict: str) -> list[str]:
    """Split a burst reply on the NEXTMSG separator word (max _MAX_BURST)."""
    parts = [p.strip() for p in re.split(r"\s*NEXTMSG\s*", verdict) if p.strip()]
    return parts[:_MAX_BURST]


def _history_note(recent: list) -> str:
    """Reminder passed to the engine every turn: the replies already sent,
    so the same incoming message never gets answered twice — even when the
    app fails to render the sent replies (known Instagram glitch)."""
    if not recent:
        return ""
    listed = "; ".join(f'"{r}"' for r in recent)
    return (f" (Memory aid — replies you already sent, oldest first: "
            f"{listed}. Never answer an incoming message you already "
            f"answered, even if these replies are not visible on the "
            f"screenshot: in that case output STANDBY.)")


def _casualize(msg: str) -> str:
    """Soften ASR-style punctuation into casual texting. The transcript layer
    inserts commas/periods the model never intended — drop mid-sentence
    commas (keeping numeric ones like 3,5) and the trailing period (keeping
    ellipses, ? and !)."""
    out = msg.replace(", ", " ")
    if out.endswith(".") and not out.endswith(".."):
        out = out[:-1]
    return " ".join(out.split())


def _active_window_id():
    """Identity of the currently focused window (handle on Windows, title
    elsewhere). Returns None when it can't be determined — guard disabled."""
    try:
        import pygetwindow as gw
        w = gw.getActiveWindow()
        if w is None:
            return None
        return getattr(w, "_hWnd", None) or getattr(w, "title", None) or None
    except Exception:
        return None


def _send_reply(reply: str, os_name: str) -> None:
    """Paste into the ALREADY-FOCUSED input box — never clicks anything.
    The user put the caret in the message box; every chat app keeps focus
    there after sending, so no coordinates are ever needed."""
    import pyautogui
    import pyperclip

    old_clip = None
    try:
        old_clip = pyperclip.paste()
    except Exception:
        pass

    pyperclip.copy(reply)          # paste instead of typing — non-ASCII safe
    pyautogui.hotkey("command" if os_name == "mac" else "ctrl", "v")
    time.sleep(0.35)
    pyautogui.press("enter")
    time.sleep(0.2)

    if old_clip is not None:
        try:
            pyperclip.copy(old_clip)
        except Exception:
            pass


# ── the takeover loop (daemon thread) ────────────────────────────────────────

def _takeover_loop(user_instruction: str, duration_s: float, player,
                   stop_evt: threading.Event) -> None:
    import pyautogui  # noqa: F401  (failsafe stays enabled — corner = abort)

    def _log(msg: str) -> None:
        if player:
            try:
                player.write_log(msg)
            except Exception:
                pass

    def _panel() -> None:
        if not player:
            return
        lines = []
        for them, mine in _state["exchanges"][-_PANEL_EXCHANGES:]:
            if them:
                lines.append(f"→ Them: {them}")
            lines.append(f"← JARVIS: {mine}")
            lines.append("")
        try:
            player.show_content("💬 CHAT TAKEOVER", "\n".join(lines).strip())
        except Exception:
            pass

    api_key = _config().get("gemini_api_key", "")
    os_name = _config().get("os_system", "windows").lower()
    ended   = "finished"
    engine  = None

    try:
        # the focus guard learns its window at the FIRST paste (that's when
        # the user is certainly on the chat) — never at start, where they may
        # still be talking to JARVIS with the chat not even open yet
        focus_id = None

        system = _LIVE_SYSTEM
        if user_instruction:
            system += (f'\nExtra instruction from the user for how to reply: '
                       f'"{user_instruction}"')
        if _config().get("chat_takeover_engine", "live") == "rest":
            engine = _RestEngine(api_key, user_instruction)
            _state["engine"] = "rest"
        else:
            engine = _LiveEngine(api_key, system)
            _state["engine"] = "live"

        _log(f"JARVIS: Chat takeover armed ({_state['engine']} engine) — "
             f"pasting into the focused input box (no clicking); the focus "
             f"guard locks onto the chat window at the first reply. Watching "
             f"the screen every {_POLL_SECONDS}s.")

        recent_replies = []    # last 3 sent bursts — the dedup memory
        last_burst     = ""
        seen_chat      = False # NOSCREEN only counts after a chat was seen
        prev_raw       = None
        misses         = 0
        errors         = 0
        deadline       = time.time() + duration_s

        while not stop_evt.is_set() and time.time() < deadline:
            try:
                raw, jpg = _grab_screen()

                # screen identical to last check → nothing new, save the call
                # (only after the first look, so we never skip check #1)
                if prev_raw is not None and not _screen_changed(prev_raw, raw):
                    stop_evt.wait(_POLL_SECONDS)
                    continue
                prev_raw = raw

                verdict = engine.check(jpg,
                                       recent_replies).strip().strip('"\'')
                errors  = 0
                upper   = verdict.upper().rstrip(".!")

                if not verdict or upper.startswith("STANDBY"):
                    if not seen_chat and verdict:
                        _log("JARVIS: Chat in sight — monitoring the "
                             "conversation now.")
                    seen_chat = True
                    misses = 0

                elif upper.startswith("NOSCREEN"):
                    if not seen_chat:
                        # the user may still be opening the conversation —
                        # wait patiently, never give up before seeing a chat
                        if misses == 0:
                            _log("JARVIS: No chat visible yet — waiting for "
                                 "you to open the conversation.")
                        misses = 1
                    else:
                        misses += 1
                        if misses >= _MAX_MISSES:
                            ended = ("the chat window disappeared from the "
                                     "screen")
                            _log("JARVIS: Chat takeover ended — I can no "
                                 "longer see the conversation on screen.")
                            break

                elif len(verdict) > _MAX_REPLY_CHARS:
                    # a real chat reply is never this long — model chatter,
                    # never paste it
                    _log("JARVIS: Skipped an over-long engine output "
                         f"({len(verdict)} chars).")

                elif verdict == last_burst:
                    # identical regenerated reply (e.g. right after a
                    # reconnect) — don't double-send
                    pass

                else:
                    seen_chat = True
                    misses = 0
                    sent = []
                    for i, part in enumerate(_split_messages(verdict)):
                        if i and stop_evt.wait(_BURST_GAP):
                            break   # stopped mid-burst
                        cur_win = _active_window_id()
                        if focus_id is None:
                            # first paste — the user is on the chat right
                            # now, so THIS window becomes the only window
                            # we will ever paste into
                            focus_id = cur_win
                            if focus_id is not None:
                                _log("JARVIS: Focus guard locked onto the "
                                     "chat window.")
                        elif cur_win not in (None, focus_id):
                            _log("JARVIS: Reply held — focus is not on the "
                                 "chat window. Click the message box to let "
                                 "me continue.")
                            break
                        part = _casualize(part)
                        _send_reply(part, os_name)
                        sent.append(part)
                        _state["exchanges"].append(("", part))
                    if sent:
                        last_burst = verdict
                        recent_replies.append(" / ".join(sent))
                        del recent_replies[:-3]   # remember the last 3 bursts
                        prev_raw = None   # our messages change the screen
                        _log(f"JARVIS: Auto-replied ({len(sent)} message"
                             f"{'s' if len(sent) > 1 else ''}) — "
                             f"“{' | '.join(sent)[:120]}”")
                        _panel()

            except pyautogui.FailSafeException:
                ended = "failsafe (mouse in screen corner)"
                _log("JARVIS: Chat takeover aborted by failsafe corner.")
                break
            except Exception as e:
                errors += 1
                _log(f"JARVIS: Chat takeover hiccup ({errors}/{_MAX_ERRORS}): {e}")
                if errors >= _MAX_ERRORS:
                    if _state["engine"] == "live":
                        # Live keeps failing — degrade to REST, keep going
                        try:
                            engine.close()
                        except Exception:
                            pass
                        engine = _RestEngine(api_key, user_instruction)
                        _state["engine"] = "rest"
                        errors = 0
                        _log("JARVIS: Live engine unavailable — switched to "
                             "the REST fallback engine.")
                    else:
                        ended = f"too many consecutive errors (last: {e})"
                        break

            stop_evt.wait(_POLL_SECONDS)

        if not stop_evt.is_set() and time.time() >= deadline:
            ended = "time limit reached"
        if stop_evt.is_set():
            ended = "stopped by the user"
    finally:
        if engine is not None:
            try:
                engine.close()
            except Exception:
                pass
        _state["ended"] = ended
        _log(f"JARVIS: Chat takeover over ({ended}) — "
             f"{len(_state['exchanges'])} replies sent this run.")


# ── entry point ──────────────────────────────────────────────────────────────

def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "start").strip().lower()
    query  = (parameters.get("query") or "").strip()

    try:
        if action == "stop":
            with _state_lock:
                if not _running():
                    return ("There is no chat takeover running right now, "
                            "sir.")
                _state["stop"].set()
            _state["thread"].join(timeout=5)
            n = len(_state["exchanges"])
            return (f"The conversation is yours again, sir. I sent {n} "
                    f"repl{'y' if n == 1 else 'ies'} while I had it.")

        if action == "status":
            if _running():
                n = len(_state["exchanges"])
                return (f"The chat takeover is active on the "
                        f"{_state['engine'] or 'live'} engine — {n} "
                        f"repl{'y' if n == 1 else 'ies'} sent so far.")
            last = _state["ended"]
            return ("No takeover is running at the moment."
                    + (f" The last one ended: {last}." if last else ""))

        # ── action == "start" ────────────────────────────────────────────
        with _state_lock:
            if _running():
                # the main model sometimes issues a duplicate start call for
                # the same spoken request — keep JARVIS from announcing twice
                if time.time() - _state.get("started_at", 0) < _START_DEBOUNCE:
                    return ("(Duplicate start call ignored — the takeover is "
                            "already active and was already announced. Do "
                            "NOT announce it again; stay silent.)")
                return ("I'm already handling that conversation, sir — say "
                        "the word if you want it back.")

            if not _config().get("gemini_api_key"):
                return "I can't take over the chat — no API key is configured."
            try:
                import mss        # noqa: F401
                import pyautogui  # noqa: F401
                import pyperclip  # noqa: F401
                import PIL        # noqa: F401
            except ImportError as e:
                return (f"A required package is missing for the chat "
                        f"takeover: {e}. Please install it with pip.")

            try:
                minutes = float(parameters.get("duration_minutes")
                                or _DEFAULT_MINUTES)
            except (TypeError, ValueError):
                minutes = _DEFAULT_MINUTES
            minutes = max(1.0, min(minutes, 120.0))

            _state["exchanges"]  = []
            _state["ended"]      = ""
            _state["engine"]     = ""
            _state["started_at"] = time.time()
            _state["stop"]       = threading.Event()
            _state["thread"]    = threading.Thread(
                target=_takeover_loop,
                args=(query, minutes * 60, player, _state["stop"]),
                daemon=True,
                name="chat-takeover",
            )
            _state["thread"].start()

        return ("Taking over the conversation, sir. Just leave the cursor "
                "in the message input box — I never click anything, I type "
                "into the box you focused. I'll watch the chat and answer "
                f"any new message in your style for up to {minutes:.0f} "
                "minutes. Say the word when you want it back.")

    except Exception as e:
        return f"Sir, the chat takeover failed: {e}"
