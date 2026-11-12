import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from core.background_tasks import BackgroundTaskManager


class _Player:
    def __init__(self):
        self.contents = []
        self.logs = []
        self.speeches = []

    def show_content(self, *args, **kwargs):
        self.contents.append((args, kwargs))

    def write_log(self, text):
        self.logs.append(text)

    def request_say(self, text):
        self.speeches.append(text)


class BackgroundTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "background_tasks.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_completed_research_is_persisted_and_delivered(self):
        player = _Player()
        manager = BackgroundTaskManager(
            self.path, search=lambda query: f"Report for {query}"
        )
        manager.bind_player(player)

        task = manager.start("Compare solar and wind power")
        thread = manager._threads[task["id"]]
        thread.join(timeout=2)

        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(manager.get(task["id"])["status"], "done")
        self.assertEqual(
            saved["tasks"][0]["result"], "Report for Compare solar and wind power"
        )
        self.assertEqual(len(player.contents), 1)
        self.assertTrue(player.logs)
        self.assertTrue(player.speeches)

    def test_running_task_is_requeued_after_restart(self):
        self.path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "tasks": [
                        {
                            "id": "resume1",
                            "query": "A saved question",
                            "status": "running",
                            "created": 1,
                            "updated": 1,
                            "result": "",
                            "error": "",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        manager = BackgroundTaskManager(
            self.path, search=lambda _query: "Recovered report"
        )
        manager.bind_player(_Player())
        manager._threads["resume1"].join(timeout=2)
        self.assertEqual(manager.get("resume1")["status"], "done")

    def test_active_task_limit_is_enforced(self):
        entered = threading.Event()
        release = threading.Event()

        def wait_for_release(_query):
            entered.set()
            release.wait(timeout=2)
            return "finished"

        manager = BackgroundTaskManager(
            self.path, search=wait_for_release, max_active=1
        )
        first = manager.start("first")
        self.assertTrue(entered.wait(timeout=1))
        with self.assertRaises(ValueError):
            manager.start("second")
        release.set()
        manager._threads[first["id"]].join(timeout=2)

    def test_cancelled_running_task_does_not_publish_a_report(self):
        entered = threading.Event()
        release = threading.Event()
        player = _Player()

        def wait_for_release(_query):
            entered.set()
            release.wait(timeout=2)
            return "must not be delivered"

        manager = BackgroundTaskManager(self.path, search=wait_for_release)
        manager.bind_player(player)
        task = manager.start("cancel me")
        self.assertTrue(entered.wait(timeout=1))
        self.assertTrue(manager.cancel(task["id"]))
        release.set()
        manager._threads[task["id"]].join(timeout=2)
        self.assertEqual(manager.get(task["id"])["status"], "cancelled")
        self.assertFalse(player.contents)

    def test_action_reports_store_errors_instead_of_claiming_success(self):
        from actions import background_tasks

        with patch.object(
            background_tasks,
            "get_background_task_manager",
            side_effect=RuntimeError("store unavailable"),
        ):
            result = background_tasks.background_tasks(
                {"action": "start", "query": "test"}
            )
        self.assertIn("status=failed", result)
        self.assertIn("store unavailable", result)


if __name__ == "__main__":
    unittest.main()
