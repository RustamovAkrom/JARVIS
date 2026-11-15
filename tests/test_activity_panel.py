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

    def test_filter_and_search_keep_a_stable_empty_state(self):
        panel = ActivityPanel()
        try:
            panel.resize(300, 500)
            panel.show()
            panel.append_event("SYS: JARVIS online")
            panel.append_event("ERR: tool failed")
            panel.append_event("TOOL: browser search started")
            self.app.processEvents()

            self.assertEqual(panel.event_count, 3)
            self.assertEqual(panel.event_list.count(), 3)
            self.assertIs(panel._content_stack.currentWidget(), panel.event_list)

            panel.search_input.setText("not-present")
            self.app.processEvents()
            self.assertEqual(panel.event_count, 3)
            self.assertEqual(panel.event_list.count(), 0)
            self.assertEqual(panel.empty_label.text(), "No activity matches your search.")
            self.assertTrue(panel._content_stack.currentWidget().isVisible())

            panel.search_input.clear()
            self.app.processEvents()
            self.assertEqual(panel.event_list.count(), 3)
            self.assertIs(panel._content_stack.currentWidget(), panel.event_list)
        finally:
            panel.close()

    def test_category_without_events_has_an_explicit_empty_state(self):
        panel = ActivityPanel()
        try:
            panel.filter_box.setCurrentIndex(panel.filter_box.findData("errors"))
            self.app.processEvents()
            self.assertEqual(panel.empty_label.text(), "No activity recorded in this session.")
            panel.append_event("SYS: JARVIS online")
            self.app.processEvents()
            self.assertEqual(panel.empty_label.text(), "No events in this category.")
        finally:
            panel.close()


if __name__ == "__main__":
    unittest.main()
