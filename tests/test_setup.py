import unittest
from unittest.mock import patch

import setup


class SetupTests(unittest.TestCase):
    def test_missing_pip_is_bootstrapped_with_ensurepip(self):
        missing_pip = setup.subprocess.CompletedProcess([], 1)
        ensured_pip = setup.subprocess.CompletedProcess([], 0)
        with patch.object(
            setup.subprocess, "run", side_effect=[missing_pip, ensured_pip]
        ) as run:
            setup._ensure_pip()

        self.assertEqual(run.call_count, 2)
        self.assertEqual(
            run.call_args_list[1].args[0],
            [setup.sys.executable, "-m", "ensurepip", "--upgrade"],
        )

    def test_missing_pip_and_ensurepip_report_recovery_steps(self):
        missing_pip = setup.subprocess.CompletedProcess([], 1)
        with (
            patch.object(
                setup.subprocess,
                "run",
                side_effect=[
                    missing_pip,
                    setup.subprocess.CalledProcessError(1, "ensurepip"),
                ],
            ),
            self.assertRaisesRegex(RuntimeError, "neither pip nor a working ensurepip"),
        ):
            setup._ensure_pip()

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

        run.assert_called_once_with(["python", "-m", "pip"], check=True, cwd=setup.HERE)

    def test_deprecated_pynvml_is_removed_from_existing_installations(self):
        with (
            patch.object(setup.metadata, "version", return_value="13.0.1"),
            patch.object(setup, "_run") as run,
        ):
            setup._remove_deprecated_pynvml()

        run.assert_called_once_with(
            "Removing deprecated pynvml package...",
            [setup.sys.executable, "-m", "pip", "uninstall", "-y", "pynvml"],
        )

    def test_deprecated_pynvml_cleanup_is_skipped_when_not_installed(self):
        missing = setup.metadata.PackageNotFoundError("pynvml")
        with (
            patch.object(setup.metadata, "version", side_effect=missing),
            patch.object(setup, "_run") as run,
        ):
            setup._remove_deprecated_pynvml()

        run.assert_not_called()

    def test_requirements_manifest_is_resolved_from_project_directory(self):
        self.assertEqual(setup.REQUIREMENTS_FILE, setup.HERE / "requirements.txt")
        self.assertTrue(setup.REQUIREMENTS_FILE.is_file())


if __name__ == "__main__":
    unittest.main()
