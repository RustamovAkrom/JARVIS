import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from gui.panels.tasks import TaskPanel


class TaskPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_snapshot_filters_and_preserves_selection(self):
        panel = TaskPanel()
        try:
            panel.set_tasks([
                {"id": "active", "title": "Running task", "status": "running", "created": 2, "used": {"rounds": 2}, "budget": {"rounds": 10}},
                {"id": "done", "title": "Finished task", "status": "done", "created": 1},
                {"id": "failed", "title": "Failed task", "status": "failed", "created": 0},
            ])
            self.assertEqual(panel.list_widget.count(), 3)
            panel.list_widget.setCurrentRow(0)
            selected = panel.selected_task_id()
            panel.filter_box.setCurrentIndex(panel.filter_box.findData("active"))
            self.assertEqual(panel.list_widget.count(), 1)
            self.assertEqual(panel.selected_task_id(), selected)
            self.assertTrue(panel.cancel_button.isEnabled())
            panel.filter_box.setCurrentIndex(panel.filter_box.findData("done"))
            self.assertEqual(panel.list_widget.count(), 1)
            self.assertFalse(panel.cancel_button.isEnabled())
        finally:
            panel.close()

    def test_actions_emit_selected_task_id(self):
        panel = TaskPanel()
        try:
            panel.set_tasks([{"id": "t1", "title": "Task", "status": "queued"}])
            panel.list_widget.setCurrentRow(0)
            emitted = []
            panel.cancel_requested.connect(lambda tid: emitted.append(("cancel", tid)))
            panel.finish_requested.connect(lambda tid: emitted.append(("finish", tid)))
            panel.steer_requested.connect(lambda tid: emitted.append(("steer", tid)))
            panel.cancel_button.click()
            panel.finish_button.click()
            panel.steer_button.click()
            self.assertEqual(emitted, [("cancel", "t1"), ("finish", "t1"), ("steer", "t1")])
        finally:
            panel.close()

    def test_details_show_progress_and_escape_event_text(self):
        panel = TaskPanel()
        try:
            panel.set_tasks([{
                "id": "detail",
                "title": "Task details",
                "task": "Inspect repository",
                "status": "running",
                "agent": "research",
                "created": 1000,
                "started": 1100,
                "used": {"rounds": 3, "seconds": 120},
                "budget": {"rounds": 10, "seconds": 600},
                "events": [{"t": 1200, "kind": "note", "text": "<script>alert(1)</script>"}],
            }])
            panel.list_widget.setCurrentRow(0)
            self.assertEqual(panel.progress_bar.value(), 30)
            self.assertIn("Task details", panel.details_title.text())
            rendered = panel.details_browser.toHtml()
            self.assertNotIn("<script>alert(1)</script>", rendered)
            self.assertIn("&lt;script&gt;", rendered)
            self.assertIn("Event history", rendered)
        finally:
            panel.close()

    def test_invalid_snapshots_are_ignored(self):
        panel = TaskPanel()
        try:
            panel.set_tasks([None, {}, {"id": ""}, {"id": "valid", "status": "queued"}])
            self.assertEqual(panel.list_widget.count(), 1)
        finally:
            panel.close()


if __name__ == "__main__":
    unittest.main()
