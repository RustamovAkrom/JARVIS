"""The programs this computer can start, as the computer itself lists them.

WHY THIS EXISTS
    Starting a program by a name used to end, when nothing else knew the
    name, in the desktop's search box: type the name, press Enter. When the
    search had no program by that name its first answer was a WEB search, and
    Enter opened the browser on "calculatorapp.exe location" and "system
    settings" (beta, 2026-10-13). The search box cannot say "I have nothing";
    a list can.

WHERE THE LIST COMES FROM - the system's own, in the user's own language
    Windows   the Start menu's apps (Get-StartApps: every desktop and Store
              app, with its display name in the UI language and an AppID that
              does not change with the language), plus shortcuts on the
              desktop - where people keep programs they installed by hand.
    macOS     the .app bundles in the Applications folders.
    Linux     the .desktop entries (Name, every translated Name[xx], the file
              id and the command), read the way the freedesktop spec says
              menus read them: $XDG_DATA_HOME then $XDG_DATA_DIRS, subfolders
              too (kde4/x.desktop is "kde4-x"), the first entry of an id wins
              (a user's own copy hides the system's), only Type=Application.
              Snap's and Flatpak's folders are added when they exist, for a
              start that did not get the session's XDG_DATA_DIRS (autostart).

HOW A NAME IS MATCHED - by shape, never by a list of words
    A program has several names: the one on screen ("Hesap Makinesi" on a
    Turkish Windows), the one a model writes ("Calculator", or the file it saw
    in a window list, "CalculatorApp.exe"), and its identity
    ("Microsoft.WindowsCalculator"). All of them are folded (core/appidentity
    .fold) and compared as: the same name; the same identity token; every
    word of one inside the other; or a long shared run of letters
    ("calculator" in "calculatorapp" and in "windowscalculator"). The best
    one wins; nothing that only shares a few letters does.
"""
from __future__ import annotations

import difflib
import glob
import json
import os
import platform
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from core.appidentity import fold

_OS = platform.system()

CACHE_S = 600                 # the list is read again after this long, or on a miss
MIN_SHARED = 5                # letters a shared run needs before it counts
SHARED_RATIO = 0.75           # ...and the share of the shorter name it must cover
GOOD_ENOUGH = 60              # a score below this is "no such program"


@dataclass
class App:
    name: str                 # as the system shows it, in its language
    launch: str               # what starts it (AppID, a path, a desktop id)
    kind: str                 # "appid" | "path" | "desktop" | "bundle"
    ids: list[str] = field(default_factory=list)   # language-free identity parts
    path: str = ""            # Linux: the .desktop file itself


_lock = threading.Lock()
_cache: tuple[float, list[App]] = (0.0, [])


# ---------------------------------------------------------------------------
# Reading the list
# ---------------------------------------------------------------------------
def _ids_of(text: str) -> list[str]:
    """Identity parts of an AppID, a path or a command: the words between
    punctuation, folded, without the noise (GUIDs, hashes, "exe", versions)."""
    parts = []
    for raw in re.split(r"[\W_]+", text or ""):      # "_" glues a name to a package hash
        part = fold(raw)
        if len(part) < 3 or part.isdigit() or part in ("exe", "app", "lnk", "desktop"):
            continue
        if re.fullmatch(r"[0-9a-f]{8,}", part) or (sum(c.isdigit() for c in part) >= 3
                                                    and len(part) >= 8):
            continue                      # a GUID piece or a package hash
        parts.append(part)
    return parts


def _windows_folder(csidl: int) -> str:
    try:
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0:
            return buf.value
    except Exception:
        pass
    return ""


