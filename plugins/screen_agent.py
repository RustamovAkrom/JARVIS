"""
Plugin — Screen Agent: does a task on screen by looking at it, step by step.

WHAT THIS FILE IS
    The shell. It owns the tool declaration, the settings, the run state, the
    HUD panel, the worker thread and the confirmation gate. The work itself is
    one loop in plugins/_screen_loop.py:

        look -> understand -> decide -> look closer at the target -> act
             -> wait until the screen settles, measure it -> the next look checks it

    A step is one request carrying the screenshot, the controls the system
    names, the model's own plan and notes and the history so far, plus a
    second, magnified look at the target before anything is clicked. The eyes
    are plugins/_screen_eyes.py; the hands are core/screenact.py.

IT NARRATES, IT DOES NOT BLOCK
    run() starts a worker and returns immediately, because a plugin's run()
    holds the thread that answers the live turn. The worker speaks through
    player.request_say and paints the HUD panel, so the conversation stays
    alive: the user can talk, ask what it is doing, or tell it to stop while it
    is working.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Optional

from memory.config_manager import get_assistant_name, get_plugin_config

_NS = "screen_agent"


# ---------------------------------------------------------------------------
# Tool declaration
# ---------------------------------------------------------------------------
PLUGIN = {
    # For the plugin manager (core/plugin_loader): never sent to a model.
    "level": "basic",
    # The product's own tools hand every task done on the screen to this
    # plugin; OFF, those tasks have nobody to do them (core/plugin_loader).
    "starts_on": True,
    "label": "Screen agent",
    # Inline, not behind use_feature (core/tool_budget). Behind it, "stop the
    # screen task" reached the agent as a new start (the router fills the
    # arguments) and the task went on while the user was told it stopped;
    # measured: stop right 0 of 2 behind the router, 2 of 2 inline (2026-10-11).
    "tier": "core",
    "summary": "Does a task on your screen with the mouse and keyboard while you watch.",
    "setup": [
        "Say a task that needs clicking: \"Open YouTube and play some lo-fi music\" — it looks at the screen and does it step by step.",
        "In these settings: ask before each step or only before risky ones, and the step limit.",
        "From your phone it works while the screen is unlocked.",
    ],
    "name": "screen_agent",
    "description": (
        "Does a task ON THE SCREEN the way a person at the computer would: it "
        "looks at the screen, decides one action, clicks or types, waits, looks "
        "again to check it worked, and repeats until the task is visibly done. "
        "USE IT FOR ANY TASK THAT TAKES MORE THAN ONE ACTION ON SCREEN, IN ANY "
        "PROGRAM OR ANY WEBSITE: filling in a form, finding and opening "
        "something, changing a setting and confirming it, sending something "
        "through a website, working inside a desktop application. Web pages "
        "are included - 'open Chrome, find my router's settings page and turn "
        "on the guest network' is ONE call to this tool with the whole task as "
        "the goal. It is not for posting, liking, following or commenting in "
        "bulk on social media. It opens and focuses programs "
        "itself, so never combine it with open_app or browser_control for the "
        "same task, and never split one task into several calls. "
        "Not for anything another tool or feature does (inline or through "
        "use_feature), nor for a feature that is switched off. "
        "Do NOT use it for: a question about what is on screen (use "
        "'screen_process'), a single keystroke or click (use "
        "'computer_control'), only launching a program (use 'open_app'), or "
        "only opening one web address (use 'browser_control'). "
        "Call with action='stop' to abort, action='status' for progress, and "
        "action='decline' the moment the user refuses a step it asked about "
        "(any form of no). A step is APPROVED only by the user pressing the "
        "confirmation button (on screen, or on their phone) - never by you."
    ),
    "brief": "Does a multi-step task on screen (any program or website) by looking and checking each step.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": ("'start' (default), 'stop', 'status', or "
                                "'decline' when the user refuses a step."),
            },
            "show_me": {
                "type": "BOOLEAN",
                "description": ("true only when the user asked to be SHOWN or "
                                "TAUGHT how to do it: each step is then said out "
                                "loud as it is done. Otherwise leave it out."),
            },
            "goal": {
                "type": "STRING",
                "description": (
                    "The whole task, written in ENGLISH, concretely: name the "
                    "program or website the user named, and include every value "
                    "they gave VERBATIM in its own language (text to type, names, "
                    "numbers). Describe what should be true when it is finished. "
                    "If the user said to go ahead without asking (to pay, send, "
                    "delete), keep that in the goal in their words; never add it "
                    "yourself."
                ),
            },
        },
        "required": [],
    },
}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
_DEFAULTS = {
    # Every step sends a screenshot, so this is a vision model. Any choice is
    # only the first rung: core/gemini walks its whole ladder behind it.
    "vision_model": "gemini-3.5-flash",
    "confirm_mode": "irreversible",
    # Started from the phone, nobody sees the screen: every step is asked on
    # the phone by default (the user can narrow it to irreversible steps).
    "phone_confirm_mode": "every step",
    # A new key on purpose. The old "max_steps" counted planned actions, with
    # requests budgeted separately; here one step IS one request, and an old
    # saved limit of a dozen would stop an ordinary web task half way.
    # 50 since long tasks keep a tidy memory (older steps shortened, notes
    # kept); a stuck run still ends early (refusals, failed checks).
    "step_limit": "50",
    "narrate": False,
    # Look again, magnified, at every target before clicking it. One more
    # request per click; the difference between the right control and its
    # neighbour on a high-resolution screen.
    "precise_aim": True,
    # Reason before each decision (bounded, see THINK_EXTRA).
    "think": True,
}

_HARD_MAX_STEPS = 80

_MODEL_CHOICES = ["gemini-3.5-flash", "gemini-3.6-flash", "gemini-flash-latest",
                  "gemini-3.5-flash-lite", "gemini-3.1-flash-lite",
                  "gemini-flash-lite-latest"]


def _product() -> bool:
    try:
        from core import edition
        return edition.is_product()
    except Exception:
        return False


if _product():
    # In the product computer_control only closes programs and browser_control
    # only opens pages (actions/), so ALL mouse and keyboard work is here —
    # even one click or one key. Two tools for the same job made the model
    # pick the blind one (beta 2026-10-12).
    PLUGIN["description"] = PLUGIN["description"].replace(
        "Do NOT use it for: a question about what is on screen (use "
        "'screen_process'), a single keystroke or click (use "
        "'computer_control'), only launching a program (use 'open_app'), or "
        "only opening one web address (use 'browser_control'). ",
        "It is the ONLY way to use the mouse and keyboard: use it for any task "
        "that needs them, even one click, one key or typing a word. Do NOT use it for: a "
        "question about what is on screen (use 'screen_process'), only launching "
        "a program (use 'open_app'), only opening one web address (use "
        "'browser_control'), files and folders (use 'file_controller'). ")
    PLUGIN["brief"] = ("The only way to use the mouse and keyboard: does any task on screen "
                       "(any program or website), even one click, by looking and checking each step.")


PLUGIN_SETTINGS = {
    "namespace": _NS,
    "title": "🖱️  SCREEN AGENT (look, act, check)",
    "fields": [
        {"key": "vision_model", "label": "Model (with your own Gemini key)", "type": "choice",
         "options": _MODEL_CHOICES, "default": _DEFAULTS["vision_model"]},
        {"key": "confirm_mode", "label": "Ask me before", "type": "choice",
         "options": ["irreversible", "every step"],
         "default": "irreversible"},
        {"key": "phone_confirm_mode", "label": "Started from the phone: ask me before",
         "type": "choice", "options": ["every step", "irreversible"],
         "default": _DEFAULTS["phone_confirm_mode"]},
        {"key": "step_limit", "label": "Step limit per task (1-80)",
         "type": "text", "default": _DEFAULTS["step_limit"]},
        {"key": "narrate", "label": "Say every step out loud",
         "type": "toggle", "default": False},
        {"key": "precise_aim", "label": "Look closer before every click (more accurate, a little slower)",
         "type": "toggle", "default": True},
        {"key": "think", "label": "Think before each step (smarter, a few seconds slower)",
         "type": "toggle", "default": True},
    ],
    "action": {"label": "CHECK WHAT I CAN SEE  (switch to any window within 5 s; clicks nothing)",
               "run": lambda values: _self_test()},
}


def _cfg() -> dict:
    try:
        stored = get_plugin_config(_NS) or {}
    except Exception:
        stored = {}
    out = dict(_DEFAULTS)
    for key, value in stored.items():
        if key in _DEFAULTS and value not in (None, ""):
            out[key] = value
    return out


def _cfg_int(cfg: dict, key: str, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(float(str(cfg.get(key, _DEFAULTS[key]))))))
    except Exception:
        return int(_DEFAULTS[key])


def _cfg_bool(cfg: dict, key: str) -> bool:
    value = cfg.get(key, _DEFAULTS[key])
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Run state — one run per process, because there is one screen
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_state: dict = {
    "running": False, "thread": None, "stop": None, "confirm": None,
    "approved": False, "pending": "", "goal": "", "lines": [],
    "step": 0, "calls": 0,
}


# ---------------------------------------------------------------------------
# HUD — every one of these is a no-op when player is None, so the whole plugin
# runs headless in a test.
# ---------------------------------------------------------------------------
def _log(player, message: str) -> None:
    if player:
        try:
            player.write_log(message)
        except Exception:
            pass


def _say(player, instruction: str) -> None:
    """Ask the assistant to speak: a turn of the model, which phrases it in
    the user's language. For the outcome only — once per task."""
    if player and hasattr(player, "request_say"):
        try:
            player.request_say(instruction)
        except Exception:
            pass


