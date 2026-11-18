"""Small purpose-bound secret storage using Windows Data Protection API.

Secrets are encrypted by Windows DPAPI for the current user on this machine.
There is deliberately no plaintext or homemade-crypto fallback on platforms
without DPAPI: callers receive a clear error instead of silently weakening
credential/biometric-profile protection.
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import os
from ctypes import wintypes

_PREFIX = "dpapi:v1:"
_ENTROPY_PREFIX = b"JARVIS\x00secretstore\x00v1\x00"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class SecretStoreError(RuntimeError):
    """Raised when the operating-system secret protection API fails."""


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _windows_crypto():
    if os.name != "nt":
        raise SecretStoreError("JARVIS secret storage requires Windows DPAPI on this platform")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR,
        ctypes.POINTER(_DATA_BLOB), wintypes.LPVOID, wintypes.LPVOID,
        wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DATA_BLOB), wintypes.LPVOID, wintypes.LPVOID,
        wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    return crypt32, kernel32


def _blob(data: bytes):
    buffer = ctypes.create_string_buffer(data, len(data))
    value = _DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return value, buffer


def _entropy(purpose: str) -> bytes:
    if not isinstance(purpose, str) or not purpose.strip():
        raise ValueError("purpose must be a non-empty string")
    return hashlib.sha256(_ENTROPY_PREFIX + purpose.encode("utf-8")).digest()


def protect(value: str, purpose: str) -> str:
    """Encrypt a UTF-8 string for the current Windows user and purpose."""
    if not isinstance(value, str):
        raise TypeError("secret value must be a string")
    crypt32, kernel32 = _windows_crypto()
    source, source_buffer = _blob(value.encode("utf-8"))
    entropy, entropy_buffer = _blob(_entropy(purpose))
    output = _DATA_BLOB()
    ok = crypt32.CryptProtectData(
        ctypes.byref(source), "JARVIS protected value", ctypes.byref(entropy),
        None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output),
    )
    # Keep source_buffer/entropy_buffer alive for the duration of the native call.
    _ = source_buffer, entropy_buffer
    if not ok:
        raise SecretStoreError(f"CryptProtectData failed: {ctypes.WinError(ctypes.get_last_error())}")
    try:
        encrypted = ctypes.string_at(output.pbData, output.cbData)
        return _PREFIX + base64.b64encode(encrypted).decode("ascii")
    finally:
        kernel32.LocalFree(output.pbData)


def reveal(value: str, purpose: str) -> str | None:
    """Decrypt a value created by :func:`protect`; return None if invalid."""
    if not isinstance(value, str) or not value.startswith(_PREFIX):
        return None
    try:
        encrypted = base64.b64decode(value[len(_PREFIX):], validate=True)
        if not encrypted:
            return None
        crypt32, kernel32 = _windows_crypto()
        source, source_buffer = _blob(encrypted)
        entropy, entropy_buffer = _blob(_entropy(purpose))
        output = _DATA_BLOB()
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(source), None, ctypes.byref(entropy), None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output),
        )
        _ = source_buffer, entropy_buffer
        if not ok:
            return None
        try:
            return ctypes.string_at(output.pbData, output.cbData).decode("utf-8")
        finally:
            kernel32.LocalFree(output.pbData)
    except (ValueError, UnicodeError, SecretStoreError, OSError):
        return None


__all__ = ["SecretStoreError", "protect", "reveal"]