def _read_windows() -> list[App]:
    apps: list[App] = []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
             "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"],
            capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        rows = json.loads(out.stdout.decode("utf-8", "replace") or "[]")
        for row in rows if isinstance(rows, list) else [rows]:
            name, appid = str(row.get("Name") or ""), str(row.get("AppID") or "")
            if not name or not appid:
                continue
            if re.match(r"https?://", appid, re.I):
                continue                  # a web link pinned in the Start menu, not a program
            apps.append(App(name=name, launch=appid, kind="appid", ids=_ids_of(appid)))
    except Exception as exc:
        print(f"[installed_apps] Start apps could not be read: {type(exc).__name__}: {exc}")
    # The desktops (the user's and everyone's): programs installed by hand often
    # have only a shortcut there.
    for csidl in (0x10, 0x19):            # DESKTOPDIRECTORY, COMMON_DESKTOPDIRECTORY
        folder = _windows_folder(csidl)
        for path in glob.glob(os.path.join(folder, "*.lnk")) if folder else []:
            name = os.path.splitext(os.path.basename(path))[0]
            apps.append(App(name=name, launch=path, kind="path", ids=_ids_of(name)))
    return apps


def _read_macos() -> list[App]:
    apps = []
    for folder in ("/Applications", "/System/Applications", "/System/Applications/Utilities",
                   os.path.expanduser("~/Applications")):
        for path in glob.glob(os.path.join(folder, "*.app")):
            name = os.path.splitext(os.path.basename(path))[0]
            apps.append(App(name=name, launch=path, kind="bundle", ids=_ids_of(name)))
    return apps


def _linux_app_dirs() -> list[str]:
    """Where .desktop entries live, in the spec's order of precedence."""
    home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    system = [d for d in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":") if d]
    # Snap's and Flatpak's own export folders (snapd, flatpak), for a start
    # that did not inherit the session's XDG_DATA_DIRS.
    extra = [os.path.join(home, "flatpak", "exports", "share"), "/var/lib/flatpak/exports/share",
             "/var/lib/snapd/desktop"]
    out = []
    for d in [home, *system, *extra]:
        folder = os.path.join(d, "applications")
        if folder not in out and os.path.isdir(folder):
            out.append(folder)
    return out


def _desktop_entry(path: str) -> dict:
    """The [Desktop Entry] keys of a .desktop file ({} when unreadable)."""
    out, names, section = {}, [], ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("["):
                    section = line
                    continue
                if section != "[Desktop Entry]" or "=" not in line or line.startswith("#"):
                    continue
                key, value = (part.strip() for part in line.split("=", 1))
                if key == "Name" or key.startswith("Name["):
                    names.append(value)
                out.setdefault(key, value)
    except OSError:
        return {}
    out["_names"] = names
    return out


def _read_linux() -> list[App]:
    apps, seen = [], set()
    for folder in _linux_app_dirs():
        for path in sorted(glob.glob(os.path.join(folder, "**", "*.desktop"), recursive=True)):
            # The desktop file id: the path below applications/, "/" as "-".
            desktop_id = os.path.relpath(path, folder)[:-len(".desktop")].replace(os.sep, "-")
            if desktop_id in seen:
                continue                      # an earlier folder's entry of this id wins
            seen.add(desktop_id)
            entry = _desktop_entry(path)
            hidden = any(entry.get(k, "").lower() == "true" for k in ("NoDisplay", "Hidden"))
            if hidden or entry.get("Type", "Application") != "Application" or not entry.get("_names"):
                continue
            ids = _ids_of(desktop_id)
            words = entry.get("Exec", "").split()
            # The command names the program — unless it is a runner that names
            # every one of its apps the same (flatpak run ..., snap's wrapper).
            if words and not (entry.get("X-Flatpak") or entry.get("X-SnapInstanceName")):
                ids += _ids_of(os.path.basename(words[0]))
            for name in dict.fromkeys(entry["_names"]):
                apps.append(App(name=name, launch=desktop_id, kind="desktop", ids=ids, path=path))
    return apps


_FIELD_CODES = re.compile(r"%[fFuUdDnNickvm]")


