import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel
from gui.panels.context import ContextPanel


class ContextPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_renders_all_context_sections(self):
        panel = ContextPanel(lambda: {
            "SESSION": {
                "Provider": "OpenAI",
                "Model": "gpt-model",
                "Environment": "Online",
                "Selection policy": "Auto",
                "Execution engine": "Provider pipeline",
                "Computer mode": "Agent",
                "Access policy": "Approve",
            },
            "WORKSPACE": {
                "Active project": "JARVIS",
                "Project path": "E:/JARVIS",
                "Current attachment": "README.md",
            },
            "CONNECTED APPS": {
                "Connection summary": "1 connected / 1 enabled",
                "Connections": ["GitHub: Connected"],
            },
            "PERMISSIONS": {"Computer permissions": "3/8 enabled"},
            "MEMORY & CHAT": {
                "Saved memory entries": 12,
                "Active chat messages": 4,
                "Conversation size (estimate)": "~2,400 tokens · 9,600 characters",
            },
        }, refresh_interval_ms=60000)
        try:
            labels = [label.text() for label in panel.findChildren(QLabel)]
            for expected in (
                "LIVE SESSION CONTEXT", "OpenAI", "gpt-model", "Online",
                "E:/JARVIS", "README.md", "GitHub: Connected",
                "3/8 enabled", "12", "4", "~2,400 tokens · 9,600 characters",
            ):
                self.assertIn(expected, labels)
            self.assertIn("Updated", panel.updated_label.text())
        finally:
            panel.close()

    def test_missing_values_are_explicit(self):
        panel = ContextPanel(lambda: {}, refresh_interval_ms=60000)
        try:
            labels = [label.text() for label in panel.findChildren(QLabel)]
            self.assertGreaterEqual(labels.count("Not available"), 1)
        finally:
            panel.close()

    def test_refresh_handles_provider_failure(self):
        panel = ContextPanel(
            lambda: (_ for _ in ()).throw(RuntimeError("offline")),
            refresh_interval_ms=60000,
        )
        try:
            panel.refresh()
            self.assertIn("RuntimeError", panel._status_label.text())
        finally:
            panel.close()

    def test_refresh_button_reloads_snapshot(self):
        calls = []
        panel = ContextPanel(
            lambda: calls.append(True) or {"SESSION": {"Provider": "Local"}},
            refresh_interval_ms=60000,
        )
        try:
            before = len(calls)
            panel.refresh_button.click()
            self.assertEqual(len(calls), before + 1)
        finally:
            panel.close()


    def test_token_usage_and_ai_actions_sections_are_available(self):
        panel = ContextPanel(lambda: {
            "TOKEN USAGE": {
                "Input tokens": "~120",
                "Output tokens": "~80",
                "Total tokens": "~200",
                "Usage source": "Estimated",
                "Context window": "Not exposed",
            },
            "AI ACTIONS": {
                "Recent actions": ["Tool call completed"],
                "Recent warnings and errors": ["No warnings"],
                "Action count": 1,
            },
        }, refresh_interval_ms=60000)
        try:
            labels = [label.text() for label in panel.findChildren(QLabel)]
            for expected in ("TOKEN USAGE", "Input tokens", "~120", "AI ACTIONS", "Tool call completed"):
                self.assertIn(expected, labels)
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

    def test_refresh_updates_existing_labels_in_place(self):
        state = {"SESSION": {"Provider": "First"}}
        panel = ContextPanel(lambda: state, refresh_interval_ms=60000)
        try:
            provider_label = panel._value_labels[("SESSION", "Provider")]
            state["SESSION"]["Provider"] = "Second"
            panel.refresh()
            self.assertIs(provider_label, panel._value_labels[("SESSION", "Provider")])
            self.assertEqual(provider_label.text(), "Second")
        finally:
            panel.close()


if __name__ == "__main__":
    unittest.main()
