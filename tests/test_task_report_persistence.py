import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.tasks.engine import TaskEngine


class TaskReportPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.engine = TaskEngine(
            root=root / "tasks",
            reports_dir=root / "reports",
            agents_dirs=[],
            autostart=False,
            log=lambda *_args: None,
        )
        self.task = self.engine.start("Write a durable report")

    def tearDown(self):
        self.engine.shutdown(wait=0)
        self.tmp.cleanup()

    def test_report_is_written_through_same_directory_atomic_replace(self):
        report = self.engine._write_report(self.task, "Persisted result")

        self.assertTrue(report.is_file())
        self.assertIn("Persisted result", report.read_text(encoding="utf-8"))
        self.assertEqual(list(report.parent.glob(f".{report.name}.*.tmp")), [])

    def test_report_failure_is_visible_without_claiming_report_exists(self):
        with patch.object(
            self.engine,
            "_write_report",
            side_effect=OSError("disk full"),
        ):
            self.engine._complete(self.task["id"], "The task result is intact")

        task = self.engine.get(self.task["id"])
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["result"], "The task result is intact")
        self.assertEqual(task["report_status"], "failed")
        self.assertIn("disk full", task["report_error"])
        self.assertEqual(task["report"], "")
        self.assertEqual(task["report_path"], "")


if __name__ == "__main__":
    unittest.main()
