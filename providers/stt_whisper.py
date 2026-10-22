"""
Speech recognition — on this machine, or through a transcription API.

TWO BACKENDS, ONE FILE, FOR THE SAME REASON AS THE LANGUAGE MODEL
    `faster-whisper` runs Whisper locally on the CPU. `/v1/audio/transcriptions`
    is the same job over HTTP, and it is what OpenAI, Groq and several local
    servers all speak. Somebody who wants recognition to stay on their machine
    and somebody who wants it fast and hosted are asking for the same thing in
    two places, so it is one provider with a switch.

CPU IS THE DEFAULT AND THAT IS A PROMISE, NOT A FALLBACK
    The project states that no GPU is required. A provider that quietly needs
    one breaks that for everybody who read it, so `device` defaults to "cpu" and
    CUDA is something a person turns on. `compute_type` follows the device
    rather than being left on "auto": auto picks the best thing the *library*
    supports, which on a CPU without AVX512 can be a type that then fails at
    load time with a message about instruction sets.

NO FFMPEG, ON ANY PLATFORM
    `WhisperModel.transcribe()` accepts a numpy array as well as a file path.
    Given a path it decodes with PyAV and can want ffmpeg; given samples it
    decodes nothing. The microphone already hands us raw 16 kHz PCM, so the
    audio never becomes a file and the whole class of "works on my Linux box,
    fails on Windows because ffmpeg is not on PATH" never arises.

THE LANGUAGE IS DETECTED, NOT CONFIGURED
    `language` defaults to empty, which means Whisper decides per utterance.
    This assistant is used in many languages and people switch mid-conversation;
    pinning a language would make it mis-transcribe the moment somebody says a
    sentence in another one. Setting it is available for the case where someone
    knows they only ever speak one and wants the small accuracy gain.

RECOGNITION IS BLOCKING WORK AND IS KEPT OFF THE EVENT LOOP
    `transcribe()` is CPU-bound and synchronous, and it returns a GENERATOR —
    the work happens while the segments are iterated, not when the call returns.
    Doing that on the asyncio thread freezes the audio loops, the UI bridge and
    the receive loop for the whole of the recognition. It runs in a worker
    thread here, and the model is loaded in one too.
"""
from __future__ import annotations

import asyncio
import os
import time
from importlib.util import find_spec
from typing import Optional

from . import audio
from .base import DEFAULT_CAPS, Field

# Ordered small to large. Every one of them is multilingual except the `.en`
# variants, which are deliberately not offered: this assistant is not an English
# appliance, and an English-only model is a trap that only shows itself when
# somebody speaks their own language into it.
MODELS = ("tiny", "base", "small", "medium", "large-v3", "large-v3-turbo")

# Measured guidance rather than marketing: on an ordinary desktop CPU, `base`
# transcribes a few seconds of speech in well under a second and is the right
# default for a voice assistant, where being fast matters more than catching a
# rare proper noun. `small` is noticeably better and roughly twice the wait.
DEFAULT_MODEL = "base"


