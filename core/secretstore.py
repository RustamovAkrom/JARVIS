"""
core/secretstore.py — a secret kept in the settings file, readable only here.

config/api_keys.json is a plain JSON file. A value written there as-is is
readable by anything that copies the file: a backup, a sync folder, a support
zip, another account on the same computer with access to the folder. For a
secret that opens something (the cloud memory's vault key), that is too much.

    protect(value, name)  -> the string to store
    reveal(stored, name)  -> the value, or "" when it cannot be opened here

WINDOWS
    DPAPI (CryptProtectData), user scope: only this Windows account on this
    computer can open it — the account's own login secret is the key, so there
    is nothing of ours to store or lose. `name` is bound in as extra entropy,
    so a blob copied from one setting into another does not open.
    It does NOT stop a program already running as this user; nothing stored on
    the computer can. It stops the file from being enough.

ELSEWHERE (Linux, macOS)
    AES-256-GCM with the program's own random key, which the operating system
    keeps (core/oskey: the desktop's Secret Service, or a private key file in
    the config folder when the system has none). `name` and the program's id
    are the authenticated data, so — as with DPAPI — a blob copied into another
    setting does not open, and a changed byte opens to nothing.
    When no key can be had in this run (a keyring that stays locked),
    `available()` is False and values are stored as they are, as before.

    `os_key` False turns this off for a process that must keep its settings
    file as it always was (a cloud body on a server: its folder is already
    locked by the owner's passphrase, and it has no user keyring).

FORMAT
    "dpapi1:<base64>" (Windows) or "aead1:<base64 nonce+ciphertext>" (elsewhere)
    when protected; anything else is a plain value from an older version,
    returned as it is — callers re-store it to upgrade it. A protected value
    from the other kind of system does not open here and reads as "".

AFTER A RENAME
    The program's id (core.paths.APP_ID) is bound in with the name. A value
    protected under an older id (core.paths.LEGACY_APP_IDS) still opens, and
    rewrap() gives the form under the current id, for the settings file to be
    rewritten once (memory/config_manager.protect_stored_secrets).
"""
from __future__ import annotations

import base64
import os
import sys

_DPAPI = "dpapi1:"
_AEAD = "aead1:"
_PREFIX = _DPAPI if sys.platform == "win32" else _AEAD   # what protect() writes here
_NONCE = 12

# Elsewhere than Windows: protect with the OS-kept key. A server process sets
# this False before it reads any setting (see the module text).
os_key = True


def _key() -> bytes | None:
    if not os_key:
        return None
    from core import oskey
    return oskey.key()


def available() -> bool:
    """True when protect() really protects on this system."""
    if sys.platform == "win32":
        return True
    return _key() is not None


def is_protected(stored: str) -> bool:
    return isinstance(stored, str) and stored.startswith((_DPAPI, _AEAD))


def _ids() -> tuple[str, ...]:
    """The program's id, then its older ids, newest first."""
    try:
        from core.paths import APP_ID, LEGACY_APP_IDS
        return (APP_ID, *reversed(LEGACY_APP_IDS))
    except Exception:
        return ("assistant",)


def _entropy(name: str, app_id: str | None = None) -> bytes:
    return f"{app_id or _ids()[0]}|{name}".encode("utf-8")


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob),
                                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                          ctypes.POINTER(_Blob)]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
                                            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                            ctypes.POINTER(_Blob)]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _UI_FORBIDDEN = 0x1          # CRYPTPROTECT_UI_FORBIDDEN: never show a prompt

    def _blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
        buf = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    def _call(fn, data: bytes, name: str, *middle, app_id: str | None = None) -> bytes:
        inp, _keep1 = _blob(data)
        ent, _keep2 = _blob(_entropy(name, app_id))
        out = _Blob()
        if not fn(ctypes.byref(inp), *middle, ctypes.byref(ent), None, None, _UI_FORBIDDEN,
                  ctypes.byref(out)):
            raise OSError(ctypes.get_last_error(), "DPAPI refused")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(out.pbData)

    def _protect(data: bytes, name: str) -> bytes:
        return _call(_crypt32.CryptProtectData, data, name, None)

    def _unprotect(data: bytes, name: str, app_id: str | None = None) -> bytes:
        return _call(_crypt32.CryptUnprotectData, data, name, None, app_id=app_id)

else:
    def _aead():
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        k = _key()
        if k is None:
            raise OSError("no key on this system")
        return AESGCM(k)

    def _protect(data: bytes, name: str) -> bytes:
        nonce = os.urandom(_NONCE)
        return nonce + _aead().encrypt(nonce, data, _entropy(name))

    def _unprotect(data: bytes, name: str, app_id: str | None = None) -> bytes:
        """Raises OSError when it does not open here, as DPAPI does."""
        from cryptography.exceptions import InvalidTag
        if len(data) <= _NONCE:
            raise OSError("too short to be a protected value")
        try:
            return _aead().decrypt(data[:_NONCE], data[_NONCE:], _entropy(name, app_id))
        except InvalidTag:
            raise OSError("does not open here (another key, another setting, or changed)") from None


def protect(value: str, name: str) -> str:
    """The string to store for `value` under setting `name`. "" stays ""."""
    if not value or not available():
        return value
    try:
        return _PREFIX + base64.b64encode(_protect(value.encode("utf-8"), name)).decode("ascii")
    except Exception:
        return value        # never lose a key because protection failed; stays readable as before


# Opened values, by (name, stored form): the settings file is read many times a
# second, and each stored form opens to the same value for as long as it
# exists. The plain value is in this process's memory once it is used anyway.
_opened: dict[tuple[str, str], str] = {}


def reveal(stored: str, name: str, cache: bool = True) -> str:
    """The value behind `stored`, or "" when it cannot be opened on this
    computer and account (a settings file copied from elsewhere). `cache`
    False for large values that change on every write (the memory file)."""
    if not isinstance(stored, str) or not stored:
        return ""
    if not is_protected(stored):
        return stored
    if not stored.startswith(_PREFIX) or not available():
        return ""
    hit = _opened.get((name, stored))
    if hit is not None:
        return hit
    value, _ = _open(stored, name)
    if value is None:
        return ""
    if not cache:
        return value
    if len(_opened) > 256:
        _opened.clear()
    _opened[(name, stored)] = value
    return value


def _open(stored: str, name: str) -> tuple[str | None, bool]:
    """(value or None, opened under an older id)."""
    try:
        raw = base64.b64decode(stored[len(_PREFIX):], validate=True)
    except Exception:
        return None, False
    for i, app_id in enumerate(_ids()):
        try:
            return _unprotect(raw, name, app_id).decode("utf-8"), i > 0
        except Exception:
            continue
    return None, False


def rewrap(stored: str, name: str) -> str | None:
    """The form to store instead of `stored` when it opens only under an older
    id of the program; None when it needs no change (current, plain, or not
    openable here at all — never replaced by something that loses it)."""
    if not available() or not is_protected(stored) or not stored.startswith(_PREFIX):
        return None
    value, legacy = _open(stored, name)
    if value is None or not legacy:
        return None
    fresh = protect(value, name)
    return fresh if is_protected(fresh) else None
