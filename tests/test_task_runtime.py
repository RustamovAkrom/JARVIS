import unittest
from pathlib import Path
from unittest.mock import patch

from core.tasks.runtime import TaskRuntime


class TaskRuntimeTests(unittest.TestCase):
    def test_constructs_engine_with_persistent_task_paths_and_profiles(self):
        data_root = Path("/tmp/jarvis-data")
        resource_root = Path("/tmp/jarvis-resources")
        user_agents = data_root / "agents"
        with patch("core.tasks.runtime.TaskEngine") as engine_cls:
            runtime = TaskRuntime(
                data_root=data_root,
                resource_root=resource_root,
                user_agents_dir=user_agents,
                max_parallel=3,
            )
            kwargs = engine_cls.call_args.kwargs
            self.assertEqual(kwargs["root"], data_root / "tasks" / "database")
            self.assertEqual(kwargs["reports_dir"], data_root / "tasks" / "reports")
            self.assertEqual(
                kwargs["agents_dirs"],
                [resource_root / "core" / "tasks" / "agents", user_agents],
            )
            self.assertEqual(kwargs["workspaces_dir"], data_root / "tasks" / "workspaces")
            self.assertEqual(kwargs["max_parallel"], 3)
            self.assertTrue(kwargs["autostart"])
            self.assertIs(runtime.engine, engine_cls.return_value)

    def test_shutdown_is_idempotent(self):
        with patch("core.tasks.runtime.TaskEngine") as engine_cls:
            runtime = TaskRuntime(
                data_root=Path("/tmp/jarvis-data"),
                resource_root=Path("/tmp/jarvis-resources"),
            )
            runtime.shutdown(wait=1.5)
            runtime.shutdown(wait=1.5)
            engine_cls.return_value.shutdown.assert_called_once_with(wait=1.5)


if __name__ == "__main__":
    unittest.main()