def _exec_argv(exec_line: str) -> list[str]:
    """The Exec line as a program and its arguments (freedesktop "Exec key"):
    field codes are for files and URLs, and none are passed; "%%" is "%"."""
    import shlex
    try:
        words = shlex.split(exec_line)
    except ValueError:
        return []
    out = []
    for w in words:
        w = _FIELD_CODES.sub("", w).replace("%%", "%")
        if w:
            out.append(w)
    return out


def _launch_desktop(app: App) -> bool:
    """A .desktop entry started as the desktop's own menu starts it: through
    the desktop's launcher when there is one (gio, gtk-launch, kioclient),
    which also honours Terminal=, D-Bus activation and the like; else from the
    entry's Exec line itself. No shell. True once something started it."""
    import shutil
    tries = []
    if app.path:
        tries.append(["gio", "launch", app.path])
    tries += [["gtk-launch", app.launch], ["gtk4-launch", app.launch]]
    if app.path:
        tries += [["kioclient", "exec", app.path], ["kioclient5", "exec", app.path]]
    for cmd in tries:
        if not shutil.which(cmd[0]):
            continue
        try:
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=15)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0:
            return True
    entry = _desktop_entry(app.path) if app.path else {}
    if entry.get("Terminal", "").lower() == "true":
        return False                          # needs a terminal, and no launcher to give it one
    argv = _exec_argv(entry.get("Exec", ""))
    if not argv or not shutil.which(argv[0]):
        return False
    subprocess.Popen(argv, cwd=entry.get("Path") or None, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return True


def catalog(refresh: bool = False) -> list[App]:
    """Every program this computer lists, cached. Empty when it cannot be read."""
    global _cache
    with _lock:
        stamp, apps = _cache
        if apps and not refresh and time.monotonic() - stamp < CACHE_S:
            return apps
        reader = {"Windows": _read_windows, "Darwin": _read_macos, "Linux": _read_linux}.get(_OS)
        apps = reader() if reader else []
        _cache = (time.monotonic(), apps)
        return apps


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
def _words(text: str) -> list[str]:
    return [w for w in (fold(p) for p in re.split(r"[\s._\-/\\]+", text or "")) if w]


def _shared(a: str, b: str, both: bool = False, of_first: bool = False) -> float:
    """The longest run of letters two folded names share, as a share of the
    shorter one (`both`: of the longer one too, whichever is less) - 0 when
    the run is too short to mean anything.

    Two display names must share most of BOTH ("calculator" is 10 of
    "calculatorapp"'s 13), or "Photoshop" would be "Photos". An identity is
    a run of words glued together ("windowscalculator"), so a name only has to
    be most of the shorter side there. `of_first`: the share of `a` alone -
    the name asked for must be mostly inside an identity part
    ("calculator" in "windowscalculator"), not the other way round
    ("photos" is most of nothing called "photoshop")."""
    if not a or not b:
        return 0.0
    m = difflib.SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    if m.size < MIN_SHARED:
        return 0.0
    if of_first:
        return m.size / len(a)
    return m.size / (max(len(a), len(b)) if both else min(len(a), len(b)))


def score(query: str, app: App) -> int:
    """How surely `query` names `app`: 100 the same name, 90 the same
    identity, 80 every word of one inside the other, 60-75 a long shared run
    of letters; 0 not this program."""
    stem = re.sub(r"\.(exe|app|lnk|desktop)$", "", (query or "").strip(), flags=re.I)
    q, name = fold(stem), fold(app.name)
    if not q or not name:
        return 0
    if q == name:
        return 100
    if q in app.ids:
        return 90
    qw, nw = _words(stem), _words(app.name)
    if qw and nw and (set(nw) <= set(qw) or set(qw) <= set(nw)):
        # Every word of the shorter is in the longer: "blender" / "Blender 3.6",
        # "system settings" / "Settings". The closer in length, the better.
        return 80 - min(10, abs(len(qw) - len(nw)) * 3)
    best = max([_shared(q, name, both=True)] + [_shared(q, i, of_first=True) for i in app.ids])
    return int(60 + 15 * (best - SHARED_RATIO) / (1 - SHARED_RATIO)) if best >= SHARED_RATIO else 0


def _parts(query: str) -> list[str]:
    """The query, and the parts of a "program - title" line copied whole."""
    parts = [p.strip() for p in re.split(r"\s[-–—|]\s", query or "") if p.strip()]
    return list(dict.fromkeys([(query or "").strip()] + parts))


def find(query: str) -> Optional[App]:
    """The program `query` names, or None. Read again once on a miss: it may
    have been installed since the list was read."""
    for refresh in (False, True):
        apps = catalog(refresh=refresh)
        if not apps:
            return None
        ranked = sorted(((score(part, app), -len(app.name), app)
                         for part in _parts(query) for app in apps),
                        key=lambda r: (r[0], r[1]), reverse=True)
        if ranked and ranked[0][0] >= GOOD_ENOUGH:
            return ranked[0][2]
        if time.monotonic() - _cache[0] < 5:
            break                          # just read: reading again changes nothing
    return None


def closest(query: str, n: int = 5) -> list[str]:
    """The names nearest to `query`, for telling a model what IS there."""
    apps = catalog()
    q = fold(query)
    ranked = sorted(apps, key=lambda a: max([_shared(q, fold(a.name))]
                                            + [_shared(q, i) for i in a.ids]
                                            + [difflib.SequenceMatcher(None, q, fold(a.name)).ratio()]),
                    reverse=True)
    return list(dict.fromkeys(a.name for a in ranked))[:n]


MODEL_TIER = "gemini-3.5-flash-lite"       # the small, fast rung (see core/tool_budget)
NAMES_SHOWN = 400


def _ask_model(prompt: str):
    """One small JSON question to the app's own model; None when it cannot be
    asked. Tests replace this."""
    try:
        from core import gemini
        return gemini.as_json(prompt, tier=MODEL_TIER, default=None)
    except Exception as exc:
        print(f"[installed_apps] the model could not be asked: {type(exc).__name__}")
        return None


def find_by_meaning(query: str) -> Optional[App]:
    """The installed program a name MEANS when no spelling of it matches - a
    name said in another language than the system's ("Not Defteri" on an
    English Windows is Notepad), a description ("the music app"). A small
    model reads the system's own list and picks one of ITS names; an answer
    that is not exactly on the list is not taken, so no program is invented.
    Asked only after a miss. Measured (2026-10-14): told to retry with the
    English name, the main model opened OneNote instead of Notepad."""
    apps = catalog()
    if not apps or not (query or "").strip():
        return None
    names = list(dict.fromkeys(a.name for a in apps))[:NAMES_SHOWN]
    prompt = ("A user asked to open a program on their computer. Which program on this "
              "computer's list do they mean? The name may be in any language, a "
              "translation, a nickname or a description.\n\n"
              f"ASKED FOR: {query.strip()[:120]}\n\nINSTALLED PROGRAMS:\n" + "\n".join(names)
              + '\n\nReply with JSON only: {"name": "<exactly one name from the list>"} - '
                'or {"name": ""} when none of them is that program. Never a similar '
                'but different program.')
    answer = _ask_model(prompt)
    picked = str((answer or {}).get("name") or "").strip() if isinstance(answer, dict) else ""
    if not picked:
        return None
    return next((a for a in apps if a.name == picked), None)


def launch(app: App) -> bool:
    """Start it the way the system starts it from its own menu. No shell."""
    try:
        if app.kind == "appid":
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app.launch}"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif app.kind == "path":
            os.startfile(app.launch)                  # noqa: S606 - a shortcut, no shell
        elif app.kind == "bundle":
            subprocess.Popen(["open", app.launch], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        elif app.kind == "desktop":
            if not _launch_desktop(app):
                print(f"[installed_apps] {app.name!r}: nothing here could start {app.launch}.desktop")
                return False
        else:
            return False
        return True
    except Exception as exc:
        print(f"[installed_apps] {app.name!r} could not be started: {exc}")
        return False
