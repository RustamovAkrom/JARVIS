import tempfile
import unittest
from pathlib import Path

from core.runtime_paths import _resolve_paths, migrate_legacy_user_data


class RuntimePathTests(unittest.TestCase):
    def test_source_mode_uses_isolated_local_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            paths = _resolve_paths(
                source_root=root / "checkout",
                frozen=False,
                env={"LOCALAPPDATA": str(root / "local")},
                system="win32",
            )
        self.assertEqual(paths.mode, "source")
        self.assertEqual(
            paths.user_data_root, root / "local" / "JARVIS" / "profiles" / "development"
        )

    def test_shared_profile_can_be_selected_in_every_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for frozen in (False, True):
                paths = _resolve_paths(
                    source_root=root / "source",
                    executable=root / "installed" / "JARVIS.exe",
                    frozen=frozen,
                    env={
                        "LOCALAPPDATA": str(root / "local"),
                        "JARVIS_PROFILE": "shared",
                    },
                    system="win32",
                )
                self.assertEqual(paths.user_data_root, root / "local" / "JARVIS")

    def test_explicit_data_directory_wins(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            paths = _resolve_paths(
                source_root=root,
                frozen=False,
                env={"JARVIS_DATA_DIR": str(root / "chosen")},
                system="win32",
            )
        self.assertEqual(paths.user_data_root, root / "chosen")

    def test_installed_and_portable_profiles_stay_outside_replaceable_app(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            install = root / "local" / "JARVIS" / "current"
            install.mkdir(parents=True)
            (install.parent / "Update.exe").touch()
            installed = _resolve_paths(
                executable=install / "JARVIS.exe",
                frozen=True,
                env={"LOCALAPPDATA": str(root / "local")},
                system="win32",
            )
            portable = _resolve_paths(
                executable=root / "portable" / "JARVIS.exe",
                frozen=True,
                env={"LOCALAPPDATA": str(root / "local")},
                system="win32",
            )
        self.assertEqual(installed.mode, "installed")
        self.assertEqual(installed.user_data_root, root / "local" / "JARVIS")
        self.assertEqual(portable.mode, "portable")
        self.assertEqual(
            portable.user_data_root,
            root / "local" / "JARVIS" / "profiles" / "portable",
        )

    def test_legacy_migration_copies_without_overwriting_or_deleting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "source"
            destination = root / "profile"
            (source / "config").mkdir(parents=True)
            (source / "memory").mkdir()
            (source / "config" / "api_keys.json").write_text('{"key":"old"}')
            (source / "memory" / "long_term.json").write_text('{"notes":{}}')
            paths = _resolve_paths(
                source_root=source,
                frozen=False,
                env={"JARVIS_PROFILE_DIR": str(destination)},
                system="win32",
            )
            paths.config_dir.mkdir(parents=True)
            (paths.api_config_file).write_text('{"key":"new"}')

            copied = migrate_legacy_user_data(paths, home=root / "empty-home")

            self.assertEqual(paths.api_config_file.read_text(), '{"key":"new"}')
            self.assertEqual((paths.long_term_memory_file).read_text(), '{"notes":{}}')
            self.assertEqual(len(copied), 1)
            self.assertTrue((source / "config" / "api_keys.json").exists())


if __name__ == "__main__":
    unittest.main()
