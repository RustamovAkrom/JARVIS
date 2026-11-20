"""
Plugin — Video summarizer.

A link from any site (YouTube, Instagram, TikTok, X, Vimeo, a podcast page, a
plain .mp4 address — whatever yt-dlp can read) or a video/audio file dropped
into the app: the plugin gets the words and the assistant's own model gives
the user exactly what they asked for — a summary, an answer to a question,
the key points, notes. Follow-up questions about the same video reuse the
words it already has.

The model is told nothing about sites or languages: what to do with the
transcript is the user's own request, passed through word for word. How the
words are obtained (subtitles first, then the sound) is in _video_core.py.
"""
from __future__ import annotations

import time

from plugins import _video_core as vc

_NS = "video_summarizer"
_DEFAULT_MINUTES = 180

PLUGIN = {
    # For the plugin manager (core/plugin_loader): never sent to a model.
    "level": "basic",
    "label": "Video summarizer",
    "summary": "Summarizes any video or recording from a link or a file, and answers questions about it.",
    "setup": [
        "Say: \"Summarize this video\" with a link (YouTube, Instagram, TikTok, X and many more).",
        "Or drop a video or audio file into the box on the right and say: \"What is this video about?\"",
        "Then ask anything about it: \"What did he say about prices?\", \"Give me the key points\".",
    ],
    "name": "video_summarizer",
    "description": (
        "Gets what is said in a video or audio recording and gives the user what they ask "
        "for about it: a summary, an answer to a question, key points, notes, the main "
        "arguments, what was said at a moment. Works with a link from any site (YouTube, "
        "Instagram, TikTok, X, Facebook, Vimeo, podcasts, direct media links) or a video or "
        "audio file on this computer, including one the user dropped into the app. Use it "
        "for ANY request about the content of a video or recording - never open, play or "
        "watch it on the screen for this. 'This video' or 'the video on the screen' with no "
        "link: call it with no source - it finds the video open on the screen by itself (the "
        "page's address), else the dropped file, else the last video. Never answer from "
        "memory that a video was already summarized: what is on the screen now may be "
        "another one - call it. Follow-up questions: action 'ask', no source. "
        "Call it at once and only once: it tells the user by itself that it is on it, and "
        "its result says what to say afterwards. "
        "To make a quiz, flashcards or a document from a video, call this first for the "
        "content, then the other feature with what it returned."
    ),
    "brief": ("Summarizes or answers questions about a video/audio: a link (any site), a file, or "
              "with no source the video open on the screen (else the last one); follow-up questions."),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["summarize", "ask", "transcript"],
                "description": ("summarize (default) or ask: give the user what the request "
                                "says; transcript: show the words themselves."),
            },
            "source": {
                "type": "STRING",
                "description": ("The video's link or the file's path, exactly as given. Leave "
                                "empty for the video open on the screen, a dropped file, or the last video."),
            },
            "request": {
                "type": "STRING",
                "description": "What the user asked, verbatim, in their own language.",
            },
        },
    },
}

PLUGIN_SETTINGS = {
    "namespace": _NS,
    "title": "🎬  VIDEO SUMMARIZER",
    "fields": [
        {"key": "max_minutes", "label": "Longest video to read, in minutes (0 = no limit)",
         "type": "text", "default": str(_DEFAULT_MINUTES)},
        {"key": "subtitles", "label": "Use the video's own subtitles when it has them (faster, uses no allowance)",
         "type": "toggle", "default": True},
    ],
}


def _setting(key: str, default):
    try:
        from memory.config_manager import get_plugin_setting
        value = get_plugin_setting(_NS, key, default)
        return default if value is None else value
    except Exception:
        return default


def _max_seconds() -> float:
    try:
        minutes = float(str(_setting("max_minutes", _DEFAULT_MINUTES)).strip() or 0)
    except ValueError:
        minutes = _DEFAULT_MINUTES
    return max(0.0, minutes) * 60.0


