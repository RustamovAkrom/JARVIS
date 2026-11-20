"""
The clap launcher's greeting as sound, ready before it is needed.

When JARVIS is started by a clap, the welcome line is meant to be heard
over the opening animation, not seconds later when the conversation engine is
up. So the line is synthesised ahead of time with the voice the pipeline uses,
kept on disk, and played straight from there.

    prepare(text) -> bytes | None   24 kHz mono 16-bit PCM, or None
    play(pcm)                        blocking; on the configured speakers

THE CACHE
    cache/welcome_<hash>.pcm, the hash covering the text, the voice provider and
    every setting of it except the key. Change the voice, the model, the
    stability or the words and a new file is made once; otherwise the voice
    service is never asked again — not even one character of its quota.

ONLY THE LOCAL PIPELINE HAS A VOICE TO ASK
    Gemini Live speaks only inside its own conversation, so there is nothing to
    synthesise with ahead of time. prepare() returns None there, and main.py
    greets through the conversation instead, as before.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

RATE = 24_000
_CACHE = Path(__file__).resolve().parent.parent / "cache"


def _tts():
    """(provider id, its settings, a built provider) for the pipeline's voice,
    or None when there is none."""
    from memory.config_manager import get_pipeline, get_provider_config, get_provider_mode
    if get_provider_mode() != "pipeline":
        return None
    pid = str(get_pipeline().get("tts") or "").strip()
    if not pid:
        return None
    from providers import registry
    cfg = dict(get_provider_config(pid))
    return pid, cfg, registry.build(pid, cfg)


def prepare(text: str) -> bytes | None:
    """The line as PCM, from the cache or made now. Never raises."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        found = _tts()
        if found is None:
            return None
        pid, cfg, tts = found
        settings = {k: v for k, v in cfg.items() if k not in ("api_key", "log")}
        key = hashlib.sha1(json.dumps([pid, settings, text], sort_keys=True,
                                      default=str).encode("utf-8")).hexdigest()[:16]
        path = _CACHE / f"welcome_{key}.pcm"
        if path.exists() and path.stat().st_size > 4800:
            return path.read_bytes()

        async def collect() -> bytes:
            out = bytearray()
            async for piece in tts.speak(text):
                out += piece
            return bytes(out)

        pcm = asyncio.run(collect())
        if len(pcm) < 4800:                      # under 0.1 s: not a real result
            print(f"[Welcome] the voice returned almost nothing ({len(pcm)} bytes)")
            return None
        _CACHE.mkdir(exist_ok=True)
        path.write_bytes(pcm)
        print(f"[Welcome] greeting synthesised and cached ({len(pcm) / (RATE * 2):.1f}s)")
        return pcm
    except Exception as e:
        print(f"[Welcome] greeting not prepared: {type(e).__name__}: {e}")
        return None


_device = {"ready": False, "index": None}


def warm_device() -> None:
    """Find the configured speakers now, not at the moment of speaking.

    Measured: resolving the saved output device the first time probes every
    host API and took 3.06 s — the greeting started after the opening it was
    meant to play over. In standby this runs long before the clap."""
    try:
        from core import audio_devices
        from memory.config_manager import get_output_device
        _device["index"] = audio_devices.resolve(get_output_device(), "output")
    except Exception as e:
        print(f"[Welcome] speakers not resolved, using the default: {e}")
        _device["index"] = None
    _device["ready"] = True


def play(pcm: bytes) -> bool:
    """Play it on the speakers JARVIS is set to use. Blocking. False if the
    sound could not be played — the caller then lets the conversation say it.
    If warm_device() has not finished, the system default is used rather than
    waiting for it."""
    try:
        import numpy as np
        import sounddevice as sd
        device = _device["index"] if _device["ready"] else None
        samples = np.frombuffer(pcm, dtype="<i2")
        sd.play(samples, RATE, device=device, blocking=True)
        return True
    except Exception as e:
        print(f"[Welcome] greeting not played: {type(e).__name__}: {e}")
        return False
