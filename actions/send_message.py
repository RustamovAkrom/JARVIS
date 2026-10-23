import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from core import outcome
from core.lazyimport import LazyModule, installed

# Imported on first USE, not at launch — see core/lazyimport.py. PAUSE is 0.06
# here rather than computer_control's 0.05, as it always was: typing into
# somebody else's chat window wants a little more room than a hotkey does.
_PYAUTOGUI = installed("pyautogui")
_PYPERCLIP = installed("pyperclip")


def _configure_pyautogui(module) -> None:
    module.FAILSAFE = True
    module.PAUSE    = 0.06


pyautogui = LazyModule("pyautogui", _configure_pyautogui)
pyperclip = LazyModule("pyperclip")

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

def _get_os() -> str:
    try:
        cfg = json.loads(
            (_base_dir() / "config" / "api_keys.json").read_text(encoding="utf-8")
        )
        return cfg.get("os_system", "windows").lower()
    except Exception:
        return "windows"


def _require_pyautogui():
    if not _PYAUTOGUI:
        raise RuntimeError("PyAutoGUI not installed. Run: pip install pyautogui")


def _paste_text(text: str) -> None:
    _require_pyautogui()

    os_name = _get_os()
    paste_hotkey = ("command", "v") if os_name == "mac" else ("ctrl", "v")

    if _PYPERCLIP:
        pyperclip.copy(text)
        time.sleep(0.15)
        pyautogui.hotkey(*paste_hotkey)
        time.sleep(0.1)
    else:
        pyautogui.write(text, interval=0.03)


def _clear_and_paste(text: str) -> None:
    _require_pyautogui()
    os_name = _get_os()
    select_all = ("command", "a") if os_name == "mac" else ("ctrl", "a")
    pyautogui.hotkey(*select_all)
    time.sleep(0.1)
    pyautogui.press("delete")
    time.sleep(0.1)
    _paste_text(text)

