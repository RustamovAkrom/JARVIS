import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from core.tasks.desktop import _migrate_legacy_background_tasks
from core.tasks.engine import TaskEngine


class LegacyBackgroundTaskMigrationTests(unittest.TestCase):
    def test_legacy_research_rows_are_imported_once_and_source_is_backed_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            databases = root / "databases"
            databases.mkdir()
            legacy = databases / "background_tasks.json"
            legacy.write_text(
                json.dumps({
                    "version": 1,
                    "tasks": [
                        {
                            "id": "old-done",
                            "query": "Completed legacy research",
                            "status": "done",
                            "created": 10,
                            "updated": 20,
                            "result": "Saved result",
                            "error": "",
                        },
                        {
                            "id": "old-running",
                            "query": "Interrupted legacy research",
                            "status": "running",
                            "created": 30,
                            "updated": 35,
                            "result": "",
                            "error": "",
                        },
                    ],
                }),
                encoding="utf-8",
            )
            engine = TaskEngine(
                root=root / "tasks",
                reports_dir=root / "reports",
                agents_dirs=[],
                autostart=False,
                log=lambda *_args: None,
            )
            try:
                runtime_paths = types.SimpleNamespace(databases_dir=databases)
                with patch("core.runtime_paths.PATHS", runtime_paths):
                    _migrate_legacy_background_tasks(engine)
                    _migrate_legacy_background_tasks(engine)

                tasks = {task["legacy_source_id"]: task for task in engine.store.all()}
                self.assertEqual(len(tasks), 2)
                self.assertEqual(tasks["old-done"]["status"], "done")
                self.assertEqual(tasks["old-done"]["result"], "Saved result")
                self.assertEqual(tasks["old-done"]["report_status"], "not_available")
                self.assertEqual(tasks["old-running"]["status"], "queued")
                self.assertEqual(tasks["old-running"]["attempts"], 1)
                self.assertFalse(legacy.exists())
                self.assertTrue((databases / "background_tasks.json.migrated").exists())
            finally:
                engine.shutdown(wait=0)


if __name__ == "__main__":
    unittest.main()
