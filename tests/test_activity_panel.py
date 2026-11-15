import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from gui.panels.activity import ActivityPanel


class ActivityPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_classifies_existing_log_messages(self):
        self.assertEqual(ActivityPanel.classify("ERR: tool failed"), "errors")
        self.assertEqual(ActivityPanel.classify("WARN: service unavailable"), "warnings")
        self.assertEqual(ActivityPanel.classify("You: hello"), "conversations")
        self.assertEqual(ActivityPanel.classify("SYS: JARVIS online"), "system")
        self.assertEqual(ActivityPanel.classify("TOOL: browser search started"), "actions")

    def test_filter_and_search_only_change_the_view(self):
        panel = ActivityPanel()
        try:
            panel.append_event("SYS: JARVIS online")
            panel.append_event("ERR: tool failed")
            panel.append_event("TOOL: browser search started")
            self.app.processEvents()
            self.assertEqual(panel.event_count, 3)
            self.assertEqual(panel.event_list.count(), 3)

            panel.filter_box.setCurrentIndex(panel.filter_box.findData("errors"))
            self.app.processEvents()
            self.assertEqual(panel.event_list.count(), 1)
            self.assertIn("ERR: tool failed", panel.event_list.item(0).text())

            panel.filter_box.setCurrentIndex(panel.filter_box.findData("all"))
            panel.search_input.setText("browser")
            self.app.processEvents()
            self.assertEqual(panel.event_list.count(), 1)
            self.assertIn("browser", panel.event_list.item(0).text())
            self.assertEqual(panel.event_count, 3)
        finally:
            panel.close()


if __name__ == "__main__":
    unittest.main()
