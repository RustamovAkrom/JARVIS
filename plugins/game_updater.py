"""
Steam and Epic control for JARVIS — installing, updating, and reporting.

WHY THIS WAS REWRITTEN

    The previous version drove Steam by guessing. It looked for a separate
    top-level window titled "install", matched button text against a list of
    English and Turkish words, and picked the login profile by screenshotting
    the window and clicking the first colourful blob of pixels. Every one of
    those assumptions is now measurably wrong, and each was costing seconds:

    THE INSTALL DIALOG IS NOT A WINDOW. Measured: triggering steam://install
    and watching for ten seconds produced NO new top-level window. Modern Steam
    draws the install dialog as a modal INSIDE the main window. The old search
    could therefore never find it, and paid `40 x 0.5s` — twenty seconds — to
    discover that, before falling through to clicking at hardcoded percentages
    of the window's width and height.

    THE WINDOW DOES NOT BELONG TO steam.exe. Measured: the login window and
    the main window are both class `SDL_app`, owned by `steamwebhelper.exe`.
    So `steam.exe` being in the task list proves only that Steam is loading —
    NOT that anybody is logged in. That is the whole "it just sits there on
    the profile screen" complaint: the old code saw its process, declared
    success and moved on to a client that was still asking who was playing.

    THE TREE IS THERE, IT IS JUST ASLEEP. Steam's UI is Chromium, and Chromium
    builds its accessibility tree ONLY once something asks for it. Measured on
    the login window: the first FindAll returned 0 elements in 0.061s; a second
    query moments later returned 35. A single query that gives up on the first
    empty answer will conclude — wrongly, and every time — that Steam exposes
    nothing and that pixels are the only way in. So the first query here is a
    WAKE-UP, and an empty answer is retried rather than believed.

    Once awake, everything needed is simply readable, and fast:

        full tree scan of the Steam window       0.030 - 0.077 s
        Invoke() on a profile / a button         0.013 - 0.015 s

    Invoke is not a click. The mouse does not move, the window is not raised,
    and the user's foreground app keeps focus — which matters because these
    calls happen while somebody is talking to JARVIS.

WHAT THAT BUYS

    THE RIGHT PROFILE, NOT THE FIRST ONE. The login screen exposes each
    account as a Hyperlink next to a Text reading "Account name: <login>", and
    `config/loginusers.vdf` on disk says which account has AutoLogin set. The
    two are matched, so the account that Steam itself would have chosen is the
    one pressed. Nothing is recognised by colour, and nothing is recognised by
    English.

    THE RIGHT DRIVE, FOR A REASON. The old code scanned every drive letter and
    took whichever had the most room free — including drives with no Steam
    library on them, which the dialog does not even offer. The real libraries
    are listed in `steamapps/libraryfolders.vdf`; measured on this machine that
    is `C:\\Program Files (x86)\\Steam` with 3.7 GB free and `F:\\SteamLibrary`
    with 54.9 GB. The dialog states the download size itself ("30.59 GB"), so
    the choice is made against the number that actually decides it, and the
    result is VERIFIED by re-reading the dialog rather than assumed.

    AN HONEST ANSWER. StateFlags is a BIT FIELD and the old code compared it
    for equality — `state == 4`, `state == 1026`. A game sitting at 68 or 1030
    was invisible to all of it. Bits are tested as bits now, and because
    `BytesDownloaded` and `BytesToDownload` are in the manifest too, "is it
    downloading" can be answered with a percentage and a remaining size instead
    of a name.

WHAT IT WILL NOT DO

    It will not press Install for a game whose download does not fit anywhere.
    It says which drive, how much is needed and how much is free, and stops.
    Filling a system drive is not a thing to be brisk about.
"""
from __future__ import annotations

import concurrent.futures
import ctypes
import json
import os
import platform as _platform_mod
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

from config import is_windows, is_mac, is_linux

_CNW: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW}
    if _platform_mod.system() == "Windows" else {}
)

BASE_DIR = Path(__file__).resolve().parent.parent


# ─────────────────────────────────────────────────────────────────────────────
# Settings (⚙ SETUP → PLUGIN SETTINGS)
#
# Every field is optional; left alone, the plugin decides for itself. Filling
# one in stops it deciding — which is the point, because a guess that is right
# most of the time is still wrong on somebody's machine every day.
# ─────────────────────────────────────────────────────────────────────────────
_NS = "game_updater"

PLUGIN_SETTINGS = {
    "namespace": _NS,
    "title": "🎮  GAME UPDATER (Steam / Epic)",
    "fields": [
        {"key": "install_drive", "label": "Install games to drive", "type": "text",
         "placeholder": "F   —   empty: whichever Steam library has room"},
        {"key": "steam_account", "label": "Steam account to sign in as", "type": "text",
         "placeholder": "empty: the account Steam remembers"},
        {"key": "schedule_hour", "label": "Daily update — hour (0-23)", "type": "text",
         "default": "3", "placeholder": "3"},
        {"key": "schedule_minute", "label": "Daily update — minute (0-59)", "type": "text",
         "default": "0", "placeholder": "0"},
        {"key": "claim_free",
         "label": "Add free games to the library when asked to install them",
         "type": "toggle", "default": True},
        {"key": "accept_eula",
         "label": "Accept game licence agreements automatically",
         "type": "toggle", "default": True},
        {"key": "shutdown_when_done",
         "label": "Shut the PC down when a download finishes", "type": "toggle",
         "default": False},
    ],
}


def _setting(key: str, default=""):
    """One stored value, or `default`. Never raises: a plugin that cannot read
    its own config still has to run."""
    try:
        from memory.config_manager import get_plugin_setting
        val = get_plugin_setting(_NS, key, default)
    except Exception:
        return default
    return default if val in (None, "") else val


def _setting_int(key: str, default: int) -> int:
    try:
        return int(str(_setting(key, default)).strip())
    except Exception:
        return default


def _setting_bool(key: str, default: bool = False) -> bool:
    """A stored toggle. The UI writes a real boolean, so the string branch only
    catches a hand-edited config file — and it accepts only machine words, never
    a word from one particular human language."""
    val = _setting(key, default)
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _log(msg: str) -> None:
    """A status line that cannot take the process down.

    main.py reconfigures stdout to UTF-8 at startup, but this file also runs on
    its own: the daily update is a scheduled task that launches
    `python game_updater.py --scheduled`, with no main.py anywhere. On a legacy
    console — cp1254 in Turkey, cp1251 in Russia, cp932 in Japan — printing one
    of the emoji below then raises UnicodeEncodeError, and the scheduled update
    dies on its first log line rather than on anything to do with games."""
    line = f"[GameUpdater] {msg}"
    try:
        print(line)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(line.encode(enc, "replace").decode(enc, "replace"))
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# VDF
#
# Steam's own key/value format, used by libraryfolders.vdf, loginusers.vdf and
# every appmanifest. A tokeniser rather than a regex because the values are
# Windows paths: `"path" "C:\\Program Files (x86)\\Steam"` carries escaped
# backslashes, and a quote inside a game's name would end a regex match in the
# middle of a value and hand back nonsense with no error.
# ─────────────────────────────────────────────────────────────────────────────
_VDF_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])', re.S)
_VDF_ESCAPE = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}


def _vdf_unescape(raw: str) -> str:
    out, i = [], 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\\" and i + 1 < len(raw):
            out.append(_VDF_ESCAPE.get(raw[i + 1], raw[i + 1]))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _vdf_parse(text: str) -> dict:
    """Nested dict for a VDF document. Never raises — a truncated file (Steam
    was writing it as we read) returns whatever parsed cleanly up to that
    point, which is always better than an exception in a voice command."""
    stack: list[dict] = [{}]
    key: str | None = None
    try:
        for m in _VDF_TOKEN.finditer(text):
            string, brace = m.group(1), m.group(2)
            if brace == "{":
                node: dict = {}
                if key is not None:
                    stack[-1][key] = node
                    key = None
                stack.append(node)
            elif brace == "}":
                if len(stack) > 1:
                    stack.pop()
            else:
                value = _vdf_unescape(string or "")
                if key is None:
                    key = value
                else:
                    stack[-1][key] = value
                    key = None
    except Exception as e:
        _log(f"⚠️ VDF parse stopped early: {e}")
    return stack[0]


