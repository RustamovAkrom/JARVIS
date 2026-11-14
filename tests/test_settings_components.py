import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QCheckBox, QPushButton, QWidget

from gui.components.settings import SettingRow, SettingsSection
from gui.settings.categories.general import GeneralSettingsPage
from gui.settings.categories.identity import AssistantIdentityPage
from gui.settings.registry import CATEGORIES, ENTRIES, search_entries
from gui.settings.settings_center import SettingsCenter
from gui.app.main_window import AudioDeviceOverlay, HueWheel
from gui.themes.tokens import Radius, Space
from gui.themes.typography import FONT_MONO, FONT_UI, TypeScale, mono_font, ui_font


class SettingsComponentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_setting_row_pairs_control_with_accessible_description(self):
        control = QCheckBox("Enabled")
        row = SettingRow("Wake word", "Listen for the configured wake phrase.", control)

        self.assertEqual(row.title_label.text(), "Wake word")
        self.assertEqual(control.accessibleName(), "Wake word")
        self.assertEqual(
            control.accessibleDescription(),
            "Listen for the configured wake phrase.",
        )
        self.assertGreaterEqual(row.layout().count(), 2)

    def test_section_adds_rows_and_shared_tokens_are_defined(self):
        section = SettingsSection("Audio", "Input and output devices")
        row = section.add_row(SettingRow("Microphone", "Selected input device"))

        self.assertIs(row.parent(), section)
        self.assertEqual(section.title_label.text(), "Audio")
        self.assertGreater(Space.LG, Space.SM)
        self.assertGreater(Radius.MD, Radius.SM)

    def test_typography_uses_readable_ui_and_separate_mono_fonts(self):
        self.assertTrue(FONT_UI)
        self.assertTrue(FONT_MONO)
        self.assertEqual(ui_font(TypeScale.BODY).pixelSize(), TypeScale.BODY)
        self.assertEqual(mono_font(TypeScale.SECONDARY).pixelSize(), TypeScale.SECONDARY)

    def test_settings_navigation_and_search_use_registered_routes(self):
        center = SettingsCenter()
        self.assertTrue(center.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertIsNotNone(center.findChild(QWidget, "SettingsTitleBar"))
        self.assertIsNotNone(center.findChild(QPushButton, "SettingsCloseButton"))
        self.assertIn("border:1px solid", center.styleSheet())
        self.assertEqual(center.navigation.count(), len(CATEGORIES))
        self.assertEqual({entry.category_id for entry in ENTRIES} - {item.id for item in CATEGORIES}, set())

        results = search_entries("microphone input")
        self.assertEqual([entry.id for entry in results], ["microphone"])
        self.assertEqual(results[0].action_id, "audio")

        category_titles = {category.id: category.title for category in CATEGORIES}
        self.assertNotIn("security", category_titles)
        self.assertEqual(category_titles["permissions"], "Security & Permissions")
        self.assertEqual(category_titles["integrations"], "AI Providers")
        wake_entries = search_entries("wake word")
        self.assertEqual(wake_entries[0].action_id, "audio")
        agent_entries = search_entries("Agent mode")
        self.assertTrue(any(entry.id == "computer_mode" for entry in agent_entries))

        center.search.setText("microphone")
        self.app.processEvents()
        self.assertGreaterEqual(center.navigation.count(), 1)
        self.assertIn("Voice & Audio", center.navigation.item(0).text())
        center.refresh_theme()
        center.close()

    def test_settings_window_uses_shared_glass_opacity(self):
        center = SettingsCenter()
        center.set_glass_opacity(72)
        self.assertAlmostEqual(center.windowOpacity(), 0.72, places=2)
        center.set_glass_opacity(140)
        self.assertAlmostEqual(center.windowOpacity(), 1.0, places=2)
        center.set_glass_opacity(20)
        self.assertAlmostEqual(center.windowOpacity(), 0.6, places=2)
        center.close()

    def test_hue_wheel_keeps_black_and_white_hex_preview(self):
        wheel = HueWheel("#000000")
        self.assertEqual(wheel.color(), "#000000")
        wheel.set_color("#FFFFFF")
        self.assertEqual(wheel.color(), "#ffffff")
        wheel.set_color("#000000")
        self.assertEqual(wheel.color(), "#000000")

    def test_category_pages_are_built_on_first_visit_and_cached(self):
        built = []
        center = SettingsCenter(
            page_builders={"identity": lambda: built.append("identity") or QWidget()}
        )
        self.assertEqual(built, [])
        center.navigation.setCurrentItem(center._category_rows["identity"])
        self.assertEqual(built, ["identity"])
        center.navigation.setCurrentRow(0)
        center.navigation.setCurrentItem(center._category_rows["identity"])
        self.assertEqual(built, ["identity"])
        center.close()

    def test_show_category_clears_search_before_resolving_category(self):
        center = SettingsCenter()
        center.search.setText("microphone")
        self.app.processEvents()
        self.assertNotIn("audio", center._category_rows)

        self.assertTrue(center.show_category("audio"))
        self.assertEqual(center.search.text(), "")
        self.assertEqual(
            center.navigation.currentItem().data(Qt.ItemDataRole.UserRole),
            ("category", "audio"),
        )
        center.close()

    def test_general_toggles_reflect_owner_state_after_handler(self):
        values = {"autostart": False, "morning_brief": True, "fullscreen": False}
        language = {"value": "en"}

        def toggle(key):
            values[key] = not values[key]

        page = GeneralSettingsPage(
            state={key: lambda key=key: values[key] for key in values},
            actions={key: lambda key=key: toggle(key) for key in values},
            languages={"en": "English", "ru": "Русский"},
            current_language=lambda: language["value"],
            language_changed=lambda: language.update(value=page.language_combo.currentData()),
        )
        auto_start = page.findChild(QCheckBox, "autostart")
        auto_start.setChecked(True)
        self.assertTrue(values["autostart"])
        self.assertTrue(auto_start.isChecked())

        # A failed/no-op handler must not leave a misleading checked toggle.
        page._actions["fullscreen"] = lambda: None
        fullscreen = page.findChild(QCheckBox, "fullscreen")
        fullscreen.setChecked(True)
        self.assertFalse(fullscreen.isChecked())
        self.assertFalse(page.error_label.isHidden())

        page.language_combo.setCurrentIndex(1)
        self.assertEqual(language["value"], "ru")

    def test_identity_apply_and_cancel_preserve_explicit_transaction(self):
        saved = []
        page = AssistantIdentityPage(
            assistant_name="JARVIS",
            user_name="Akrom",
            voice="Charon",
            voices=("Charon", "Kore"),
            apply_changes=lambda name, user, voice: saved.append((name, user, voice)) or True,
        )
        page.assistant_name_input.setText("Astra")
        page.user_name_input.setText("Alex")
        page.voice_combo.setCurrentIndex(1)
        self.assertTrue(page.apply())
        self.assertEqual(saved, [("Astra", "Alex", "Kore")])

        page.assistant_name_input.setText("Unsaved")
        page.cancel()
        self.assertEqual(page.assistant_name_input.text(), "Astra")
        self.assertEqual(page.user_name_input.text(), "Alex")

        failed = AssistantIdentityPage(
            assistant_name="JARVIS",
            user_name="",
            voice="Charon",
            voices=("Charon",),
            apply_changes=lambda *_: False,
        )
        self.assertFalse(failed.apply())
        self.assertFalse(failed.feedback.isHidden())

    def test_embedded_audio_panel_keeps_apply_live_without_legacy_close_button(self):
        with (
            patch("core.audio_devices.list_devices", return_value=[]),
            patch("core.audio_devices.get_output_volume", return_value=None),
            patch("memory.config_manager.get_input_device", return_value=""),
            patch("memory.config_manager.get_output_device", return_value=""),
            patch("memory.config_manager.save_input_device"),
            patch("memory.config_manager.save_output_device"),
        ):
            panel = AudioDeviceOverlay(embedded=True)
            applied = []
            panel.picked.connect(lambda: applied.append(True))
            self.assertIsNotNone(panel.findChild(QWidget, "microphone"))
            self.assertIsNotNone(panel.findChild(QWidget, "speaker"))
            self.assertIsNotNone(panel.findChild(QPushButton, "apply_audio_devices"))
            self.assertIsNone(panel.findChild(QPushButton, "close_audio_devices"))
            panel.show()
            panel._apply()
            self.assertEqual(applied, [True])
            self.assertTrue(panel.isVisible())
            panel.close()


if __name__ == "__main__":
    unittest.main()
