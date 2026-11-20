import threading
import time
import unittest
from unittest.mock import Mock, patch

from core.connected_apps import ConnectedAppsAgent, ConnectedAppsService


class Adapter:
    name = "test-app"
    display_name = "Test App"

    def __init__(self, result="ok", *, read_only=False):
        self.result = result
        self.read_only = read_only
        self.started = threading.Event()
        self.cancel_seen = threading.Event()

    def requires_confirmation(self, task):
        return not self.read_only

    def execute(self, task, cancel_event):
        self.started.set()
        if task == "wait":
            while not cancel_event.wait(0.01):
                pass
            self.cancel_seen.set()
            return "cancelled"
        return self.result


class ConnectedAppsCoreTests(unittest.TestCase):
    def setUp(self):
        self.service = ConnectedAppsService()
        self.agent = ConnectedAppsAgent(self.service)

    def test_register_lookup_summary_and_unregister(self):
        adapter = Adapter(read_only=True)
        self.service.register(adapter)
        self.assertIs(self.service.get("TEST APP"), adapter)
        self.assertEqual(self.service.summary(), "Test App")
        self.assertEqual(self.service.list_apps()[0]["name"], "test-app")
        self.assertTrue(self.service.unregister("Test App"))
        self.assertEqual(self.service.summary(), "")

    def test_duplicate_registration_is_rejected(self):
        self.service.register(Adapter())
        with self.assertRaises(ValueError):
            self.service.register(Adapter())

    def test_unknown_app_returns_failure(self):
        result = self.agent.start("missing", "read something")
        self.assertIn("status=failed", result)

    def test_read_only_task_starts_without_confirmation(self):
        adapter = Adapter(read_only=True)
        self.service.register(adapter)
        result = self.agent.start("Test App", "read")
        self.assertIn("status=done", result)
        self.assertTrue(adapter.started.wait(1))

    def test_write_task_requires_ui_confirmation(self):
        adapter = Adapter(read_only=False)
        self.service.register(adapter)
        captured = {}
        with patch("core.confirm.request", side_effect=lambda **kw: captured.update(kw) or "pending"):
            result = self.agent.start("test-app", "create a page")
        self.assertEqual(result, "pending")
        self.assertFalse(adapter.started.is_set())
        self.assertEqual(captured["key"], "connected_apps.test-app")
        self.assertIn("create a page", captured["detail"])
        self.assertEqual(captured["run"]().split()[0], "status=done")
        self.assertTrue(adapter.started.wait(1))

    def test_stop_requests_cooperative_cancellation(self):
        adapter = Adapter(read_only=True)
        self.service.register(adapter)
        self.agent.start("test-app", "wait")
        self.assertTrue(adapter.started.wait(1))
        result = self.agent.stop("test-app")
        self.assertIn("status=done", result)
        self.assertTrue(adapter.cancel_seen.wait(1))

    def test_worker_exception_is_contained(self):
        adapter = Adapter(read_only=True)
        adapter.execute = Mock(side_effect=RuntimeError("adapter crashed"))
        say = Mock()
        self.service.register(adapter)
        result = self.agent.start("test-app", "read", say=say)
        self.assertIn("status=done", result)
        for _ in range(100):
            if say.called:
                break
            time.sleep(0.01)
        self.assertTrue(say.called)
        self.assertIn("failed", say.call_args.args[0].lower())


if __name__ == "__main__":
    unittest.main()