def _open_app(app_name: str) -> bool:
    _require_pyautogui()
    os_name = _get_os()

    try:
        if os_name == "windows":
            pyautogui.press("win")
            time.sleep(0.5)
            _paste_text(app_name)
            time.sleep(0.6)
            pyautogui.press("enter")
            time.sleep(2.5)
            return True

        elif os_name == "mac":
            result = subprocess.run(
                ["open", "-a", app_name],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
            )
            if result.returncode != 0:
                result = subprocess.run(
                    ["open", "-a", f"{app_name}.app"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
                )
            time.sleep(2.5)
            return result.returncode == 0

        else:
            launched = False
            for launcher in [
                ["gtk-launch", app_name.lower()],
                [app_name.lower()],
            ]:
                try:
                    subprocess.Popen(
                        launcher,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    launched = True
                    break
                except FileNotFoundError:
                    continue
            time.sleep(2.5)
            return launched

    except Exception as e:
        print(f"[SendMessage] ⚠️ Could not open {app_name}: {e}")
        return False


def _open_browser_url(url: str) -> bool:
    import webbrowser
    try:
        webbrowser.open(url)
        time.sleep(4.0)
        return True
    except Exception as e:
        print(f"[SendMessage] ⚠️ Could not open browser: {e}")
        return False

def _search_in_app(query: str) -> None:
    _require_pyautogui()
    os_name = _get_os()
    search_hotkey = ("command", "f") if os_name == "mac" else ("ctrl", "f")

    pyautogui.hotkey(*search_hotkey)
    time.sleep(0.5)
    _clear_and_paste(query)
    time.sleep(1.0)

def _desktop_send(app_name: str, receiver: str, message: str) -> str:
    if not _open_app(app_name):
        return outcome.failed(
            "send_message", f"{app_name} could not be opened, so nothing was "
            f"typed and no message was sent",
            platform=app_name, to=receiver,
        )

    time.sleep(1.0)
    _search_in_app(receiver)
    pyautogui.press("enter")
    time.sleep(0.8)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)

    # UNVERIFIED, AND THAT IS THE HONEST ANSWER.
    #
    # Everything above is keystrokes with sleeps between them: press Win, type
    # the app name, Ctrl+F, type the contact, Enter, paste, Enter. Nothing
    # reads back which window received any of it. On a machine one second
    # slower than these sleeps assume, the whole sequence lands somewhere else
    # — and the old code still returned "Message sent to X via Y."
    #
    # There is no read-back to add here without a driver per application, and
    # `_send_whatsapp` shows what that costs to build. Until one exists for a
    # given app, the truthful thing is to say the keystrokes went out and the
    # result is unconfirmed, which is something the user can glance at their
    # screen and check.
    return outcome.unverified(
        "send_message",
        f"the message was typed into {app_name} blind - no confirmation is "
        f"available for this app, so it is NOT known to have been sent",
        platform=app_name, to=receiver,
    )

def _send_whatsapp(receiver: str, message: str) -> str:
    """WhatsApp goes through the verified driver when there is one.

    _desktop_send below is a sequence of keystrokes with sleeps between them:
    it presses Win, types the app name, presses Ctrl+F, types the contact,
    presses Enter twice and reports success — without ever reading back which
    window received any of that. When the machine is a second slower than the
    sleeps assume, the whole sequence lands somewhere else and the user is
    still told the message was sent.

    plugins/_whatsapp_core.py already drives WhatsApp properly for the calling
    plugins: it finds the real window, opens the conversation, checks the
    conversation is the right person's, types, and confirms the box emptied
    before it says "sent". Using it here costs nothing and removes the one
    failure that matters — a message reported as delivered that never left.

    The blind path stays as the fallback for a machine where the driver cannot
    run at all (the plugin file removed, a missing dependency, no WhatsApp).
    But once the driver HAS run, its answer stands: falling back after it
    refused to send is how a message ends up typed into the wrong
    conversation, which is worse than not sending it.
    """
    try:
        from plugins import _whatsapp_core as wa
    except Exception as e:
        print(f"[SendMessage] WhatsApp driver unavailable ({e}) — typing blind.")
        return _desktop_send("WhatsApp", receiver, message)

    try:
        transport, why = wa.get()
    except Exception as e:
        print(f"[SendMessage] WhatsApp driver failed to start ({e}) — typing blind.")
        return _desktop_send("WhatsApp", receiver, message)

    if transport is None:
        print(f"[SendMessage] {why} — typing blind.")
        return _desktop_send("WhatsApp", receiver, message)

    sent, failure = transport.send_message_to(receiver, message)
    if sent:
        # The one platform that can say `done`: the driver opened the
        # conversation, proved it was the right person's, typed, and confirmed
        # the box emptied.
        return outcome.done("send_message", platform="WhatsApp",
                            to=receiver, verified=True)
    return outcome.failed("send_message", failure,
                          platform="WhatsApp", to=receiver)

def _send_telegram(receiver: str, message: str) -> str:
    return _desktop_send("Telegram", receiver, message)

def _send_signal(receiver: str, message: str) -> str:
    return _desktop_send("Signal", receiver, message)


def _send_discord(receiver: str, message: str) -> str:
    return _desktop_send("Discord", receiver, message)


def _send_instagram(receiver: str, message: str) -> str:
    _require_pyautogui()

    if not _open_browser_url("https://www.instagram.com/direct/new/"):
        return outcome.failed(
            "send_message", "Instagram could not be opened in the browser, so "
            "nothing was sent", platform="Instagram", to=receiver)

    _paste_text(receiver)
    time.sleep(1.5)

    pyautogui.press("down")
    time.sleep(0.3)
    pyautogui.press("enter")
    time.sleep(0.4)

    for _ in range(4):
        pyautogui.press("tab")
        time.sleep(0.15)
    pyautogui.press("enter")
    time.sleep(2.0)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)

    return outcome.unverified(
        "send_message",
        "the message was typed into Instagram in the browser blind - the page "
        "was never read back, so it is NOT known to have been sent",
        platform="Instagram", to=receiver)


def _send_messenger(receiver: str, message: str) -> str:
    _require_pyautogui()

    if not _open_browser_url("https://www.messenger.com/"):
        return outcome.failed(
            "send_message", "Messenger could not be opened in the browser, so "
            "nothing was sent", platform="Messenger", to=receiver)


    _search_in_app(receiver)
    time.sleep(0.5)
    pyautogui.press("down")
    time.sleep(0.3)
    pyautogui.press("enter")
    time.sleep(1.0)

    _paste_text(message)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(0.3)

    return outcome.unverified(
        "send_message",
        "the message was typed into Messenger in the browser blind - the page "
        "was never read back, so it is NOT known to have been sent",
        platform="Messenger", to=receiver)

_PLATFORM_MAP = [
    ({"whatsapp", "wp", "wapp"},              _send_whatsapp),
    ({"telegram", "tg"},                      _send_telegram),
    ({"instagram", "ig", "insta"},            _send_instagram),
    ({"signal"},                               _send_signal),
    ({"discord"},                              _send_discord),
    ({"messenger", "facebook", "fb"},         _send_messenger),
]


