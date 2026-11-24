import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel
from gui.panels.context import ContextPanel


class ContextPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_renders_session_context_and_missing_values_safely(self):
        panel = ContextPanel(lambda: {
            "SESSION": {"Provider": "OpenAI", "Model": "gpt-model", "Execution mode": "Online", "Active agent": "general"},
            "WORKSPACE": {"Active project": "E:/JARVIS", "Attached files": ["README.md"]},
            "TOOLS & ACCESS": {"Connected tools": ["GitHub"], "Permissions": {"read": True}},
            "MEMORY": {"Memory status": True, "Context usage": "4k / 32k"},
        })
        try:
            labels = [label.text() for label in panel.findChildren(QLabel)]
            for expected in ("CURRENT SESSION", "OpenAI", "gpt-model", "E:/JARVIS", "GitHub", "4k / 32k"):
                self.assertIn(expected, labels)
        finally:
            panel.close()

    def test_refresh_handles_provider_failure(self):
        panel = ContextPanel(lambda: (_ for _ in ()).throw(RuntimeError("offline")))
        try:
            panel.refresh()
            labels = [label.text() for label in panel.findChildren(QLabel)]
            self.assertTrue(any("Unavailable" in value for value in labels))
        finally:
            panel.close()

    def test_refresh_button_reloads_snapshot(self):
        calls = []
        panel = ContextPanel(lambda: calls.append(True) or {"SESSION": {"Provider": "Local"}})
        try:
            before = len(calls)
            panel.refresh_button.click()
            self.assertEqual(len(calls), before + 1)
        finally:
            panel.close()
