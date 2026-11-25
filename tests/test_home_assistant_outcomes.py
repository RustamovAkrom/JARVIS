import unittest
from unittest.mock import patch

from plugins import home_assistant


class HomeAssistantOutcomeTests(unittest.TestCase):
    def test_non_dict_tuya_response_is_failure(self):
        error = home_assistant._problem("ok")
        self.assertIsInstance(error, home_assistant.TuyaError)
        self.assertIn("not verified", str(error))

    def test_missing_success_marker_is_failure(self):
        error = home_assistant._problem({"result": "accepted"})
        self.assertIsInstance(error, home_assistant.TuyaError)

    def test_explicit_success_is_accepted(self):
        self.assertIsNone(home_assistant._problem({"success": True}))

    def test_invalid_white_temperature_is_rejected(self):
        with patch.object(home_assistant, "_send") as send:
            result = home_assistant._set_white("purple")
        self.assertEqual(result, "Choose warm, neutral, or cool white.")
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
