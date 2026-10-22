"""
Speech synthesis through the operating system's own voices.

WHY THIS ONE EXISTS AT ALL
    Every other way of speaking locally starts with a download — a neural voice
    is tens of megabytes and one per language. That is fine as a choice and
    wrong as the only option, because it means local mode does not work until
    something has been fetched, and it works in one language until something
    else has been fetched.

    Every one of the three supported systems already ships a speech synthesiser
    with the languages its owner installed. Nothing to download, nothing to
    build, no model files in the repository. It is not as good as a neural
    voice. It is available immediately, everywhere, which for a fallback matters
    more.

        Windows   System.Speech (SAPI5), driven through PowerShell
        macOS     `say`
        Linux     `espeak-ng`, or `spd-say` where that is what is installed

    THE HONEST LIMIT: it speaks the languages the machine has VOICES for, which
    is not the same as the languages the machine displays. Measured on an
    ordinary Windows 10 install: two voices, both English. A Turkish sentence
    came out as English phonetics — poor, but audible; a Russian one came out as
    nothing at all, with a success code. `speak()` turns that silence into an
    error that names the cause, because a fallback that goes quiet in some
    languages and not others is worse than one that says what is wrong.

    For an assistant that genuinely switches language mid-conversation, a
    multilingual hosted voice is the better choice and this is the floor beneath
    it.

THE ENCODING TRAP, WHICH IS THE WHOLE REASON THIS FILE IS CAREFUL
    The text to speak is whatever language the conversation is in. Passing it as
    a command-line argument means it goes through the shell's code page, and on
    a Turkish Windows that is cp1254, on a Russian one cp1251, on a Japanese one
    cp932 — none of which can carry the others. This project has already been
    bitten by exactly this: a status line with an emoji in it raised
    UnicodeEncodeError and took the session down.

    So the text is NEVER an argument. It is written to a UTF-8 file and the
    engine is told to read that file. Every one of the three supports it, and it
    is the difference between a synthesiser that works in one language and one
    that works in all of them.

NO LANGUAGE DETECTION HERE EITHER
    Which voice speaks is a setting, listed from what the system actually has
    installed. Sentences are split on punctuation — the marks that end a
    sentence in Latin, CJK, Arabic and Devanagari scripts — not by recognising
    words. Splitting is only there so speech can start before the whole answer
    has been synthesised.
"""
from __future__ import annotations

import asyncio
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import AsyncIterator, Optional

from . import audio
from .base import DEFAULT_CAPS, Field

_SYS = platform.system()

# Sentence-ending punctuation across the scripts this assistant is actually used
# in. Not grammar and not language detection — just the marks after which it is
# safe to start speaking while the rest is still being made.
#
# TWO RULES, BECAUSE THE SCRIPTS DIFFER
#     After a full stop, a space is required: without it "3.14" and every
#     version number in a technical answer become two sentences.
#     After 。！？ and the Arabic and Devanagari stops, no space is required —
#     Japanese and Chinese do not put one there, and demanding it meant a whole
#     CJK paragraph was synthesised as a single unbroken block. Measured: the
#     splitter left "今日はいい天気です。مرحبا بك." whole.
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|(?<=[。！？؟۔।])\s*")

# Windows only: keep a console window from flashing open on every sentence.
_NO_WINDOW = 0x08000000 if _SYS == "Windows" else 0


def _run(args: list[str], timeout: float = 60.0,
         stdin_data: Optional[bytes] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        input=stdin_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        creationflags=_NO_WINDOW,
    )


def _powershell() -> Optional[str]:
    """PowerShell 7 if it is there, Windows PowerShell otherwise."""
    return shutil.which("pwsh") or shutil.which("powershell")


# ─────────────────────────────────────────────────────────────────────────────
#  Listing the voices the machine actually has
# ─────────────────────────────────────────────────────────────────────────────
# Enumerating voices costs a subprocess — PowerShell on Windows, `say -v ?` on
# macOS, `espeak-ng --voices` on Linux. Measured on Windows: **1.70 seconds**,
# every single call.
#
# The panel rebuilds its form on every dropdown change, and each rebuild asks
# for this list, so an uncached version freezes the interface for the better
# part of two seconds every time somebody touches a control. That is the same
# fault as the 2.104-second `openwakeword` import that used to make the settings
# drawer stutter — measured, found, and fixed once already in this project.
#
# The installed voices do not change while the app is running, so this is
# answered once. `refresh=True` exists for somebody who has just installed a
# voice and does not want to restart.
_voice_cache: Optional[list] = None
_voice_lock = threading.Lock()


def prefetch(on_ready=None) -> None:
    """Warm the voice list off the interface thread.

    `on_ready` is called from the worker when the list lands, so an interface
    that drew an empty dropdown can fill it in. It runs on a NON-UI thread —
    whatever is passed must marshal itself back, and the panel does that with a
    Qt signal rather than touching a widget from here.

    Called when the panel opens, never at startup: nothing in this folder may
    run before somebody asks for it."""
    if _voice_cache is not None:
        if on_ready:
            on_ready()
        return

    def work():
        list_voices()
        if on_ready:
            try:
                on_ready()
            except Exception:
                pass

    threading.Thread(target=work, daemon=True).start()


