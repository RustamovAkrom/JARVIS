"""
core/paths.py — where Jarvis keeps its files, under the names plugins use.

    from core.paths import data_dir, data, resource, APP_ID

Plugins written for Torvis (the same plugin file runs in both) find their
storage through these names. Here they mean what Jarvis already does
everywhere else (memory/config_manager.get_base_dir): the project folder when
run from source, the folder beside the executable when packaged. Nothing moves:
this module only names the places that already exist.

Deliberately no override (Torvis reads ASSISTANT_DATA_DIR): every other module
here works out the same folder by itself, and a plugin writing somewhere else
than the settings it reads would be two installs in one. To test against a
clean install, test in a copy of the folder.
"""
from __future__ import annotations

import sys
from pathlib import Path

# The program's name on disk and in file names (".jarvis_profiles", "jarvis_screenshot.png").
APP_ID = "Jarvis"
LEGACY_APP_IDS: tuple[str, ...] = ()

_SOURCE_ROOT = Path(__file__).resolve().parent.parent


def is_compiled() -> bool:
    """True inside a frozen build (PyInstaller, Nuitka)."""
    return "__compiled__" in globals() or bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """The folder the program itself is in."""
    return Path(sys.executable).parent if is_compiled() else _SOURCE_ROOT


def data_dir() -> Path:
    """The folder the program writes to — where config/ and memory/ are."""
    return app_dir()


def resource(*parts: str) -> Path:
    """A file that ships with the program, e.g. resource("models", "face", "x.onnx")."""
    base = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "_MEIPASS", None) else _SOURCE_ROOT
    return base.joinpath(*parts)


def data(*parts: str) -> Path:
    """A path under data_dir(). The parent folder is NOT created."""
    return data_dir().joinpath(*parts)


def config_dir() -> Path:
    return data("config")


def memory_dir() -> Path:
    return data("memory")


def api_keys_file() -> Path:
    """config/api_keys.json — the settings file every module reads."""
    return data("config", "api_keys.json")


def pip_install_argv(*packages: str) -> list[str] | None:
    """argv that installs packages for this program, or None in a frozen build."""
    if is_compiled():
        return None
    return [sys.executable, "-m", "pip", "install", *packages]


def missing_hint(package: str) -> str:
    """One sentence on how to get a missing package."""
    if is_compiled():
        return f"This version of the app does not include '{package}'."
    return f"Install it with: pip install {package}"


def user_python() -> str | None:
    """A Python for running the user's own code: this one from source, else
    whatever is on PATH (never the Microsoft Store placeholder)."""
    if not is_compiled():
        return sys.executable
    import shutil
    for name in ("python", "python3", "py"):
        found = shutil.which(name)
        if found and "WindowsApps" not in found:
            return found
    return None
