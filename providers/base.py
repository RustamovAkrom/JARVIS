"""
The contract every provider signs — and the only thing the rest of the app is
allowed to know about this folder.

WHY THIS FOLDER EXISTS
    The assistant is built on the Gemini Live API, which is one socket carrying
    audio in, audio out, transcripts and tool calls together. That is the
    fastest shape there is, and it is also a single point of failure and a
    single vendor. Some people cannot send their microphone to a third party,
    some have no Gemini quota left, and some simply want to choose. So this
    folder adds a second way to hold the conversation — speech recognition, a
    language model and speech synthesis wired together — and a way to point the
    one-shot calls at somebody else's API.

WHY IT COSTS NOTHING WHEN IT IS NOT USED
    Nothing in the app imports `providers` unless the configuration says a
    provider is selected. When the mode is "gemini" — the default, and what
    every existing install has — this package is never executed at all. No
    import, no discovery, no thread, no measurable startup cost.

    That is why the folder CAN be deleted and also why deleting it is pointless.

    Inside a provider file the same discipline applies one level down: heavy
    imports (`faster_whisper`, `httpx`, a TTS engine) live inside `factory()`
    and NOT at module scope. Whether a dependency is installed is answered by
    `importlib.util.find_spec`, which does not execute the module. This is the
    same fix that took plugin discovery from 211 ms to 39 ms — the mistake it
    prevents is real and has already been made once in this project.

THE DIRECTION OF THE CONTRACT — THIS IS THE IMPORTANT PART
    A provider speaks Gemini Live's dialect. Never the reverse.

    It is tempting to invent a neutral interface and translate both sides onto
    it. That would mean rewriting main.py's receive loop, the viseme extractor,
    the echo guard, the avatar's mouth, the tool dispatcher and the interrupt
    path — eight thousand lines that already work — so that a folder which may
    not even be installed can feel symmetrical. The new thing adapts to the old
    thing, because the old thing is the one with the users.

    The payoff is not small. An engine that emits Live-shaped events inherits
    the avatar, the lip-sync, the self-echo guard, push-to-talk, the wake word,
    undo, the confirmation gate, memory, and every action and plugin — without
    one line of any of them changing.

WHAT main.py ACTUALLY TOUCHES
    Measured, not assumed: `self.session` appears 29 times in main.py and every
    one of them is one of four operations, and the receive loop reads five
    fields. That whole surface is written out in `Engine` and `Msg` below.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional, Protocol, Sequence


# ─────────────────────────────────────────────────────────────────────────────
#  Capabilities
# ─────────────────────────────────────────────────────────────────────────────
# What an engine can and cannot do. This is not documentation — two systems that
# ALREADY EXIST read it and change their behaviour:
#
#   * main.py's tool-declaration assembly drops tools the engine cannot serve,
#     so `screen_process` is never offered to an engine with no eyes.
#   * `_describe_limits()` turns the same facts into the [SELF] block of the
#     system prompt, so the model KNOWS it cannot see and says so plainly
#     instead of inventing a description of a screen it never received.
#
# The second is why this is data rather than a pile of `if`s. Runtime
# self-knowledge was built for exactly this shape of problem; a capability dict
# is the input it was waiting for.
DEFAULT_CAPS: dict[str, Any] = {
    # Can the model be handed an image? Decides whether `screen_process` and the
    # camera are declared at all.
    "vision": False,

    # Can it call functions? Everything this assistant does that is not talking
    # happens through a tool call, so an engine with this off is a chat box.
    "tools": False,

    # How its tool declarations are shaped. providers/schema.py converts.
    #   "gemini"    — the app's native form, no conversion
    #   "openai"    — {"type":"function","function":{...}}, lowercase JSON Schema
    #   "anthropic" — {"name","description","input_schema":{...}}
    #   "prompt"    — no native tool calling; ask for JSON in the reply, parse it
    "tool_format": None,

    # How many declarations this model can be given before it starts choosing
    # badly. None means no limit, which is the honest answer for Gemini.
    #
    # It matters more than it looks. Measured on this install: 8 inline tools,
    # 12 bundled actions and one declaration per enabled plugin, totalling
    # 12,907 characters — `computer_settings` alone is 2,916 of them, because it
    # names all 56 of its actions. Gemini carries that comfortably. An 8B model
    # handed forty tools picks the wrong one, or emits JSON that does not parse.
    # So a small engine states a budget and schema.budget() fills it core-first.
    "max_tools": None,

    # Does the engine produce speech itself? An LLM-only provider does not; the
    # pipeline engine is what gives it a voice.
    "audio_out": False,

    # Can the user talk over it and have it stop? Live does this server-side. A
    # local pipeline has to detect it, and until it does, `interrupt()` can only
    # stop playback rather than the generation behind it.
    "barge_in": False,

    # Does it honour NON_BLOCKING tool behaviour + scheduling (see
    # core/action_loader.py)? Only Live. Everywhere else those tools still run —
    # they just hold the turn, which is a slower assistant, not a broken one.
    "non_blocking": False,

    # Server-side session-resumption handles. Only Live. A pipeline engine owns
    # its own history, so it has nothing to resume from a server.
    "resumption": False,
}


# ─────────────────────────────────────────────────────────────────────────────
#  Configuration fields — the UI form is GENERATED from these
# ─────────────────────────────────────────────────────────────────────────────
# A provider says what it needs and the settings panel draws it. This is the
# whole reason adding a provider never touches ui.py, which is 5,848 lines and
# should not grow a row of widgets every time somebody supports one more vendor.
# The same promise the plugin system already makes: a new one is one file.
@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str = "text"            # text | secret | choice | path | bool | number
    default: Any = ""
    choices: Sequence[str] = ()
    required: bool = True
    hint: str = ""                # one dim line under the input
    # Hidden behind the panel's MORE toggle. A setting almost nobody changes
    # costs more in the confusion it adds to the five that matter than it saves
    # for the one person who wants it, so it is present and out of the way.
    advanced: bool = False


# ─────────────────────────────────────────────────────────────────────────────
#  Live-shaped events
# ─────────────────────────────────────────────────────────────────────────────
# main.py's receive loop reads exactly these and nothing else:
#
#     response.data                                    24 kHz mono PCM
#     response.server_content.output_transcription.text
#     response.server_content.input_transcription.text
#     response.server_content.turn_complete
#     response.tool_call.function_calls[]               .id .name .args
#     response.session_resumption_update                (read with getattr)
#
# Every attribute must EXIST on every message, because the loop tests them with
# plain truthiness (`if response.data:`) rather than getattr. A missing attribute
# is an AttributeError inside the receive loop, which is the one place in this
# app where an exception ends the session.
@dataclass
class Transcription:
    text: str = ""


@dataclass
class FunctionCall:
    id: str = ""
    name: str = ""
    args: dict = field(default_factory=dict)


@dataclass
class ToolCall:
    function_calls: list[FunctionCall] = field(default_factory=list)


@dataclass
class ServerContent:
    output_transcription: Optional[Transcription] = None
    input_transcription: Optional[Transcription] = None
    turn_complete: bool = False
    interrupted: bool = False


@dataclass
class Msg:
    """One event out of `Engine.receive()`, shaped like the SDK's own."""
    data: Optional[bytes] = None
    server_content: Optional[ServerContent] = None
    tool_call: Optional[ToolCall] = None
    session_resumption_update: Any = None

    # -- constructors for the five things an engine actually emits ------------
    @staticmethod
    def audio(pcm: bytes) -> "Msg":
        return Msg(data=pcm)

    @staticmethod
    def said(text: str) -> "Msg":
        """What the assistant is saying — feeds the log AND the avatar's mouth."""
        return Msg(server_content=ServerContent(
            output_transcription=Transcription(text)))

    @staticmethod
    def heard(text: str) -> "Msg":
        """What the user said — feeds the log and the self-echo guard's clock."""
        return Msg(server_content=ServerContent(
            input_transcription=Transcription(text)))

    @staticmethod
    def turn_end() -> "Msg":
        return Msg(server_content=ServerContent(turn_complete=True))

    @staticmethod
    def calls(items: Sequence[FunctionCall]) -> "Msg":
        return Msg(tool_call=ToolCall(list(items)))


