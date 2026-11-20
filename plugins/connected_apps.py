"""
plugins/connected_apps.py — the assistant's way into the apps the user connected.

One tool, three steps (core/connected_apps/service does the work):
    find     the app's actions that fit what is needed, with their arguments
    use      run one; anything not marked read-only is confirmed on screen
    connect  open the Connected apps panel (adding an app and signing in
             happen there, by the user)

The connections themselves, the sign-ins and the panel are the program's
own (core/connected_apps, gui/apps_panel.py): this file only describes the
tool and hands each call over.
"""
from __future__ import annotations

from core import outcome

PLUGIN = {
    "name": "connected_apps",
    "label": "Connected apps",
    "level": "basic",
    "panel": "apps",
    "summary": "Lets your assistant work in apps you connect, such as Notion or GitHub: "
               "read things there, and change them after you confirm.",
    "setup": [
        "Switch it ON, then press OPEN (or say \"connect my Notion\").",
        "Type the app's name, pick it, and press Connect. Sign in to it in the browser window "
        "that opens.",
        "Then just ask: \"add this to my Notion\", \"what are my open GitHub issues?\". Anything "
        "that changes something asks you first.",
    ],
    # With nothing connected yet this line is all the model knows: worded as
    # "when the user asks to connect one", a request to DO something in such
    # an app ("add a page in my Notion", "my open GitHub issues") went to the
    # screen, 4 of 4 (bench 2026-10-25).
    "description": (
        "For an online service that has NO tool of its own in this list (look first: mail, "
        "calendar or notes may have one) - the user's notes, documents, code, tasks or projects "
        "in a service they have an account with. None is connected yet: for anything the user "
        "wants done or read in such a service, call action=connect with its name; they connect "
        "it once and it works from then on."),
    "tier": "core",
    # Answers at once ("started"); the task goes on in the background and its
    # result is said when it ends, so the conversation never waits on an app.
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "enum": ["do", "connect", "stop"],
                       "description": "do: carry out the user's request in the app. connect: open "
                                      "the panel where the user adds and signs in to an app. stop: "
                                      "stop a task that is running."},
            "app": {"type": "STRING", "description": "The app's name, as listed."},
            "task": {"type": "STRING", "description": "For do: the user's whole request for that "
                                                      "app, with every detail they gave (names, "
                                                      "colours, text, dates), in their words."},
        },
        "required": ["action"],
    },
}


def describe() -> str:
    """The description as it is now: which apps are connected, and that
    work in them goes through this tool (not the screen)."""
    from core.connected_apps import service
    now = service.summary()
    if not now:
        return PLUGIN["description"]
    return ("Work in the apps the user connected: " + now + ". For ANYTHING to do or to read in one "
            "of these apps, call this tool with action=do, the app, and the whole request as `task` "
            "- never the screen agent and never by opening the app on screen. One call does the "
            "whole task: say two to five words, then wait; the result is said by itself when it is "
            "done, and every change asks the user on screen first. To stop it: action=stop. To add "
            "an app, or when the app named is not listed: action=connect.")


def run(parameters: dict, player=None) -> str:
    from core.connected_apps import agent, service
    p = parameters or {}
    action = str(p.get("action") or "").strip().lower()
    app = str(p.get("app") or "").strip()
    if action == "connect":
        opener = getattr(player, "open_panel", None) if player is not None else None
        if not callable(opener):
            return outcome.failed("connected_apps.connect", "the Connected apps panel cannot be "
                                                            "opened here")
        opener("apps", {"search": app})
        return outcome.done("connected_apps.connect", app=app or None, panel="open",
                            next="the user picks the app in the panel, presses Connect and signs "
                                 "in in the browser; tell them so in one short sentence")
    if action == "stop":
        return agent.stop(app)
    if not app:
        return outcome.ask("connected_apps", "which app?", connected=service.summary() or "none")
    say = getattr(player, "request_say", None) if player is not None else None
    return agent.start(app, str(p.get("task") or p.get("need") or ""), say=say)