def _threads() -> int:
    """How many CPU threads recognition may use.

    Not all of them. This runs while the assistant is also playing audio,
    drawing an avatar at ~50 mouth shapes a second and holding a socket open.
    ctranslate2's default is to take every core, which starves exactly those and
    produces stuttering playback during recognition — a fault that looks like a
    sound problem and is really a scheduling one.
    """
    try:
        n = os.cpu_count() or 4
    except Exception:
        n = 4
    return max(1, min(8, n // 2))


class WhisperSTT:
    def __init__(self, cfg: dict):
        self.backend = (cfg.get("backend") or "local").strip().lower()
        self.model_name = (cfg.get("model") or DEFAULT_MODEL).strip()
        self.device = (cfg.get("device") or "cpu").strip().lower()
        self.language = (cfg.get("language") or "").strip()
        self.base_url = (cfg.get("base_url") or "").strip()
        self.api_key = (cfg.get("api_key") or "").strip()
        self.download_root = (cfg.get("download_root") or "").strip() or None
        self._model = None
        self._load_lock = asyncio.Lock()
        # Set by each recognition: the language Whisper heard, as a code like
        # "tr". Empty until something has been said.
        self.detected_language = ""
        self.caps = dict(DEFAULT_CAPS)

    # ── local ────────────────────────────────────────────────────────────────
    def _compute_type(self) -> str:
        # int8 on CPU is several times faster than float32 and the accuracy cost
        # on speech this short is not audible. float16 is a GPU format; asking
        # for it on a CPU fails at load, not at use, which is at least loud.
        return "float16" if self.device.startswith("cuda") else "int8"

    def _load_blocking(self):
        from faster_whisper import WhisperModel
        t0 = time.monotonic()
        model = WhisperModel(
            self.model_name,
            device=self.device,
            compute_type=self._compute_type(),
            cpu_threads=_threads(),
            download_root=self.download_root,
        )
        return model, time.monotonic() - t0

    async def _ensure_model(self, log=None):
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is not None:
                return self._model
            if find_spec("faster_whisper") is None:
                raise RuntimeError(
                    "faster-whisper is not installed — install it from the "
                    "providers panel, or with: pip install faster-whisper")
            if log:
                log(f"SYS: loading the {self.model_name} speech model "
                    f"(the first time also downloads it)")
            # Loading is slow the first time — it downloads the model — and
            # blocking, so it happens in a thread like the recognition itself.
            self._model, took = await asyncio.to_thread(self._load_blocking)
            if log:
                log(f"SYS: speech model ready in {took:.1f}s "
                    f"({self.device}, {self._compute_type()}, "
                    f"{_threads()} threads)")
            return self._model

    def _transcribe_blocking(self, samples) -> str:
        segments, info = self._model.transcribe(
            samples,
            language=self.language or None,     # None = detect it
            beam_size=1,          # greedy: a voice command is short and beam
                                  # search costs multiples of the time for an
                                  # accuracy gain that does not show up here
            vad_filter=True,      # drops the silence a push-to-talk release
                                  # leaves at both ends, which Whisper otherwise
                                  # fills with an invented sentence
            condition_on_previous_text=False,   # stops one bad transcript from
                                                # steering every one after it
        )
        # The work happens HERE — `segments` is a generator.
        text = " ".join(s.text.strip() for s in segments).strip()
        self._accept_language(
            str(getattr(info, "language", "") or ""),
            float(getattr(info, "language_probability", 0.0) or 0.0),
            text,
        )
        return text

    # How sure Whisper has to be, and how much it has to have heard, before the
    # answer switches to another language.
    #
    # THIS EXISTS BECAUSE THE VOICE CHANGED ACCENT AT RANDOM.
    #     Whisper reports a language for EVERY utterance and is confident about
    #     all of them, including "ok", "hmm", "evet" and "thanks". One-word
    #     replies carry almost no signal, so the guess lands on whatever the
    #     phonemes resemble — and because tts_piper follows the reported code,
    #     the assistant answered one turn in a Portuguese voice and the next in
    #     an English one, with the user having done nothing.
    #
    #     `language_probability` was sitting on the same object and was being
    #     thrown away. It is the whole fix: below the threshold the previous
    #     language stands, because "I could not tell" should keep the voice
    #     where it is rather than move it somewhere new.
    #
    #     The length rule catches the other half — a short utterance can score
    #     high and still be a coin toss, since there is not enough audio to be
    #     wrong about.
    _LANG_MIN_CONFIDENCE = 0.65
    _LANG_MIN_CHARS = 12

    def _accept_language(self, code: str, confidence: float, text: str) -> None:
        """Update the language the voice follows — only on real evidence.

        The FIRST detection is always taken, whatever it scores: at the start of
        a session there is nothing to keep, and refusing to choose would leave
        the assistant with no language at all.
        """
        code = code.strip().lower()
        if not code:
            return
        if not self.detected_language:
            self.detected_language = code
            return
        if code == self.detected_language:
            return
        if confidence >= self._LANG_MIN_CONFIDENCE and len(text) >= self._LANG_MIN_CHARS:
            print(f"[STT] language {self.detected_language} -> {code} "
                  f"({confidence:.0%}, {len(text)} chars)")
            self.detected_language = code
            return
        print(f"[STT] ignoring language guess {code} ({confidence:.0%}, "
              f"{len(text)} chars) - keeping {self.detected_language}")

    # ── hosted ───────────────────────────────────────────────────────────────
    def _api_blocking(self, pcm: bytes, sample_rate: int) -> str:
        import httpx
        from .llm_openai import _normalise, _is_loopback, _terse

        base = _normalise(self.base_url)
        wav = audio.to_wav(pcm, rate=sample_rate)
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        data = {"model": self.model_name, "response_format": "json"}
        if self.language:
            data["language"] = self.language
        with httpx.Client(timeout=60.0, trust_env=not _is_loopback(base)) as cl:
            r = cl.post(f"{base}/audio/transcriptions", headers=headers,
                        data=data,
                        files={"file": ("speech.wav", wav, "audio/wav")})
            if r.status_code >= 400:
                raise RuntimeError(f"{r.status_code} {_terse(r.text)}")
            payload = r.json()
        # The hosted endpoint reports no confidence, so a language it names is
        # taken as it always was — but through the same door, so the first-vs-
        # switch rules stay in one place.
        self._accept_language(
            str(payload.get("language") or self.language or ""), 1.0, "x" * 99)
        return (payload.get("text") or "").strip()

    # ── the contract ─────────────────────────────────────────────────────────
    async def transcribe(self, pcm: bytes, sample_rate: int = audio.MIC_RATE,
                         language: str = "", log=None) -> str:
        if not pcm:
            return ""
        # Whisper invents sentences out of silence — the well-known artefact
        # where a fraction of a second of room tone becomes "Thank you." in
        # whatever language it guessed. A quarter of a second is below anything
        # a person says on purpose, so it is dropped before it can become one.
        if audio.duration_s(pcm, sample_rate) < 0.25:
            return ""
        if language:
            self.language = language

        if self.backend == "api":
            return await asyncio.to_thread(self._api_blocking, pcm, sample_rate)

        await self._ensure_model(log)
        samples = audio.to_float32(
            audio.resample(pcm, sample_rate, 16_000))    # Whisper is a 16 kHz model
        return await asyncio.to_thread(self._transcribe_blocking, samples)

    async def warmup(self, log=None) -> None:
        """Load the model AND run one throwaway recognition, before the user
        ever speaks.

        Loading alone is not enough, and the difference was measured rather than
        assumed. On this machine, `base` on the CPU:

            load the model                 1.4s
            FIRST transcription            8.5s
            every transcription after      1.1s   (for 3.9s of speech)

        The eight seconds are ctranslate2 bringing up its kernels on first use,
        and without this they are paid by whatever the user says first — which
        is the single worst moment to spend them, because a person who gets no
        answer for eight seconds concludes the assistant is broken and says it
        again, producing two overlapping turns.

        A tenth of a second of silence is enough to trigger all of it, so the
        warm-up costs nothing but the wait it is moving off the critical path.
        """
        if self.backend != "local":
            return
        try:
            await self._ensure_model(log)
            t0 = time.monotonic()
            silence = audio.to_float32(b"\x00" * 3200)   # 0.1s at 16 kHz
            await asyncio.to_thread(self._transcribe_blocking, silence)
            if log:
                log(f"SYS: speech recognition warm "
                    f"({time.monotonic() - t0:.1f}s, paid once)")
        except Exception as e:
            if log:
                log(f"SYS: speech model not ready — {type(e).__name__}: "
                    f"{str(e)[:100]}")

    def probe(self) -> tuple[bool, str]:
        if self.backend == "api":
            if not self.base_url:
                return False, "No address set."
            return True, "Will be checked on first use."
        if find_spec("faster_whisper") is None:
            return False, "faster-whisper is not installed."
        return True, (f"{self.model_name} on {self.device} "
                      f"({self._compute_type()}, {_threads()} threads)")


def _make(cfg: dict) -> WhisperSTT:
    return WhisperSTT(cfg)


PROVIDER = {
    "id": "whisper",
    "kind": "stt",
    "label": "Whisper",
    "hint": "On this machine with faster-whisper, or through a transcription API",
    # Neither backend's dependency is required to LIST this provider — httpx is
    # always present, and faster-whisper is only needed once "local" is chosen.
    # Reporting it as unavailable would hide the API backend, which needs
    # nothing installed at all.
    "requires": (),
    "install": "faster-whisper",
    # Hearing, named the way somebody choosing it would name it. The local entry
    # is first and is the one that needs no account, no key and no connection.
    "catalog": (
        {"id": "local", "label": "Whisper on this computer",
         "note": "free · private · runs on the CPU", "local": True,
         "values": {"backend": "local", "base_url": "", "api_key": ""}},
        {"id": "groq", "label": "Groq Whisper",
         "note": "cloud · very fast · free tier", "local": False,
         "values": {"backend": "api", "model": "whisper-large-v3-turbo",
                    "base_url": "https://api.groq.com/openai/v1"}},
        {"id": "openai", "label": "OpenAI Whisper",
         "note": "cloud · needs a key", "local": False,
         "values": {"backend": "api", "model": "whisper-1",
                    "base_url": "https://api.openai.com/v1"}},
    ),
    "needs": (
        Field("backend", "Where it runs", kind="choice",
              choices=("local", "api"), default="local",
              hint="local = on this machine, nothing leaves it"),
        Field("model", "Model", kind="choice", choices=MODELS,
              default=DEFAULT_MODEL,
              hint="base is the fast default; small is more accurate and slower"),
        Field("device", "Processor", kind="choice", choices=("cpu", "cuda"),
              default="cpu", required=False, advanced=True,
              hint="CPU is the default and needs no graphics card"),
        Field("language", "Language", default="", required=False, advanced=True,
              hint="Leave empty to detect it every time — the right setting "
                   "unless you only ever speak one language"),
        Field("base_url", "API address", default="", required=False,
              hint="Only for the api backend, e.g. https://api.groq.com/openai/v1"),
        Field("api_key", "API key", kind="secret", default="", required=False),
        Field("download_root", "Model folder", kind="path", default="",
              required=False, advanced=True,
              hint="Leave empty for the usual cache in your home folder"),
    ),
    "caps": {},
    "factory": _make,
}


def available() -> bool:
    # The API backend needs nothing beyond httpx, which ships with the app.
    return find_spec("httpx") is not None
