"""Read-only access to the folder selected in JARVIS' PROJECT panel."""

from __future__ import annotations

from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath

_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    "target",
}
_MAX_LIST_ITEMS = 120
_MAX_READ_CHARS = 24_000
_MAX_FILE_BYTES = 2_000_000


def _workspace() -> Path | None:
    from memory.config_manager import get_workspace_path

    raw = get_workspace_path()
    return Path(raw).resolve() if raw else None


def _resolve_workspace_file(root: Path, relative_path: str) -> Path:
    root = root.resolve(strict=True)
    posix_path = PurePosixPath(relative_path.replace("\\", "/"))
    windows_path = PureWindowsPath(relative_path)
    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or ".." in posix_path.parts
        or ".." in windows_path.parts
    ):
        raise ValueError("The requested path is outside the selected project.")
    candidate = (root / Path(*posix_path.parts)).resolve(strict=True)
    if not candidate.is_relative_to(root):
        raise ValueError("The requested path is outside the selected project.")
    if not candidate.is_file():
        raise ValueError("The requested path is not a file.")
    return candidate


def list_project_files() -> str:
    root = _workspace()
    if root is None:
        return "No project folder is selected in JARVIS. Choose one in the PROJECT panel first."

    items: list[str] = []
    try:
        for path in root.rglob("*"):
            if any(part in _IGNORED_DIRS for part in path.relative_to(root).parts):
                continue
            try:
                if path.is_file():
                    relative = path.relative_to(root)
                    items.append(str(relative))
                    if len(items) >= _MAX_LIST_ITEMS:
                        break
            except OSError:
                continue
    except OSError as exc:
        return f"Could not list the selected project: {exc}"

    if not items:
        return f"The selected project folder is empty: {root.name}"
    suffix = "\n[Listing capped at 120 files.]" if len(items) == _MAX_LIST_ITEMS else ""
    return (
        f"Selected project: {root.name}\n"
        f"Project root: {root}\n"
        "Project files (paths relative to the root):\n" + "\n".join(items) + suffix
    )


def read_project_file(relative_path: str) -> str:
    root = _workspace()
    if root is None:
        return "No project folder is selected in JARVIS. Choose one in the PROJECT panel first."
    try:
        # Resolve the root here as well as the target. macOS temporary folders
        # may traverse /var -> /private/var, and Windows may expose an 8.3
        # alias for the same directory; comparing either spelling directly
        # with the resolved target makes relative_to fail.
        root = root.resolve(strict=True)
        path = _resolve_workspace_file(root, relative_path)
        if path.stat().st_size > _MAX_FILE_BYTES:
            return f"File is too large to read safely (limit: {_MAX_FILE_BYTES} bytes)."
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as exc:
        return f"Could not read project file '{relative_path}': {exc}"

    if "\x00" in content:
        return f"'{relative_path}' does not look like a text file."
    truncated = len(content) > _MAX_READ_CHARS
    if truncated:
        content = content[:_MAX_READ_CHARS]
    return (
        f"Project file: {path.relative_to(root)}\n"
        f"Project root: {root}\n"
        f"```{path.suffix.lstrip('.')}\n{content}\n```"
        + ("\n[File content truncated.]" if truncated else "")
    )


def read_active_project_file() -> str:
    from memory.config_manager import get_active_project_file

    relative_path = get_active_project_file()
    if not relative_path:
        return "No project file is currently open in the JARVIS editor."
    return read_project_file(relative_path)


def workspace_files(parameters: dict | None = None) -> str:
    params = parameters or {}
    action = str(params.get("action", "list")).strip().lower()
    if action == "list":
        return list_project_files()
    if action == "read_current":
        return read_active_project_file()
    if action == "read":
        path = str(params.get("path", "")).strip()
        if not path:
            return "Provide a project-relative file path to read."
        return read_project_file(path)
    return f"Unknown workspace_files action: {action}"


TOOL = {
    "name": "workspace_files",
    "description": (
        "Inspect the project folder currently selected by the user in JARVIS. "
        "Use this for questions about the user's project: first list files to "
        "discover relative paths, then read relevant text files. For questions "
        "about the file open in the JARVIS editor, use read_current. This is "
        "read-only and cannot access files outside the selected project."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "list, read_current for the file open in the editor, "
                    "or read for a project-relative file"
                ),
            },
            "path": {
                "type": "STRING",
                "description": "Project-relative file path, required when action is read",
            },
        },
        "required": ["action"],
    },
    "handler": workspace_files,
}
