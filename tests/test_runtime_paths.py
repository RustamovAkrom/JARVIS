import tempfile
import unittest
from pathlib import Path

from core.runtime_paths import (
    _load_dotenv,
    _resolve_paths,
    migrate_legacy_user_data,
)


class RuntimePathTests(unittest.TestCase):
    def test_source_mode_uses_appdata_when_debug_is_false(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            paths = _resolve_paths(
                source_root=root / "checkout",
                frozen=False,
                env={"DEBUG": "false", "LOCALAPPDATA": str(root / "local")},
                system="win32",
            )
        self.assertEqual(paths.mode, "source")
        self.assertFalse(paths.debug)
        self.assertEqual(
            paths.user_data_root, root / "local" / "JARVIS" / "profiles" / "development"
        )

    def test_source_debug_mode_uses_repository_config_and_memory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            checkout = root / "checkout"
            paths = _resolve_paths(
                source_root=checkout,
                frozen=False,
                env={"DEBUG": "true", "LOCALAPPDATA": str(root / "local")},
                system="win32",
            )
        self.assertTrue(paths.debug)
        self.assertEqual(paths.user_data_root, checkout)
        self.assertEqual(paths.config_dir, checkout / "config")
        self.assertEqual(paths.memory_dir, checkout / "memory")
        self.assertEqual(paths.api_config_file, checkout / "config" / "api_keys.json")
        self.assertEqual(paths.long_term_memory_file, checkout / "memory" / "long_term.json")

    def test_source_checkout_creates_gitignored_env_with_debug_enabled(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with patch.dict("os.environ", {}, clear=True):
                _load_dotenv(root)
                env_file = root / ".env"
                self.assertTrue(env_file.is_file())
                self.assertIn("DEBUG=true", env_file.read_text(encoding="utf-8"))
                self.assertEqual(__import__("os").environ.get("DEBUG"), "true")

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

    def test_debug_mode_imports_legacy_appdata_files_into_checkout(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            checkout = root / "checkout"
            appdata = root / "local"
            legacy = appdata / "JARVIS" / "profiles" / "development"
            (legacy / "config").mkdir(parents=True)
            (legacy / "memory").mkdir()
            (legacy / "config" / "api_keys.json").write_text(
                '{"gemini_api_key":"legacy-key"}', encoding="utf-8"
            )
            (legacy / "memory" / "long_term.json").write_text(
                '{"notes":{"old":"kept"}}', encoding="utf-8"
            )
            paths = _resolve_paths(
                source_root=checkout,
                frozen=False,
                env={"DEBUG": "true"},
                system="win32",
            )
            with patch("core.runtime_paths._local_data_root", return_value=appdata):
                copied = migrate_legacy_user_data(paths, home=root / "empty-home")

            self.assertEqual(len(copied), 2)
            self.assertEqual(
                paths.api_config_file.read_text(encoding="utf-8"),
                '{"gemini_api_key":"legacy-key"}',
            )
            self.assertTrue(paths.long_term_memory_file.exists())
            self.assertTrue((legacy / "config" / "api_keys.json").exists())

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
