"""Stable GitHub release checks and Velopack update operations.

Release metadata is read from one fixed HTTPS endpoint. Package downloads and
their integrity checks are delegated to Velopack's verified release feed.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import httpx
from packaging.version import InvalidVersion, Version

from core.runtime_paths import PATHS
from core.version import APP_RELEASE_VERSION, RELEASE_CHANNEL

GITHUB_REPOSITORY = "https://github.com/RustamovAkrom/JARVIS"
LATEST_RELEASE_API = "https://api.github.com/repos/RustamovAkrom/JARVIS/releases/latest"
_STABLE_TAG = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    published_at: str
    notes: str
    url: str
    size_bytes: int


@dataclass(frozen=True)
class UpdateCheck:
    current_version: str
    release: ReleaseInfo
    update_available: bool
    install_supported: bool
    update_info: object | None = None


def parse_release_metadata(payload: object) -> ReleaseInfo:
    if not isinstance(payload, dict):
        raise ValueError("GitHub returned invalid release metadata.")
    if payload.get("draft") is True or payload.get("prerelease") is True:
        raise ValueError("Only published stable releases are supported.")
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not _STABLE_TAG.fullmatch(tag):
        raise ValueError(
            "The latest release does not use a stable vMAJOR.MINOR.PATCH tag."
        )
    try:
        version = Version(tag[1:])
    except InvalidVersion as exc:
        raise ValueError("The latest release has an invalid semantic version.") from exc
    assets = payload.get("assets", [])
    if not isinstance(assets, list):
        assets = []
    size = sum(
        int(asset.get("size", 0))
        for asset in assets
        if isinstance(asset, dict) and isinstance(asset.get("size", 0), int)
    )
    name = payload.get("name")
    body = payload.get("body")
    date = payload.get("published_at")
    return ReleaseInfo(
        version=str(version),
        published_at=date if isinstance(date, str) else "",
        notes=(body.strip()[:20_000] if isinstance(body, str) else "")
        or (name.strip() if isinstance(name, str) else "No release notes provided."),
        # Construct the link locally; do not trust arbitrary URLs from metadata.
        url=f"{GITHUB_REPOSITORY}/releases/tag/{tag}",
        size_bytes=max(0, size),
    )


def is_newer_version(current: str, available: str) -> bool:
    try:
        return Version(available) > Version(current)
    except InvalidVersion as exc:
        raise ValueError("Cannot compare an invalid application version.") from exc


def fetch_latest_release(timeout: float = 12.0) -> ReleaseInfo:
    response = httpx.get(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "JARVIS-Updater",
        },
        timeout=timeout,
        follow_redirects=False,
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError(
            "GitHub returned malformed JSON for the latest release."
        ) from exc
    return parse_release_metadata(payload)


def sha256_file(path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path, expected: str) -> bool:
    expected = str(expected or "").strip().lower()
    return bool(re.fullmatch(r"[0-9a-f]{64}", expected)) and hmac.compare_digest(
        sha256_file(path), expected
    )


def _velopack_source():
    import velopack

    source_namespace = getattr(velopack, "Sources", None)
    if source_namespace is None:
        from velopack import Sources as source_namespace
    source_type = getattr(source_namespace, "GithubSource")
    return source_type(GITHUB_REPOSITORY, None, False)


class UpdateService:
    """Background-friendly service; callers must run blocking methods off-UI."""

    def __init__(
        self, release_fetcher: Callable[[], ReleaseInfo] = fetch_latest_release
    ):
        self._release_fetcher = release_fetcher
        self._manager = None
        self._pending_info = None
        self._operation_lock = threading.Lock()

    @property
    def install_supported(self) -> bool:
        if PATHS.mode != "installed":
            return False
        try:
            return bool(self._manager_instance().get_is_portable() is False)
        except Exception:
            return False

    def current_version(self) -> str:
        if self.install_supported:
            try:
                return str(self._manager_instance().get_current_version())
            except Exception:
                pass
        return APP_RELEASE_VERSION

    def _manager_instance(self):
        if self._manager is None:
            import velopack

            self._manager = velopack.UpdateManager(_velopack_source())
        return self._manager

    def check_for_updates(self) -> UpdateCheck:
        release = self._release_fetcher()
        current = self.current_version()
        newer = is_newer_version(current, release.version)
        supported = self.install_supported
        info = None
        if newer and supported:
            info = self._manager_instance().check_for_updates()
            newer = info is not None
            if not newer:
                raise RuntimeError(
                    "A newer stable release exists, but its verified Velopack package is unavailable."
                )
        self._pending_info = info
        return UpdateCheck(current, release, newer, supported, info)

    def download_update(self, progress: Callable[[int], None] | None = None):
        if not self.install_supported:
            raise RuntimeError(
                "Update installation is available only in an installed JARVIS copy."
            )
        if self._pending_info is None:
            raise RuntimeError("Check for updates before starting a download.")
        if not self._operation_lock.acquire(blocking=False):
            raise RuntimeError("An update operation is already running.")
        try:
            self._manager_instance().download_updates(self._pending_info, progress)
        finally:
            self._operation_lock.release()

    def apply_and_restart(self):
        if not self.install_supported:
            raise RuntimeError(
                "Update installation is available only in an installed JARVIS copy."
            )
        if self._pending_info is None:
            raise RuntimeError("Check for updates and download the package first.")
        self._manager_instance().apply_updates_and_restart(self._pending_info)


def format_release_date(value: str) -> str:
    try:
        return (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            .astimezone()
            .strftime("%Y-%m-%d %H:%M %Z")
        )
    except ValueError, TypeError:
        return value or "Unknown"


__all__ = [
    "APP_RELEASE_VERSION",
    "RELEASE_CHANNEL",
    "LATEST_RELEASE_API",
    "ReleaseInfo",
    "UpdateCheck",
    "UpdateService",
    "fetch_latest_release",
    "format_release_date",
    "is_newer_version",
    "parse_release_metadata",
    "sha256_file",
    "verify_sha256",
]
