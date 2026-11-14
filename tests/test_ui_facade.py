import re
import unittest
import gui.app.main_window as implementation
import ui
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QCheckBox, QLineEdit, QScrollArea, QWidget, QComboBox
from gui.components.inputs import ChatInput
from gui.panels.debug.log_widget import LogWidget
from gui.panels.system_monitor.metrics import _SysMetrics
from gui.panels.system_monitor.widgets import MetricGauge, MetricTrendChart


class UiFacadeTests(unittest.TestCase):
    def test_application_entry_point_symbols_stay_available(self):
        self.assertIs(ui.JarvisUI, implementation.JarvisUI)
        self.assertIs(ui.MainWindow, implementation.MainWindow)

    def test_extracted_components_keep_legacy_imports(self):
        self.assertIs(ui.ChatInput, ChatInput)
        self.assertIs(ui.MetricGauge, MetricGauge)
        self.assertIs(ui.MetricTrendChart, MetricTrendChart)
        self.assertIs(ui.LogWidget, LogWidget)
        self.assertIs(ui._SysMetrics, _SysMetrics)

    def test_left_monitor_scroll_surfaces_do_not_cover_panel_theme(self):
        app = QApplication.instance() or QApplication([])
        window = ui.MainWindow("")
        try:
            self.assertFalse(hasattr(window, "_ctrl_btn"))
            panel = window._left_panel
            scroll = panel.findChild(QScrollArea)
            self.assertIsNotNone(scroll)
            self.assertTrue(panel.testAttribute(Qt.WidgetAttribute.WA_StyledBackground))
            self.assertRegex(
                panel.styleSheet(),
                rf"background\s*:\s*{re.escape(ui.C.PANEL)}"
            )
            self.assertIn("transparent", scroll.viewport().styleSheet())
            self.assertIn("transparent", scroll.widget().styleSheet())

            routed = []
            window._open_audio_devices = lambda: routed.append("audio")
            window._drawer_btn.click()
            self.assertIsNotNone(window._settings_center)
            general_page = window._settings_center._category_pages["general"].widget()
            self.assertIsNotNone(general_page.findChild(QCheckBox, "autostart"))
            window._settings_center.navigation.setCurrentItem(
                window._settings_center._category_rows["identity"]
            )
            identity_page = window._settings_center._category_pages["identity"].widget()
            self.assertIsNotNone(identity_page.findChild(QLineEdit, "assistant_name"))
            window._settings_center.action_requested.emit("audio")
            self.assertTrue(window._settings_center.isVisible())
            self.assertEqual(
                window._settings_center.pages.currentWidget(),
                window._settings_center._category_pages["audio"],
            )
            self.assertEqual(routed, [])
            window._settings_center.action_requested.emit("controls")
            self.assertTrue(window._settings_center.isVisible())
            controls_page = window._settings_center._category_pages["cursor"].widget()

            self.assertIsNotNone(
                controls_page.findChild(QCheckBox, "cursor_quick_actions_enabled")
            )
            self.assertIsNotNone(
                controls_page.findChild(QCheckBox, "cursor_custom_pointer_enabled")
            )
            self.assertIsNotNone(
                controls_page.findChild(QComboBox, "cursor_pointer_style")
            )
        finally:
            window.close()
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
