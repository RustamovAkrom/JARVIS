import unittest

from core.live_session_errors import policy_close_reason


class LiveSessionErrorTests(unittest.TestCase):
    def test_classifies_client_inactivity_inside_exception_group(self):
        error = ExceptionGroup(
            "unhandled errors in a TaskGroup (1 sub-exception)",
            [RuntimeError("1008 None. Session closed due to client inactivity.")],
        )

        self.assertEqual(policy_close_reason(error), "inactivity")

    def test_classifies_generic_server_abort_inside_exception_group(self):
        error = ExceptionGroup(
            "receive task failed",
            [RuntimeError("1008 The operation was aborted.")],
        )

        self.assertEqual(policy_close_reason(error), "aborted")

    def test_ignores_other_policy_violations(self):
        self.assertIsNone(policy_close_reason(RuntimeError("1008 invalid config")))


if __name__ == "__main__":
    unittest.main()
