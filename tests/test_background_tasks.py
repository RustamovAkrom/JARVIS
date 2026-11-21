import unittest
from unittest.mock import Mock, patch

from core.background_tasks import BackgroundTaskManager


class BackgroundTaskFacadeTests(unittest.TestCase):
    def setUp(self):
        self.engine = Mock()
        self.player = object()

    @patch("core.background_tasks.desktop.ensure")
    def test_start_uses_shared_research_agent(self, ensure):
        ensure.return_value = self.engine
        self.engine.start.return_value = {
            "id": "task-1",
            "status": "queued",
            "task": "Compare solar and wind power",
        }

        manager = BackgroundTaskManager(self.player)
        task = manager.start("  Compare   solar and wind power  ")

        ensure.assert_called_once_with(self.player)
        self.engine.start.assert_called_once_with(
            "Compare solar and wind power",
            agent="researcher",
            title="Compare solar and wind power",
            origin="assistant",
            deliver="voice",
        )
        self.assertEqual(task["id"], "task-1")

    @patch("core.background_tasks.desktop.ensure")
    def test_legacy_list_maps_task_to_query(self, ensure):
        ensure.return_value = self.engine
        self.engine.list.return_value = [
            {"id": "task-2", "task": "Read official docs", "status": "running"}
        ]

        rows = BackgroundTaskManager().list(limit=5)

        self.engine.list.assert_called_once_with(5)
        self.assertEqual(rows[0]["query"], "Read official docs")

    @patch("core.background_tasks.desktop.ensure")
    def test_get_preserves_result_and_error(self, ensure):
        ensure.return_value = self.engine
        self.engine.get.return_value = {
            "id": "task-3",
            "title": "Research",
            "task": "Research API",
            "result": "Collected facts",
            "error": "",
        }

        task = BackgroundTaskManager().get("task-3")

        self.assertEqual(task["query"], "Research API")
        self.assertEqual(task["result"], "Collected facts")
        self.assertEqual(task["error"], "")

    @patch("core.background_tasks.desktop.ensure")
    def test_cancel_delegates_to_shared_engine(self, ensure):
        ensure.return_value = self.engine
        self.engine.cancel.return_value = True

        self.assertTrue(BackgroundTaskManager().cancel("task-4"))
        self.engine.cancel.assert_called_once_with("task-4")

    @patch("core.background_tasks.desktop.attach")
    @patch("core.background_tasks.desktop.ensure")
    def test_bind_player_attaches_to_shared_engine(self, ensure, attach):
        ensure.return_value = self.engine
        BackgroundTaskManager().bind_player(self.player)
        attach.assert_called_once_with(self.player)

    @patch("core.background_tasks.desktop.shutdown")
    def test_shutdown_uses_same_engine_lifecycle(self, shutdown):
        from core.background_tasks import shutdown_background_task_manager

        shutdown.return_value = False
        self.assertFalse(shutdown_background_task_manager(timeout=1.25))
        shutdown.assert_called_once_with(wait=1.25)

    def test_action_surfaces_store_errors(self):
        from actions import background_tasks

        with patch(
            "actions.background_tasks.get_background_task_manager",
            side_effect=RuntimeError("store unavailable"),
        ):
            result = background_tasks.background_tasks(
                {"action": "start", "query": "test"}
            )

        self.assertIn("status=failed", result)
        self.assertIn("store unavailable", result)


if __name__ == "__main__":
    unittest.main()
