import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
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
            # Active is the product default; switch to All to test snapshots.
            self.assertEqual(panel.list_widget.count(), 1)
            panel.filter_box.setCurrentIndex(panel.filter_box.findData("all"))
            self.assertEqual(panel.list_widget.count(), 3)
            panel.list_widget.setCurrentRow(0)
            selected = panel.selected_task_id()
            panel.filter_box.setCurrentIndex(panel.filter_box.findData("active"))
            self.assertEqual(panel.list_widget.count(), 1)
            self.assertEqual(panel.selected_task_id(), selected)
            self.assertTrue(panel.cancel_button.isEnabled())
            panel.filter_box.setCurrentIndex(panel.filter_box.findData("history"))
            self.assertEqual(panel.list_widget.count(), 2)
            self.assertFalse(panel.cancel_button.isEnabled())
        finally:
            panel.close()

    def test_search_and_agent_filter(self):
        panel = TaskPanel()
        try:
            panel.set_agent_profiles({"research": object(), "coding": object()})
            panel.set_tasks([
                {"id": "r1", "title": "Research API", "task": "Find endpoints", "status": "running", "agent": "research"},
                {"id": "c1", "title": "Fix API", "task": "Patch endpoint", "status": "done", "agent": "coding"},
            ])
            panel.filter_box.setCurrentIndex(panel.filter_box.findData("all"))
            panel.agent_filter.setCurrentIndex(panel.agent_filter.findData("research"))
            self.assertEqual(panel.list_widget.count(), 1)
            panel.agent_filter.setCurrentIndex(panel.agent_filter.findData("all"))
            panel.list_widget.setCurrentRow(1)
            panel.search_box.setText("patch")
            self.assertEqual(panel.list_widget.count(), 1)
            self.assertEqual(panel.selected_task_id(), "c1")
        finally:
            panel.close()

    def test_sorting_and_statistics(self):
        panel = TaskPanel()
        try:
            panel.set_tasks([
                {"id": "old", "title": "Old", "status": "done", "created": 1},
                {"id": "new", "title": "New", "status": "running", "created": 2,
                 "used": {"rounds": 5}, "budget": {"rounds": 10}},
                {"id": "failed", "title": "Failed", "status": "failed", "created": 3},
            ])
            panel.filter_box.setCurrentIndex(panel.filter_box.findData("all"))
            self.assertEqual(panel.list_widget.item(0).data(Qt.ItemDataRole.UserRole), "failed")
            panel.sort_box.setCurrentIndex(panel.sort_box.findData("oldest"))
            self.assertEqual(panel.list_widget.item(0).data(Qt.ItemDataRole.UserRole), "old")
            panel.sort_box.setCurrentIndex(panel.sort_box.findData("progress"))
            self.assertEqual(panel.list_widget.item(0).data(Qt.ItemDataRole.UserRole), "new")
            self.assertIn("1 running", panel.stats_label.text())
            self.assertIn("1 done", panel.stats_label.text())
            self.assertIn("1 failed", panel.stats_label.text())
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

    def test_recovery_metadata_is_rendered(self):
        panel = TaskPanel()
        try:
            panel.set_tasks([{
                "id": "recovered",
                "title": "Recovered task",
                "status": "queued",
                "attempts": 2,
                "quota_until": 0,
                "events": [{"kind": "state", "text": "resuming after an interruption"}],
            }])
            panel.list_widget.setCurrentRow(0)
            html = panel.details_browser.toHtml()
            self.assertIn("Attempts:", html)
            self.assertIn("Recovery:", html)
            self.assertIn("Resumed after interruption", html)
        finally:
            panel.close()

    def test_live_active_task_summary_updates_with_snapshots(self):
        panel = TaskPanel()
        try:
            panel.set_tasks([
                {
                    "id": "active",
                    "title": "Background research",
                    "status": "running",
                    "used": {"rounds": 3},
                    "budget": {"rounds": 10},
                },
                {"id": "done", "title": "Finished task", "status": "done"},
            ])
            self.assertIn("ACTIVE BACKGROUND TASKS · 1", panel.active_summary.text())
            self.assertIn("Background research", panel.active_summary.text())
            self.assertIn("30%", panel.active_summary.text())
            panel.set_tasks([{"id": "done", "title": "Finished task", "status": "done"}])
            self.assertEqual(panel.active_summary.text(), "No active background tasks")
        finally:
            panel.close()

    def test_identical_task_snapshot_does_not_rebuild_panel(self):
        panel = TaskPanel()
        try:
            snapshot = [{"id": "stable", "title": "Completed", "status": "done", "created": 1}]
            panel.set_tasks(snapshot)
            selected = panel.selected_task_id()
            render_calls = []
            original_render = panel.render
            panel.render = lambda *args: render_calls.append(args)
            panel.set_tasks(snapshot)
            self.assertEqual(render_calls, [])
            self.assertEqual(panel.selected_task_id(), selected)
        finally:
            panel.close()

    def test_selection_reveals_details_and_only_relevant_actions(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "report.md"
            report.write_text("# Completed report", encoding="utf-8")
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "result.txt").write_text("result", encoding="utf-8")

            panel = TaskPanel()
            try:
                panel.set_tasks([
                    {"id": "active", "title": "Running task", "status": "running"},
                    {
                        "id": "done", "title": "Completed task", "status": "done",
                        "report_path": str(report), "workspace": str(workspace),
                    },
                ])
                panel.list_widget.setCurrentRow(0)
                self.assertFalse(panel.progress_bar.isHidden())
                self.assertFalse(panel.details_browser.isHidden())
                self.assertFalse(panel.task_actions_widget.isHidden())
                self.assertFalse(panel.cancel_button.isHidden())
                self.assertFalse(panel.steer_button.isHidden())
                self.assertTrue(panel.report_actions_widget.isHidden())
                self.assertTrue(panel.workspace_actions_widget.isHidden())

                panel.filter_box.setCurrentIndex(panel.filter_box.findData("all"))
                done_item = next(
                    panel.list_widget.item(i)
                    for i in range(panel.list_widget.count())
                    if panel.list_widget.item(i).data(Qt.ItemDataRole.UserRole) == "done"
                )
                panel.list_widget.setCurrentItem(done_item)
                self.assertFalse(panel.progress_bar.isHidden())
                self.assertFalse(panel.details_browser.isHidden())
                self.assertTrue(panel.task_actions_widget.isHidden())
                self.assertFalse(panel.report_actions_widget.isHidden())
                self.assertFalse(panel.workspace_actions_widget.isHidden())

                panel.filter_box.setCurrentIndex(panel.filter_box.findData("history"))
                self.assertFalse(panel.history_actions_widget.isHidden())
                self.assertTrue(panel.delete_button.isEnabled())
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