def _vdf_file(path: Path) -> dict:
    try:
        return _vdf_parse(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Where Steam is
# ─────────────────────────────────────────────────────────────────────────────
def _find_steam_path() -> Path | None:
    if is_windows():
        return _find_steam_windows()
    if is_mac():
        return _find_steam_mac()
    return _find_steam_linux()


def _find_steam_windows() -> Path | None:
    try:
        import winreg
        for hive, key_path, value in [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
            (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Valve\Steam", "SteamPath"),
        ]:
            try:
                key = winreg.OpenKey(hive, key_path)
                val, _ = winreg.QueryValueEx(key, value)
                winreg.CloseKey(key)
                p = Path(str(val))
                if (p / "steam.exe").exists():
                    return p
            except Exception:
                continue
    except ImportError:
        pass
    for p in [
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Steam",
        Path(os.environ.get("ProgramFiles", "")) / "Steam",
        Path("C:/Steam"), Path("D:/Steam"), Path("E:/Steam"), Path("F:/Steam"),
    ]:
        try:
            if (p / "steam.exe").exists():
                return p
        except Exception:
            continue
    return None


def _find_steam_mac() -> Path | None:
    for p in [
        Path.home() / "Library" / "Application Support" / "Steam",
        Path("/Applications/Steam.app/Contents/MacOS"),
    ]:
        if p.exists():
            return p
    return None


def _find_steam_linux() -> Path | None:
    for p in [
        Path.home() / ".steam" / "steam",
        Path.home() / ".steam" / "root",
        Path.home() / ".local" / "share" / "Steam",
        Path("/usr/share/steam"), Path("/opt/steam"),
    ]:
        if p.exists():
            return p
    return None


def _steam_exe(steam_path: Path) -> Path:
    if is_windows():
        return steam_path / "steam.exe"
    if is_mac():
        return Path("/Applications/Steam.app/Contents/MacOS/steam_osx")
    return steam_path / "steam.sh"


def _steam_url(exe: Path, url: str) -> None:
    """Hand a steam:// URL to the client. Popen and not run(): these return
    only when Steam feels like it, and nothing here has anything to wait for."""
    try:
        if is_mac():
            subprocess.Popen(["open", url])
        elif is_linux():
            subprocess.Popen(["xdg-open", url])
        else:
            subprocess.Popen([str(exe), url], **_CNW)
    except Exception as e:
        _log(f"⚠️ steam URL failed ({url}): {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Steam libraries — the real ones
#
# The install dialog offers LIBRARIES, not drives. A drive with 900 GB free and
# no library on it is not on that list, so choosing it is choosing something
# the user will then have to fix by hand. libraryfolders.vdf is the list, and
# shutil.disk_usage is the free space; neither is a guess.
# ─────────────────────────────────────────────────────────────────────────────
def _libraries(steam_path: Path) -> list[dict]:
    """[{path, steamapps, drive, free, total}], most room first."""
    roots: list[Path] = []
    data = _vdf_file(steam_path / "steamapps" / "libraryfolders.vdf")
    folders = data.get("libraryfolders") or data.get("LibraryFolders") or {}
    if isinstance(folders, dict):
        for key, entry in folders.items():
            if isinstance(entry, dict):
                raw = entry.get("path")
            elif key.isdigit():
                raw = entry            # very old clients: "1" "D:\\SteamLibrary"
            else:
                continue
            if raw:
                roots.append(Path(str(raw)))
    if steam_path not in roots:
        roots.insert(0, steam_path)

    out, seen = [], set()
    for root in roots:
        try:
            steamapps = root / "steamapps"
            if not steamapps.exists():
                continue
            resolved = str(steamapps.resolve()).lower()
            if resolved in seen:
                continue
            seen.add(resolved)
            usage = shutil.disk_usage(str(root))
            out.append({
                "path": root,
                "steamapps": steamapps,
                "drive": str(root)[:1].upper() if is_windows() else "",
                "free": int(usage.free),
                "total": int(usage.total),
            })
        except Exception:
            continue
    out.sort(key=lambda lib: lib["free"], reverse=True)
    return out


def _pick_library(libs: list[dict], need_bytes: int = 0) -> tuple[dict | None, str]:
    """(library, why). The user's setting wins whenever it can hold the game;
    otherwise the emptiest library that can. `why` is for the log and for the
    sentence spoken back, because "it went to F:" is not an answer on its own.
    """
    if not libs:
        return None, "no Steam library folder was found"

    margin = 2 * 1024 ** 3        # Steam unpacks while it downloads; do not fill the disk
    fits = [lib for lib in libs if not need_bytes or lib["free"] >= need_bytes + margin]

    wanted = str(_setting("install_drive", "")).strip(" :/\\").upper()[:1]
    if wanted:
        chosen = next((lib for lib in libs if lib["drive"] == wanted), None)
        if chosen is None:
            _log(f"⚠️ settings ask for drive {wanted}:, which holds no Steam library")
        elif need_bytes and chosen["free"] < need_bytes + margin:
            _log(f"⚠️ drive {wanted}: is the configured one but cannot hold this download")
        else:
            return chosen, f"drive {wanted}: is the one you configured"

    if fits:
        best = fits[0]
        if len(fits) == 1 and len(libs) > 1:
            why = (f"{_drive_label(best)} is the only library with room for "
                   f"{_fmt_bytes(need_bytes)}")
        else:
            why = f"{_drive_label(best)} has the most room, {_fmt_bytes(best['free'])}"
        return best, why

    return None, ("no Steam library has room for it — "
                  + ", ".join(f"{_drive_label(l)} has {_fmt_bytes(l['free'])}"
                              for l in libs))


def _drive_label(lib: dict) -> str:
    return f"{lib['drive']}:" if lib.get("drive") else str(lib["path"])


def _fmt_bytes(n: int | float) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


_SIZE_RE = re.compile(r"(\d[\d.,]*)\s*(B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)\b", re.I)
_UNIT = {"b": 1, "kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3, "tb": 1024 ** 4,
         "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3, "tib": 1024 ** 4}


def _parse_size(text: str) -> int:
    """'30.59 GB' / '30,59 GB' / '1.234,5 MB' -> bytes; 0 when there is no size.

    Steam writes this number in the user's own locale, so neither separator can
    be assumed to mean one thing: an English client says 30.59 GB and a Turkish
    one 30,59 GB for the same download. The last separator is the decimal point
    and any earlier one groups thousands, which reads both correctly.

    One case stays genuinely ambiguous — a lone separator followed by exactly
    three digits, where '1.234 GB' is 1.234 GB in one locale and 1234 GB in
    another. That is resolved to the LARGER reading on purpose. Over-estimating
    a download only makes the drive choice more cautious; under-estimating it
    sends a game to a disk that cannot hold it."""
    m = _SIZE_RE.search(text or "")
    if not m:
        return 0
    raw = m.group(1).rstrip(".,")
    seps = [i for i, ch in enumerate(raw) if ch in ".,"]
    if not seps:
        number = raw
    else:
        last = seps[-1]
        tail = raw[last + 1:]
        if len(tail) == 3:                       # grouping, or the ambiguous case
            number = raw.replace(".", "").replace(",", "")
        else:
            number = (raw[:last].replace(".", "").replace(",", "") + "." + tail)
    try:
        return int(float(number) * _UNIT[m.group(2).lower()])
    except Exception:
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# Installed games — StateFlags is a bit field
#
# 1 uninstalled · 2 update required · 4 fully installed · 32 files missing
# 64 running · 128 corrupt · 256 update running · 512 paused · 1024 started
# 2048 uninstalling · 131072 validating · 262144 adding files
# 524288 preallocating · 1048576 downloading · 2097152 staging · 4194304 committing
#
# Comparing the whole number to 4 or to 1026 — as this file used to — misses
# every combination nobody thought to write down.
# ─────────────────────────────────────────────────────────────────────────────
SF_UNINSTALLED = 1
SF_UPDATE_REQUIRED = 2
SF_INSTALLED = 4
SF_FILES_MISSING = 32
SF_RUNNING = 64
SF_PAUSED = 512
SF_UNINSTALLING = 2048
SF_BUSY = (256 | 1024 | 131072 | 262144 | 524288 | 1048576 | 2097152 | 4194304)


def _games(steam_path: Path) -> list[dict]:
    out = []
    for lib in _libraries(steam_path):
        for acf in lib["steamapps"].glob("appmanifest_*.acf"):
            try:
                app = _vdf_file(acf).get("AppState") or {}
                app_id, name = app.get("appid"), app.get("name")
                if not app_id or not name:
                    continue
                done = int(app.get("BytesDownloaded") or 0)
                total = int(app.get("BytesToDownload") or 0)
                out.append({
                    "id": str(app_id),
                    "name": str(name).strip(),
                    "state": int(app.get("StateFlags") or 0),
                    "size": int(app.get("SizeOnDisk") or 0),
                    "done": done,
                    "total": total,
                    "lib": lib,
                    "acf": acf,
                })
            except Exception:
                continue
    return out


def _is_busy(game: dict) -> bool:
    return bool(game["state"] & SF_BUSY)


def _is_paused(game: dict) -> bool:
    return bool(game["state"] & SF_PAUSED) and not _is_busy(game)


def _needs_update(game: dict) -> bool:
    return bool(game["state"] & SF_UPDATE_REQUIRED) and not _is_busy(game)


def _is_ready(game: dict) -> bool:
    return (bool(game["state"] & SF_INSTALLED)
            and not game["state"] & (SF_UPDATE_REQUIRED | SF_FILES_MISSING | SF_UNINSTALLING)
            and not _is_busy(game))


def _progress(game: dict) -> str:
    """'42% — 5.1 GB of 12.0 GB' when the manifest knows, '' when it does not."""
    total, done = game.get("total") or 0, game.get("done") or 0
    if total <= 0:
        return ""
    pct = max(0, min(100, int(done * 100 / total)))
    return f"{pct}% — {_fmt_bytes(done)} of {_fmt_bytes(total)}"


def _in_library(steam_path: Path, app_id: str) -> bool:
    """Whether this app looks like one the account already has.

    Steam keeps the artwork for everything in a person's library under
    `appcache/librarycache`, so the presence of a folder there is evidence of
    ownership. Measured on this machine: 112 entries, holding every installed
    game and the free-to-play ones in the library, and NOT holding Portal 2 or
    either of the free titles whose install was refused for "No licenses".

    EVIDENCE, NOT PROOF. It is a cache: a game bought on another machine and
    never shown here may be missing from it. So this is only ever used to
    explain a refusal Steam has ALREADY given — never to refuse one itself,
    because a wrong "you do not own that" would be a worse failure than the one
    it prevents."""
    if not app_id:
        return False
    cache = steam_path / "appcache" / "librarycache"
    try:
        if (cache / str(app_id)).is_dir():
            return True
        # Older clients keep flat files named `<appid>_header.jpg` instead.
        return any(cache.glob(f"{app_id}_*"))
    except Exception:
        return False


def _find_games(games: list[dict], name: str) -> list[dict]:
    """Exact match first, then prefix, then substring — so asking for 'Rust'
    on a library holding 'Rust' and 'Rustler' gets Rust rather than both."""
    want = (name or "").strip().lower()
    if not want:
        return []
    exact = [g for g in games if g["name"].lower() == want]
    if exact:
        return exact
    starts = [g for g in games if g["name"].lower().startswith(want)]
    return starts or [g for g in games if want in g["name"].lower()]


# ─────────────────────────────────────────────────────────────────────────────
# Accounts — who Steam would sign in as
# ─────────────────────────────────────────────────────────────────────────────
def _accounts(steam_path: Path) -> list[dict]:
    """[{login, persona, autologin, remembered, timestamp}], most recent first."""
    data = _vdf_file(steam_path / "config" / "loginusers.vdf").get("users") or {}
    out = []
    for _steam_id, entry in (data.items() if isinstance(data, dict) else []):
        if not isinstance(entry, dict):
            continue
        out.append({
            "login": str(entry.get("AccountName") or ""),
            "persona": str(entry.get("PersonaName") or ""),
            "autologin": str(entry.get("AutoLogin") or "0") == "1",
            "remembered": str(entry.get("RememberPassword") or "0") == "1",
            "timestamp": int(entry.get("Timestamp") or 0),
        })
    out.sort(key=lambda a: a["timestamp"], reverse=True)
    return out


def _preferred_account(steam_path: Path) -> dict | None:
    """The account to press on the profile screen, in the order that a person
    would expect: the one they configured, the one Steam auto-logs in as, then
    the one used most recently."""
    accounts = [a for a in _accounts(steam_path) if a["login"]]
    if not accounts:
        return None

    wanted = str(_setting("steam_account", "")).strip().lower()
    if wanted:
        for a in accounts:
            if wanted in (a["login"].lower(), a["persona"].lower()):
                return a
        _log(f"⚠️ configured account '{wanted}' is not remembered by Steam")

    return next((a for a in accounts if a["autologin"]),
                next((a for a in accounts if a["remembered"]), accounts[0]))


# ─────────────────────────────────────────────────────────────────────────────
# UI Automation — one apartment, on its own thread, always on a budget
#
# Lifted from the WhatsApp layer for the same reason it exists there: COM wants
# one apartment, the calls must never block a voice command, and a scan that
# has gone slow has to be abandoned rather than waited for.
# ─────────────────────────────────────────────────────────────────────────────
_uia_lock = threading.Lock()
_uia_queue: "queue.Queue | None" = None
_uia_thread: threading.Thread | None = None

_user32 = ctypes.windll.user32 if _platform_mod.system() == "Windows" else None

_CT_PROP = 30003
_P_RECT, _P_NAME, _P_CTYPE, _P_OFFSCREEN = 30001, 30005, 30003, 30022
_TREE_SUBTREE = 7
_PAT_INVOKE = 10000

CT_BUTTON, CT_HYPERLINK, CT_TEXT, CT_LISTITEM = 50000, 50005, 50020, 50007
CT_CUSTOM, CT_GROUP, CT_WINDOW = 50025, 50026, 50032


def _uia_loop(jobs: "queue.Queue") -> None:
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass
    while True:
        job = jobs.get()
        if job is None:
            break
        fn, fut = job
        if not fut.set_running_or_notify_cancel():
            continue
        try:
            fut.set_result(fn())
        except BaseException as exc:      # noqa: BLE001 — carried to the caller
            fut.set_exception(exc)


def _submit(fn) -> concurrent.futures.Future:
    global _uia_queue, _uia_thread
    with _uia_lock:
        if _uia_thread is None or not _uia_thread.is_alive():
            _uia_queue = queue.Queue()
            _uia_thread = threading.Thread(target=_uia_loop, args=(_uia_queue,),
                                           daemon=True, name="steam-uia")
            _uia_thread.start()
        fut: concurrent.futures.Future = concurrent.futures.Future()
        _uia_queue.put((fn, fut))
        return fut


def _budget(fn, seconds: float, default=None):
    """Run `fn` on the UI Automation thread, giving up after `seconds`."""
    try:
        return _submit(fn).result(timeout=seconds)
    except concurrent.futures.TimeoutError:
        _log("⚠️ UI Automation exceeded its time budget")
        return default
    except Exception as e:
        _log(f"⚠️ UI Automation failed: {e}")
        return default


# ── the windows Steam actually has ───────────────────────────────────────────
def _steam_pids() -> set[int]:
    """Both processes. steam.exe is the client; steamwebhelper.exe owns every
    window you can see, and confusing the two is what made the old code think
    a client sitting on the login screen was ready to take orders."""
    pids: set[int] = set()
    if not is_windows():
        return pids
    for image in ("steam.exe", "steamwebhelper.exe"):
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", **_CNW).stdout
            for line in out.splitlines():
                if line.startswith('"'):
                    try:
                        pids.add(int(line.split('","')[1]))
                    except Exception:
                        continue
        except Exception:
            continue
    return pids


def _active_user() -> int:
    """The SteamID of whoever is signed in, or 0 for nobody.

    Steam maintains this itself under HKCU\\Software\\Valve\\Steam\\ActiveProcess,
    and it is the only unambiguous answer to "is the client actually usable".
    A process in the task list is not: measured, `steam.exe` appears within a
    second of launch and this value stays 0 for as long as the sign-in screen
    is up. Reading it costs microseconds, so a Steam that is already signed in
    is recognised without going near UI Automation.
    """
    if not is_windows():
        return 0
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Valve\Steam\ActiveProcess")
        try:
            value, _ = winreg.QueryValueEx(key, "ActiveUser")
        finally:
            winreg.CloseKey(key)
        return int(value or 0)
    except Exception:
        return 0


def _client_running() -> bool:
    if is_windows():
        try:
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq steam.exe"],
                                 capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", **_CNW).stdout
            return "steam.exe" in out.lower()
        except Exception:
            return False
    proc = "steam_osx" if is_mac() else "steam"
    try:
        return bool(subprocess.run(["pgrep", "-x", proc], capture_output=True,
                                   text=True, errors="replace").stdout.strip())
    except Exception:
        return False


def _steam_windows() -> list[dict]:
    """Every visible Steam window, largest first. Class `SDL_app` is what the
    client draws into; the process check keeps another program's SDL window out.
    """
    if not is_windows() or _user32 is None:
        return []
    pids = _steam_pids()
    found: list[dict] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        try:
            if not _user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in pids:
                return True
            cls = ctypes.create_unicode_buffer(256)
            _user32.GetClassNameW(hwnd, cls, 256)
            n = _user32.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(n + 1)
            _user32.GetWindowTextW(hwnd, title, n + 1)
            rect = wintypes.RECT()
            _user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w, h = rect.right - rect.left, rect.bottom - rect.top
            # Measured: a Steam that has been closed to the notification area
            # keeps its window alive at 199x34, and grows the SAME handle back
            # to full size when a dialog needs showing. A 200x150 floor —
            # which is what this used to have — threw that window away, so a
            # signed-in Steam sitting in the tray looked like a Steam with no
            # windows at all, and every install reported that no dialog had
            # appeared. The floor now only skips the zero-size ghosts, and
            # whether a window is worth reading is decided by its tree.
            if w < 60 or h < 20:
                return True
            found.append({"hwnd": int(hwnd), "title": title.value,
                          "cls": cls.value, "w": w, "h": h})
        except Exception:
            pass
        return True

    try:
        _user32.EnumWindows(_cb, 0)
    except Exception:
        return []
    found.sort(key=lambda w: w["w"] * w["h"], reverse=True)
    return found


def _client_windows() -> list[dict]:
    """Only the windows the client itself draws.

    Measured: the sign-in screen and the library are both class `SDL_app`,
    while the launcher's update splash is `BootstrapUpdateUIClass`. The splash
    has an automation tree of its own, so anything that merely asks "did I find
    a Steam window with elements in it" will happily mistake the thing that
    says "updating Steam" for a client that is ready to take orders."""
    windows = _steam_windows()
    client = [w for w in windows if w["cls"] == "SDL_app"]
    return client or [w for w in windows if w["cls"] != "BootstrapUpdateUIClass"]


# ── reading one window ───────────────────────────────────────────────────────
def _scan_window(hwnd: int) -> list[dict]:
    """[{el, type, name, rect}] for every on-screen element. One walk, the
    properties fetched in the same round trip.

    Empty is a legitimate answer here and NOT an error: Chromium has not built
    its tree yet. `_scan` is what turns that into a retry."""
    def work():
        if not _user32.IsWindow(hwnd):
            return []
        from pywinauto.uia_defines import IUIA
        from pywinauto.uia_element_info import UIAElementInfo
        iuia = IUIA().iuia
        root = UIAElementInfo(hwnd).element
        # Asked about a dead handle, UIA quietly answers with the DESKTOP, and
        # then every window on the machine is inside the search — including
        # buttons in other programs that this code is about to press.
        if int(getattr(root, "CurrentNativeWindowHandle", 0) or 0) != int(hwnd):
            return []

        cached = True
        try:
            req = iuia.CreateCacheRequest()
            for pid in (_P_RECT, _P_NAME, _P_CTYPE, _P_OFFSCREEN):
                req.AddProperty(pid)
            found = root.FindAllBuildCache(_TREE_SUBTREE, iuia.CreateTrueCondition(), req)
        except Exception:
            found, cached = root.FindAll(_TREE_SUBTREE, iuia.CreateTrueCondition()), False

        out = []
        for i in range(found.Length):
            try:
                el = found.GetElement(i)
                if (el.CachedIsOffscreen if cached else el.CurrentIsOffscreen):
                    continue
                rect = el.CachedBoundingRectangle if cached else el.CurrentBoundingRectangle
                if rect.right <= rect.left or rect.bottom <= rect.top:
                    continue
                out.append({
                    "el": el,
                    "type": int(el.CachedControlType if cached else el.CurrentControlType),
                    "name": (el.CachedName if cached else el.CurrentName) or "",
                    "rect": (rect.left, rect.top, rect.right, rect.bottom),
                })
            except Exception:
                continue
        return out

    return _budget(work, 4.0, []) or []


def _scan(hwnd: int, tries: int = 4, gap: float = 0.35) -> list[dict]:
    """A scan that survives Chromium's sleeping accessibility tree.

    MEASURED, on the Steam login window: first FindAll 0.061s -> 0 elements,
    a second one moments later -> 35. The first query is what wakes the tree.
    Believing that first empty answer is the difference between reading Steam
    and concluding it cannot be read."""
    for attempt in range(max(1, tries)):
        items = _scan_window(hwnd)
        if len(items) > 4:                 # a bare window/pane shell is not a tree
            return items
        if attempt < tries - 1:
            time.sleep(gap)
    return []


def _invoke(item: dict) -> bool:
    """Press an element without touching the mouse, the focus or the z-order.

    Measured at 13-15 ms. A real click would need the window raised, which
    steals the foreground from whatever the user is doing while they talk."""
    def work():
        from comtypes.gen.UIAutomationClient import IUIAutomationInvokePattern
        pattern = item["el"].GetCurrentPattern(_PAT_INVOKE)
        if not pattern:
            return False
        pattern.QueryInterface(IUIAutomationInvokePattern).Invoke()
        return True

    ok = bool(_budget(work, 3.0, False))
    if not ok:
        _log(f"⚠️ could not invoke {item.get('name', '')!r}")
    return ok


def _named(items: list[dict], *types: int) -> list[dict]:
    return [i for i in items if i["name"].strip() and (not types or i["type"] in types)]


# ─────────────────────────────────────────────────────────────────────────────
# Which screen is this?
#
# Recognised by STRUCTURE and by facts already on disk — never by English
# words and never by pixel colour. Steam ships in 29 languages; a check that
# reads "Install" works for one of them.
# ─────────────────────────────────────────────────────────────────────────────
_DRIVE_RE = re.compile(r"\(([A-Za-z]):\)")


def _login_candidates(items: list[dict], accounts: list[dict]) -> list[dict]:
    """The clickable profiles on the sign-in screen, or []. Ground truth comes
    from loginusers.vdf: a Hyperlink whose name is one of the account or
    persona names on this machine is a profile, in any language."""
    if not accounts:
        return []
    known = {a["persona"].lower() for a in accounts if a["persona"]}
    known |= {a["login"].lower() for a in accounts if a["login"]}
    return [i for i in _named(items, CT_HYPERLINK, CT_LISTITEM, CT_BUTTON)
            if i["name"].strip().lower() in known]


def _account_for(item: dict, items: list[dict], accounts: list[dict]) -> dict | None:
    """Which stored account this on-screen profile belongs to.

    The tile shows the persona name; the login name is in a Text underneath it
    ("Account name: eobard_thaawne"). Two accounts can share a persona, so the
    login line is checked first and the persona is only the fallback."""
    label = item["name"].strip().lower()
    left, top, right, bottom = item["rect"]
    nearby = " ".join(
        i["name"] for i in items
        if i["type"] == CT_TEXT
        and i["rect"][0] >= left - 80 and i["rect"][2] <= right + 80
        and top - 40 <= i["rect"][1] <= bottom + 90
    ).lower()
    for account in accounts:
        if account["login"] and account["login"].lower() in nearby:
            return account
    return next((a for a in accounts if a["persona"].lower() == label), None)


def _install_rows(items: list[dict]) -> list[dict]:
    """Every element naming a drive — '(F:)' — with its letter and free space.

    The volume label is the operating system's and the size words are Steam's,
    so they can be in two different languages at once; measured here as
    'Yeni Birim (F:) 54.87 GB FREE' on an English client. The bracketed drive
    letter is the one part of that string that no locale changes."""
    rows = []
    for item in items:
        m = _DRIVE_RE.search(item["name"])
        if not m:
            continue
        rows.append({
            **item,
            "drive": m.group(1).upper(),
            "free_text": _parse_size(item["name"]),
        })
    return rows


def _download_size(items: list[dict], rows: list[dict]) -> int:
    """How big this download is, told apart from the free-space figures.

    Both are written the same way — '30.59 GB' and '3.7 GB FREE' — and the word
    that separates them is localized, so the layout is what separates them here.
    Measured on the dialog: the download size sits beside the game's title at
    the top (y 508-527) and every free-space figure sits inside the row of the
    drive it belongs to (y 705-730 and y 767-793). So anything ABOVE the first
    drive row is a candidate and anything level with a drive is not.

    Reading the largest number on the dialog instead would work until somebody
    has a mostly-empty 2 TB drive, and then the tool would refuse to install a
    30 GB game for want of two terabytes."""
    if not rows:
        return 0
    list_top = min(r["rect"][1] for r in rows)
    sizes = [_parse_size(i["name"]) for i in items if i["rect"][3] < list_top]
    sizes = [s for s in sizes if s > 0]
    return max(sizes) if sizes else 0


def _error_dialog(items: list[dict], app_id: str) -> dict | None:
    """Steam's own failure modal about THIS request, or None.

    Steam echoes the AppID back inside the message — measured, 'An error
    occurred while installing AppID 2368940: "No licenses"' — and that number
    is the one part of the sentence no locale changes. Matching on it means
    this is recognised in every language Steam ships in, and cannot be confused
    with an error about something else the client happens to be doing.

    Nothing here tries to work out WHAT went wrong. The message is handed back
    whole and the model explains it in the user's own language, which is both
    more accurate than a table of English phrases and impossible to get wrong
    in a locale nobody tested."""
    if not app_id:
        return None
    named = [item for item in items if item["name"].strip()]
    message = next((item["name"].strip() for item in named
                    if str(app_id) in item["name"]), "")
    if not message:
        return None
    return {"message": message, "buttons": _named(items, CT_BUTTON)}


def _pending_modal(items: list[dict]) -> dict | None:
    """{message, buttons} for a modal that is waiting on somebody, or None.

    Used after Install has been pressed, to find out what Steam put up next.
    Measured on Soccer Manager 2026: pressing Install produced a licence
    agreement — topmost text 'Please read this agreement in its entirety. You
    must agree to the terms of the EULA to play Soccer Manager 2026', with
    Accept and Cancel underneath.

    Nothing is classified and nothing is pressed. Steam's own topmost line
    explains the dialog better than any rule here could, and it is already in
    the user's language, so it is quoted and the buttons are named. Whatever it
    turns out to be, it is the user's to answer: this function exists so that
    the assistant can say what is on screen instead of reporting that the
    download mysteriously did not start.

    The body of an agreement is itself exposed as a Button — a focusable scroll
    region — so anything with a name too long to be a label is left out of the
    button list."""
    # The install dialog is not a new thing waiting on anybody — it is the one
    # just dealt with, still on screen for the moment it takes to close. Without
    # this, pressing Install and looking immediately reports the dialog that was
    # pressed as the reason the press did not work.
    if _install_dialog(items):
        return None

    # A modal announces itself structurally: a NAMED group stretched across the
    # whole window, holding the dialog. Measured, that is how all three of them
    # look — 'Install', 'Failure' and 'EULA …' — and Steam's ordinary pages
    # have no such group at all.
    #
    # Without this test any Steam page with buttons and text counts as a dialog
    # waiting on somebody. Measured after an install finished and the store came
    # back: the store page was reported as a modal blocking the download, its
    # "buttons" being Browse, Recommendations, Categories and Search.
    window = next((i for i in items if i["type"] == CT_WINDOW), None)
    if window is None:
        return None
    wl, wt, wr, wb = window["rect"]
    window_area = max(1, (wr - wl) * (wb - wt))
    covering = any(
        i["name"].strip()
        and (i["rect"][2] - i["rect"][0]) * (i["rect"][3] - i["rect"][1])
        >= 0.85 * window_area
        for i in items if i["type"] in (CT_GROUP, CT_CUSTOM)
    )
    if not covering:
        return None

    buttons = [item["name"].strip() for item in _named(items, CT_BUTTON)
               if len(item["name"].strip()) <= 60]
    texts = sorted((item for item in items
                    if item["type"] == CT_TEXT and item["name"].strip()),
                   key=lambda item: item["rect"][1])
    if not buttons or not texts:
        return None
    return {"message": texts[0]["name"].strip(), "buttons": buttons}


def _install_dialog(items: list[dict]) -> dict | None:
    """{size, rows, buttons} when the install modal is on screen, else None.

    A drive-naming element plus at least two buttons is the shape of it: an
    'install to' list and a confirm/cancel pair. Nothing here is a word."""
    rows = _install_rows(items)
    buttons = _named(items, CT_BUTTON)
    if not rows or len(buttons) < 2:
        return None
    return {
        "size": _download_size(items, rows),
        "rows": rows,
        "buttons": buttons,
        "items": items,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Which button confirms?
#
# The same problem the WhatsApp layer has with call buttons, solved the same
# way: ask the model once for this set of labels and keep the answer on disk,
# so every later install in that language is free. The heuristic below answers
# without a network at all in the common case, and the model is only consulted
# when it cannot.
# ─────────────────────────────────────────────────────────────────────────────
_ROLE_CACHE = BASE_DIR / "memory" / "steam_controls.json"
_role_lock = threading.Lock()
_role_memo: dict[str, dict] = {}

_ROLE_PROMPT = (
    "These are the accessible names of the buttons on a Steam dialog, in "
    "whatever language that Steam is set to. Say what each one is for.\n\n"
    "{listing}\n\n"
    "Return ONLY minified JSON, no markdown fences and no prose, with exactly "
    "these keys:\n"
    '  "confirm"   : the button that STARTS the installation, else null\n'
    '  "cancel"    : the button that CLOSES the dialog without doing it, else null\n'
    '  "agreement" : the button that ACCEPTS a licence, a EULA or terms of use '
    '— the one that lets the install carry on — else null\n\n'
    "Each value must be one button name copied EXACTLY from the list above, on "
    "its own, with no translation and no numbering — or null. A button naming a "
    "disk or a drive letter is a place to install to, so it is null for every "
    "key.\n"
    "'agreement' is only ever the button that AGREES. A button that merely "
    "opens the terms to be read, or that declines them, is not it.\n"
    "A button that SPENDS MONEY — buy, purchase, add to cart, checkout, "
    "subscribe, upgrade — is none of these three. It is null for every key, "
    "without exception, however much it looks like the way forward.\n"
    "Use null whenever you are not sure. A wrong answer here starts a download "
    "the user did not ask for, or agrees to something on their behalf."
)


def _role_key(labels: tuple[str, ...]) -> str:
    import hashlib
    return hashlib.sha1("\n".join(labels).encode("utf-8")).hexdigest()[:16]


def _role_cache_load() -> dict:
    try:
        return json.loads(_ROLE_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _role_cache_save(store: dict) -> None:
    try:
        _ROLE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        if len(store) > 40:
            for stale in list(store)[:-40]:
                store.pop(stale, None)
        _ROLE_CACHE.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _ask_model_roles(labels: tuple[str, ...]) -> dict:
    try:
        from core import gemini
        raw = gemini.as_json(_ROLE_PROMPT.format(listing="\n".join(labels)),
                             tier=gemini.FAST)
    except Exception as e:
        _log(f"⚠️ could not ask the model which button installs: {e}")
        return {}
    if not isinstance(raw, dict):
        return {}
    lookup = {name.strip().lower(): i for i, name in enumerate(labels)}
    out: dict[str, int] = {}
    for role in ("confirm", "cancel", "agreement"):
        value = raw.get(role)
        if isinstance(value, str):
            idx = lookup.get(value.strip().lower())
            if idx is not None and idx not in out.values():
                out[role] = idx
    return out


def _roles_for(buttons: list[dict]) -> tuple[list[dict], dict]:
    """(action row, {role: index into that row}).

    The action row is the bottom line of buttons; a dialog's other buttons are
    the drive entries and the scroll region holding an agreement's text, and
    neither is an action. The answer for a given set of labels is asked of the
    model once and kept on disk, so a language costs one request ever."""
    row_candidates = [b for b in buttons
                      if not _DRIVE_RE.search(b["name"])
                      and len(b["name"].strip()) <= 60]
    if not row_candidates:
        return [], {}

    bottom = max(b["rect"][3] for b in row_candidates)
    row = sorted((b for b in row_candidates if b["rect"][3] >= bottom - 40),
                 key=lambda b: b["rect"][0])
    labels = tuple(b["name"].strip() for b in row)

    key = _role_key(labels)
    with _role_lock:
        if key in _role_memo:
            return row, dict(_role_memo[key])
        store = _role_cache_load()
        if key in store:
            _role_memo[key] = store[key]
            return row, dict(store[key])

    roles = _ask_model_roles(labels)
    if roles:
        with _role_lock:
            _role_memo[key] = roles
            store = _role_cache_load()
            store[key] = roles
            _role_cache_save(store)
    return row, roles


_ACCEPT_PROMPT = (
    "A dialog has appeared in Steam during a game installation. Its first line "
    "reads:\n\n"
    "{message}\n\n"
    "Its buttons are:\n{listing}\n\n"
    "Answer TWO things about it, as minified JSON only — no fences, no prose:\n"
    '{{"is_agreement": true|false, "agree": "<button name>"|null}}\n\n'
    '"is_agreement" is true ONLY if this dialog asks the person to accept a '
    "licence, an end-user licence agreement, terms of use, terms of service or "
    "a privacy policy. That is the only kind of dialog it is true for.\n"
    "It is FALSE for everything else, including: a dialog about restarting or "
    "updating Steam, an error or failure, a warning about disk space, a "
    "download or bandwidth notice, a login or security prompt, a purchase, or "
    "anything asking a question about how to proceed. Those are not "
    "agreements even when they have an obvious button to press.\n\n"
    '"agree" is the button that accepts the terms, copied exactly from the list '
    "above and untranslated — and null whenever is_agreement is false. Never "
    "name a button that declines, cancels or closes, and never one that buys, "
    "subscribes or spends money.\n"
    "If you are not certain the dialog is a licence agreement, is_agreement is "
    "false. Being wrong here means agreeing to something on a person's behalf "
    "that they were never shown."
)


def _agreement_button(items: list[dict]) -> dict | None:
    """The button that agrees to the licence now on screen, or None.

    ASKED WITH THE DIALOG'S OWN WORDS, and deliberately not through the cached
    label lookup that the install dialog uses. Out of context, 'Accept' and
    'Install' are both just the affirmative button on a two-button dialog, and
    a cache keyed on labels alone would answer for a dialog it never saw. One
    request per installation is a fair price for knowing what is being agreed
    to; installations are not frequent.

    There is no positional fallback and there must never be one: which button
    signs a legal document is not a thing to be approximately right about."""
    buttons = [b for b in _named(items, CT_BUTTON)
               if len(b["name"].strip()) <= 60 and not _DRIVE_RE.search(b["name"])]
    texts = sorted((i for i in items if i["type"] == CT_TEXT and i["name"].strip()),
                   key=lambda i: i["rect"][1])
    if len(buttons) < 2 or not texts:
        return None

    try:
        from core import gemini
        answer = gemini.as_json(
            _ACCEPT_PROMPT.format(
                message=texts[0]["name"].strip()[:400],
                listing="\n".join(b["name"].strip() for b in buttons)),
            tier=gemini.FAST)
    except Exception as exc:
        _log(f"⚠️ could not ask which button agrees: {exc}")
        return None

    if not isinstance(answer, dict) or answer.get("is_agreement") is not True:
        # Measured, before this second field existed: asked only which button
        # agrees, the model answered "Restart now" for a dialog reading "Steam
        # needs to restart to finish updating". Naming a button is a question
        # that always has a plausible answer, so it has to be preceded by one
        # that can be answered no.
        return None
    wanted = answer.get("agree")
    if not isinstance(wanted, str):
        return None
    return next((b for b in buttons
                 if b["name"].strip().lower() == wanted.strip().lower()), None)


def _confirm_button(dialog: dict) -> tuple[dict | None, dict | None]:
    """(confirm, cancel), or (None, …) when it is not known which is which.

    THERE IS NO GUESS HERE, AND THERE USED TO BE. When the model could not name
    the confirm button this fell back to "the leftmost button on the bottom
    row", on the reasoning that reading order puts the primary action first.
    That reasoning is fine for an OK/Cancel pair and wrong for everything else:
    a dialog carrying a licence agreement puts something else in that position
    entirely, and the fallback pressed it. Accepting somebody's terms of use
    for them — silently, as a side effect of "install this game" — is worse
    than any failure this function could report.

    So an unidentified dialog is now left alone and handed to the user. A tool
    that stops is recoverable; a tool that agrees to things is not."""
    row, roles = _roles_for(dialog["buttons"])
    if not row:
        return None, None

    def pick(role: str) -> dict | None:
        index = roles.get(role)
        return row[index] if isinstance(index, int) and index < len(row) else None

    confirm, cancel, agreement = pick("confirm"), pick("cancel"), pick("agreement")

    if agreement is not None and not _setting_bool("accept_eula", True):
        _log(f"🚫 this dialog wants an agreement accepted ({agreement['name']!r}) "
             f"and automatic acceptance is switched off — leaving it for you")
        return None, cancel

    if confirm is None:
        _log(f"🚫 cannot tell which button installs, out of "
             f"{[b['name'] for b in row]} — leaving the dialog for you")
    return confirm, cancel


# ─────────────────────────────────────────────────────────────────────────────
# Getting Steam to a usable state
# ─────────────────────────────────────────────────────────────────────────────
def _steam_ready(steam_path: Path, timeout: float = 45.0,
                 speak=None) -> tuple[bool, str]:
    """(ready, message). Ready means signed in and showing the library — not
    merely that a process exists."""
    if not is_windows():
        # No UI automation off Windows; starting the client is all there is.
        if _client_running():
            return True, ""
        exe = _steam_exe(steam_path)
        try:
            if is_mac():
                subprocess.Popen(["open", "-a", "Steam"])
            else:
                subprocess.Popen([str(exe)])
        except Exception as e:
            return False, f"Steam could not be started: {e}"
        for _ in range(int(timeout)):
            time.sleep(1)
            if _client_running():
                return True, ""
        return False, "Steam did not start."

    # Already signed in: nothing to look at, nothing to wait for.
    if _active_user():
        return True, ""

    if not _client_running():
        exe = _steam_exe(steam_path)
        if not exe.exists():
            return False, "Steam is not installed where the registry says it is."
        _log("🚀 starting Steam")
        try:
            # -silent keeps the library window out of the user's face. The
            # sign-in screen still appears when it is needed, which is exactly
            # the moment this function exists for.
            subprocess.Popen([str(exe), "-silent"], **_CNW)
        except Exception as e:
            return False, f"Steam could not be started: {e}"

    accounts = _accounts(steam_path)
    wanted = _preferred_account(steam_path)
    deadline = time.time() + timeout
    announced = False
    pressed: set[str] = set()

    while time.time() < deadline:
        # Signed in is signed in — Steam says so itself, and it says so before
        # any window has finished drawing.
        if _active_user():
            return True, ""

        for win in _client_windows():
            items = _scan(win["hwnd"], tries=1)
            if not items:
                continue
            profiles = _login_candidates(items, accounts)
            if not profiles:
                continue

            if not announced and speak:
                speak("Tell the user Steam is asking which account to use and "
                      "that you are picking theirs; one short sentence.")
                announced = True

            target = None
            if wanted:
                target = next(
                    (p for p in profiles
                     if (_account_for(p, items, accounts) or {}).get("login")
                     == wanted["login"]), None)
            if target is None:
                target = profiles[0]
                _log("ℹ️ no remembered account matched — taking the first profile")

            who = _account_for(target, items, accounts) or {}
            login = who.get("login") or target["name"]
            # Pressing the same tile over and over cannot help, and Steam takes
            # a few seconds to act on the first press. One attempt per account.
            if login in pressed:
                continue
            pressed.add(login)
            _log(f"👤 sign-in screen — choosing {login}")
            if not _invoke(target):
                return False, ("Steam is asking who is playing and would not let "
                               "me choose. Pick an account and ask me again.")
        time.sleep(0.4)

    if _active_user():
        return True, ""
    if pressed:
        return False, ("I chose your account on Steam's sign-in screen but it "
                       "has not finished signing in. It may want your password.")
    return False, "Steam did not finish starting."


# ─────────────────────────────────────────────────────────────────────────────
# Installing
# ─────────────────────────────────────────────────────────────────────────────
_KNOWN_APPIDS: dict[str, tuple[str, str]] = {
    "pubg":                ("578080",  "PUBG: Battlegrounds"),
    "pubg battlegrounds":  ("578080",  "PUBG: Battlegrounds"),
    "pubg: battlegrounds": ("578080",  "PUBG: Battlegrounds"),
    "battlegrounds":       ("578080",  "PUBG: Battlegrounds"),
    "gta5":                ("271590",  "Grand Theft Auto V"),
    "gta v":               ("271590",  "Grand Theft Auto V"),
    "grand theft auto v":  ("271590",  "Grand Theft Auto V"),
    "cs2":                 ("730",     "Counter-Strike 2"),
    "csgo":                ("730",     "Counter-Strike 2"),
    "counter-strike 2":    ("730",     "Counter-Strike 2"),
    "counter strike 2":    ("730",     "Counter-Strike 2"),
    "dota2":               ("570",     "Dota 2"),
    "dota 2":              ("570",     "Dota 2"),
    "rust":                ("252490",  "Rust"),
    "valheim":             ("892970",  "Valheim"),
    "cyberpunk":           ("1091500", "Cyberpunk 2077"),
    "cyberpunk 2077":      ("1091500", "Cyberpunk 2077"),
    "elden ring":          ("1245620", "ELDEN RING"),
    "minecraft":           ("1672970", "Minecraft Launcher"),
    "apex legends":        ("1172470", "Apex Legends"),
    "apex":                ("1172470", "Apex Legends"),
    "fortnite":            ("1517990", "Fortnite"),
    "goose goose duck":    ("1568590", "Goose Goose Duck"),
    "among us":            ("945360",  "Among Us"),
    "fall guys":           ("1097150", "Fall Guys"),
    "rocket league":       ("252950",  "Rocket League"),
    "warframe":            ("230410",  "Warframe"),
    "destiny 2":           ("1085660", "Destiny 2"),
    "team fortress 2":     ("440",     "Team Fortress 2"),
    "tf2":                 ("440",     "Team Fortress 2"),
    "left 4 dead 2":       ("550",     "Left 4 Dead 2"),
    "l4d2":                ("550",     "Left 4 Dead 2"),
    "paladins":            ("444090",  "Paladins"),
    "smite":               ("386360",  "SMITE"),
    "war thunder":         ("236390",  "War Thunder"),
    "world of warships":   ("552990",  "World of Warships"),
    "path of exile":       ("238960",  "Path of Exile"),
    "poe":                 ("238960",  "Path of Exile"),
    "lost ark":            ("1599340", "Lost Ark"),
    "new world":           ("1063730", "New World: Aeternum"),
}


_store_memo: dict[str, dict] = {}


def _store_details(app_id: str) -> dict:
    """What the Steam store says about this app: {name, type, is_free, price}.

    STRUCTURED DATA, NOT A PAGE. The obvious way to install something the
    account does not own is to drive the store page and press whatever offers
    it — and that is the one place in this whole plugin where a wrong press
    SPENDS MONEY. Measured, the page is also a poor thing to read: a free game
    can answer with an age-verification gate instead of itself, and a paid one
    shows 'Add to Cart' next to a price, in the store's own language.

    The API answers the only two questions that matter — is it free, and what
    does it cost — as a boolean and a preformatted string, in any locale, with
    nothing to click. Empty dict when the store will not say."""
    key = str(app_id)
    if key in _store_memo:
        return _store_memo[key]
    try:
        import urllib.parse
        import urllib.request
        url = ("https://store.steampowered.com/api/appdetails"
               f"?appids={urllib.parse.quote(key)}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            payload = json.loads(resp.read().decode()).get(key, {})
        if not payload.get("success"):
            return {}
        data = payload.get("data") or {}
        out = {
            "name": str(data.get("name") or ""),
            "type": str(data.get("type") or ""),
            "is_free": bool(data.get("is_free")),
            "price": str((data.get("price_overview") or {}).get("final_formatted") or ""),
        }
    except Exception as exc:
        _log(f"⚠️ the store would not describe {app_id}: {exc}")
        return {}
    _store_memo[key] = out
    return out


def _claimable(store: dict) -> bool:
    """Whether a free licence can be claimed for this app without the store.

    Free is not enough — it has to be a GAME. Measured, `steam://freelicense`
    claimed Path of Exile and War Thunder within about four seconds each, and
    did nothing at all for a free demo, which was still refused with "No
    licenses" thirty-five seconds later. Steam hands demos out through the
    store page's own button and by some other route than this one, so a demo
    is reported honestly instead of being retried in hope."""
    return bool(store.get("is_free")) and store.get("type") == "game"


def _acquire_free(steam_path: Path, app_id: str) -> None:
    """Claim a free title so that it can then be installed.

    `steam://freelicense/<appid>` adds the licence without opening the store,
    which keeps this away from the cart entirely — the one place in this plugin
    where a wrong press would spend money is a place it never goes.

    Only ever called for an app the store has SAID is a free game. There is no
    branch in this file that can buy anything, and there is not meant to be
    one. The wait is measured rather than hopeful: four seconds was enough for
    both titles tested, and the caller retries the install afterwards, so a
    licence that takes longer shows up as an honest refusal rather than as a
    silent failure."""
    _log(f"🎟️ claiming the free licence for AppID {app_id}")
    _steam_url(_steam_exe(steam_path), f"steam://freelicense/{app_id}")
    time.sleep(5.0)


def _search_appid(steam_path: Path | None, game_name: str) -> tuple[str | None, str | None]:
    name = (game_name or "").lower().strip()
    if not name:
        return None, None

    if steam_path:
        for g in _find_games(_games(steam_path), name):
            return g["id"], g["name"]

    if name in _KNOWN_APPIDS:
        app_id, canonical = _KNOWN_APPIDS[name]
        return app_id, canonical
    for key, (app_id, canonical) in _KNOWN_APPIDS.items():
        if name in key or key in name:
            return app_id, canonical

    try:
        import urllib.request
        import urllib.parse
        url = ("https://store.steampowered.com/api/storesearch/"
               f"?term={urllib.parse.quote(game_name)}&l=english&cc=US")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            items = json.loads(resp.read().decode()).get("items", [])
        if items:
            best = items[0]
            _log(f"🌐 store search: {best['name']} ({best['id']})")
            return str(best["id"]), best["name"]
    except Exception as e:
        _log(f"⚠️ AppID lookup failed: {e}")
    return None, None


def _await_dialog(steam_path: Path, app_id: str,
                  timeout: float = 15.0) -> tuple[str, int, dict]:
    """('install' | 'error' | 'signin' | '', hwnd, dialog) once Steam answers.

    Every outcome is a modal INSIDE the main window, so this reads the trees of
    the windows already there rather than waiting for a new one to appear —
    which, measured, never happens.

    Waiting only for the install dialog is what made a refused install look
    like a broken tool: Steam had answered immediately, with a failure modal,
    and this sat through its whole timeout before reporting that nothing had
    appeared.

    'signin' is here for the same reason. Observed: Steam signed itself out
    between the readiness check and the install, and this then waited the full
    fifteen seconds to report that no dialog had appeared — when the truth was
    on screen the whole time, asking who was playing."""
    accounts = _accounts(steam_path)
    deadline = time.time() + timeout
    while time.time() < deadline:
        for win in _client_windows():
            items = _scan(win["hwnd"], tries=1)
            if not items:
                continue
            dialog = _install_dialog(items)
            if dialog:
                return "install", win["hwnd"], dialog
            failure = _error_dialog(items, app_id)
            if failure:
                return "error", win["hwnd"], failure
            if _login_candidates(items, accounts):
                return "signin", win["hwnd"], {}
        time.sleep(0.2)
    return "", 0, {}


def _dismiss(hwnd: int, dialog: dict) -> None:
    """Close a modal that has only one way out.

    The single-button rule is the whole of it. A dialog offering one button
    cannot be agreed to, bought from or consented to — the button dismisses it
    and that is all it can do. The moment there are two, one of them means
    something, and this leaves the choice to the person whose account it is."""
    buttons = dialog.get("buttons") or []
    if len(buttons) == 1:
        _invoke(buttons[0])


def _choose_library_in_dialog(hwnd: int, dialog: dict, target_drive: str) -> tuple[bool, str]:
    """Select `target_drive` in the install dialog and confirm that it took."""
    already = [r for r in dialog["rows"]
               if r["drive"] == target_drive and r["type"] != CT_BUTTON]
    clickable = [r for r in dialog["rows"]
                 if r["drive"] == target_drive and r["type"] == CT_BUTTON]

    if not clickable:
        if already:
            return True, "already selected"
        # The list is collapsed, and the control that opens it is an unnamed
        # button. "The first unnamed button" is not good enough to press: a
        # dialog's close cross is also an unnamed button, and so is anything
        # else Steam draws as an icon. The one wanted is the one sitting level
        # with the drive rows, so it is chosen by where it is rather than by
        # the order it happens to appear in the tree.
        rows = dialog["rows"]
        top = min(r["rect"][1] for r in rows)
        bottom = max(r["rect"][3] for r in rows)
        openers = sorted(
            (b for b in dialog["items"]
             if b["type"] == CT_BUTTON and not b["name"].strip()
             and top - 60 <= (b["rect"][1] + b["rect"][3]) / 2 <= bottom + 60),
            key=lambda b: abs((b["rect"][1] + b["rect"][3]) / 2 - (top + bottom) / 2))
        if openers:
            _invoke(openers[0])
            time.sleep(0.4)
            items = _scan(hwnd, tries=2)
            refreshed = _install_dialog(items)
            if refreshed:
                dialog.update(refreshed)
                clickable = [r for r in dialog["rows"]
                             if r["drive"] == target_drive and r["type"] == CT_BUTTON]
    if not clickable:
        offered = ", ".join(sorted({r["drive"] + ":" for r in dialog["rows"]}))
        return False, f"Steam only offered {offered or 'no drives'}"

    if not _invoke(clickable[0]):
        return False, "the drive entry would not take the click"

    time.sleep(0.45)
    # Whether the dialog now SHOWS the choice is not worth interrogating: Steam
    # keeps the open list on screen, so the same drive appears both as the
    # selection and as an entry, and different client versions draw it
    # differently. The claim is settled after the fact instead — the manifest
    # says which library the download actually landed in, and that is what gets
    # reported to the user.
    return True, "selected"


def _install_steam_game(steam_path: Path, game_name: str | None = None,
                        app_id: str | None = None, speak=None) -> str:
    ready, note = _steam_ready(steam_path, speak=speak)
    if not ready:
        return note or "Steam could not be started."

    games = _games(steam_path)
    if app_id:
        existing = next((g for g in games if g["id"] == str(app_id)), None)
    elif game_name:
        matches = _find_games(games, game_name)
        existing = matches[0] if matches else None
    else:
        return "Tell me which game to install."

    if existing:
        return _describe_state(existing, steam_path)

    if not app_id and game_name:
        app_id, canonical = _search_appid(None, game_name)
        if not app_id:
            return (f"I could not find '{game_name}' on Steam. "
                    f"Give me the AppID and I will install it directly.")
        game_name = canonical or game_name
    label = game_name or f"AppID {app_id}"

    libs = _libraries(steam_path)
    if not libs:
        return "Steam has no library folder I can read, so I cannot choose where to install."

    _log(f"📦 installing {label} (AppID {app_id})")

    if not is_windows():
        _steam_url(_steam_exe(steam_path), f"steam://install/{app_id}")
        return (f"Steam is opening the install dialog for {label}. "
                f"Choose a drive there and confirm.")

    claimed = False          # a free licence is claimed at most once
    signed_in_again = False
    claimed_note = ""
    while True:
        _steam_url(_steam_exe(steam_path), f"steam://install/{app_id}")
        kind, hwnd, dialog = _await_dialog(steam_path, str(app_id))

        if kind == "signin" and not signed_in_again:
            # Steam dropped back to the account picker between being asked
            # whether it was ready and being asked to install. Signing in is
            # exactly what _steam_ready does, so it is done once — a second
            # sign-in screen after a successful sign-in is Steam refusing
            # rather than Steam being slow.
            _log("👤 Steam signed itself out mid-request — signing back in")
            signed_in_again = True
            ready, note = _steam_ready(steam_path, speak=speak)
            if not ready:
                return note or "Steam signed out and would not sign back in."
            continue
        if kind == "signin":
            return ("Steam keeps returning to its sign-in screen, so I could "
                    "not start the install. It may want your password.")

        if kind == "error":
            _dismiss(hwnd, dialog)
            store = _store_details(str(app_id))

            # Not owned, but free to own. Claiming it is the whole of what the
            # store page's button would have done, without going near the page.
            if (_claimable(store) and not claimed
                    and _setting_bool("claim_free", True)):
                claimed = True
                _acquire_free(steam_path, str(app_id))
                claimed_note = (f" {label} was not in your library, so I added "
                                f"it — it is free.")
                continue

            # Steam's own words, passed straight through: it knows why it
            # refused and this does not. The price, when there is one, is the
            # useful half of the answer — and buying it is not on offer here.
            price = store.get("price")
            if price and not store.get("is_free"):
                aside = (f" It is not in your library and it is not free: the "
                         f"store lists it at {price}. I do not buy games, so "
                         f"that part is yours.")
            elif store.get("is_free"):
                kind_word = store.get("type") or "title"
                aside = (f" It is free, but it is a {kind_word} rather than a "
                         f"full game, and Steam only hands those out from its "
                         f"own store page — open it there and it will install.")
            elif not _in_library(steam_path, str(app_id)):
                aside = (f" It is also not in this account's library, so it "
                         f"would have to be added from the Steam store first.")
            else:
                aside = ""
            return (f"Steam refused to install {label} — it said: "
                    f"\"{dialog['message']}\".{aside} Nothing was started. Do "
                    f"not try this title again; ask the user which game they "
                    f"want, or offer one that is already installed.")

        if kind != "install":
            return (f"Steam did not show the install dialog for {label}, and did "
                    f"not report an error either. It may already be installing.")
        break

    need = dialog["size"]
    library, why = _pick_library(libs, need)
    if library is None:
        _cancel_dialog(hwnd, dialog)
        return (f"{label} needs {_fmt_bytes(need)} and there is nowhere to put it: {why}. "
                f"I have not started anything.")

    ok, how = _choose_library_in_dialog(hwnd, dialog, library["drive"])
    if not ok:
        _cancel_dialog(hwnd, dialog)
        return (f"I could not point the install at {_drive_label(library)} — {how}. "
                f"Nothing has been started.")

    confirm, _cancel = _confirm_button(_install_dialog(_scan(hwnd, tries=2)) or dialog)
    if confirm is None or not _invoke(confirm):
        # Deliberately left open rather than closed: the work of finding the
        # game and choosing the drive is done and on screen, so the user is one
        # press from finished. The activity log says which of the two reasons
        # it was.
        return (f"{label} is set up to install to {_drive_label(library)} and the "
                f"dialog is waiting on screen, but I will not press the last "
                f"button — either I could not tell which one starts it, or it is "
                f"asking for terms to be accepted, and that one is yours to press.")

    size_note = f" ({_fmt_bytes(need)})" if need else ""
    auto_accept = _setting_bool("accept_eula", True)
    accepted: list[str] = []
    started = blocking = None

    # Some publishers put the download behind a licence, and a few behind more
    # than one. The loop is bounded: a dialog that keeps coming back whatever
    # is pressed must not turn into a program that keeps pressing it.
    for attempt in range(3):
        started, blocking, modal_hwnd = _wait_for_download(
            steam_path, str(app_id), timeout=15.0 if attempt == 0 else 10.0)
        if started or not blocking or not auto_accept:
            break
        agreement = _agreement_button(_scan(modal_hwnd, tries=2))
        if agreement is None:
            break                       # something else is waiting — say so
        _log(f"📜 accepting a licence agreement for {label}: {agreement['name']!r}")
        if not _invoke(agreement):
            break
        accepted.append(agreement["name"].strip())
        blocking = None

    # Said out loud, every time, and never merely logged. Accepting somebody's
    # licence for them is a reasonable convenience and an unreasonable secret:
    # the one thing that must not happen is the user finding out later that
    # their assistant agreed to something without mentioning it.
    accepted_note = (f" I accepted the licence agreement for you to start it."
                     if accepted else "")

    if started:
        # Where it landed comes from the manifest, not from what was clicked.
        # Saying "downloading to F:" because F: was pressed is how a tool ends
        # up confidently reporting something that did not happen.
        return (f"{label} is downloading to {_drive_label(started['lib'])}"
                f"{size_note} — {why}.{claimed_note}{accepted_note}")

    if blocking:
        # Steam's own sentence, and the names of the buttons under it. Reached
        # when the dialog is not an agreement, or is one this could not identify
        # — and an unidentified dialog is left alone rather than guessed at.
        return (f"{label} is set to install to {_drive_label(library)}"
                f"{size_note} and I pressed Install, but Steam is now asking "
                f"something I cannot answer for you: \"{blocking['message']}\" "
                f"— the buttons are {', '.join(blocking['buttons'])}. It is "
                f"waiting on screen.{claimed_note}{accepted_note}")

    return (f"I confirmed the install of {label} on {_drive_label(library)}{size_note}, "
            f"but Steam has not reported a download yet. Worth a look."
            f"{claimed_note}{accepted_note}")


def _cancel_dialog(hwnd: int, dialog: dict) -> None:
    """Close a dialog we have decided not to go through with. Leaving a modal
    open in front of somebody is its own small failure."""
    _confirm, cancel = _confirm_button(dialog)
    if cancel is not None:
        _invoke(cancel)
    elif hwnd:
        fresh = _install_dialog(_scan(hwnd, tries=1))
        if fresh:
            _c, late = _confirm_button(fresh)
            if late is not None:
                _invoke(late)


def _wait_for_download(steam_path: Path, app_id: str, watch: bool = True,
                       timeout: float = 15.0
                       ) -> tuple[dict | None, dict | None, int]:
    """(manifest, blocking modal, that modal's window) — whichever comes first.

    This is the difference between reporting what was pressed and reporting
    what happened. The manifest appears within a second or two of a real
    install, and it also says which library the game went into, which is the
    only trustworthy answer to "where is it installing".

    Watching the screen at the same time is what stops the other outcome being
    described as a mystery. Measured: a game whose publisher requires a licence
    agreement puts that agreement up INSTEAD of starting, and waiting the full
    timeout to then say "Steam has not reported a download yet" describes the
    one thing on screen as an absence.

    THE WINDOW IS LOOKED UP EVERY TIME, never carried in. Steam replaces the
    window between the install dialog and the licence that follows it — the
    handle captured a second earlier is dead, and a dead handle makes every
    scan return nothing. Measured with a handle held across that change: the
    agreement sat on screen for the whole fifteen seconds and none of the
    thirty-odd scans saw it, while the same call against a freshly-found window
    answered in 0.1s."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for g in _games(steam_path):
            if g["id"] == str(app_id) and (_is_busy(g) or g["total"] > 0):
                return g, None, 0
        if watch:
            for win in _client_windows():
                items = _scan(win["hwnd"], tries=1)
                modal = _pending_modal(items) if items else None
                if modal:
                    return None, modal, win["hwnd"]
        time.sleep(0.4)
    return None, None, 0


def _describe_state(game: dict, steam_path: Path) -> str:
    name = game["name"]
    if _is_busy(game):
        progress = _progress(game)
        return f"{name} is already downloading — {progress}." if progress else \
               f"{name} is already downloading."
    if _is_paused(game):
        return f"{name}'s download is paused. Say resume and I will start it again."
    if _needs_update(game):
        _steam_url(_steam_exe(steam_path), f"steam://update/{game['id']}")
        pending = _fmt_bytes(game["total"] - game["done"]) if game["total"] else ""
        return (f"{name} had an update waiting{f' ({pending})' if pending else ''} "
                f"— I have started it.")
    if game["state"] & SF_FILES_MISSING:
        return f"{name} is installed but files are missing; it needs verifying in Steam."
    return f"{name} is installed and up to date on {_drive_label(game['lib'])}."


# ─────────────────────────────────────────────────────────────────────────────
# Updating
# ─────────────────────────────────────────────────────────────────────────────
def _update_steam(steam_path: Path, game_name: str | None = None,
                  speak=None) -> str:
    ready, note = _steam_ready(steam_path, speak=speak)
    if not ready:
        return note or "Steam could not be started."

    games = _games(steam_path)
    if not games:
        return "I cannot see any installed Steam games."

    if game_name:
        targets = _find_games(games, game_name)
        if not targets:
            listed = ", ".join(g["name"] for g in games[:5])
            return f"'{game_name}' is not installed. You have: {listed}."
        if len(targets) == 1:
            return _describe_state(targets[0], steam_path)
    else:
        targets = games

    exe = _steam_exe(steam_path)
    started, busy, ready_already, failed = [], [], [], []
    for game in targets:
        if _is_busy(game):
            busy.append(game)
        elif _needs_update(game) or _is_paused(game) or game["state"] & SF_FILES_MISSING:
            try:
                _steam_url(exe, f"steam://update/{game['id']}")
                started.append(game)
                time.sleep(0.25)
            except Exception as e:
                failed.append(f"{game['name']}: {e}")
        elif _is_ready(game):
            ready_already.append(game)

    parts = []
    if started:
        pending = sum(max(0, g["total"] - g["done"]) for g in started)
        names = ", ".join(g["name"] for g in started[:3])
        more = f" and {len(started) - 3} more" if len(started) > 3 else ""
        size = f", {_fmt_bytes(pending)} to fetch" if pending else ""
        parts.append(f"Updating {names}{more}{size}.")
    if busy:
        detail = "; ".join(
            f"{g['name']} {_progress(g)}".strip() for g in busy[:3])
        parts.append(f"Already running: {detail}.")
    if ready_already:
        parts.append(f"{ready_already[0]['name']} is already up to date."
                     if game_name else
                     f"{len(ready_already)} games are already up to date.")
    if failed:
        parts.append("Failed: " + "; ".join(failed))
    return " ".join(parts) or "Everything is already up to date."


def _download_status(steam_path: Path) -> str:
    games = _games(steam_path)
    active = [g for g in games if _is_busy(g)]
    paused = [g for g in games if _is_paused(g)]
    pending = [g for g in games if _needs_update(g)]

    lines = []
    for game in active[:3]:
        progress = _progress(game)
        left = max(0, game["total"] - game["done"])
        lines.append(f"{game['name']} {progress}"
                     + (f", {_fmt_bytes(left)} left" if left else ""))
    if paused:
        lines.append("Paused: " + ", ".join(g["name"] for g in paused[:3]))
    if pending:
        total = sum(max(0, g["total"] - g["done"]) for g in pending)
        names = ", ".join(g["name"] for g in pending[:4])
        more = f" and {len(pending) - 4} more" if len(pending) > 4 else ""
        lines.append(f"Waiting: {names}{more}"
                     + (f" — {_fmt_bytes(total)} in total" if total else ""))
    return ". ".join(lines) + "." if lines else "Nothing is downloading and nothing is waiting."


def _list_games(steam_path: Path | None) -> str:
    results = []
    if steam_path:
        games = _games(steam_path)
        if games:
            by_lib: dict[str, int] = {}
            for g in games:
                by_lib[_drive_label(g["lib"])] = by_lib.get(_drive_label(g["lib"]), 0) + 1
            where = ", ".join(f"{n} on {d}" for d, n in by_lib.items())
            names = ", ".join(g["name"] for g in games[:8])
            more = f" and {len(games) - 8} more" if len(games) > 8 else ""
            results.append(f"Steam has {len(games)} games ({where}): {names}{more}.")
        else:
            results.append("Steam: no games installed.")
    else:
        results.append("Steam: not installed.")
    return " ".join(results)


# ─────────────────────────────────────────────────────────────────────────────
# Shutdown watch
# ─────────────────────────────────────────────────────────────────────────────
def _system_shutdown() -> None:
    if is_windows():
        subprocess.run(["shutdown", "/s", "/t", "10"], **_CNW)
    elif is_mac():
        subprocess.run(["osascript", "-e", 'tell app "System Events" to shut down'])
    else:
        subprocess.run(["systemctl", "poweroff"])


def _watch_and_shutdown(steam_path: Path, speak=None,
                        check_interval: int = 30, timeout_hours: int = 12) -> None:
    deadline = time.time() + timeout_hours * 3600
    for _ in range(24):
        time.sleep(5)
        active = [g for g in _games(steam_path) if _is_busy(g)]
        if active:
            if speak:
                speak(f"Tell the user the download of "
                      f"{', '.join(g['name'] for g in active)} has begun and that "
                      f"you will shut the machine down when it finishes.")
            break
    else:
        return

    while time.time() < deadline:
        time.sleep(check_interval)
        if not any(_is_busy(g) for g in _games(steam_path)):
            if speak:
                speak("Tell the user the download is finished and that you are "
                      "shutting the computer down now.")
            time.sleep(5)
            _system_shutdown()
            return

    if speak:
        speak("Tell the user the download is taking too long and that you have "
              "cancelled the automatic shutdown.")


# ─────────────────────────────────────────────────────────────────────────────
# Epic
# ─────────────────────────────────────────────────────────────────────────────
def _find_epic_exe() -> Path | None:
    if is_windows():
        return _find_epic_exe_windows()
    if is_mac():
        return _find_epic_exe_mac()
    return _find_epic_exe_linux()


def _find_epic_exe_windows() -> Path | None:
    try:
        import winreg
        for hive, key_path in [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\EpicGames\EpicGamesLauncher"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\EpicGames\EpicGamesLauncher"),
            (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\EpicGames\EpicGamesLauncher"),
        ]:
            try:
                key = winreg.OpenKey(hive, key_path)
                val, _ = winreg.QueryValueEx(key, "AppDataPath")
                winreg.CloseKey(key)
                exe = Path(val) / "Binaries" / "Win64" / "EpicGamesLauncher.exe"
                if exe.exists():
                    return exe
            except Exception:
                continue
    except ImportError:
        pass
    for candidate in [
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Epic Games" / "Launcher"
        / "Portal" / "Binaries" / "Win64" / "EpicGamesLauncher.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Epic Games" / "Launcher"
        / "Portal" / "Binaries" / "Win64" / "EpicGamesLauncher.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "EpicGamesLauncher"
        / "Portal" / "Binaries" / "Win64" / "EpicGamesLauncher.exe",
    ]:
        if candidate.exists():
            return candidate
    return None


def _find_epic_exe_mac() -> Path | None:
    p = Path("/Applications/Epic Games Launcher.app/Contents/MacOS/EpicGamesLauncher")
    return p if p.exists() else None


def _find_epic_exe_linux() -> Path | None:
    for c in [Path.home() / ".local" / "bin" / "heroic", Path("/usr/bin/heroic")]:
        if c.exists():
            return c
    return None


def _epic_manifests_path() -> Path | None:
    if is_windows():
        p = (Path(os.environ.get("PROGRAMDATA", "C:/ProgramData"))
             / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests")
        return p if p.exists() else None
    if is_mac():
        p = (Path.home() / "Library" / "Application Support"
             / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests")
        return p if p.exists() else None
    return None


def _epic_games() -> list[dict]:
    manifests = _epic_manifests_path()
    if not manifests:
        return []
    games = []
    for item_file in manifests.glob("*.item"):
        try:
            data = json.loads(item_file.read_text(encoding="utf-8"))
            name = data.get("DisplayName") or data.get("AppName", "")
            if name:
                games.append({"id": data.get("AppName", ""), "name": str(name)})
        except Exception:
            continue
    return games


def _update_epic(epic_exe: Path | None, game_name: str | None = None) -> str:
    games = _epic_games()

    if game_name:
        matched = [g for g in games if game_name.lower() in g["name"].lower()]
        if not matched:
            return f"'{game_name}' is not in your Epic library."
        url = (f"com.epicgames.launcher://apps/{matched[0]['id']}"
               f"?action=launch&silent=true")
        try:
            if is_mac():
                subprocess.Popen(["open", url])
            elif is_linux():
                subprocess.Popen([str(epic_exe), url] if epic_exe else ["xdg-open", url])
            else:
                subprocess.Popen([str(epic_exe), url], **_CNW)
            return f"Epic is checking {matched[0]['name']}."
        except Exception as e:
            return f"Epic update failed: {e}"

    try:
        if is_mac():
            subprocess.Popen(["open", "-a", "Epic Games Launcher"])
        elif is_linux():
            if not epic_exe:
                return ("Epic has no Linux client. Heroic Launcher does the same job "
                        "if you want one.")
            subprocess.Popen([str(epic_exe)])
        else:
            subprocess.Popen([str(epic_exe)], **_CNW)
        return (f"Epic Games Launcher is open; it will check {len(games)} games."
                if games else "Epic Games Launcher is open.")
    except Exception as e:
        return f"Epic launch failed: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# Scheduling
# ─────────────────────────────────────────────────────────────────────────────
def _schedule_daily_update(hour: int = 3, minute: int = 0) -> str:
    if is_windows():
        return _schedule_windows(hour, minute)
    if is_mac():
        return _schedule_mac(hour, minute)
    return _schedule_linux(hour, minute)


def _schedule_windows(hour: int, minute: int) -> str:
    task_name = "JARVIS_GameUpdater"
    script_path = Path(__file__).resolve()
    subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"],
                   capture_output=True, **_CNW)
    result = None
    for extra in (["/RL", "HIGHEST", "/RU", "SYSTEM"], []):
        cmd = ["schtasks", "/Create", "/TN", task_name,
               "/TR", f'"{sys.executable}" "{script_path}" --scheduled',
               "/SC", "DAILY", "/ST", f"{hour:02d}:{minute:02d}", "/F", *extra]
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", **_CNW)
        if result.returncode == 0:
            return f"Daily game update scheduled for {hour:02d}:{minute:02d}."
    return f"Scheduling failed: {(result.stderr if result else '').strip()}"


def _schedule_mac(hour: int, minute: int) -> str:
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / "com.jarvis.gameupdater.plist"
    script_path = Path(__file__).resolve()
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
    <key>Label</key><string>com.jarvis.gameupdater</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>{script_path}</string>
        <string>--scheduled</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>{hour}</integer>
        <key>Minute</key><integer>{minute}</integer>
    </dict>
    <key>RunAtLoad</key><false/>
</dict></plist>"""
    try:
        plist_path.write_text(plist, encoding="utf-8")
        subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
        result = subprocess.run(["launchctl", "load", str(plist_path)],
                                capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        if result.returncode == 0:
            return f"Daily game update scheduled for {hour:02d}:{minute:02d}."
        return f"Scheduling failed: {result.stderr.strip()}"
    except Exception as e:
        return f"Scheduling failed: {e}"


def _schedule_linux(hour: int, minute: int) -> str:
    script_path = Path(__file__).resolve()
    marker = "# JARVIS_GameUpdater"
    entry = f"{minute} {hour} * * * {sys.executable} {script_path} --scheduled  {marker}"
    try:
        existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
        lines = [l for l in existing.stdout.splitlines()
                 if marker not in l and str(script_path) not in l]
        lines.append(entry)
        proc = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n",
                              text=True, encoding="utf-8", errors="replace",
                              capture_output=True)
        if proc.returncode == 0:
            return f"Daily game update scheduled for {hour:02d}:{minute:02d}."
        return f"Scheduling failed: {proc.stderr.strip()}"
    except Exception as e:
        return f"Scheduling failed: {e}"


def _cancel_scheduled_update() -> str:
    if is_windows():
        result = subprocess.run(["schtasks", "/Delete", "/TN", "JARVIS_GameUpdater", "/F"],
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", **_CNW)
        return ("The scheduled update is cancelled."
                if result.returncode == 0 else "There was no scheduled update.")
    if is_mac():
        plist_path = (Path.home() / "Library" / "LaunchAgents"
                      / "com.jarvis.gameupdater.plist")
        if plist_path.exists():
            subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
            plist_path.unlink()
            return "The scheduled update is cancelled."
        return "There was no scheduled update."
    try:
        existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
        lines = [l for l in existing.stdout.splitlines() if "JARVIS_GameUpdater" not in l]
        subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n",
                       text=True, encoding="utf-8", errors="replace")
        return "The scheduled update is cancelled."
    except Exception as e:
        return f"Cancel failed: {e}"


def _next_run_windows() -> str:
    """When the scheduled update next runs, or ''.

    Asked of the task scheduler as DATA. `schtasks /Query` prints a table whose
    row labels are translated — "Next Run Time" on an English Windows,
    "Sonraki Çalışma Zamanı" on a Turkish one — and this used to look for that
    label in a list of five languages it had been told about. Every other
    language fell through it, which is a tool that works in five countries.

    Get-ScheduledTaskInfo returns an object instead, and `NextRunTime` is
    called that in every locale."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-ScheduledTaskInfo -TaskName 'JARVIS_GameUpdater')"
             ".NextRunTime.ToString('yyyy-MM-dd HH:mm')"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=20, **_CNW)
        value = result.stdout.strip()
        return value if result.returncode == 0 and value else ""
    except Exception:
        return ""


def _schedule_status() -> str:
    if is_windows():
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", "JARVIS_GameUpdater", "/FO", "LIST"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", **_CNW)
        if result.returncode != 0:
            return "No game update is scheduled."
        when = _next_run_windows()
        return (f"A game update is scheduled; the next one runs at {when}."
                if when else "A game update is scheduled.")
    if is_mac():
        plist_path = (Path.home() / "Library" / "LaunchAgents"
                      / "com.jarvis.gameupdater.plist")
        return ("A game update is scheduled."
                if plist_path.exists() else "No game update is scheduled.")
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        for line in result.stdout.splitlines():
            if "JARVIS_GameUpdater" in line:
                return f"A game update is scheduled: {line.split('#')[0].strip()}"
        return "No game update is scheduled."
    except Exception:
        return "No game update is scheduled."


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
def game_updater(parameters: dict, player=None, speak=None) -> str:
    p = parameters or {}
    action = str(p.get("action") or "update").lower().strip()
    platform_name = str(p.get("platform") or "both").lower().strip()
    game_name = (p.get("game_name") or "").strip() or None
    app_id = (str(p.get("app_id") or "")).strip() or None
    hour = int(p.get("hour", _setting_int("schedule_hour", 3)))
    minute = int(p.get("minute", _setting_int("schedule_minute", 0)))
    raw_shutdown = p.get("shutdown_when_done")
    shutdown = (_setting_bool("shutdown_when_done", False) if raw_shutdown is None
                else str(raw_shutdown).lower() in ("true", "1", "yes"))

    if action == "schedule":
        return _schedule_daily_update(hour=hour, minute=minute)
    if action == "cancel_schedule":
        return _cancel_scheduled_update()
    if action == "schedule_status":
        return _schedule_status()

    steam_path = _find_steam_path()
    results: list[str] = []

    if action == "list":
        if platform_name in ("steam", "both"):
            results.append(_list_games(steam_path))
        if platform_name in ("epic", "both"):
            if is_linux():
                results.append("Epic: no Linux client.")
            else:
                games = _epic_games()
                results.append(
                    f"Epic has {len(games)} games: "
                    + ", ".join(g["name"] for g in games[:8])
                    + (f" and {len(games) - 8} more." if len(games) > 8 else ".")
                    if games else "Epic: no games found.")
        return " ".join(results) or "I found neither Steam nor Epic."

    if action == "download_status":
        if platform_name in ("steam", "both"):
            results.append(_download_status(steam_path) if steam_path
                           else "Steam is not installed.")
        if platform_name in ("epic", "both"):
            results.append("Epic does not publish its download progress to me.")
        return " ".join(results)

    if action in ("install", "update"):
        if platform_name in ("steam", "both"):
            if not steam_path:
                results.append("Steam is not installed.")
            else:
                if action == "install" and not game_name and not app_id:
                    results.append("Tell me which game to install.")
                elif game_name or app_id:
                    # "Install X" for a game already on disk is an update, and
                    # "update X" for one that is missing is an install. The
                    # user's word for it does not decide that; what is on the
                    # disk does.
                    installed = _find_games(_games(steam_path), game_name) if game_name else []
                    results.append(
                        _update_steam(steam_path, game_name=game_name, speak=speak)
                        if installed else
                        _install_steam_game(steam_path, game_name=game_name,
                                            app_id=app_id, speak=speak))
                else:
                    results.append(_update_steam(steam_path, speak=speak))

                if shutdown:
                    threading.Thread(target=_watch_and_shutdown,
                                     kwargs={"steam_path": steam_path, "speak": speak},
                                     daemon=True).start()
                    results.append("I will shut the machine down when it finishes.")

        if platform_name in ("epic", "both") and action == "update":
            if is_linux():
                results.append("Epic has no Linux client; Heroic Launcher does the same job.")
            else:
                epic_exe = _find_epic_exe()
                results.append(_update_epic(epic_exe, game_name=game_name)
                               if epic_exe else "Epic is not installed.")

        output = " ".join(results) or "There was nothing to do."
        if player:
            try:
                player.write_log(f"[GameUpdater] {output[:120]}")
            except Exception:
                pass
        return output

    return (f"I do not know the action '{action}'. I can update, install, list, "
            f"report download_status, or schedule.")


if __name__ == "__main__":
    if "--scheduled" in sys.argv:
        print(f"[GameUpdater] 🕐 scheduled run at {datetime.now().strftime('%H:%M')}")
        print(f"[GameUpdater] ✅ {game_updater({'action': 'update', 'platform': 'both'})}")


# ── Plugin entry point (auto-discovered by core/plugin_loader.py) ────────────

def _speaker(player):
    """The `speak` channel, plugin-side.

    Plugins are handed `player.request_say`, which main.py wires to the same
    client-content injection the built-in actions get — so this is the identical
    channel under another name. Returns None when no session is connected,
    which every `if speak:` above already copes with."""
    fn = getattr(player, "request_say", None) if player is not None else None
    if not callable(fn):
        return None

    def speak(text: str) -> None:
        try:
            fn(text)
        except Exception:
            pass

    return speak


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        return game_updater(parameters, player=player, speak=_speaker(player))
    except Exception as e:
        _log(f"❌ unhandled: {e}")
        return f"The game tool failed: {e}"


# ── Plugin declaration (auto-discovered by core/plugin_loader.py) ───────────
PLUGIN = {
    "name": "game_updater",
    "description": (
        "THE ONLY tool for ANY Steam or Epic Games request: installing and "
        "downloading games, updating them, listing what is installed, reporting "
        "download progress, and scheduling a daily update. It starts Steam, "
        "signs in past the account-picker screen and chooses the drive itself. "
        "ALWAYS call this directly for any Steam/Epic/game request — NEVER "
        "browser_control, open_app or web_search. "
        "It installs what the account owns, and it adds FREE titles to the "
        "library by itself when asked to install one. It CANNOT BUY: for a "
        "paid game the user does not own it reports the store price and stops. "
        "So NEVER invent a title. If the user asks you to pick a game, or names "
        "none, call action='list' and choose from what comes back, or ask them "
        "which one — a famous game you thought of yourself is probably one they "
        "would have to buy."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": ("update | install | list | download_status | "
                                "schedule | cancel_schedule | schedule_status "
                                "(default: update)"),
            },
            "platform": {
                "type": "STRING",
                "description": "steam | epic | both (default: both)",
            },
            "game_name": {
                "type": "STRING",
                "description": "Game name as the user said it; partial names are fine.",
            },
            "app_id": {
                "type": "STRING",
                "description": "Steam AppID, when the user gives one (optional).",
            },
            "hour": {
                "type": "INTEGER",
                "description": "Hour 0-23 for a scheduled daily update (default 3).",
            },
            "minute": {
                "type": "INTEGER",
                "description": "Minute 0-59 for a scheduled daily update (default 0).",
            },
            "shutdown_when_done": {
                "type": "BOOLEAN",
                "description": "Shut the computer down once the download finishes.",
            },
        },
        "required": [],
    },
}