def _busy(player, what: str) -> None:
    """Tell the host what the assistant is busy with ("" = done): sound from
    what the task opens may reach the microphone, and is not a question
    (main.set_busy). A host without it: nothing."""
    fn = getattr(player, "set_busy", None) if player else None
    if callable(fn):
        try:
            fn(what)
        except Exception:
            pass


def _announce(player, text: str) -> None:
    """Say a step's sentence as it is, with no model turn (main.plugin_announce):
    showing the user how, or each step when they asked to hear them. Through
    _say each one was a whole turn of the model — billed like a question and
    heard as a second answer."""
    fn = getattr(player, "request_announce", None) if player else None
    if fn is None:
        return _say(player, "Say this to the user in one short sentence, in their "
                            "language, and call no tool: " + text)
    try:
        fn(text)
    except Exception:
        pass


def _panel(player, line: Optional[str] = None) -> None:
    with _lock:
        if line is not None:
            _state["lines"].append(line)
            del _state["lines"][:-40]
        head = f"🎯 {_state['goal']}"
        if _state["calls"]:
            head += (f"   {_state['calls']} request"
                     f"{'s' if _state['calls'] != 1 else ''}")
        body = "\n".join(_state["lines"])
    if not player:
        return
    try:
        player.show_content("SCREEN AGENT", f"{head}\n\n{body}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# The bridge to the assistant's own tools
# ---------------------------------------------------------------------------
def _open_app_param(player) -> str:
    """The name open_app gives its program parameter, read from its own schema."""
    getter = getattr(player, "tool_catalog", None) if player else None
    try:
        for tool in (getter() if callable(getter) else []) or []:
            if tool.get("name") == "open_app":
                schema = tool.get("parameters") or {}
                required = list(schema.get("required") or [])
                props = list((schema.get("properties") or {}).keys())
                return (required or props or ["app_name"])[0]
    except Exception:
        pass
    return "app_name"


def _make_open_app(player):
    def open_app(name: str) -> str:
        from core import outcome
        runner = getattr(player, "run_tool", None) if player else None
        if not callable(runner):
            return outcome.failed("open_app", "the assistant's tools are not "
                                              "reachable from the screen agent")
        try:
            return str(runner("open_app", {_open_app_param(player): name}) or "")
        except Exception as e:
            return outcome.failed("open_app", f"the tool raised an error: {e}")
    return open_app


# THINKING, BOUNDED
#     The subscription's model can reason before it answers. Off for speech,
#     where the first word matters; on for a screen decision, where the
#     measured failures were decisions made without thinking (alt+tab to go
#     back a page, a table-of-contents entry taken for a link). Bounded so it
#     can never stall a task:
#       * a token cap for thinking and answer together, and a shorter timeout;
#       * Qwen's own sampling for thinking — its authors warn that greedy
#         decoding (temperature 0) in thinking mode can repeat itself forever;
#       * nothing back in time (or a cut-off answer): the same request at once
#         without thinking, so the worst case is a few lost seconds.
#     Only the subscription's own server receives these fields (core/gemini);
#     every other provider gets the request it always got.
#     The bound was 1600 tokens and 25 s; measured (2026-10-12) a thought
#     decision usually took 6-8 s but some ran to 14 and 25 s, with the
#     answer itself now asked to be terse. 1100 tokens is ~12 s at the
#     A40's pace; past it the plain retry decides.
THINK_EXTRA = {
    "chat_template_kwargs": {"enable_thinking": True},
    "max_tokens": 1100,
    "temperature": 0.6, "top_p": 0.95, "top_k": 20,
}
THINK_TIMEOUT_MS = 15_000
# "light": the DONE check. Measured (2026-10-13, Blender) a full think there
# ran into the cap and came back empty, then the plain retry - 17 s of a
# 58 s task. A yes/no about one picture needs less room.
THINK_LIGHT = dict(THINK_EXTRA, max_tokens=600)
THINK_LIGHT_TIMEOUT_MS = 9_000
PLAIN_TIMEOUT_MS = 45_000


def _make_ask(model: str, thinking: bool = True):
    def ask(system: str, prompt: str, pictures: list, think=False) -> Optional[str]:
        # The instructions travel as the system instruction: the same text for
        # every step of a run, so a server that caches prompt prefixes (the
        # subscription's own, Gemini's) reads them once. Every rung of
        # core/gemini honours it — an OpenAI-protocol provider gets it as the
        # system message.
        from core import gemini
        from google.genai import types as gtypes
        parts = [gtypes.Part.from_bytes(data=p, mime_type="image/jpeg")
                 for p in pictures] + [prompt]
        config = gtypes.GenerateContentConfig(
            system_instruction=system, temperature=0.0,
            response_mime_type="application/json")
        if think and thinking:
            light = think == "light"
            response = gemini.call(parts, tier=model, config=config,
                                   timeout_ms=THINK_LIGHT_TIMEOUT_MS if light else THINK_TIMEOUT_MS,
                                   vision=True,
                                   extra_body=THINK_LIGHT if light else THINK_EXTRA)
            text = (getattr(response, "text", "") or "") if response is not None else ""
            if "{" in text:
                return text
            # Thought too long, or answered without the object: decide now.
        response = gemini.call(parts, tier=model, config=config,
                               timeout_ms=PLAIN_TIMEOUT_MS, vision=True)
        if response is None:
            return None
        return getattr(response, "text", "") or ""
    return ask


# ---------------------------------------------------------------------------
# The confirmation gate
# ---------------------------------------------------------------------------
def _await_approval(player, description: str, irreversible: bool = False,
                    timeout: float = 180.0) -> bool:
    """Ask and wait. True only if a PERSON pressed the confirmation button.

    The question is the shared gate (core/confirm): a button on screen, or on
    the phone during a phone turn. The model never approves anything — it can
    only decline (action='decline') or stop, both of which end the step.
    """
    from core import confirm
    from core.i18n import t
    event = _state["confirm"]
    event.clear()
    with _lock:
        _state["approved"] = False
        _state["pending"] = description

    def yes() -> str:
        with _lock:
            _state["approved"] = True
        event.set()
        return "approved"

    title = t("confirm.screen_step_irreversible" if irreversible else "confirm.screen_step")
    chat = _state.get("phone_chat")
    if chat is not None:
        # Started from the phone: every question goes to the phone for the
        # whole task, even after the phone's turn has closed — never to a
        # screen nobody is looking at. An ordinary step once, an irreversible
        # one the number of times the user chose (core/remotemode).
        from core import phonegate
        asked = phonegate.ask(chat, title, description[:400], yes, report=lambda _r: None,
                              on_cancel=event.set, tool="screen_agent",
                              times=None if irreversible else 1)
    else:
        asked = confirm.request(
            key="screen_agent", title=title,
            detail=description[:400], run=yes, report=lambda _r: None, on_cancel=event.set)
    if not event.is_set():
        _panel(player, f"   ⏸️  waiting for you — {description}")
        _say(player, f"In one short sentence, tell the user what you are about to do "
                     f"({description}) and that they need to press the confirmation "
                     f"button for it. {asked}")
        event.wait(timeout)
    confirm.withdraw("screen_agent")
    with _lock:
        approved = bool(_state["approved"])
        _state["pending"] = ""
    return approved


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------
STOPPED_MSG = "Tell the user you have stopped working on the screen."


try:
    from core import sensors as _sensors          # what watches, shown on the window
except ImportError:                               # an older core: nothing to show
    _sensors = None


def confirm_mode(cfg: dict, from_phone: bool) -> str:
    """"every step" or "irreversible" for this task. Started from the phone
    it is the phone's own setting, every step unless the user narrowed it."""
    if from_phone:
        return str(cfg.get("phone_confirm_mode") or _DEFAULTS["phone_confirm_mode"]).lower()
    return str(cfg.get("confirm_mode") or "irreversible").lower()


def _phone_chat():
    """The phone's chat when this task was asked for from the phone, else None."""
    try:
        from core import phonegate
        return phonegate.active()
    except Exception:
        return None


def _stop_from_window() -> None:
    """"Stop" on the window's status strip (core/sensors)."""
    run({"action": "stop"})


def _worker(player, goal: str, cfg: dict, show_me: bool = False) -> None:
    from core import screenview
    from plugins import _screen_loop as loop
    if _sensors is not None:
        _sensors.start("screen_agent", "screen", stop=_stop_from_window)

    stop = _state["stop"]
    narrate = _cfg_bool(cfg, "narrate")
    confirm_raw = confirm_mode(cfg, _state.get("phone_chat") is not None)

    def spent() -> None:
        with _lock:
            _state["calls"] += 1

    last_action = {"name": ""}

    def progress(line: str) -> None:
        if line.startswith("["):
            with _lock:
                _state["step"] += 1
            if narrate:
                _announce(player, re.sub(r"^\[[^\]]*\]\s*", "", line)[:160])
        _log_timing(line, last_action)
        _panel(player, line)

    def approve(description: str, irreversible: bool) -> bool:
        # Always asked: an older saved "never" no longer skips the question.
        return _await_approval(player, description, irreversible)

    hooks = loop.Hooks(
        ask=_make_ask(str(cfg.get("vision_model") or _DEFAULTS["vision_model"]),
                      thinking=_cfg_bool(cfg, "think")),
        approve=approve,
        progress=progress,
        say=lambda text: _announce(player, text),
        open_app=_make_open_app(player),
        spent=spent,
    )

    try:
        screenview.forget_slow_windows()
        _panel(player, "👀 looking at the screen...")
        result = loop.run(
            goal, hooks, stop=stop,
            max_steps=_cfg_int(cfg, "step_limit", 1, _HARD_MAX_STEPS),
            confirm_every_step=confirm_raw.startswith("every"),
            os_name=screenview.capabilities().get("os", ""),
            precise_aim=_cfg_bool(cfg, "precise_aim"),
            show_me=bool(show_me or narrate))
        _report(player, result)
    except Exception as e:
        print(f"[ScreenAgent] {type(e).__name__}: {e}")
        _finish(player, f"Tell the user the screen task could not run: {e}", f"❌ {e}")


def _log_timing(line: str, last: dict) -> None:
    """Where each step's time went, in the terminal and the log file - the
    kind of action and the seconds, never what was on the screen or typed
    (the log holds no content: commit 7dd4434)."""
    text = line.strip()
    match = re.match(r"\[[^\]]*\]\s+([a-z_]+)\b", text)
    if match:
        last["name"] = match.group(1)
        return
    if text.startswith("time:"):
        with _lock:
            step = _state["step"]
        print(f"[ScreenAgent] step {step}: {last['name'] or '-'} | {text}")


def _report(player, result) -> None:
    """Turn the loop's result into what the user hears.

    `DONE` is spoken as done because the model saw it done on a screenshot, and
    nothing else is. A run that stops says what stopped it.
    """
    from plugins._screen_loop import DONE, FAILED, LIMIT, NEED_USER, STOPPED

    with _lock:
        spent_total = _state["calls"]
    timing = getattr(result, "timing", {}) or {}
    print(f"[ScreenAgent] finished: {result.status} | {result.steps} steps, {spent_total} requests | "
          + " · ".join(f"{k} {v:.1f}s" for k, v in timing.items()))
    tail = (f"{result.steps} step{'s' if result.steps != 1 else ''}, "
            f"{spent_total} request{'s' if spent_total != 1 else ''}")

    if result.status == DONE:
        return _finish(player,
                       f"Tell the user the task on screen is finished: {result.message}",
                       f"✅ Done — {tail}")
    if result.status == NEED_USER:
        return _finish(player,
                       f"Tell the user you stopped because this part needs them: "
                       f"{result.message}. Ask them to do it and then tell you "
                       f"to carry on.",
                       f"🙋 {result.message}")
    if result.status == STOPPED:
        return _finish(player, STOPPED_MSG, f"⏹️  {result.message}")

    # NEVER END BY ASKING THEM TO DO IT
    #     The assistant has the screen, the keyboard and the mouse; anything it
    #     asks the user to look at, it could have looked at, and anything it asks
    #     them to finish, it was asked to do. Asking is reserved for NEED_USER.
    no_delegating = ("Do NOT ask the user to check the screen, to confirm "
                     "anything you could have looked at yourself, or to finish "
                     "the task. Offer to try again instead.")
    verb = "could not finish" if result.status == LIMIT else "failed"
    if result.status not in (FAILED, LIMIT):
        verb = "ended"
    return _finish(player,
                   f"Tell the user the task on screen {verb}, and exactly why, "
                   f"without claiming any part of it worked that you did not "
                   f"see: {result.message}. {no_delegating}",
                   f"❌ {result.message}  ({tail})")


def _finish(player, spoken: str, line: str) -> None:
    if _sensors is not None:
        _sensors.stop("screen_agent")
    _busy(player, "")
    _log(player, f"{get_assistant_name()}: {line.splitlines()[0]}")
    _panel(player, line)
    _say(player, spoken)
    with _lock:
        _state["running"] = False
        _state["pending"] = ""
        _state["approved"] = False
        _state["phone_chat"] = None


# ---------------------------------------------------------------------------
# Settings self test — reads the screen, clicks nothing.
# ---------------------------------------------------------------------------
def _self_test() -> str:
    from core import screenact, screenview
    from plugins import _screen_eyes as eyes

    missing = screenview.missing_packages()
    if missing:
        return f"Missing: pip install {' '.join(missing)}"

    can_act, why = screenact.available()

    # The button is on the assistant's own window, which is never read, so the
    # user is given a few seconds to put the window they care about in front.
    deadline = time.monotonic() + 5.0
    view = screenview.look()
    while view.source == "self" and time.monotonic() < deadline:
        time.sleep(0.25)
        view = screenview.look()
    if view.source == "self":
        return ("I read only other programs' windows. Press the button, then "
                "click the window you want me to check within 5 seconds.")

    lines = [view.summary()]
    try:
        frame = eyes.capture(view)
        named = eyes.named_controls(view, frame)
        lines.append(f"Picture: {frame.image.width}x{frame.image.height} px, "
                     f"{len(frame.jpeg) // 1024} KB sent per look. "
                     f"{len(named)} controls named by the window itself, "
                     f"{len(frame.shapes())} shapes found in the picture.")
    except Exception as e:
        lines.append(f"Could not take a picture: {e}")
    lines.append(f"Hands: {'ready' if can_act else 'unavailable — ' + why}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run(parameters: dict, player=None, session_memory=None) -> str:
    action = str((parameters or {}).get("action") or "start").strip().lower()

    if action in ("stop", "cancel", "abort"):
        with _lock:
            running_now = _state["running"]
            stop_event, confirm_event = _state["stop"], _state["confirm"]
        if not running_now:
            return "The screen agent is not doing anything right now."
        if stop_event:
            stop_event.set()
        if confirm_event:
            with _lock:
                _state["approved"] = False
            confirm_event.set()      # release a pending approval so it can exit
        _log(player, f"{get_assistant_name()}: Screen agent stopping.")
        return ("Stopping. Confirm to the user that you have stopped working "
                "on the screen.")

    if action in ("decline", "deny", "reject", "no"):
        with _lock:
            pending, confirm_event = _state["pending"], _state["confirm"]
        if not pending or not confirm_event:
            return ("There is no step waiting for approval. Say nothing about "
                    "approval and continue the conversation normally.")
        with _lock:
            _state["approved"] = False
        confirm_event.set()
        return ("Declined - that step will not be done and the screen task stops "
                "there. Acknowledge in one short sentence; I report the rest myself.")

    if action in ("confirm", "approve", "yes", "continue"):
        with _lock:
            pending = _state["pending"]
        if not pending:
            return ("There is no step waiting for approval. Say nothing about "
                    "approval and continue the conversation normally.")
        # Only a person approves, with the button: a tool call cannot.
        return (f'The step "{pending}" is approved only by the user pressing the '
                f"confirmation button (on screen, or on their phone). Tell them that "
                f"in one short sentence. Do not call this tool to approve it.")

    if action == "status":
        with _lock:
            if not _state["running"]:
                return "The screen agent is idle."
            goal, step, calls = _state["goal"], _state["step"], _state["calls"]
            pending = _state["pending"]
            last = _state["lines"][-1] if _state["lines"] else ""
        if pending:
            return (f"Paused, waiting for the user to approve: {pending}. "
                    f"Goal: {goal}")
        return (f'Working on the screen: "{goal}". {step} steps so far, {calls} '
                f"model requests. Last: {last.strip() or 'starting'}")

    # -- start --------------------------------------------------------------
    from core import screenact, screenview

    missing = screenview.missing_packages()
    if missing:
        return (f"I cannot work on the screen: {', '.join(missing)} "
                f"{'is' if len(missing) == 1 else 'are'} not installed. "
                f"Install with: pip install {' '.join(missing)}")

    can_act, why = screenact.available()
    if not can_act:
        # Told to the model, so the assistant says what this session will not
        # allow instead of narrating a click that never happened.
        return f"I cannot drive this screen: {why}"

    # The model sometimes names it "task" (beta log 2026-10-12): same thing.
    params = parameters or {}
    goal = str(params.get("goal") or params.get("task") or params.get("request") or "").strip()
    if not goal:
        return ("Ask the user what exactly they want done on screen — I need a "
                "concrete goal before I can start.")

    # ONE CHECK-AND-CLAIM, UNDER ONE LOCK: the model sometimes issues the same
    # call twice at once, and two workers on one screen fight over one mouse.
    cfg = _cfg()
    stop_event, confirm_event = threading.Event(), threading.Event()
    with _lock:
        if _state["running"]:
            return (f'I am already working on the screen: "{_state["goal"]}". '
                    f"Ask the user whether to stop that first.")
        _state.update({
            "running": True, "stop": stop_event, "confirm": confirm_event,
            "approved": False, "pending": "", "goal": goal, "lines": [],
            "step": 0, "calls": 0, "phone_chat": _phone_chat(),
        })

    _panel(player, "👀 Looking at the screen...")
    _log(player, f"{get_assistant_name()}: Working on the screen — {goal}")

    # The list of installed programs takes a couple of seconds to read the
    # first time; read it now, while the first look is being decided, so an
    # "open" step does not wait for it.
    try:
        from core import installed_apps
        threading.Thread(target=installed_apps.catalog, name="installed-apps",
                         daemon=True).start()
    except Exception:
        pass
    show_me = params.get("show_me") in (True, "true", "True", 1, "1")
    thread = threading.Thread(target=_worker, name="screen-agent",
                              args=(player, goal, cfg, show_me), daemon=True)
    with _lock:
        _state["thread"] = thread
    _busy(player, f"working on the screen ({goal[:120]})")
    thread.start()

    return (f'[SCREEN_AGENT_STARTED] I am now working on the screen: "{goal}". '
            f"Tell the user you are starting, in one short sentence, then stop "
            f"and say nothing more — I narrate the steps and the result myself. "
            f"Do not call this tool again unless the user asks to stop, or "
            f"declines a step.")