def list_voices(refresh: bool = False) -> list[str]:
    """Installed voice names. Empty when the platform's engine is missing, which
    the panel shows as 'no system voices found' rather than an empty dropdown
    with no explanation."""
    global _voice_cache
    with _voice_lock:
        if _voice_cache is not None and not refresh:
            return list(_voice_cache)
    found = _read_voices()
    with _voice_lock:
        _voice_cache = found
    return list(found)


def _read_voices() -> list[str]:
    try:
        if _SYS == "Windows":
            ps = _powershell()
            if not ps:
                return []
            out = _run([ps, "-NoProfile", "-NonInteractive", "-Command",
                        "Add-Type -AssemblyName System.Speech; "
                        "(New-Object System.Speech.Synthesis.SpeechSynthesizer)"
                        ".GetInstalledVoices() | "
                        "ForEach-Object { $_.VoiceInfo.Name }"], timeout=20)
            return [l.strip() for l in
                    out.stdout.decode("utf-8", "replace").splitlines() if l.strip()]

        if _SYS == "Darwin":
            out = _run(["say", "-v", "?"], timeout=20)
            names = []
            for line in out.stdout.decode("utf-8", "replace").splitlines():
                # "Alice               it_IT    # Salve, mi chiamo Alice"
                name = line.split("  ")[0].strip()
                if name:
                    names.append(name)
            return names

        # Linux
        if shutil.which("espeak-ng") or shutil.which("espeak"):
            exe = shutil.which("espeak-ng") or shutil.which("espeak")
            out = _run([exe, "--voices"], timeout=20)
            names = []
            for line in out.stdout.decode("utf-8", "replace").splitlines()[1:]:
                cols = line.split()
                if len(cols) >= 4:
                    names.append(cols[3])
            return sorted(set(names))
        return []
    except Exception:
        return []


def engine_name() -> str:
    if _SYS == "Windows":
        return "System.Speech (SAPI5)" if _powershell() else ""
    if _SYS == "Darwin":
        return "say" if shutil.which("say") else ""
    for exe in ("espeak-ng", "espeak", "spd-say"):
        if shutil.which(exe):
            return exe
    return ""


