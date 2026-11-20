"""
Talking to the clap launcher (clap_launcher.pyw) from anywhere: is it running,
what is it doing, please stop.

A Qt local socket — a named pipe on Windows, a Unix socket on macOS and Linux —
so it works the same on every platform the app runs on, needs no port and is
reachable only by this user on this machine. The launcher listens on it; the
HUD's SETUP drawer and a second copy of the launcher ask through it.

Commands (one line each, one reply line):
    ping    -> "pong"
    status  -> JSON: {"listening", "paused", "jarvis", "pattern", "preloaded"}
    show    -> "ok"   (a second copy was opened: say so in a notification)
    quit    -> "ok"   (stop listening and exit)

Needs a Q(Core)Application to exist in the calling process.
"""
from __future__ import annotations

import getpass
import json
import re
from typing import Optional


def server_name() -> str:
    """Per user, so two people on one machine each have their own launcher."""
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return "jarvis-clap-launcher-" + (re.sub(r"[^A-Za-z0-9_.-]", "_", user) or "user")


def ask(command: str, timeout_ms: int = 700) -> Optional[str]:
    """Send one command; the reply, or None when no launcher answered."""
    try:
        from PyQt6.QtNetwork import QLocalSocket
    except Exception:
        return None
    sock = QLocalSocket()
    sock.connectToServer(server_name())
    if not sock.waitForConnected(timeout_ms):
        return None
    try:
        sock.write((command.strip() + "\n").encode("utf-8"))
        sock.flush()
        sock.waitForBytesWritten(timeout_ms)
        reply = b""
        while sock.waitForReadyRead(timeout_ms):
            reply += bytes(sock.readAll())
            if reply.endswith(b"\n"):
                break
        return reply.decode("utf-8", "replace").strip()
    finally:
        sock.disconnectFromServer()


def status() -> Optional[dict]:
    """What the running launcher is doing, or None when none is running."""
    reply = ask("status")
    if not reply:
        return None
    try:
        data = json.loads(reply)
        return data if isinstance(data, dict) else None
    except ValueError:
        return None
