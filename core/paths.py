"""Compatibility path helpers backed by JARVIS RuntimePaths.

Immutable application resources (models, templates, bundled assets) resolve
from PATHS.resource_root. Mutable data resolves from PATHS.user_data_root.
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

from core.runtime_paths import PATHS, RuntimePaths

_PathPart = Union[str, Path]


def resource(*parts: _PathPart) -> Path:
    """Return an absolute path to a bundled/source-tree resource.

    Examples: resource("models", "face", "model.onnx"). The path is kept
    inside the configured resource root to prevent accidental traversal.
    """
    root = PATHS.resource_root.resolve()
    target = root.joinpath(*(Path(part) for part in parts)).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("Resource path must stay inside the application resource root") from exc
    return target


def data_dir(*parts: _PathPart) -> Path:
    """Return the persistent per-user data directory (create it if needed)."""
    root = PATHS.user_data_root.resolve()
    target = root.joinpath(*(Path(part) for part in parts)).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("Data path must stay inside the JARVIS user-data directory") from exc
    target.mkdir(parents=True, exist_ok=True)
    return target


__all__ = ["resource", "data_dir", "PATHS", "RuntimePaths"]
