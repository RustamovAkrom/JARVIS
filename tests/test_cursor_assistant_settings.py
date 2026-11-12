import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.gemini import LIVE, VISION_FAST, _LADDERS
from memory import config_manager
from memory.translations import LANGUAGES, translate


class CursorAssistantSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "api_keys.json"
        self.config_dir = Path(self.temp_dir.name)
        self.config_path.write_text(
            json.dumps({"gemini_api_key": "preserved"}), encoding="utf-8"
        )
        self.file_patch = patch.object(config_manager, "CONFIG_FILE", self.config_path)
        self.dir_patch = patch.object(config_manager, "CONFIG_DIR", self.config_dir)
        self.file_patch.start()
        self.dir_patch.start()

    def tearDown(self):
        self.file_patch.stop()
        self.dir_patch.stop()
        self.temp_dir.cleanup()

    def test_defaults_are_unobtrusive_and_menu_is_available(self):
        settings = config_manager.get_cursor_assistant_settings()
        self.assertTrue(settings["enabled"])
        self.assertTrue(settings["cursor_visible"])
        self.assertEqual(settings["style"], "halo")
        self.assertEqual(settings["size"], 24)
        self.assertEqual(settings["hotkey"], "Ctrl+Shift+Space")
        self.assertEqual(settings["region_hotkey"], "Ctrl+Alt+R")

    def test_ui_opacity_defaults_and_clamps_to_readable_range(self):
        self.assertEqual(config_manager.get_ui_opacity(), 100)
        for value, expected in (
            (72.4, 72),
            (45, 60),
            (140, 100),
            (True, 100),
            ("75", 100),
            (float("nan"), 100),
        ):
            with self.subTest(value=value):
                self.config_path.write_text(
                    json.dumps({"ui_opacity": value}), encoding="utf-8"
                )
                self.assertEqual(config_manager.get_ui_opacity(), expected)

    def test_ui_language_validation_and_persistence(self):
        self.assertEqual(config_manager.get_ui_language(), "en")
        for language in ("en", "ru", "uz"):
            config_manager.save_ui_language(language)
            self.assertEqual(config_manager.get_ui_language(), language)
        with self.assertRaises(ValueError):
            config_manager.save_ui_language("xx")
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["gemini_api_key"], "preserved")

    def test_workspace_must_be_an_existing_directory(self):
        workspace = Path(self.temp_dir.name) / "project"
        workspace.mkdir()
        config_manager.save_workspace_path(str(workspace))
        self.assertEqual(config_manager.get_workspace_path(), str(workspace.resolve()))
        with self.assertRaises(ValueError):
            config_manager.save_workspace_path(str(workspace / "missing"))
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["gemini_api_key"], "preserved")

    def test_project_file_opener_preference_defaults_and_validates(self):
        self.assertEqual(config_manager.get_project_open_mode(), "jarvis")
        for mode in ("jarvis", "external"):
            config_manager.save_project_open_mode(mode)
            self.assertEqual(config_manager.get_project_open_mode(), mode)
        with self.assertRaises(ValueError):
            config_manager.save_project_open_mode("unknown")

    def test_active_project_file_must_be_relative_and_inside_root(self):
        config_manager.save_active_project_file("src/main.py")
        self.assertEqual(config_manager.get_active_project_file(), "src/main.py")
        for path in (r"..\secret.txt", r"C:\secret.txt"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                config_manager.save_active_project_file(path)

    def test_primary_panel_labels_are_available_in_supported_languages(self):
        self.assertEqual(set(LANGUAGES), {"en", "ru", "uz"})
        for language in LANGUAGES:
            with self.subTest(language=language):
                self.assertTrue(translate("CONTROLS", language))
                self.assertTrue(translate("PROJECT", language))
                self.assertTrue(translate("COMPUTER PERMISSIONS", language))
        self.assertEqual(
            translate("Push-to-talk  ·  {key}", "ru", key="F8"), "Нажми и говори · F8"
        )

    def test_saves_custom_settings_and_preserves_other_config(self):
        config_manager.save_cursor_assistant_settings(
            {
                "style": "reticle",
                "size": 32,
                "hotkey": "Ctrl+Alt+J",
                "region_hotkey": "Ctrl+Shift+X",
                "cursor_visible": True,
                "menu_actions": ["ask", "fix", "ask", "not-an-action"],
            }
        )
        settings = config_manager.get_cursor_assistant_settings()
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(settings["style"], "reticle")
        self.assertEqual(settings["size"], 32)
        self.assertEqual(settings["hotkey"], "Ctrl+Alt+J")
        self.assertEqual(settings["region_hotkey"], "Ctrl+Shift+X")
        self.assertTrue(settings["cursor_visible"])
        self.assertEqual(settings["menu_actions"], ["ask", "fix"])
        self.assertEqual(saved["gemini_api_key"], "preserved")

    def test_invalid_settings_fall_back_to_supported_values(self):
        config_manager.save_cursor_assistant_settings(
            {
                "style": "unknown",
                "size": 999,
                "hotkey": "Ctrl+Alt+Delete",
                "region_hotkey": "F12",
                "menu_actions": "invalid",
            }
        )
        settings = config_manager.get_cursor_assistant_settings()
        self.assertEqual(settings["style"], "halo")
        self.assertEqual(settings["size"], 24)
        self.assertEqual(settings["hotkey"], "Ctrl+Shift+Space")
        self.assertEqual(settings["region_hotkey"], "Ctrl+Alt+R")
        self.assertEqual(
            settings["menu_actions"],
            list(config_manager.DEFAULT_CURSOR_MENU_ACTIONS),
        )

    def test_vision_fast_route_uses_flash_lite_without_live_fallback(self):
        ladder = _LADDERS[VISION_FAST]
        self.assertEqual(ladder[0], "gemini-3.5-flash-lite")
        self.assertNotIn(LIVE, ladder)

    def test_computer_permissions_default_to_observe_only(self):
        permissions = config_manager.get_computer_permissions()
        self.assertTrue(permissions["observe_screen"])
        self.assertFalse(permissions["find_elements"])
        self.assertFalse(permissions["move_mouse"])
        self.assertFalse(permissions["click"])
        self.assertFalse(permissions["type"])

    def test_computer_permission_updates_preserve_other_config(self):
        config_manager.save_computer_permissions({"click": True, "type": True})
        permissions = config_manager.get_computer_permissions()
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertTrue(permissions["click"])
        self.assertTrue(permissions["type"])
        self.assertFalse(permissions["move_mouse"])
        self.assertEqual(saved["gemini_api_key"], "preserved")

    def test_malformed_permission_values_never_enable_actions(self):
        self.config_path.write_text(
            json.dumps(
                {
                    "computer_permissions": {
                        "observe_screen": "false",
                        "click": "true",
                        "type": 1,
                    },
                }
            ),
            encoding="utf-8",
        )
        permissions = config_manager.get_computer_permissions()
        self.assertTrue(permissions["observe_screen"])
        self.assertFalse(permissions["click"])
        self.assertFalse(permissions["type"])


if __name__ == "__main__":
    unittest.main()
