"""
Sample rates, formats and the conversions between them.

WHY THIS IS SHARED RATHER THAN REPEATED
    Three rates meet in this app and none of them agree. The microphone opens at
    16 kHz because that is what the Live API wants; the speaker opens at 24 kHz
    because that is what the Live API sends; and every speech engine has its own
    idea — Piper builds most voices at 22.05 kHz, Windows SAPI answers at
    whatever the voice was recorded at, macOS `say` gives you what you ask for,
    ElevenLabs will hand over 24 kHz if you ask precisely.

    Getting that wrong does not raise. It plays the sentence at the wrong speed
    and pitch — a 22.05 kHz clip through a 24 kHz stream is nine percent fast
    and slightly chipmunked, which sounds like a bad voice rather than a bug,
    and so gets reported as "the local voice is terrible".

    So the conversion lives in one place, and every provider states its own rate
    rather than assuming the app's.

NO NEW DEPENDENCY
    numpy is already required by the app. Resampling is linear interpolation,
    which for speech between neighbouring rates is inaudible against the
    synthesiser's own artefacts, and it costs no library, no build step and no
    platform-specific wheel.
"""
from __future__ import annotations

import io
import struct
import wave
from typing import Iterator

# What main.py opens its streams at — see SEND_SAMPLE_RATE / RECEIVE_SAMPLE_RATE.
# Named here so a provider never has to guess, and so changing one of them is a
# single edit rather than a hunt.
MIC_RATE = 16_000
SPEAKER_RATE = 24_000
SAMPLE_WIDTH = 2          # 16-bit signed little-endian
CHANNELS = 1


def to_float32(pcm: bytes):
    """16-bit PCM -> float32 in [-1, 1], which is what speech models take."""
    import numpy as np
    if not pcm:
        return np.zeros(0, dtype=np.float32)
    # An odd byte count means a chunk was cut mid-sample. Dropping the stray
    # byte is right; letting numpy raise would lose the whole utterance over one
    # byte at the end of a buffer.
    if len(pcm) % 2:
        pcm = pcm[:-1]
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


def to_pcm16(samples) -> bytes:
    """float32 in [-1, 1] -> 16-bit PCM, clipped rather than wrapped.

    Wrapping is what happens if you cast without clipping, and it turns a moment
    of loudness into a burst of white noise — much worse than the clipping it
    replaces."""
    import numpy as np
    arr = np.asarray(samples, dtype=np.float32)
    return (np.clip(arr, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def resample(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """16-bit PCM from one rate to another. Returns the input when they match,
    so the common case costs a comparison."""
    if src_rate == dst_rate or not pcm:
        return pcm
    import numpy as np
    src = to_float32(pcm)
    if src.size == 0:
        return b""
    n_out = int(round(src.size * (dst_rate / float(src_rate))))
    if n_out <= 0:
        return b""
    # endpoint=False keeps the spacing uniform; with endpoint=True the last
    # sample lands exactly on the final input sample and every interval before
    # it is stretched, which drifts on long clips.
    idx = np.linspace(0, src.size, num=n_out, endpoint=False)
    return to_pcm16(np.interp(idx, np.arange(src.size), src))


def to_mono(pcm: bytes, channels: int) -> bytes:
    """Average interleaved channels down to one. Some system voices answer in
    stereo even when asked for mono, and playing that as mono halves the pitch."""
    if channels <= 1 or not pcm:
        return pcm
    import numpy as np
    arr = to_float32(pcm)
    usable = (arr.size // channels) * channels
    return to_pcm16(arr[:usable].reshape(-1, channels).mean(axis=1))


def wav_to_pcm(data: bytes, want_rate: int = SPEAKER_RATE) -> bytes:
    """A WAV file (however it was produced) -> mono 16-bit PCM at `want_rate`.

    This is how every system speech engine hands audio back: Windows SAPI writes
    a wave file, macOS `say` writes one, espeak-ng writes one to stdout. Parsed
    with the standard library, so there is nothing to install and nothing that
    behaves differently per platform.
    """
    if not data:
        return b""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            channels = w.getnchannels()
            width = w.getsampwidth()
            rate = w.getframerate()
            frames = w.readframes(w.getnframes())
    except Exception:
        # Not a WAV, or a truncated one. Some engines write a header whose
        # length field is wrong when they stream; salvage the payload rather
        # than losing the sentence.
        return _salvage(data, want_rate)

    if width != SAMPLE_WIDTH:
        frames = _requantise(frames, width)
    frames = to_mono(frames, channels)
    return resample(frames, rate, want_rate)


def _requantise(frames: bytes, width: int) -> bytes:
    """8-, 24- or 32-bit PCM down to 16-bit. Rare, but a voice that reports 32
    is not a reason to play silence."""
    import numpy as np
    if width == 1:                                   # unsigned 8-bit
        arr = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128) / 128.0
    elif width == 3:                                 # packed 24-bit
        raw = np.frombuffer(frames, dtype=np.uint8)
        usable = (raw.size // 3) * 3
        b = raw[:usable].reshape(-1, 3).astype(np.int32)
        val = (b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16))
        val = np.where(val & 0x800000, val - 0x1000000, val)
        arr = val.astype(np.float32) / 8388608.0
    elif width == 4:
        arr = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        return frames
    return to_pcm16(arr)


def _salvage(data: bytes, want_rate: int) -> bytes:
    """Find the `data` chunk by hand when the header will not parse."""
    i = data.find(b"data")
    if i < 0 or len(data) < 28:
        return b""
    try:
        rate = struct.unpack_from("<I", data, data.find(b"fmt ") + 12)[0]
        channels = struct.unpack_from("<H", data, data.find(b"fmt ") + 10)[0]
    except Exception:
        rate, channels = want_rate, 1
    body = data[i + 8:]
    return resample(to_mono(body, channels or 1), rate or want_rate, want_rate)


def chunk(pcm: bytes, ms: int = 50, rate: int = SPEAKER_RATE) -> Iterator[bytes]:
    """Split PCM into slices for playback.

    Fifty milliseconds because that is the size main.py already cuts Live's
    audio into, and it is what bounds how quickly an interrupt can stop the
    speakers: playback stops at the next slice boundary, so the slice length is
    the worst-case delay between the user talking over the assistant and the
    assistant going quiet.
    """
    step = max(2, int(rate * (ms / 1000.0)) * SAMPLE_WIDTH)
    step -= step % SAMPLE_WIDTH
    for i in range(0, len(pcm), step):
        piece = pcm[i:i + step]
        if piece:
            yield piece


def to_wav(pcm: bytes, rate: int = MIC_RATE, channels: int = CHANNELS) -> bytes:
    """Raw PCM wrapped in a WAV header — what a transcription API expects to be
    uploaded, and what makes a captured utterance playable for debugging."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def duration_s(pcm: bytes, rate: int = MIC_RATE) -> float:
    return len(pcm) / float(rate * SAMPLE_WIDTH * CHANNELS) if rate else 0.0