def _resolve_platform(platform_str: str):
    key = platform_str.lower().strip()
    for keywords, handler in _PLATFORM_MAP:
        if any(k in key for k in keywords):
            return handler
    return lambda r, m: _desktop_send(platform_str.strip().title(), r, m)


def send_message(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params       = parameters or {}
    receiver     = params.get("receiver", "").strip()
    message_text = params.get("message_text", "").strip()
    platform     = params.get("platform", "whatsapp").strip()

    if not receiver:
        return outcome.failed("send_message", "no recipient was given")
    if not message_text:
        return outcome.failed("send_message", "no message text was given",
                              to=receiver)
    if not _PYAUTOGUI:
        return outcome.failed(
            "send_message",
            "PyAutoGUI is not installed, so the desktop cannot be driven - "
            "run: pip install pyautogui",
            to=receiver, platform=platform,
        )

    preview = message_text[:50] + ("…" if len(message_text) > 50 else "")
    print(f"[SendMessage] 📨 {platform} → {receiver}: {preview}")
    if player:
        player.write_log(f"[msg] {platform} → {receiver}")   # the message itself IS the news

    threading.Thread(
        target=_send_and_report,
        args=(player, platform, receiver, message_text),
        daemon=True, name="send-message",
    ).start()

    # Sending opens an application, searches for a person and types — several
    # seconds during which the old code held the whole conversation still. The
    # answer goes out now and the result follows, which is the shape
    # whatsapp_call.py settled on for the same reason.
    return outcome.started("send_message", platform=platform, to=receiver)


def _send_and_report(player, platform: str, receiver: str,
                     message_text: str) -> None:
    """Do the sending, then say what actually happened — including when it did
    not happen, and including when nobody can tell.

    Every path here reports. A tool that says `started` and then goes quiet on
    failure has only moved the false success one step later, which is the whole
    thing this rewrite exists to stop.
    """
    try:
        result = _resolve_platform(platform)(receiver, message_text)
    except Exception as exc:
        print(f"[SendMessage] {type(exc).__name__}: {exc}")
        result = outcome.failed("send_message", f"it failed: {exc}",
                                to=receiver, platform=platform)

    # A prefix test on a fixed vocabulary, not a search for the word "sent".
    # The old check was `"sent" in text and "not sent" not in text`, which is
    # one careless rewording away from putting a tick next to a message that
    # never went.
    verified = result.startswith(f"status={outcome.DONE} ")

    # The full status line goes to the TERMINAL, where a developer reads it.
    # The activity log beside the assistant's own sentences is for the
    # conversation and for things that went wrong — a line reading
    # `status=done action=send_message verified=true` there is machinery
    # showing through, and it was reported as exactly that.
    print(f"[SendMessage] {'✅' if verified else '⚠️'} {result[:160]}")
    if player and outcome.is_problem(result):
        player.write_log(f"[msg] could not confirm the message to {receiver} — "
                         f"see the terminal")

    if verified:
        # It went, and the user asked for it to go. Announcing a success they
        # already expect is noise — the log and the panel carry the record.
        return

    if player and hasattr(player, "request_say"):
        try:
            player.request_say(
                "Tell the user, in one or two short sentences in their own "
                "language, exactly what this says happened — and if it says "
                "the message was not sent or not confirmed, say that plainly "
                "rather than claiming it was sent: " + result
            )
        except Exception:
            pass


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "send_message",
    "description": (
        "Sends a text message via WhatsApp, Telegram, or another messaging "
        "platform. Write 'message_text' in the USER'S OWN LANGUAGE, exactly "
        "what they asked to be said. "
        "This tool answers immediately and sends in the background. On "
        "status=started say only that you are sending it, never that it is "
        "sent. The result follows separately: status=done means it was "
        "confirmed delivered to the conversation, status=unverified means the "
        "keystrokes went out and NOBODY can confirm it arrived - say exactly "
        "that - and status=failed means it did not go. Never tell the user a "
        "message was sent unless the result said status=done."
    ),
    "tier": "core",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "receiver": {
                "type": "STRING",
                "description": "Recipient contact name"
            },
            "message_text": {
                "type": "STRING",
                "description": "The message to send"
            },
            "platform": {
                "type": "STRING",
                "description": "Platform: WhatsApp, Telegram, etc."
            }
        },
        "required": [
            "receiver",
            "message_text",
            "platform"
        ]
    },
    "handler": send_message,
}