# ─────────────────────────────────────────────────────────────────────────────
#  The four protocols
# ─────────────────────────────────────────────────────────────────────────────
class Engine(Protocol):
    """A way to hold the conversation. `gemini_live` is one; `pipeline` is the
    other. main.py talks to nothing but this."""

    caps: dict

    async def send_realtime_input(self, *, audio) -> None:
        """A chunk of microphone PCM, 16 kHz mono. Called continuously."""

    async def send_client_content(self, *, turns, turn_complete: bool = True) -> None:
        """Text or an image injected as a user turn. `turns` is the Gemini shape:
        {"role": "user", "parts": [{"text": ...} | {"inline_data": {...}}]}."""

    async def send_tool_response(self, *, function_responses) -> None:
        """Results for a tool_call.

        WORTH KNOWING: on Live this only hands the result back and the server
        carries on by itself. Everywhere else the conversation is stateless, so
        this call is what triggers the SECOND model round trip of the turn. The
        signature is identical either way — main.py cannot tell — but it is why
        a tool-using turn costs more on a pipeline engine than on Live.
        """

    def receive(self) -> AsyncIterator[Msg]:
        """The event stream. Ends when the turn source is closed."""

    async def close(self) -> None: ...


class LLM(Protocol):
    """Text in, text out, with tools.

    ONE implementation covers OpenAI, Groq, DeepSeek, Mistral, xAI, OpenRouter,
    Together, Cerebras, Ollama, LM Studio, llama.cpp and vLLM — all of them serve
    /v1/chat/completions and differ only in base_url and key. This is the single
    reason this folder is seven files instead of twenty."""

    caps: dict

    async def chat(self, messages: list[dict], tools: list[dict] | None = None,
                   *, stream: bool = True) -> AsyncIterator[dict]:
        """Yields {"text": str} and/or {"tool_calls": [FunctionCall, ...]}."""


