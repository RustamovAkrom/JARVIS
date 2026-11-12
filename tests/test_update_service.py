"""Tests for the stable release metadata and safe version comparisons."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.update_service import (
    ReleaseInfo,
    UpdateService,
    is_newer_version,
    parse_release_metadata,
    verify_sha256,
)


def release(tag="v1.2.3", **overrides):
    payload = {
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "published_at": "2026-11-12T12:00:00Z",
        "name": "JARVIS release",
        "body": "Release notes",
        "assets": [{"name": "setup.exe", "size": 100}],
        "html_url": "https://attacker.invalid/",
    }
    payload.update(overrides)
    return payload


class UpdateServiceTests(unittest.TestCase):
    def test_parse_stable_release_and_construct_trusted_url(self):
        result = parse_release_metadata(release())
        self.assertEqual(result.version, "1.2.3")
        self.assertEqual(result.size_bytes, 100)
        self.assertEqual(result.notes, "Release notes")
        self.assertEqual(result.url, "https://github.com/RustamovAkrom/JARVIS/releases/tag/v1.2.3")

    def test_rejects_prerelease_draft_and_non_semver(self):
        for payload in (
            release(prerelease=True),
            release(draft=True),
            release("v1.2.3-rc.1"),
            release("latest"),
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    parse_release_metadata(payload)

    def test_semantic_version_comparison(self):
        self.assertTrue(is_newer_version("1.9.9", "1.10.0"))
        self.assertFalse(is_newer_version("1.2.3", "1.2.3"))
        self.assertFalse(is_newer_version("2.0.0", "1.99.0"))

    def test_checksum_verification(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "package.bin"
            path.write_bytes(b"jarvis update")
            expected = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertTrue(verify_sha256(path, expected))
            self.assertFalse(verify_sha256(path, "0" * 64))
            self.assertFalse(verify_sha256(path, "not-a-hash"))

    def test_source_copy_never_offers_installer_update(self):
        service = UpdateService(lambda: ReleaseInfo("1.0.0", "", "", "", 0))
        with patch("core.update_service.PATHS") as paths:
            paths.mode = "source"
            self.assertFalse(service.install_supported)


if __name__ == "__main__":
    unittest.main()