def _ask(prompt: str, system: str, quick: bool = False) -> str:
    """One request to the assistant's own model (the subscription's, on its GPU)."""
    from core import gemini
    from google.genai import types as gtypes
    reply = gemini.call([prompt], tier=gemini.FAST if quick else gemini.SMART,
                        config=gtypes.GenerateContentConfig(system_instruction=system),
                        timeout_ms=20_000 if quick else 180_000)
    if reply is None:
        raise RuntimeError("the assistant's model did not answer (the allowance may be used up)")
    return str(getattr(reply, "text", "") or "")


def _temp_root():
    try:
        from core.paths import data
        d = data("video_tmp")
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:
        return None


def _their_words(player, request: str) -> str:
    """What the reply's language follows: the user's own latest words when
    the app has them (player.last_user_words), else the request as the model
    passed it. The model once passed an English request in Arabic and the
    whole answer came out in Arabic (2026-10-20)."""
    get = getattr(player, "last_user_words", None) if player is not None else None
    try:
        words = str(get() or "") if callable(get) else ""
    except Exception:
        words = ""
    return words.strip() or request


def _on_screen() -> list:
    """The web addresses open on the screen, front window first
    (core/openpages: read from the windows' address fields, any browser)."""
    try:
        from core.openpages import open_addresses
        return [a for a in open_addresses(limit=3, may_copy=True) if vc.find_url(a)]
    except Exception:
        return []


def _transcript(source: str, player, on_start=lambda: None, screen_first: bool = True) -> vc.Transcript:
    """The words of the video the user means: a link, a file path, the file
    they dropped into the app, the video open on the screen, or (none of
    those) the last video. With no link, the video in front of the user wins
    - not the last one summarized, which is what the model assumed when
    another video was open (2026-10-21), and called it a follow-up ("ask").
    A real follow-up is about the video still on the screen, which is then
    the same one (remembered, nothing read again). `on_start` is called once,
    just before a video is actually read (not for one that is remembered)."""
    src = (source or "").strip().strip('"')
    if not src:
        dropped = str(getattr(player, "current_file", "") or "") if player is not None else ""
        if dropped and vc.is_media_file(dropped):
            src = dropped
    if not src and screen_first:
        for address in _on_screen():
            key = vc.source_key(address)
            known = vc.recall(key)
            if known is not None:
                return known
            try:
                return _transcript(address, player, on_start)
            except vc.VideoError:
                continue                       # a page with no video on it: the next window
    if not src:
        t = vc.last()
        if t is None:
            raise vc.VideoError("there is no video yet: send its link, open it on the screen, "
                                "or drop the video file into the app")
        return t
    key = vc.source_key(src)
    t = vc.recall(key)
    if t is not None:
        return t
    if not vc.is_media_file(src) and not vc.find_url(src):
        raise vc.VideoError("that is neither a link nor a video or audio file on this computer")
    on_start()
    started = time.monotonic()
    if vc.is_media_file(src):
        t = vc.read_file(src, max_seconds=_max_seconds(), engine_factory=vc.speech_engine)
    else:
        url = vc.find_url(src)
        if not url:
            raise vc.VideoError("that is neither a link nor a video or audio file on this computer")
        t = vc.read_link(url, max_seconds=_max_seconds(), subtitles=bool(_setting("subtitles", True)),
                         engine_factory=vc.speech_engine, temp_root=_temp_root())
    # Sizes and timings only: the log never holds what a video says or is called.
    print(f"[Video] {t.via}: {vc.clock(t.duration)} of video, {len(t.lines)} blocks, "
          f"{sum(len(x) for _, x in t.lines):,} chars in {time.monotonic() - started:.1f}s")
    vc.remember(key, t)
    return t


_REFERENCE_CHARS = 6000


