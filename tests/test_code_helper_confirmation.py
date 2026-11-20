import unittest
from unittest.mock import patch

from plugins import code_helper


class CodeHelperConfirmationTests(unittest.TestCase):
    def test_mutating_actions_require_confirmation(self):
        for action in ("write", "edit", "optimize", "run", "build"):
            with self.subTest(action=action):
                captured = {}
                with patch("core.confirm.request", side_effect=lambda **kwargs: captured.update(kwargs) or "pending"):
                    result = code_helper.run({
                        "action": action,
                        "description": "test task",
                        "file_path": "example.py",
                        "output_path": "example.py",
                    })
                self.assertEqual(result, "pending")
                self.assertEqual(captured["key"], f"code_helper.{action}")
                self.assertTrue(callable(captured["run"]))

    def test_confirmation_callback_is_the_only_execution_path(self):
        captured = {}
        with patch("core.confirm.request", side_effect=lambda **kwargs: captured.update(kwargs) or "pending"), \
             patch.object(code_helper, "code_helper", return_value="executed") as execute:
            result = code_helper.run({"action": "write", "description": "create file"})
            self.assertEqual(result, "pending")
            execute.assert_not_called()
            self.assertEqual(captured["run"](), "executed")
            execute.assert_called_once()


if __name__ == "__main__":
    unittest.main()