class STT(Protocol):
    """Audio in, text out.

    CPU is the default target. The project's stated requirement is that no GPU
    is needed, and a provider that quietly needs one breaks that promise for
    everybody who reads it. GPU stays an option in the panel, never an
    assumption in the code."""

    caps: dict

    async def transcribe(self, pcm: bytes, sample_rate: int = 16000,
                         language: str = "") -> str: ...


class TTS(Protocol):
    """Text in, 24 kHz mono PCM out — the rate `_play_audio` already opens the
    speaker at, so nothing downstream has to resample.

    Yields in chunks rather than returning a finished clip: the avatar's mouth
    and the interrupt path both work on a stream, and waiting for a whole
    sentence to synthesise before the first sample plays is the difference
    between an assistant that answers and one that pauses."""

    caps: dict

    async def speak(self, text: str, voice: str = "") -> AsyncIterator[bytes]: ...


# ─────────────────────────────────────────────────────────────────────────────
#  The declaration every provider file exposes
# ─────────────────────────────────────────────────────────────────────────────
# Deliberately the same idea as actions/*.py's TOOL and plugins/*.py's PLUGIN: a
# module-level dict, discovered rather than registered, so the registry never has
# to be edited to add one.
#
#     PROVIDER = {
#         "id":       "openai_compat",       # unique, stable, stored in config
#         "kind":     "llm",                 # llm | stt | tts | engine
#         "label":    "OpenAI-compatible",   # what the panel shows
#         "hint":     "Ollama · LM Studio · Groq · DeepSeek · OpenRouter · vLLM",
#         "requires": ("httpx",),            # checked with find_spec, NOT imported
#         "install":  "httpx",               # what the one-click button installs
#         "needs":    (Field("base_url", "Base URL",
#                            default="http://localhost:11434/v1"),
#                      Field("model",    "Model"),
#                      Field("api_key",  "API key", kind="secret", required=False)),
#         "caps":     {"tools": True, "tool_format": "openai", "max_tools": 12},
#         "factory":  _make,                 # called ONCE, lazily, on first use
#     }
_KINDS = ("llm", "stt", "tts", "engine")
_REQUIRED_KEYS = ("id", "kind", "label", "factory")


def validate(decl: dict) -> str:
    """Empty string when the declaration is usable, else why it is not.

    Mirrors action_loader on purpose: a bad provider is reported and skipped, and
    never raises out of discovery to take the others down with it."""
    if not isinstance(decl, dict):
        return "PROVIDER is not a dict"
    for k in _REQUIRED_KEYS:
        if not decl.get(k):
            return f"PROVIDER is missing '{k}'"
    if decl["kind"] not in _KINDS:
        return f"kind must be one of {_KINDS}, not {decl['kind']!r}"
    if not callable(decl["factory"]):
        return "factory is not callable"
    for f in decl.get("needs", ()):
        if not isinstance(f, Field):
            return f"needs must contain Field objects, found {type(f).__name__}"
    return ""


def caps_of(decl: dict) -> dict:
    """A provider's capabilities with every key present. Callers read this
    without guarding, so a provider that mentions two keys still answers all
    of them."""
    out = dict(DEFAULT_CAPS)
    out.update(decl.get("caps") or {})
    return out
