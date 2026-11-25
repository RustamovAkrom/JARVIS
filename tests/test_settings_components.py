import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QProcess, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QLabel,
    QMainWindow,
    QPushButton,
    QToolButton,
    QWidget,
)

from gui.components.settings import SettingRow, SettingsSection
from gui.panels.context import ContextPanel
from gui.onboarding import OnboardingDialog
from memory.translations import translate, translate_from
from gui.settings.categories.general import GeneralSettingsPage
from gui.settings.categories.identity import AssistantIdentityPage
from gui.settings.registry import CATEGORIES, ENTRIES, search_entries
from gui.settings.settings_center import SettingsCenter
from gui.app.main_window import (
    PluginManagerOverlay,
    _QProcessTerminal,
    AudioDeviceOverlay,
    CustomizeOverlay,
    HueWheel,
    MainWindow,
    MemoryOverlay,
    ProvidersOverlay,
)
from gui.themes.styles import glass_rgba, menu_qss
from gui.themes.tokens import (
    C,
    DEFAULT_UI_COLOR,
    Radius,
    Space,
    apply_ui_accent,
    apply_ui_theme,
    current_theme_mode,
)
from gui.themes.typography import FONT_MONO, FONT_UI, TypeScale, mono_font, ui_font


class SettingsComponentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_event_filter_is_safe_before_window_chrome_initialization(self):
        # Some Qt test paths and teardown callbacks can reach eventFilter before
        # MainWindow.__init__ has populated every UI-only attribute.
        window = MainWindow.__new__(MainWindow)
        QMainWindow.__init__(window)
        child = QWidget(window)
        window.__dict__.pop("_custom_chrome_enabled", None)
        from PyQt6.QtCore import QEvent

        self.assertFalse(
            MainWindow.eventFilter(window, child, QEvent(QEvent.Type.User))
        )
        # This instance intentionally skips MainWindow.__init__. Calling close()
        # would enter the full-window closeEvent lifecycle, which is outside
        # the eventFilter contract being tested and requires initialized UI state.
        window.deleteLater()

    def test_agent_permissions_popup_uses_current_theme_surface(self):
        from types import SimpleNamespace

        owner = SimpleNamespace(
            _access_status_button=QToolButton(),
            _chat_plan_mode=False,
            _chat_interaction_mode="agent",
            _open_permissions_settings=lambda: None,
        )
        saved = {
            "observe_screen": True,
            "find_elements": True,
            "move_mouse": True,
            "click": True,
            "type": True,
            "clipboard": False,
        }
        with (
            patch("memory.config_manager.get_computer_access_settings", return_value={
                "mode": "agent", "access_level": "approve"
            }),
            patch("memory.config_manager.get_computer_permissions", return_value=saved),
            patch("memory.config_manager.get_configured_computer_permissions", return_value=saved),
            patch("memory.config_manager.get_provider_environment", return_value="online"),
            patch("memory.config_manager.get_ui_opacity", return_value=100),
        ):
            MainWindow._refresh_access_status(owner)

        menu = owner._access_status_button.menu()
        self.assertIsNotNone(menu)
        self.assertIn(f"background-color:{C.PANEL}", menu.styleSheet())
        self.assertIn(f"color:{C.TEXT}", menu.styleSheet())
        self.assertIn(f"background-color:{C.PRI_GHO}", menu.styleSheet())
        self.assertIn("QMenu::item:disabled", menu.styleSheet())
        self.assertIn(f"background-color:{C.PANEL}", menu_qss())
        menu.close()
        owner._access_status_button.close()

    def test_security_page_exposes_enforced_safeguards_not_todo_placeholders(self):
        owner = QMainWindow()
        owner._tr = lambda text, **values: text
        owner._save_computer_access_controls = lambda *_: None
        owner._save_computer_permission_controls = lambda *_: None
        owner._save_computer_safety_controls = lambda *_: None
        owner._save_computer_temporary_access_duration = lambda *_: None
        with (
            patch("memory.config_manager.get_computer_access_settings", return_value={
                "mode": "chat", "access_level": "approve", "remaining_seconds": 0
            }),
            patch("memory.config_manager.get_computer_permissions", return_value={
                "observe_screen": True, "find_elements": False, "move_mouse": False,
                "click": False, "type": False, "clipboard": False
            }),
            patch("memory.config_manager.get_computer_safety_settings", return_value={
                "microphone_during_screen_actions": True,
                "application_rules_enabled": False,
                "application_rule_mode": "blocklist",
                "applications": [],
            }),
        ):
            page = MainWindow._build_permissions_settings_page(owner)

        for name in (
            "full_access_duration",
            "microphone_during_screen_actions",
            "application_rules_enabled",
            "application_rule_mode",
            "application_rules",
        ):
            self.assertIsNotNone(page.findChild(QWidget, name), name)
        self.assertNotIn(
            "PLANNED",
            " ".join(label.text() for label in page.findChildren(QLabel)),
        )
        page.close()
        owner.close()

    def test_clear_current_chat_requires_confirmation(self):
        from types import SimpleNamespace
        from PyQt6.QtWidgets import QMessageBox

        store = SimpleNamespace(
            get_chat=lambda chat_id: {"id": chat_id, "turns": [{"id": "turn-1"}]},
            clear_chat=lambda chat_id: self.fail("clear_chat must not run after cancellation"),
        )
        owner = SimpleNamespace(_chat_store=store, _active_chat_id="chat-1")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            MainWindow._clear_chat(owner)

    def test_delete_chat_turn_requires_confirmation(self):
        from types import SimpleNamespace
        from PyQt6.QtWidgets import QMessageBox

        store = SimpleNamespace(
            get_chat=lambda chat_id: {
                "id": chat_id,
                "turns": [{"id": "turn-1", "request": "hello", "result": "world"}],
            },
            save_chat=lambda *args, **kwargs: self.fail("save_chat must not run after cancellation"),
        )
        owner = SimpleNamespace(_chat_store=store, _active_chat_id="chat-1")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            MainWindow._delete_chat_turn(owner, "turn-1")

    def test_workspace_layout_restore_applies_matching_splitter_sizes(self):
        from types import SimpleNamespace

        class Splitter:
            def __init__(self, count):
                self._count = count
                self.applied = None

            def count(self):
                return self._count

            def setSizes(self, sizes):
                self.applied = list(sizes)

        owner = SimpleNamespace(
            _main_splitter=Splitter(2),
            _root_splitter=Splitter(2),
            _workspace_split=Splitter(2),
            _center_split=Splitter(3),
        )
        saved = {
            "main": [250, 750],
            "root": [900, 300],
            "workspace": [800, 200],
            "center": [600, 200, 100],
        }
        with patch("memory.config_manager.get_workspace_layout_settings", return_value=saved):
            MainWindow._restore_workspace_layout(owner)
        self.assertEqual(owner._main_splitter.applied, [250, 750])
        self.assertEqual(owner._root_splitter.applied, [900, 300])
        self.assertEqual(owner._workspace_split.applied, [800, 200])
        self.assertEqual(owner._center_split.applied, [600, 200, 100])

    def test_workspace_layout_restore_ignores_mismatched_splitter_sizes(self):
        from types import SimpleNamespace

        class Splitter:
            def __init__(self, count):
                self._count = count
                self.applied = None

            def count(self):
                return self._count

            def setSizes(self, sizes):
                self.applied = list(sizes)

        owner = SimpleNamespace(
            _main_splitter=Splitter(2),
            _root_splitter=Splitter(2),
            _workspace_split=Splitter(2),
            _center_split=Splitter(3),
        )
        saved = {"main": [100, 200, 300], "root": [500, 400]}
        with patch("memory.config_manager.get_workspace_layout_settings", return_value=saved):
            MainWindow._restore_workspace_layout(owner)
        self.assertIsNone(owner._main_splitter.applied)
        self.assertEqual(owner._root_splitter.applied, [500, 400])

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
        self.assertIsNotNone(center.findChild(QPushButton, "SettingsMaximizeButton"))
        self.assertIsNotNone(center.findChild(QWidget, "SettingsSizeGrip"))
        self.assertIn("border:1px solid", center.styleSheet())
        self.assertEqual(center.navigation.count(), len(CATEGORIES))
        self.assertEqual({entry.category_id for entry in ENTRIES} - {item.id for item in CATEGORIES}, set())

        results = search_entries("microphone input")
        self.assertEqual([entry.id for entry in results], ["microphone"])
        self.assertEqual(results[0].action_id, "audio")

        # The original JARVIS HUD is not exposed as a Mark LIX centrepiece
        # selector; Appearance only controls the original theme and opacity.
        self.assertEqual(search_entries("orbital HUD"), [])
        self.assertNotIn("hud_style", {entry.id for entry in ENTRIES})

        live_entries = search_entries("speech detection silence")
        self.assertEqual([entry.id for entry in live_entries], ["turn_tuning"])
        proactive_entries = search_entries("ambient audio")
        self.assertEqual([entry.id for entry in proactive_entries], ["proactive_audio"])

        quiet_entries = search_entries("quiet hours notifications")
        self.assertEqual([entry.id for entry in quiet_entries], ["quiet_hours"])
        presence_entries = search_entries("idle absence minutes")
        self.assertEqual([entry.id for entry in presence_entries], ["presence_away_threshold"])

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

    def test_glass_slider_is_fully_solid_at_100_percent(self):
        # 100% is the Solid end of the Appearance slider, not a glass preset.
        self.assertTrue(glass_rgba("#010d14", 100).endswith(", 255)"))
        self.assertTrue(glass_rgba("#010d14", 60).endswith(", 153)"))

        center = SettingsCenter()
        center.set_glass_opacity(100)
        self.assertIn(", 255)", center.styleSheet())
        center.set_glass_opacity(60)
        self.assertIn(", 245)", center.styleSheet())
        center.close()

    def test_settings_window_uses_shared_glass_opacity(self):
        center = SettingsCenter()
        center.set_glass_opacity(72)
        self.assertEqual(center._glass_opacity, 72)
        self.assertIn("rgba(", center.styleSheet())
        self.assertFalse(center.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        # Glass changes panel surfaces while keeping text and window chrome crisp.
        self.assertAlmostEqual(center.windowOpacity(), 1.0, places=2)
        center.set_glass_opacity(140)
        self.assertEqual(center._glass_opacity, 100)
        center.set_glass_opacity(20)
        self.assertEqual(center._glass_opacity, 60)
        center.close()

    def test_glass_opacity_reaches_categories_built_later(self):
        built = []

        def build_identity():
            section = SettingsSection("Identity", "Profile settings")
            built.append(section)
            return section

        center = SettingsCenter(page_builders={"identity": build_identity})
        center.set_glass_opacity(76)
        center._ensure_category_page("identity")
        self.assertEqual(len(built), 1)
        self.assertEqual(built[0]._glass_opacity, 76)
        self.assertIn("rgba(", built[0].styleSheet())
        center.close()

    def test_settings_sections_follow_glass_opacity_without_fading_text(self):
        section = SettingsSection("Appearance", "Visual preferences")
        section.set_glass_opacity(68)
        self.assertEqual(section._glass_opacity, 68)
        self.assertIn("rgba(", section.styleSheet())
        self.assertIn("color:", section.styleSheet())

    def test_hue_wheel_keeps_black_and_white_hex_preview(self):
        wheel = HueWheel("#000000")
        self.assertEqual(wheel.color(), "#000000")
        wheel.set_color("#FFFFFF")
        self.assertEqual(wheel.color(), "#ffffff")
        wheel.set_color("#000000")
        self.assertEqual(wheel.color(), "#000000")

    def test_context_actions_use_responsive_two_column_layout(self):
        panel = ContextPanel(lambda: {})
        try:
            self.assertEqual(panel._action_grid.rowCount(), 2)
            self.assertEqual(panel._action_grid.columnCount(), 2)
            for button in (
                panel.activity_button,
                panel.providers_button,
                panel.permissions_button,
                panel.connected_apps_button,
            ):
                self.assertEqual(button.minimumWidth(), 0)
                self.assertTrue(button.accessibleName().startswith("Open "))
        finally:
            panel._refresh_timer.stop()
            panel.close()

    def test_chat_bubbles_use_theme_surface_and_empty_state_is_structured(self):
        from types import SimpleNamespace

        class ScrollBar:
            def value(self):
                return 0

            def maximum(self):
                return 0

            def setValue(self, _value):
                pass

        class ChatView:
            def __init__(self):
                self.html = ""

            def verticalScrollBar(self):
                return ScrollBar()

            def set_response_times(self, _values):
                pass

            def setHtml(self, value):
                self.html = value

        view = ChatView()
        owner = SimpleNamespace(
            _chat_view=view,
            _active_chat_turns=lambda: [{
                "id": "turn-1",
                "request": "Hello",
                "result": "Hi",
                "status": "complete",
            }],
            _expanded_chat_turns=set(),
            _tr=lambda value: value,
        )
        original_mode = current_theme_mode()
        original_accent = C.ACCENT
        try:
            for mode, accent in (
                ("dark", DEFAULT_UI_COLOR),
                ("light", DEFAULT_UI_COLOR),
                ("light", "#ff003b"),
            ):
                with self.subTest(mode=mode, accent=accent):
                    self.assertTrue(apply_ui_theme(mode))
                    self.assertTrue(apply_ui_accent(accent))
                    MainWindow._refresh_chat(owner)
                    self.assertIn(f"background:{C.PANEL3}", view.html)
                    self.assertIn("Hello", view.html)
                    self.assertIn("Hi", view.html)

            owner._active_chat_turns = lambda: []
            MainWindow._refresh_chat(owner)
            self.assertIn("No messages yet", view.html)
            self.assertIn("Send a message to start a conversation", view.html)
            self.assertIn(
                "Previous conversations and saved memories are unchanged",
                view.html,
            )
        finally:
            apply_ui_theme(original_mode)
            apply_ui_accent(original_accent)

    def test_advanced_telemetry_waits_until_both_columns_fit(self):
        from types import SimpleNamespace

        class Panel:
            def __init__(self):
                self.visible = None

            def setVisible(self, value):
                self.visible = value

        class Left:
            def __init__(self, width):
                self._width = width

            def isVisible(self):
                return True

            def width(self):
                return self._width

        class Tabs:
            def __init__(self, current):
                self.current = current

            def currentWidget(self):
                return self.current

        class Scroll:
            def __init__(self):
                self.policy = None

            def setVerticalScrollBarPolicy(self, value):
                self.policy = value

        system_page = QWidget()
        for width, expected in ((390, False), (479, False), (480, True)):
            owner = SimpleNamespace(
                _advanced_panel=Panel(),
                _left_panel=Left(width),
                _left_tabs=Tabs(system_page),
                _system_page=system_page,
                _system_scroll=Scroll(),
            )
            MainWindow._update_advanced_telemetry_visibility(owner)
            self.assertEqual(owner._advanced_panel.visible, expected)
            self.assertEqual(
                owner._system_scroll.policy,
                Qt.ScrollBarPolicy.ScrollBarAsNeeded
                if expected
                else Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
            )

    def test_appearance_presets_have_readable_touch_targets(self):
        panel = CustomizeOverlay(embedded=True, appearance_only=True)
        for object_name in (
            "AppearanceBlackPreset",
            "AppearanceWhitePreset",
            "AppearanceDefaultPreset",
        ):
            button = panel.findChild(QPushButton, object_name)
            self.assertIsNotNone(button)
            self.assertGreaterEqual(button.height(), 28)
            self.assertTrue(button.accessibleName())
        panel.close()

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

    def test_reopening_cached_category_refreshes_live_widget_state(self):
        class RefreshablePage(QWidget):
            def __init__(self):
                super().__init__()
                self.refresh_count = 0

            def refresh_state(self):
                self.refresh_count += 1

        child = RefreshablePage()
        page = QWidget()
        from PyQt6.QtWidgets import QVBoxLayout

        QVBoxLayout(page).addWidget(child)
        center = SettingsCenter(page_builders={"identity": lambda: page})

        self.assertTrue(center.show_category("identity"))
        self.assertEqual(child.refresh_count, 0)
        self.assertTrue(center.show_category("identity"))
        self.assertEqual(child.refresh_count, 1)
        center.close()

    def test_context_settings_route_opens_center_before_navigation(self):
        from types import SimpleNamespace

        calls = []

        class FakeSettingsCenter:
            def show_category(self, category_id):
                calls.append(("category", category_id))
                return True

            def raise_(self):
                calls.append(("raise",))

            def activateWindow(self):
                calls.append(("activate",))

        owner = SimpleNamespace(
            _settings_center=None,
            _open_settings_center=lambda: (
                calls.append(("open",)),
                setattr(owner, "_settings_center", FakeSettingsCenter()),
            ),
            _append_activity=lambda message: calls.append(("log", message)),
        )
        MainWindow._open_context_settings_category(owner, "integrations")
        self.assertEqual(calls[0], ("open",))
        self.assertIn(("category", "integrations"), calls)
        self.assertIn(("raise",), calls)
        self.assertIn(("activate",), calls)

        calls.clear()
        MainWindow._open_context_settings_category(owner, "permissions")
        self.assertIn(("category", "permissions"), calls)

    def test_real_ai_providers_settings_page_stays_visible_after_apply(self):
        # Exercise MainWindow's actual category builder and embedding path.
        # The old test instantiated ProvidersOverlay(embedded=True) directly,
        # skipping the production page builder which originally left it floating.
        owner = QMainWindow()
        owner._ui_language = "en"
        owner._settings_center = None
        owner._tr = MainWindow._tr.__get__(owner, MainWindow)
        owner._embed_settings_panels = MainWindow._embed_settings_panels.__get__(
            owner, MainWindow
        )
        owner._build_integrations_settings_page = (
            MainWindow._build_integrations_settings_page.__get__(
                owner, MainWindow
            )
        )
        owner._on_providers_applied = lambda: None

        with (
            patch("providers.registry.discover", return_value=[]),
            patch("providers.registry.reset"),
            patch("memory.config_manager.get_provider_environment", return_value="online"),
            patch("memory.config_manager.get_provider_mode", return_value="gemini"),
            patch("memory.config_manager.get_online_provider_mode", return_value="gemini"),
            patch("memory.config_manager.get_pipeline", return_value={}),
            patch("memory.config_manager.get_provider_config", return_value={}),
            patch("memory.config_manager.get_sidecar_provider", return_value=""),
            patch("memory.config_manager.get_sidecar_first", return_value=False),
            patch("memory.config_manager.save_providers"),
        ):
            page = owner._build_integrations_settings_page()
            panel = page.findChild(ProvidersOverlay)
            self.assertIsNotNone(panel)
            self.assertTrue(panel._embedded)
            self.assertTrue(panel._close_button.isHidden())

            page.show()
            panel.show()
            self.app.processEvents()
            self.assertTrue(panel.isVisible())

            panel._apply()
            self.app.processEvents()
            self.assertTrue(
                panel.isVisible(),
                "The real AI Providers page must remain mounted after Save & Apply.",
            )

        page.close()
        owner.close()

    def test_plugin_settings_are_exposed_per_plugin_row_only_when_declared(self):
        plugins = [
            {
                "name": "configured",
                "valid": True,
                "enabled": True,
                "description": "Has settings",
                "settings_namespace": "configured",
            },
            {
                "name": "plain",
                "valid": True,
                "enabled": True,
                "description": "No settings",
                "settings_namespace": "",
            },
        ]
        sections = [
            {"plugin": "configured", "namespace": "configured", "fields": [{"key": "endpoint", "type": "text"}]}
        ]
        manager = PluginManagerOverlay(plugins, settings_sections=sections)
        configured_button = manager.findChild(QPushButton, "plugin_settings_configured")
        self.assertIsNotNone(configured_button)
        self.assertIsNone(manager.findChild(QPushButton, "plugin_settings_plain"))
        received = []
        manager.configuration_requested.connect(received.append)
        configured_button.click()
        self.assertEqual(received, sections)
        manager.close()

    def test_memory_overlay_refresh_reloads_saved_facts(self):
        with patch("memory.memory_manager.all_entries_for_ui", return_value=[]):
            overlay = MemoryOverlay(embedded=True)
        with patch.object(overlay, "_rebuild") as rebuild:
            overlay.refresh_state()
        rebuild.assert_called_once_with()
        overlay.close()

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
            apply_changes=lambda *values: saved.append(values) or True,
        )
        page.assistant_name_input.setText("Astra")
        page.user_name_input.setText("Alex")
        page.voice_combo.setCurrentIndex(1)
        page.personality_combo.setCurrentIndex(page.personality_combo.findData("technical"))
        page.response_style_combo.setCurrentIndex(page.response_style_combo.findData("detailed"))
        page.custom_instructions.setPlainText("Prefer practical examples.")
        self.assertTrue(page.apply())
        self.assertEqual(
            saved,
            [("Astra", "Alex", "Kore", "technical", "detailed", "Prefer practical examples.")],
        )

        page.assistant_name_input.setText("Unsaved")
        page.personality_combo.setCurrentIndex(page.personality_combo.findData("friendly"))
        page.custom_instructions.setPlainText("Unsaved")
        page.cancel()
        self.assertEqual(page.assistant_name_input.text(), "Astra")
        self.assertEqual(page.personality_combo.currentData(), "technical")
        self.assertEqual(page.custom_instructions.toPlainText(), "Prefer practical examples.")
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


    def test_onboarding_uses_frameless_jarvis_theme(self):
        dialog = OnboardingDialog(language="en")
        self.assertTrue(
            dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
        )
        self.assertEqual(dialog._title_bar.height(), 34)
        self.assertIn("QCheckBox::indicator", dialog.styleSheet())
        self.assertIn("QWidget#OnboardingPage", dialog.styleSheet())
        dialog.close()

    def test_qprocess_terminal_adapter_reads_shell_output_without_winpty(self):
        process = QProcess()
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.start(
            __import__("sys").executable,
            ["-c", "print('qprocess-terminal-ok')"],
        )
        self.assertTrue(process.waitForStarted(3000))
        self.assertTrue(process.waitForFinished(5000))
        terminal = _QProcessTerminal(process)
        self.assertFalse(terminal.isalive())
        self.assertIn("qprocess-terminal-ok", terminal.read())
        terminal.dispose()

    def test_onboarding_requires_terms_and_cloud_voice_consent(self):
        dialog = OnboardingDialog(language="en")
        self.assertEqual(dialog._stack.count(), 4)

        dialog._step = 1
        dialog._update_navigation()
        self.assertFalse(dialog._next_btn.isEnabled())

        dialog._agreement_check.setChecked(True)
        self.assertFalse(dialog._next_btn.isEnabled())
        dialog._cloud_voice_check.setChecked(True)
        self.assertTrue(dialog._next_btn.isEnabled())

        dialog._language_combo.setCurrentIndex(
            dialog._language_combo.findData("ru")
        )
        self.assertEqual(dialog._labels["Choose your interface language"].text(),
                         "Выберите язык интерфейса")
        dialog.reject()

    def test_onboarding_and_settings_strings_have_ru_and_uz_translations(self):
        keys = (
            "Terms of use and privacy",
            "Choose what JARVIS may access",
            "Connect Google Gemini",
            "I consent to sending voice audio to Google Gemini.",
            "AI providers",
            "Runtime mode",
        )
        for key in keys:
            with self.subTest(key=key):
                self.assertNotEqual(translate(key, "ru"), key)
                self.assertNotEqual(translate(key, "uz"), key)

    def test_translate_from_converts_already_rendered_text(self):
        self.assertEqual(
            translate_from("Выберите язык интерфейса", "ru", "uz"),
            "Interfeys tilini tanlang",
        )
        self.assertEqual(
            translate_from("Выберите язык интерфейса", "ru", "en"),
            "Choose your interface language",
        )

    def test_settings_navigation_uses_selected_locale(self):
        with patch("memory.config_manager.get_ui_language", return_value="ru"):
            center = SettingsCenter()
        self.assertEqual(center._category_rows["integrations"].text(), "AI-провайдеры")
        self.assertEqual(center.search.placeholderText(), "Поиск настроек")
        center.close()


if __name__ == "__main__":
    unittest.main()
