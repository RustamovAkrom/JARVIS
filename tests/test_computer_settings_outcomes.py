import unittest
from unittest.mock import patch

from actions import computer_settings


class ComputerSettingsOutcomeTests(unittest.TestCase):
    def test_typing_is_unverified_without_readback(self):
        with patch.object(computer_settings, "type_text") as typing:
            result = computer_settings.computer_settings({"action": "type_text", "value": "hello"})
        typing.assert_called_once_with("hello", press_enter_after=False)
        self.assertIn("status=unverified", result)
        self.assertNotIn("status=done", result)

    def test_keypress_is_unverified_without_observing_effect(self):
        with patch.object(computer_settings, "press_key") as press:
            result = computer_settings.computer_settings({"action": "press_key", "value": "enter"})
        press.assert_called_once_with("enter")
        self.assertIn("status=unverified", result)
        self.assertNotIn("status=done", result)

    def test_empty_typing_is_failed(self):
        result = computer_settings.computer_settings({"action": "type_text", "value": ""})
        self.assertIn("status=failed", result)


if __name__ == "__main__":
    unittest.main()
