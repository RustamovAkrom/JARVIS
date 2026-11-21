"""
Discovery and configuration for the providers folder.

Providers are declared explicitly and imported only when requested. A broken
optional provider must never prevent discovery or use of other providers.
"""
from __future__ import annotations

import importlib
import threading
from typing import Any, Optional

from .base import Field, caps_of, validate

_MODULES: dict[str, tuple[str, ...]] = {
    "llm": ("llm_openai",),
    "stt": ("stt_whisper",),
    "tts": ("tts_piper", "tts_system", "tts_elevenlabs"),
    "engine": ("engine_pipeline",),
}

_declarations: dict[str, dict] = {}
_instances: dict[str, Any] = {}
_failed: dict[str, str] = {}
_discovery_lock = threading.RLock()
_instance_lock = threading.RLock()


def _load(module_name: str) -> Optional[dict]:
    """Import, validate and check availability as one isolated operation."""
    with _discovery_lock:
        try:
            mod = importlib.import_module(f".{module_name}", __package__)
            decl = getattr(mod, "PROVIDER", None)
            err = validate(decl or {})
            if err:
                raise ValueError(err)
            available = bool(getattr(mod, "available", lambda: True)())
        except Exception as exc:
            _failed[module_name] = f"{type(exc).__name__}: {exc}"
            return None

        decl = dict(decl)
        decl["_module"] = module_name
        decl["_available"] = available
        _declarations[decl["id"]] = decl
        _failed.pop(module_name, None)
        return decl


def discover(kind: str = "") -> list[dict]:
    """Return declarations for one kind, skipping providers that fail."""
    names = (
        _MODULES.get(kind, ())
        if kind
        else tuple(name for group in _MODULES.values() for name in group)
    )
    out = []
    with _discovery_lock:
        for name in names:
            found = next(
                (decl for decl in _declarations.values()
                 if decl.get("_module") == name),
                None,
            )
            out.append(found or _load(name))
    return [decl for decl in out if decl]


def get_declaration(provider_id: str) -> Optional[dict]:
    with _discovery_lock:
        if provider_id in _declarations:
            return _declarations[provider_id]
        for group in _MODULES.values():
            for name in group:
                decl = _load(name)
                if decl and decl["id"] == provider_id:
                    return decl
    return None


def failures() -> dict[str, str]:
    """Return provider import/validation/availability failures."""
    with _discovery_lock:
        return dict(_failed)


def _fingerprint(provider_id: str, cfg: dict) -> str:
    # A runtime logger is not provider configuration and must not bust the cache.
    items = sorted((key, str(value)) for key, value in cfg.items() if key != "log")
    return provider_id + "|" + "|".join(f"{key}={value}" for key, value in items)


def build(provider_id: str, cfg: dict):
    """Build exactly one instance for each provider/configuration pair."""
    key = _fingerprint(provider_id, cfg)
    # Hold the lock during construction so a large local model loads only once.
    with _instance_lock:
        cached = _instances.get(key)
        if cached is not None:
            return cached

        decl = get_declaration(provider_id)
        if decl is None:
            raise LookupError(f"no provider called {provider_id!r}")
        if not decl.get("_available", True):
            missing = ", ".join(decl.get("requires", ()))
            raise RuntimeError(
                f"{decl['label']} needs {missing}, which is not installed"
            )

        instance = decl["factory"](cfg)
        if not hasattr(instance, "caps"):
            instance.caps = caps_of(decl)
        _instances[key] = instance
        return instance


def reset() -> None:
    """Drop cached instances after provider settings change."""
    with _instance_lock:
        _instances.clear()


def defaults(provider_id: str) -> dict:
    decl = get_declaration(provider_id)
    if decl is None:
        return {}
    return {
        field.key: field.default
        for field in decl.get("needs", ())
        if isinstance(field, Field)
    }


def missing_fields(provider_id: str, cfg: dict) -> list[str]:
    decl = get_declaration(provider_id)
    if decl is None:
        return []
    return [
        field.label
        for field in decl.get("needs", ())
        if isinstance(field, Field)
        and field.required
        and not str(cfg.get(field.key, "")).strip()
    ]
