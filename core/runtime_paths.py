"""Central paths for source, installed, and portable JARVIS runtimes.

Application resources are read from the source tree or PyInstaller bundle;
mutable user data is always kept outside those replaceable locations.
"""

from __future__ import annotations

import os
import logging
import re
from logging.handlers import RotatingFileHandler
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


APP_ID = "JARVIS"


def _source_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _local_data_root(
    env: dict[str, str] | None = None, system: str | None = None
) -> Path:
    env = os.environ if env is None else env
    system = sys.platform if system is None else system
    if system == "win32":
        value = env.get("LOCALAPPDATA")
        if value:
            return Path(value).expanduser()
        return Path.home() / "AppData" / "Local"
    if system == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(env.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))


@dataclass(frozen=True)
class RuntimePaths:
    mode: str
    app_root: Path
    resource_root: Path
    user_data_root: Path

    @property
    def config_dir(self) -> Path:
        return self.user_data_root / "config"

    @property
    def memory_dir(self) -> Path:
        return self.user_data_root / "memory"

    @property
    def databases_dir(self) -> Path:
        return self.user_data_root / "databases"

    @property
    def logs_dir(self) -> Path:
        return self.user_data_root / "logs"

    @property
    def plugins_dir(self) -> Path:
        return self.user_data_root / "plugins"

    @property
    def cache_dir(self) -> Path:
        return self.user_data_root / "cache"

    @property
    def lectures_dir(self) -> Path:
        return self.user_data_root / "lectures"

    @property
    def temp_update_dir(self) -> Path:
        return self.user_data_root / "updates" / "tmp"

    @property
    def api_config_file(self) -> Path:
        return self.config_dir / "api_keys.json"

    @property
    def long_term_memory_file(self) -> Path:
        return self.memory_dir / "long_term.json"

    def ensure_user_dirs(self) -> None:
        for path in (
            self.config_dir,
            self.memory_dir,
            self.databases_dir,
            self.logs_dir,
            self.plugins_dir,
            self.cache_dir,
            self.lectures_dir,
            self.temp_update_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


def _resolve_paths(
    *,
    source_root: Path | None = None,
    executable: Path | None = None,
    frozen: bool | None = None,
    env: dict[str, str] | None = None,
    system: str | None = None,
) -> RuntimePaths:
    """Resolve paths; keyword overrides keep platform behavior unit-testable."""
    env = os.environ if env is None else env
    source_root = Path(source_root or _source_root()).resolve()
    executable = Path(executable or sys.executable).resolve()
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    system = sys.platform if system is None else system

    if not frozen:
        mode = "source"
        app_root = source_root
        resource_root = source_root
    else:
        app_root = executable.parent
        resource_root = Path(getattr(sys, "_MEIPASS", app_root)).resolve()
        # Velopack's installed layout keeps the mutable execution stub beside
        # Update.exe and runs the versioned application from a `current` folder.
        if app_root.name.casefold() == "current" and (app_root.parent / "Update.exe").exists():
            mode = "installed"
        else:
            mode = "portable"

    explicit = env.get("JARVIS_PROFILE_DIR") or env.get("JARVIS_DATA_DIR")
    if explicit:
        user_data_root = Path(explicit).expanduser().resolve()
    else:
        base = _local_data_root(env, system) / APP_ID
        profile = env.get("JARVIS_PROFILE", "").strip()
        if profile and profile.casefold() != "shared" and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", profile
        ):
            raise ValueError("JARVIS_PROFILE must be a simple profile name.")
        if profile.casefold() == "shared":
            user_data_root = base
        elif profile:
            user_data_root = base / "profiles" / profile
        elif mode == "source":
            user_data_root = base / "profiles" / "development"
        elif mode == "portable":
            user_data_root = base / "profiles" / "portable"
        else:
            user_data_root = base
    return RuntimePaths(mode, app_root, resource_root, user_data_root.resolve())


PATHS = _resolve_paths()

# Keep model and browser caches out of the source tree and replaceable bundle.
os.environ.setdefault("HF_HOME", str(PATHS.cache_dir / "huggingface"))
os.environ.setdefault("TORCH_HOME", str(PATHS.cache_dir / "torch"))
os.environ.setdefault("XDG_CACHE_HOME", str(PATHS.cache_dir / "xdg"))


