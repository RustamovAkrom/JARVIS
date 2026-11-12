import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
from io import BytesIO
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QImage, QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QMainWindow,
    QScrollArea,
    QWidget,
)

from memory import config_manager
from ui import (
    ChatInput,
    ClipboardPanel,
    CursorRadialMenu,
    CustomizeOverlay,
    HudCanvas,
    MetricGauge,
    MetricTrendChart,
    _SysMetrics,
    LanguagePickerOverlay,
    MainWindow,
    SetupOverlay,
    _ProjectEditorDialog,
    _WindowHeader,
    _cursor_style_pixmap,
)


class _Log:
    def append_log(self, _text):
        pass


class _Owner:
    _muted = False
    _log = _Log()

    def _dispatch_text(self, _text):
        pass

    def _toggle_mute(self):
        pass

    def _on_file_selected(self, _path):
        pass

    def _do_interrupt(self):
        pass

    def _open_cursor_settings(self):
        pass

    def _start_region_capture(self):
        pass


class CursorAssistantUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_metric_gauge_animates_to_the_latest_reading(self):
        gauge = MetricGauge("CPU", "#00d4ff")
        try:
            gauge.set_value(76, "76%")
            self.assertEqual(gauge._text, "76%")
            self.assertLess(gauge._display_value, 76)

            QTest.qWait(600)
            self.assertAlmostEqual(gauge._display_value, 76, places=1)
        finally:
            gauge.close()

    def test_metric_trend_chart_keeps_a_bounded_recent_window(self):
        chart = MetricTrendChart("LOAD HISTORY", "COLLECTING", "MEM")
        for value in range(35):
            chart.set_values(value, 100 - value)

        self.assertEqual(len(chart._samples), 30)
        self.assertEqual(chart._samples[-1], (34.0, 66.0))

    def test_hud_orbit_reacts_to_cursor_and_recenters_on_leave(self):
        canvas = HudCanvas("", "JARVIS")
        try:
            canvas.resize(300, 300)
            event = QMouseEvent(
                QEvent.Type.MouseMove,
                QPointF(290, 20),
                Qt.MouseButton.NoButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
            canvas.mouseMoveEvent(event)
            self.assertGreater(canvas._pointer_target_x, 0)
            self.assertLess(canvas._pointer_target_y, 0)

            canvas._step_t -= 0.1
            canvas._step()
            tilted_x = canvas._pointer_tilt_x
            tilted_y = canvas._pointer_tilt_y
            self.assertGreater(tilted_x, 0)
            self.assertLess(tilted_y, 0)

            canvas.leaveEvent(QEvent(QEvent.Type.Leave))
            canvas._step_t -= 0.1
            canvas._step()
            self.assertAlmostEqual(canvas._pointer_target_x, 0)
            self.assertAlmostEqual(canvas._pointer_target_y, 0)
            self.assertLess(abs(canvas._pointer_tilt_x), abs(tilted_x))
            self.assertLess(abs(canvas._pointer_tilt_y), abs(tilted_y))
        finally:
            canvas._tmr.stop()
            canvas.close()

    def test_all_cursor_styles_render_at_the_selected_size(self):
        rendered = set()
        for style in config_manager.CURSOR_STYLES:
            with self.subTest(style=style):
                pixmap = _cursor_style_pixmap(style, 24)
                self.assertEqual((pixmap.width(), pixmap.height()), (24, 24))
                self.assertFalse(pixmap.isNull())
                self.assertGreater(pixmap.toImage().pixelColor(12, 12).alpha(), 0)
                image = pixmap.toImage()
                rendered.add(bytes(image.bits().asstring(image.sizeInBytes())))
        self.assertEqual(len(rendered), len(config_manager.CURSOR_STYLES))

    def test_quick_actions_use_a_compact_card_not_a_radial_overlay(self):
        settings = {
            "menu_actions": list(config_manager.DEFAULT_CURSOR_MENU_ACTIONS),
        }
        with patch.object(
            config_manager, "get_cursor_assistant_settings", return_value=settings
        ):
            menu = CursorRadialMenu(_Owner())
        try:
            self.assertEqual(menu.width(), 356)
            self.assertLessEqual(menu.height(), 110)
            self.assertAlmostEqual(menu.windowOpacity(), 0.9, places=2)
            self.assertIn("screen", menu._buttons)
            self.assertFalse(menu._prompt_row.isVisible())
            menu.show_near_cursor()
            self.app.processEvents()
            self.assertTrue(menu.isVisible())
            menu._run("ask")
            self.assertTrue(menu._prompt_row.isVisible())
            self.assertFalse(menu._dismiss_timer.isActive())
            menu.prompt.setText("Please keep this request open")
            menu.prompt.clearFocus()
            menu._dismiss_if_idle()
            self.assertTrue(menu.isVisible())
            self.assertEqual(menu.prompt.text(), "Please keep this request open")
            menu.prompt.clear()
            menu._dismiss_if_idle()
            self.assertFalse(menu.isVisible())
        finally:
            menu.close()

    def test_custom_window_header_double_click_toggles_maximize(self):
        owner = QMainWindow()
        owner._toggle_window_maximized = Mock()
        header = _WindowHeader(owner)
        header.resize(400, 50)
        header.show()
        try:
            QTest.mouseDClick(header, Qt.MouseButton.LeftButton)
            owner._toggle_window_maximized.assert_called_once_with()
        finally:
            owner.close()

    def test_custom_window_edge_resize_uses_qt_system_resize(self):
        owner = QMainWindow()
        owner.resize(500, 400)
        child = QWidget(owner)
        child.setGeometry(0, 0, 500, 400)
        owner._custom_chrome_enabled = True
        handle = Mock()
        handle.startSystemResize.return_value = True
        event = Mock()
        event.type.return_value = QEvent.Type.MouseButtonPress
        event.button.return_value = Qt.MouseButton.LeftButton
        event.globalPosition.return_value = QPointF(owner.mapToGlobal(QPoint(2, 100)))
        try:
            with patch.object(owner, "windowHandle", return_value=handle):
                consumed = MainWindow.eventFilter(owner, child, event)
            self.assertTrue(consumed)
            handle.startSystemResize.assert_called_once_with(Qt.Edge.LeftEdge)
        finally:
            owner.close()

    def test_windows_gpu_probe_is_disabled_to_keep_monitor_thread_safe(self):
        metrics = _SysMetrics.__new__(_SysMetrics)
        with patch("ui._OS", "Windows"), patch("ui.subprocess.run") as run:
            self.assertEqual(metrics._get_gpu(), -1.0)
        run.assert_not_called()

    def test_changing_cursor_style_automatically_enables_custom_cursor(self):
        class Controls:
            def __init__(self):
                self._cursor_visible_check = QCheckBox()
                self.saved = 0
                self._cursor_visible_check.toggled.connect(self._save_cursor_controls)

            def _save_cursor_controls(self):
                self.saved += 1

        controls = Controls()
        MainWindow._enable_custom_cursor(controls)
        self.assertTrue(controls._cursor_visible_check.isChecked())
        self.assertEqual(controls.saved, 1)

        controls._cursor_visible_check.setChecked(True)
        controls.saved = 0
        MainWindow._enable_custom_cursor(controls)
        self.assertEqual(controls.saved, 1)

    def test_region_hotkey_starts_screen_selection_once_per_press(self):
        class HotkeyOwner:
            _cursor_key_down = False
            _region_key_down = False
            menu_toggles = 0
            region_starts = 0

            def _toggle_cursor_menu(self):
                self.menu_toggles += 1

            def _start_region_capture(self):
                self.region_starts += 1

        owner = HotkeyOwner()
        MainWindow._handle_cursor_hotkey_states(owner, False, True)
        MainWindow._handle_cursor_hotkey_states(owner, False, True)
        self.assertEqual(owner.region_starts, 1)
        MainWindow._handle_cursor_hotkey_states(owner, False, False)
        MainWindow._handle_cursor_hotkey_states(owner, True, True)
        self.assertEqual(owner.region_starts, 2)
        self.assertEqual(owner.menu_toggles, 1)

    def test_customize_opacity_previews_saves_and_reverts(self):
        overlay = CustomizeOverlay(ui_opacity=100)
        previews = []
        saved = []
        overlay.on_opacity_preview = previews.append
        overlay.saved.connect(lambda *args: saved.append(args))
        try:
            overlay._opacity_slider.setValue(72)
            self.assertEqual(previews, [72])
            self.assertEqual(overlay._opacity_value.text(), "72%")
            overlay._cancel()
            self.assertEqual(previews[-1], 100)

            overlay._opacity_slider.setValue(81)
            overlay._save()
            self.assertEqual(saved[0][-1], 81)
        finally:
            overlay.close()

    def test_clipboard_popover_only_dispatches_after_action_selection(self):
        panel = ClipboardPanel()
        actions = []
        panel.action_requested.connect(actions.append)
        try:
            panel.show_clipboard("short copied text")
            self.assertEqual(actions, [])
            self.assertAlmostEqual(panel.windowOpacity(), 0.9, places=2)
            panel._trigger("Explain this: {text}")
            self.assertEqual(actions, ["Explain this: short copied text"])
            self.assertFalse(panel.isVisible())
        finally:
            panel.close()

    def test_chat_input_pastes_clipboard_image_as_bounded_jpeg(self):
        image = QImage(1600, 900, QImage.Format.Format_RGB32)
        image.fill(QColor(30, 120, 200))
        QApplication.clipboard().setImage(image)
        field = ChatInput()
        pasted = []
        failures = []
        field.image_pasted.connect(pasted.append)
        field.image_paste_failed.connect(failures.append)
        try:
            field.setFocus()
            QTest.keyClick(field, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
            self.assertEqual(failures, [])
            self.assertEqual(len(pasted), 1)
            self.assertTrue(pasted[0].startswith(b"\xff\xd8"))
            with Image.open(BytesIO(pasted[0])) as pasted_image:
                self.assertEqual(pasted_image.size, (1280, 720))
        finally:
            field.close()
            QApplication.clipboard().clear()

    def test_send_routes_pasted_image_and_question_to_vision(self):
        class Field:
            def text(self):
                return "What does this dialog say?"

            def clear(self):
                self.cleared = True

        class Answer:
            class Question:
                def setText(self, text):
                    self.text = text

            _question = Question()

        class Sender:
            _pending_chat_image = b"jpeg"
            _input = Field()
            _cursor_answer = Answer()

            def _clear_pending_chat_image(self):
                self._pending_chat_image = None

            def _on_region_captured(self, image):
                self.captured = image

            def _analyze_selected_region(self, question):
                self.analyzed = question

        sender = Sender()
        MainWindow._send(sender)
        self.assertIsNone(sender._pending_chat_image)
        self.assertEqual(sender.captured, b"jpeg")
        self.assertEqual(sender.analyzed, "What does this dialog say?")
        self.assertEqual(
            sender._cursor_answer._question.text, "What does this dialog say?"
        )

    def test_controls_drawer_groups_controls_in_a_scrollable_panel(self):
        class PreviewWindow(QMainWindow):
            def _noop(self, *_args):
                pass

            def _tr(self, text, **_values):
                return text

            _toggle_fullscreen = _noop
            _toggle_brief = _noop
            _toggle_wake_word = _noop
            _tap_wake_manual = _noop
            _toggle_ptt = _noop
            _enable_custom_cursor = _noop
            _save_cursor_controls = _noop
            _save_computer_permission_controls = _noop
            _change_interface_language = _noop
            _refresh_talk_btns = MainWindow._refresh_talk_btns

        window = PreviewWindow()
        central = QWidget()
        central.resize(820, 580)
        window.setCentralWidget(central)
        window._ui_language = "en"
        window._quick_drawer = QWidget()
        window._BTN_DIM = "QPushButton { color: white; }"
        try:
            drawer = MainWindow._build_controls_drawer(window)
            window._ctrl_drawer = drawer
            MainWindow._place_drawer(window, drawer, 12)
            self.assertEqual(drawer.width(), 330)
            self.assertGreaterEqual(drawer.height(), 490)
            self.assertIsNotNone(drawer.findChild(QScrollArea))
            self.assertEqual(len(drawer.findChildren(QComboBox)), 5)
            self.assertGreaterEqual(len(drawer.findChildren(QCheckBox)), 17)
        finally:
            window.deleteLater()
            self.app.processEvents()

    def test_setup_and_legacy_language_picker_offer_three_languages(self):
        setup = SetupOverlay(language="uz")
        picker = LanguagePickerOverlay(language="ru")
        try:
            self.assertEqual(setup._language_combo.currentData(), "uz")
            self.assertEqual(setup._language_combo.count(), 3)
            self.assertEqual(picker._language_combo.currentData(), "ru")
            self.assertEqual(picker._language_combo.count(), 3)
        finally:
            setup.close()
            picker.close()

    def test_terminal_panel_does_not_create_web_terminal_until_opened(self):
        class TerminalPreview(MainWindow):
            def __init__(self):
                QMainWindow.__init__(self)
                self._ui_language = "en"
                self._terminal_sessions = {}
                self._terminal_sequence = 0
                self._terminal_active_id = ""

        window = TerminalPreview()
        try:
            panel = window._build_terminal_panel()
            self.assertEqual(window._terminal_tabs.count(), 0)
            self.assertEqual(window._terminal_sessions, {})
        finally:
            window.deleteLater()
            self.app.processEvents()

    def test_project_editor_saves_changes_to_the_open_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "main.py"
            path.write_text("before\n", encoding="utf-8")
            owner = QMainWindow()
            editor = _ProjectEditorDialog(owner, path, "before\n")
            try:
                editor.editor.setPlainText("after\n")
                self.assertTrue(editor._save())
                self.assertEqual(path.read_text(encoding="utf-8"), "after\n")
                self.assertFalse(editor.editor.document().isModified())
            finally:
                editor.close()
                owner.close()

    def test_terminal_tab_activation_updates_the_active_working_directory(self):
        class Tab:
            def property(self, name):
                self.asserted_property = name
                return "terminal-1"

        class Tabs:
            def widget(self, index):
                return Tab() if index == 0 else None

        class Field:
            def __init__(self):
                self.value = None

            def setText(self, value):
                self.value = value

        class TerminalOwner:
            _terminal_tabs = Tabs()
            _terminal_sessions = {
                "terminal-1": type("Session", (), {"cwd": r"C:\projects\jarvis"})()
            }
            _terminal_cwd = Field()

        owner = TerminalOwner()
        MainWindow._activate_terminal_session(owner, 0)
        self.assertEqual(owner._terminal_active_id, "terminal-1")
        self.assertEqual(owner._terminal_cwd.value, r"C:\projects\jarvis")

    def test_terminal_start_directory_uses_selected_project_or_home_fallback(self):
        project = r"E:\work\demo"
        with patch("memory.config_manager.get_workspace_path", return_value=project):
            self.assertEqual(MainWindow._terminal_start_directory(), project)
        with patch("memory.config_manager.get_workspace_path", return_value=""):
            self.assertEqual(MainWindow._terminal_start_directory(), str(Path.home()))

    def test_closing_terminal_tab_interrupts_and_schedules_forced_cleanup(self):
        class View:
            def property(self, name):
                return "terminal-1"

        class Tabs:
            def widget(self, index):
                return View() if index == 0 else None

        session = type("Session", (), {"pty": object(), "closing": False})()
        owner = type(
            "TerminalOwner",
            (),
            {
                "_terminal_tabs": Tabs(),
                "_terminal_sessions": {"terminal-1": session},
                "_terminal_write": Mock(),
            },
        )()
        with patch("ui.QTimer.singleShot") as single_shot:
            MainWindow._close_terminal_session(owner, 0)
        self.assertTrue(session.closing)
        owner._terminal_write.assert_called_once_with(
            "\x03", "terminal-1", allow_closing=True
        )
        self.assertEqual(single_shot.call_count, 2)
        self.assertEqual(single_shot.call_args_list[0].args[0], 180)
        self.assertEqual(single_shot.call_args_list[1].args[0], 1500)

    def test_terminal_close_button_closes_sessions_instead_of_only_hiding(self):
        session = type("Session", (), {"view": QWidget()})()
        terminal_panel = QWidget()
        toggle = QCheckBox()
        tabs = type("Tabs", (), {"indexOf": lambda self, view: 0})()
        owner = type(
            "TerminalOwner",
            (),
            {
                "_terminal_panel": terminal_panel,
                "_terminal_toggle_btn": toggle,
                "_terminal_sessions": {"terminal-1": session},
                "_terminal_tabs": tabs,
                "_close_terminal_session": Mock(),
            },
        )()
        MainWindow._close_terminal_panel(owner)
        self.assertFalse(terminal_panel.isVisible())
        self.assertFalse(toggle.isChecked())
        owner._close_terminal_session.assert_called_once_with(0)

    def test_force_close_terminates_the_specific_windows_pty_process(self):
        class Pty:
            pid = 12345

            def __init__(self):
                self.alive = True

            def isalive(self):
                return self.alive

        pty = Pty()
        session = type("Session", (), {"pty": pty, "closing": True})()
        owner = type(
            "TerminalOwner",
            (),
            {
                "_terminal_sessions": {"terminal-1": session},
                "_append_activity": Mock(),
                "_remove_terminal_session": Mock(),
            },
        )()

        def terminate_process(*_args, **_kwargs):
            pty.alive = False
            return Mock(stderr="")

        with (
            patch("ui.os.name", "nt"),
            patch("ui.subprocess.run", side_effect=terminate_process) as run,
        ):
            MainWindow._force_close_terminal_session(owner, "terminal-1")
        self.assertEqual(
            run.call_args.args[0], ["taskkill", "/PID", "12345", "/T", "/F"]
        )
        self.assertIsNone(session.pty)
        owner._remove_terminal_session.assert_called_once_with("terminal-1")


if __name__ == "__main__":
    unittest.main()