# ─────────────────────────────────────────────────────────────────────────────
#  Synthesis
# ─────────────────────────────────────────────────────────────────────────────
class SystemTTS:
    def __init__(self, cfg: dict):
        self.voice = (cfg.get("voice") or "").strip()
        self.rate = int(cfg.get("rate") or 0)        # -10..10 on Windows
        self.caps = dict(DEFAULT_CAPS)
        self.caps["audio_out"] = True
        self._engine = engine_name()

    # -- per platform, all returning a WAV file's bytes ----------------------
    def _windows(self, text_file: str, wav_file: str) -> None:
        ps = _powershell()
        if not ps:
            raise RuntimeError("PowerShell was not found")
        # The text is READ FROM A UTF-8 FILE, never interpolated into this
        # script — see the module docstring. The voice name is the only thing
        # substituted, and it is quoted and doubled so a name with an
        # apostrophe cannot end the string early.
        voice = self.voice.replace("'", "''")
        select = (f"$s.SelectVoice('{voice}');" if self.voice else "")
        script = (
            "Add-Type -AssemblyName System.Speech;"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            f"{select}"
            f"$s.Rate = {max(-10, min(10, self.rate))};"
            f"$t = [System.IO.File]::ReadAllText('{text_file}',"
            " [System.Text.Encoding]::UTF8);"
            f"$s.SetOutputToWaveFile('{wav_file}');"
            "$s.Speak($t);"
            "$s.Dispose();"
        )
        r = _run([ps, "-NoProfile", "-NonInteractive", "-Command", script])
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode("utf-8", "replace")[:200]
                               or "the speech engine failed")

    def _macos(self, text_file: str, wav_file: str) -> None:
        args = ["say", "-f", text_file, "-o", wav_file,
                # Ask for exactly what the speaker wants, so nothing is
                # resampled on the way out.
                "--data-format=LEI16@24000"]
        if self.voice:
            args[1:1] = ["-v", self.voice]
        if self.rate:
            # `say` takes words per minute; 175 is its default, and the -10..10
            # scale is mapped onto it so one setting means the same thing on
            # every platform.
            args[1:1] = ["-r", str(max(90, min(400, 175 + self.rate * 15)))]
        r = _run(args)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode("utf-8", "replace")[:200]
                               or "`say` failed")

    def _linux(self, text_file: str, wav_file: str) -> None:
        exe = shutil.which("espeak-ng") or shutil.which("espeak")
        if exe:
            args = [exe, "-f", text_file, "-w", wav_file]
            if self.voice:
                args += ["-v", self.voice]
            if self.rate:
                args += ["-s", str(max(80, min(450, 175 + self.rate * 15)))]
            r = _run(args)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.decode("utf-8", "replace")[:200]
                                   or "espeak-ng failed")
            return
        raise RuntimeError(
            "no speech engine found — install espeak-ng "
            "(apt install espeak-ng, dnf install espeak-ng, pacman -S espeak-ng)")

    def _synth_blocking(self, text: str) -> bytes:
        # Both files are created and closed before the engine is pointed at
        # them: on Windows a file still open in this process cannot be written
        # by another one, which is the standard way this fails there and not
        # anywhere else.
        td = tempfile.mkdtemp(prefix="mark_tts_")
        txt = os.path.join(td, "say.txt")
        wav = os.path.join(td, "say.wav")
        try:
            with open(txt, "w", encoding="utf-8") as f:
                f.write(text)
            if _SYS == "Windows":
                self._windows(txt, wav)
            elif _SYS == "Darwin":
                self._macos(txt, wav)
            else:
                self._linux(txt, wav)
            with open(wav, "rb") as f:
                return f.read()
        finally:
            shutil.rmtree(td, ignore_errors=True)

    # -- the contract -------------------------------------------------------
    async def speak(self, text: str, voice: str = "") -> AsyncIterator[bytes]:
        """24 kHz mono PCM, a sentence at a time.

        Sentence by sentence rather than all at once because the whole answer
        can be several seconds of synthesis, and waiting for the last word
        before the first one is heard is the difference between an assistant
        that replies and one that pauses.
        """
        if voice:
            self.voice = voice
        produced = 0
        sentences = _split(text)
        for sentence in sentences:
            wav = await asyncio.to_thread(self._synth_blocking, sentence)
            pcm = audio.wav_to_pcm(wav, audio.SPEAKER_RATE)
            produced += len(pcm)
            for piece in audio.chunk(pcm):
                yield piece

        # A system voice given a script it does not cover returns SUCCESS and no
        # audio. Measured on an ordinary Windows install: the two voices present
        # were English, and a Cyrillic sentence produced exactly zero bytes with
        # no error anywhere. Silence that reports success is the worst failure
        # mode there is — the assistant looks like it ignored the user, and the
        # obvious suspicion falls on the speakers or the microphone.
        #
        # So it is turned into something that can be said out loud.
        if sentences and produced == 0:
            raise RuntimeError(
                f"{self._engine} produced no audio for that text — the "
                f"installed voice ({self.voice or 'system default'}) does not "
                f"cover this script. Install a voice for this language in your "
                f"system settings, or use a downloadable or hosted voice."
            )

    def probe(self) -> tuple[bool, str]:
        if not self._engine:
            if _SYS == "Linux":
                return False, "No speech engine — install espeak-ng."
            return False, "No speech engine was found on this system."
        try:
            wav = self._synth_blocking("ok")
            pcm = audio.wav_to_pcm(wav, audio.SPEAKER_RATE)
        except Exception as e:
            return False, str(e)[:120]
        if not pcm:
            return False, f"{self._engine} produced no audio."
        n = len(list_voices())
        return True, (f"{self._engine}, {n} voice{'s' if n != 1 else ''} installed "
                      f"({audio.duration_s(pcm, audio.SPEAKER_RATE):.2f}s of test audio)")


def _split(text: str, max_chars: int = 240) -> list[str]:
    """Break an answer into speakable pieces.

    Punctuation only. A very long sentence with no punctuation in it — which is
    a whole class of perfectly normal writing in several languages — is cut on
    whitespace so it does not become one enormous synthesis call.
    """
    text = (text or "").strip()
    if not text:
        return []
    pieces = []
    for part in _SENTENCE_END.split(text):
        part = part.strip()
        if not part:
            continue
        while len(part) > max_chars:
            cut = part.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            pieces.append(part[:cut].strip())
            part = part[cut:].strip()
        if part:
            pieces.append(part)
    return pieces


def _make(cfg: dict) -> SystemTTS:
    return SystemTTS(cfg)


PROVIDER = {
    "id": "system_tts",
    "kind": "tts",
    "label": "System voice",
    "hint": "The voices already installed on this computer — nothing to download",
    "requires": (),
    "catalog": (
        {"id": "system", "label": "This computer's own voices",
         "note": "free · instant · nothing to download", "local": True,
         "values": {}},
    ),
    "needs": (
        Field("voice", "Voice", kind="choice", choices=(), default="",
              required=False,
              hint="Empty uses the system default; the list is read from this "
                   "machine"),
        Field("rate", "Speed", kind="number", default=0, required=False,
              advanced=True,
              hint="-10 to 10, 0 is normal"),
    ),
    "caps": {"audio_out": True},
    "factory": _make,
}


def available() -> bool:
    return bool(engine_name())


def choices_for(field_key: str) -> tuple:
    """Live values for a `choices` field the panel cannot know in advance.

    NEVER blocks. Reading the voices costs 1.70 seconds of subprocess, and this
    is called while a form is being drawn; waiting for it here is what made the
    panel take two seconds to open. Before the cache is warm this answers with
    nothing, `prefetch()` fills it in the background, and the panel redraws once
    it arrives. The voice already saved is still shown, because the form adds
    the stored value to the list whether or not it was offered."""
    if field_key != "voice":
        return ()
    with _voice_lock:
        return tuple(_voice_cache) if _voice_cache is not None else ()
