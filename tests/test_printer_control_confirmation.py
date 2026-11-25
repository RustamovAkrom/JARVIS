import unittest
from unittest.mock import Mock, patch

from plugins import printer_control


class PrinterControlConfirmationTests(unittest.TestCase):
    def test_stop_is_gated_even_when_model_supplies_confirm_true(self):
        backend = Mock()
        backend.stop.return_value = True
        captured = {}

        def request(**kwargs):
            captured.update(kwargs)
            return "pending"

        with patch("core.confirm.request", side_effect=request), \
             patch.object(printer_control, "get_active_backend", return_value=backend):
            result = printer_control.run({"action": "stop", "confirm": True})
            self.assertEqual(result, "pending")
            backend.stop.assert_not_called()
            self.assertEqual(captured["key"], "printer_control.stop")

            # Callback is deferred; keep backend patch active until confirmation.
            self.assertEqual(captured["run"](), "Print stopped.")
            backend.stop.assert_called_once()

    def test_home_refuses_while_printing_after_confirmation(self):
        backend = Mock()
        backend.get_status.return_value.state = "printing"
        captured = {}

        def request(**kwargs):
            captured.update(kwargs)
            return "pending"

        with patch("core.confirm.request", side_effect=request), \
             patch.object(printer_control, "get_active_backend", return_value=backend):
            result = printer_control.run({"action": "home"})
            self.assertEqual(result, "pending")
            self.assertIn("CONFIRM", captured["title"])
            self.assertIn("home", captured["key"])
            self.assertIn("Refused", captured["run"]())
            backend.home.assert_not_called()


if __name__ == "__main__":
    unittest.main()
