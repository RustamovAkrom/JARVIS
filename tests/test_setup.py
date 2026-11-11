import unittest
from unittest.mock import patch

import setup


class SetupTests(unittest.TestCase):
    def test_platform_names_cover_supported_desktops(self):
        for system, expected in (
            ("Windows", "Windows"),
            ("Linux", "Linux"),
            ("Darwin", "macOS"),
        ):
            with self.subTest(system=system):
                self.assertEqual(setup._platform_name(system), expected)

    def test_unknown_platform_fails_with_supported_platforms(self):
        with self.assertRaisesRegex(RuntimeError, "Windows, Linux, macOS"):
            setup._platform_name("Plan9")

    def test_installer_runs_in_project_directory(self):
        with patch.object(setup.subprocess, "run") as run:
            setup._run("install", ["python", "-m", "pip"])

        run.assert_called_once_with(
            ["python", "-m", "pip"], check=True, cwd=setup.HERE)

    def test_requirements_manifest_is_resolved_from_project_directory(self):
        self.assertEqual(
            setup.REQUIREMENTS_FILE,
            setup.HERE / "requirements.txt")
        self.assertTrue(setup.REQUIREMENTS_FILE.is_file())


if __name__ == "__main__":
    unittest.main()
