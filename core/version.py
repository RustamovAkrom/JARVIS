"""Application release version read from the single pyproject.toml source."""

from __future__ import annotations

import importlib.metadata
import tomllib

from core.runtime_paths import PATHS


def get_app_version() -> str:
    project_file = PATHS.resource_root / "pyproject.toml"
    try:
        project = tomllib.loads(project_file.read_text(encoding="utf-8"))
        return str(project["project"]["version"])
    except OSError, KeyError, TypeError, tomllib.TOMLDecodeError:
        try:
            return importlib.metadata.version("new-jarvis")
        except importlib.metadata.PackageNotFoundError:
            return "0.0.0"


APP_RELEASE_VERSION = get_app_version()
RELEASE_CHANNEL = "stable"
