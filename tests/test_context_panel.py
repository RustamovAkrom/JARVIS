import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel
from gui.panels.context import ContextPanel


class ContextPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_renders_compact_context_dashboard(self):
        panel = ContextPanel(lambda: {
            "SESSION": {
                "Provider": "Ollama",
                "Model": "llama3.2",
                "Environment": "Offline",
            },
            "TOKEN USAGE": {
                "Input tokens": 300,
                "Output tokens": 100,
                "Total tokens": 400,
                "Usage source": "Estimate · visible conversation text only",
                "Context window tokens": 4096,
            },
            "WORKSPACE": {"Active project": "JARVIS", "Current attachment": "README.md"},
            "CONNECTED APPS": {
                "Connection summary": "1 connected / 2 enabled",
                "Connections": ["GitHub: Connected", "Calendar: Authorization expired"],
            },
            "AI ACTIONS": {
                "Recent actions": ["15:20 · open_file completed"],
                "Recent warnings and errors": ["15:21 · provider timeout"],
            },
        }, refresh_interval_ms=60000)
        try:
            labels = [label.text() for label in panel.findChildren(QLabel)]
            for expected in (
                "Ollama", "llama3.2", "Offline", "400", "Prompt · 300 tokens",
                "Response · 100 tokens", "Context window · 400 / 4,096 tokens (10%)",
                "JARVIS", "README.md", "1 connected / 2 enabled",
                "open_file completed", "provider timeout",
            ):
                self.assertTrue(any(expected in label for label in labels), expected)
            self.assertEqual(panel.input_bar.value(), 75)
            self.assertEqual(panel.output_bar.value(), 25)
        finally:
            panel.close()

    def test_usage_bars_are_safe_when_usage_is_empty(self):
        panel = ContextPanel(lambda: {}, refresh_interval_ms=60000)
        try:
            self.assertEqual(panel.input_bar.value(), 0)
            self.assertEqual(panel.output_bar.value(), 0)
            self.assertEqual(panel.token_total.text(), "0")
            self.assertIn("not reported", panel.context_capacity.text())
        finally:
            panel.close()

    def test_refresh_button_reloads_snapshot(self):
        calls = []
        panel = ContextPanel(
            lambda: calls.append(True) or {
                "SESSION": {"Provider": "Local", "Model": "Test", "Environment": "Offline"}
            },
            refresh_interval_ms=60000,
        )
        try:
            before = len(calls)
            panel.refresh_button.click()
            self.assertEqual(len(calls), before + 1)
        finally:
            panel.close()

    def test_quick_actions_dispatch_to_callbacks(self):
        calls = []
        panel = ContextPanel(
            lambda: {},
            refresh_interval_ms=60000,
            action_callbacks={
                "activity": lambda: calls.append("activity"),
                "providers": lambda: calls.append("providers"),
                "permissions": lambda: calls.append("permissions"),
            },
        )
        try:
            panel.activity_button.click()
            panel.providers_button.click()
            panel.permissions_button.click()
            self.assertEqual(calls, ["activity", "providers", "permissions"])
        finally:
            panel.close()

    def test_action_callback_failure_does_not_crash_panel(self):
        panel = ContextPanel(
            lambda: {},
            refresh_interval_ms=60000,
            action_callbacks={"providers": lambda: (_ for _ in ()).throw(RuntimeError())},
        )
        try:
            panel.providers_button.click()
            self.assertIn("Could not open providers", panel._status_label.text())
        finally:
            panel.close()


if __name__ == "__main__":
    unittest.main()
