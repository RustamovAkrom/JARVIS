"""
core/oskey.py — one random key for this program, kept by the operating system's
own key store, for core/secretstore on systems without DPAPI.

    key() -> 32 bytes, or None when no key can be had in this run

WHERE THE KEY LIVES
    1. The Secret Service (the freedesktop D-Bus standard: GNOME Keyring,
       KWallet, KeePassXC...), whichever one this desktop runs. The user's
       login opens it, so nothing of ours is on the disk. Found by attributes
       (`application`, `purpose`), never by a name of a product or a desktop.
    2. When this system has no Secret Service at all (no D-Bus session, no
       service on it): a key file in the user's config folder, created 0600 in
       a 0700 folder — NOT in the data folder, so a copy of the data folder (a
       backup, a support zip, a sync folder) is not enough to open the values.

    A key file made in an earlier run is used before the Secret Service is
    asked, so a key is never split in two. A Secret Service that IS there but
    locked, or that refuses, gives no key for this run (the caller stores
    nothing new protected and opens nothing) — never a second key in a file
    that would then hide the first one.

NEVER BLOCKS FOR LONG
    Unlocking a locked keyring shows the desktop's own password prompt. The
    wait for it is bounded (`_UNLOCK_WAIT`); after that the answer for this run
    is "no key", and the start-up goes on.

AFTER A RENAME
    The key is looked up under the program's id and then its older ids
    (core.paths.LEGACY_APP_IDS); a key found under an older id is used as it
    is — the values it protects keep opening.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

_PURPOSE = "settings-key"
_KEY_BYTES = 32
_UNLOCK_WAIT = 60.0          # seconds the desktop's unlock prompt may take

_lock = threading.Lock()
_done = False
_key: bytes | None = None
# Where the key came from, for the log and tests: "secret-service", "file", "".
source = ""


def _ids() -> tuple[str, ...]:
    try:
        from core.paths import APP_ID, LEGACY_APP_IDS
        return (APP_ID, *reversed(LEGACY_APP_IDS))
    except Exception:
        return ("assistant",)


def _config_base() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))


def key_file(app_id: str | None = None) -> Path:
    return _config_base() / (app_id or _ids()[0]) / "settings.key"


# ── the key file ─────────────────────────────────────────────────────────────

def _read_file() -> bytes | None:
    for app_id in _ids():
        path = key_file(app_id)
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            continue
        try:
            st = os.fstat(fd)
            if hasattr(os, "getuid") and st.st_uid != os.getuid():
                continue                       # not ours: never trusted
            if st.st_mode & 0o077:
                os.fchmod(fd, 0o600)           # repair, then use
            raw = os.read(fd, 256).strip()
        except OSError:
            continue
        finally:
            os.close(fd)
        try:
            value = bytes.fromhex(raw.decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            continue
        if len(value) == _KEY_BYTES:
            return value
    return None


def _make_file() -> bytes | None:
    path = key_file()
    value = os.urandom(_KEY_BYTES)
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileExistsError:
        return _read_file()                    # another start made it a moment ago
    except OSError:
        return None
    try:
        os.write(fd, value.hex().encode("ascii"))
        os.fsync(fd)
    except OSError:
        os.close(fd)
        try:
            path.unlink()
        except OSError:
            pass
        return None
    os.close(fd)
    return value


# ── the Secret Service ───────────────────────────────────────────────────────

class _NoService(Exception):
    """This system has no Secret Service to ask."""


def _bounded(fn, wait: float):
    """fn() in a daemon thread; (done, result). A prompt the user never answers
    leaves the thread waiting on its own, not the start-up."""
    box: dict = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as exc:          # noqa: BLE001 — handed back below
            box["error"] = exc

    t = threading.Thread(target=run, name="oskey-unlock", daemon=True)
    t.start()
    t.join(wait)
    if t.is_alive():
        return False, None
    if "error" in box:
        raise box["error"]
    return True, box.get("value")


def _from_service() -> bytes | None:
    """The key from the Secret Service (made there when it has none).
    Raises _NoService when there is no service to ask at all."""
    try:
        import secretstorage
        from jeepney.wrappers import DBusErrorResponse
    except Exception as exc:
        raise _NoService(f"secretstorage missing: {exc}") from exc
    try:
        conn = secretstorage.dbus_init()
    except Exception as exc:                  # no session bus
        raise _NoService(str(exc)) from exc
    try:
        try:
            if not secretstorage.check_service_availability(conn):
                raise _NoService("no Secret Service on the session bus")
        except DBusErrorResponse as exc:
            raise _NoService(str(exc)) from exc

        for app_id in _ids():
            for item in secretstorage.search_items(conn, {"application": app_id, "purpose": _PURPOSE}):
                if item.is_locked():
                    done, _ = _bounded(item.unlock, _UNLOCK_WAIT)
                    if not done or item.is_locked():
                        return None
                value = bytes(item.get_secret())
                if len(value) == _KEY_BYTES:
                    return value

        # Asking for the default collection may make one, which shows the
        # desktop's "choose a password" prompt: bounded like an unlock.
        done, collection = _bounded(lambda: secretstorage.get_default_collection(conn), _UNLOCK_WAIT)
        if not done or collection is None:
            return None
        if collection.collection_path.rstrip("/").endswith("/session"):
            # Only the in-memory collection (gone at logout): a key kept there
            # would lose every value with it. Not a store to keep a key in.
            raise _NoService("the Secret Service has no lasting collection")
        if collection.is_locked():
            done, _ = _bounded(collection.unlock, _UNLOCK_WAIT)
            if not done or collection.is_locked():
                return None
        value = os.urandom(_KEY_BYTES)
        collection.create_item(f"{_ids()[0]} settings key",
                               {"application": _ids()[0], "purpose": _PURPOSE},
                               value, replace=False, content_type="application/octet-stream")
        return value
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── the one entry point ──────────────────────────────────────────────────────

def key() -> bytes | None:
    """The program's key, decided once per run. Never raises."""
    global _done, _key, source
    if _done:
        return _key
    with _lock:
        if _done:
            return _key
        value, where = _read_file(), "file"
        if value is None:
            try:
                value, where = _from_service(), "secret-service"
            except _NoService:
                value, where = _make_file(), "file"
            except Exception:
                value = None
        _key, source, _done = value, (where if value else ""), True
        return _key


def forget_cached() -> None:
    """Decide again on the next key() call (tests; a keyring unlocked later)."""
    global _done, _key, source
    with _lock:
        _done, _key, source = False, None, ""
