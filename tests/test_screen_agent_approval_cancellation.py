import threading
import unittest
from unittest.mock import patch

from plugins import screen_agent


class ScreenAgentApprovalCancellationTests(unittest.TestCase):
    def setUp(self):
        screen_agent._state.update({
            "confirm": threading.Event(),
            "stop": threading.Event(),
            "approved": False,
            "pending": "",
            "phone_chat": None,
        })

    def test_stop_before_request_refuses_approval(self):
        screen_agent._state["stop"].set()
        with patch("core.confirm.request") as request:
            approved = screen_agent._await_approval(None, "delete file", timeout=0.01)
        self.assertFalse(approved)
        request.assert_not_called()
        self.assertEqual(screen_agent._state["pending"], "")

    def test_late_confirmation_callback_cannot_approve_after_stop(self):
        captured = {}

        def request(**kwargs):
            captured.update(kwargs)
            return "confirmation requested"

        with patch("core.confirm.request", side_effect=request), \
             patch("plugins.screen_agent._panel"), \
             patch("plugins.screen_agent._say"):
            worker = threading.Thread(
                target=lambda: captured.setdefault(
                    "result", screen_agent._await_approval(None, "delete file", timeout=1)
                )
            )
            worker.start()
            # Wait until the confirmation callback is registered.
            for _ in range(100):
                if callable(captured.get("run")):
                    break
                threading.Event().wait(0.01)
            self.assertTrue(callable(captured.get("run")))
            screen_agent._state["stop"].set()
            self.assertEqual(captured["run"](), "cancelled")
            worker.join(1)

        self.assertFalse(worker.is_alive())
        self.assertFalse(captured["result"])
        self.assertFalse(screen_agent._state["approved"])
        self.assertEqual(screen_agent._state["pending"], "")

    def test_timeout_does_not_leave_pending_approval(self):
        with patch("core.confirm.request", return_value="confirmation requested"), \
             patch("plugins.screen_agent._panel"), \
             patch("plugins.screen_agent._say"):
            approved = screen_agent._await_approval(None, "delete file", timeout=0.01)
        self.assertFalse(approved)
        self.assertEqual(screen_agent._state["pending"], "")
        self.assertFalse(screen_agent._state["approved"])


if __name__ == "__main__":
    unittest.main()