def _reference(t: "vc.Transcript") -> str:
    """The video's words for the model to work from in what comes next - a
    quiz, flashcards, a document about it - and NOT to read out. With only
    the spoken line it had nothing to build "5 questions about this video"
    from (2026-10-20)."""
    from core import outcome
    text = t.text()
    cut = text[:_REFERENCE_CHARS] + (" [...]" if len(text) > _REFERENCE_CHARS else "")
    return ("REFERENCE - do not read this out. The video's own words, for follow-up requests about "
            "it (a quiz, questions, notes, a document): use them as the content when you call "
            "another feature.\n" + outcome.untrusted("video transcript", cut))


def _finish_on_screen(player, t: "vc.Transcript", request: str, words: str) -> None:
    """The long answer, written on a side thread and shown when ready."""
    import threading

    def work():
        try:
            _show(player, t.title, vc.answer(t, request, _ask, words))
        except Exception as e:
            print(f"[Video] answer failed: {type(e).__name__}")
    threading.Thread(target=work, daemon=True, name="video-answer").start()


def _show(player, title: str, text: str) -> None:
    if player is None:
        return
    try:
        player.show_content(title or "VIDEO", text)
    except Exception:
        pass


def run(parameters: dict, player=None, session_memory=None) -> str:
    from core import outcome
    params = parameters or {}
    action = str(params.get("action") or "summarize").strip().lower()
    request = str(params.get("request") or "").strip()
    source = str(params.get("source") or "").strip() or vc.find_url(request)
    words = _their_words(player, request)
    if not request or not vc.same_writing(words, request):
        request = words                      # the model's paraphrase drifted: their own words
    try:
        # "On it" is said by the app for any slow tool (main._on_it_if_slow).
        t = _transcript(source or vc.find_url(words), player)
    except vc.VideoError as e:
        return f"status=failed reason={e}. Tell the user this in one sentence, in their language."
    except Exception as e:
        print(f"[Video] failed: {type(e).__name__}")
        return (f"status=failed reason=the video could not be read ({type(e).__name__}). "
                "Tell the user in one sentence.")

    facts = (f"title={t.title!r} length={vc.clock(t.duration) if t.duration else 'unknown'} "
             f"from={'its subtitles' if t.via == 'subtitles' else 'its sound'}")
    if action == "transcript":
        _show(player, t.title, t.text())
        return (f"status=done action=transcript {facts}. The transcript is on screen (the panel "
                "shows its beginning). Tell the user in one sentence.")

    # A transcript that fits one request: the spoken reply is written from it
    # straight away and returned, and the long answer is finished on a side
    # thread and put on screen when ready - the user heard nothing for 26 s
    # of an 8-minute video while the long answer was being written first.
    if vc.can_speak_from_transcript(t):
        try:
            line = vc.spoken(t, request, "", _ask, words)
        except Exception:
            line = ""
        if line:
            _finish_on_screen(player, t, request, words)
            return (f"status=done {facts}. The full answer is being written and appears on screen "
                    "in a moment. Say ONLY the 'what to say' text to the user, as it is - it is "
                    "already in their language - and nothing else.\n"
                    + outcome.untrusted("what to say", line[:600]) + "\n" + _reference(t))
    try:
        result = vc.answer(t, request, _ask, words)
    except Exception as e:
        print(f"[Video] answer failed: {type(e).__name__}")
        return (f"status=failed {facts} reason=the transcript is ready but the model did not answer "
                f"({e}). Tell the user in one sentence; they can ask again.")
    _show(player, t.title, result)
    try:
        line = vc.spoken(t, request, result, _ask, words)
    except Exception:
        line = ""
    if line:
        # Written for speech, in the user's language, at most two sentences.
        return (f"status=done {facts}. The full answer is on screen. Say ONLY the 'what to say' "
                "text to the user, as it is - it is already in their language - and nothing else.\n"
                + outcome.untrusted("what to say", line[:600]) + "\n" + _reference(t))
    return (f"status=done {facts}. The full answer is on screen. Tell the user the gist in one "
            "or two sentences, in their language, then ask what they would like to know.\n"
            + outcome.untrusted("video summary", result[:4000]))