def configure_logging(paths: RuntimePaths = PATHS) -> logging.Logger:
    """Configure a small rotating application log in persistent user data."""
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("jarvis")
    if not any(getattr(handler, "baseFilename", "").casefold() == str(
        paths.logs_dir / "jarvis.log"
    ).casefold() for handler in logger.handlers):
        handler = RotatingFileHandler(
            paths.logs_dir / "jarvis.log",
            maxBytes=4 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def migrate_legacy_user_data(
    paths: RuntimePaths = PATHS, *, home: Path | None = None
) -> list[Path]:
    """Copy legacy config/memory files into the profile, without overwriting.

    The originals are deliberately retained. Existing destination files always
    win, which protects newer settings and credentials from older app copies.
    """
    copied: list[Path] = []
    candidates = []
    if paths.mode == "source":
        candidates.append(paths.app_root)
    else:
        candidates.append(paths.app_root)
        if paths.resource_root != paths.app_root:
            candidates.append(paths.resource_root)

    for root in candidates:
        for directory in ("config", "memory"):
            origin = root / directory
            if not origin.is_dir():
                continue
            target_root = paths.user_data_root / directory
            try:
                for source in origin.rglob("*"):
                    if not source.is_file() or source.is_symlink():
                        continue
                    relative = source.relative_to(origin)
                    target = target_root / relative
                    if target.exists():
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary = target.with_name(
                        f".{target.name}.{os.getpid()}.migration"
                    )
                    try:
                        shutil.copy2(source, temporary)
                        # Link creation is atomic and fails if another process
                        # already created the destination; never overwrite it.
                        os.link(temporary, target)
                        copied.append(target)
                    except FileExistsError:
                        pass
                    except OSError:
                        # Filesystems without hard-link support still get a
                        # race-safe exclusive create and a complete byte copy.
                        try:
                            with source.open("rb") as src, target.open("xb") as dst:
                                shutil.copyfileobj(src, dst)
                            shutil.copystat(source, target)
                            copied.append(target)
                        except FileExistsError:
                            pass
                    finally:
                        try:
                            temporary.unlink(missing_ok=True)
                        except OSError:
                            pass
            except OSError as exc:
                print(f"[Paths] Could not migrate legacy {directory} data: {exc}")

    # Older releases stored these outside the repository. Copy only missing
    # files, keep originals for rollback, and avoid importing symlinks.
    home = Path(home or Path.home())
    legacy_trees = (
        (home / ".jarvis" / "reminders", paths.databases_dir / "reminders"),
        (home / ".jarvis_profiles", paths.cache_dir / "browser_profiles"),
    )
    for source_root, target_root in legacy_trees:
        if not source_root.is_dir():
            continue
        try:
            for source in source_root.rglob("*"):
                if not source.is_file() or source.is_symlink():
                    continue
                target = target_root / source.relative_to(source_root)
                if target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with source.open("rb") as src, target.open("xb") as dst:
                        shutil.copyfileobj(src, dst)
                    shutil.copystat(source, target)
                    copied.append(target)
                except FileExistsError:
                    continue
        except OSError as exc:
            print(f"[Paths] Could not migrate legacy data from {source_root}: {exc}")

    legacy_tasks = home / ".jarvis" / "background_tasks.json"
    task_target = paths.databases_dir / "background_tasks.json"
    if legacy_tasks.is_file() and not task_target.exists():
        task_target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with legacy_tasks.open("rb") as src, task_target.open("xb") as dst:
                shutil.copyfileobj(src, dst)
            shutil.copystat(legacy_tasks, task_target)
            copied.append(task_target)
        except FileExistsError:
            pass
        except OSError as exc:
            print(f"[Paths] Could not migrate legacy task history: {exc}")
    return copied


def initialize_user_data(paths: RuntimePaths = PATHS) -> None:
    """Create the user profile and perform safe first-run legacy copying."""
    paths.ensure_user_dirs()
    logger = configure_logging(paths)
    migrated = migrate_legacy_user_data(paths)
    if migrated:
        logger.info("Copied %d legacy user-data file(s) to %s", len(migrated), paths.user_data_root)
