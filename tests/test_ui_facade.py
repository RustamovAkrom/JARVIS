import unittest

import gui.app.main_window as implementation
import ui
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QScrollArea
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
            panel = window._left_panel
            scroll = panel.findChild(QScrollArea)
            self.assertIsNotNone(scroll)
            self.assertTrue(panel.testAttribute(Qt.WidgetAttribute.WA_StyledBackground))
            self.assertIn(f"background: {ui.C.PANEL}", panel.styleSheet())
            self.assertIn("transparent", scroll.viewport().styleSheet())
            self.assertIn("transparent", scroll.widget().styleSheet())
        finally:
            window.close()
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
