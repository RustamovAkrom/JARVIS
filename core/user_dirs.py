"""
core/user_dirs.py — the user's own folders, where THIS system keeps them.

    folder("desktop")   the folder the user sees as their desktop
    find("downloads")   the same, or None when the system has none

Names: desktop, documents, downloads, pictures, videos, music.

Guessed as ~/Desktop, a spreadsheet, a script or a screenshot "saved to your
Desktop" landed in a folder the user never sees (bench 2026-10-25):
  - Windows: a desktop moved into OneDrive is C:\\Users\\x\\OneDrive\\Desktop
    (or a translated "Bureau"/"Escritorio" there); Windows' own known-folder
    API says where it is, wherever the user or their company moved it.
  - Linux: user-dirs.dirs names the folders in the system's language (a
    German desktop is ~/Schreibtisch, a Turkish one ~/Masaüstü); set to $HOME
    itself, it means "none". The XDG_*_DIR variables some code read are not
    set by a desktop session - they live in that file.
  - macOS: ~/Desktop and the others are the real paths (their names are only
    translated on screen).
No list of languages or folder names: each system is asked.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Optional

# Windows' known-folder ids (KNOWNFOLDERID), the freedesktop user-dirs keys,
# and the English name every system falls back to.
_WINDOWS = {
    "desktop":   "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "pictures":  "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "videos":    "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
    "music":     "{4BD8D571-6D19-48D3-BE97-422220080E43}",
}
_XDG = {"desktop": "DESKTOP", "documents": "DOCUMENTS", "downloads": "DOWNLOAD",
        "pictures": "PICTURES", "videos": "VIDEOS", "music": "MUSIC"}
NAMES = tuple(_WINDOWS)


def _windows(name: str) -> Optional[str]:
    guid = _WINDOWS.get(name)
    if not guid or os.name != "nt":
        return None
    try:
        import ctypes
        import uuid
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]
        u = uuid.UUID(guid)
        g = GUID(u.fields[0], u.fields[1], u.fields[2],
                 (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:]))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) == 0:
            path = out.value
            ctypes.windll.ole32.CoTaskMemFree(out)
            return path or None
    except Exception:
        return None
    return None


def _xdg(name: str) -> Optional[str]:
    """Linux's own idea of the folder (user-dirs.dirs, in the system's
    language); an XDG_*_DIR variable, when a session does set one, wins."""
    key = _XDG.get(name)
    if not key:
        return None
    home = str(Path.home())
    value = os.environ.get(f"XDG_{key}_DIR", "")
    if not value:
        cfg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "user-dirs.dirs"
        try:
            for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.match(rf'\s*XDG_{key}_DIR\s*=\s*"(.*)"\s*$', line)
                if m:
                    value = m.group(1)
                    break
        except OSError:
            return None
    value = value.replace("$HOME", home).replace("${HOME}", home)
    if not value or os.path.abspath(value) == os.path.abspath(home):
        return None                      # set to $HOME itself: "none" (the spec)
    return os.path.normpath(value)


def find(name: str) -> Optional[Path]:
    """The user's `name` folder as this system keeps it, or None when it has
    none (an existing directory only)."""
    name = str(name or "").strip().lower()
    if name not in _WINDOWS:
        return None
    if os.name == "nt":
        found = _windows(name)
    elif sys.platform == "darwin":
        found = None
    else:
        found = _xdg(name)
    for candidate in (found, str(Path.home() / name.capitalize())):
        if candidate and os.path.isdir(candidate):
            return Path(candidate)
    return None


def folder(name: str) -> Path:
    """Where to put something "on the user's <name>": that folder, else their
    home folder - always one that exists, never a guessed path."""
    return find(name) or Path.home()
