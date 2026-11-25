import os
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from gui.panels.reminders import RemindersPanel


class RemindersPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _reminder(reminder_id, minutes, message):
        due = datetime.now() + timedelta(minutes=minutes)
        return {
            "id": reminder_id,
            "due": due.timestamp(),
            "message": message,
        }

    def test_refresh_preserves_selected_reminder_and_cancel_action(self):
        panel = RemindersPanel()
        try:
            first = self._reminder("first", 20, "First reminder")
            second = self._reminder("second", 10, "Second reminder")
            panel.set_reminders([first, second])
            self.assertEqual(
                panel.list_widget.item(0).data(Qt.ItemDataRole.UserRole),
                "second",
            )
            selected = panel.list_widget.item(1)
            panel.list_widget.setCurrentItem(selected)
            self.assertTrue(panel.cancel_button.isEnabled())

            emitted = []
            panel.cancel_requested.connect(emitted.append)
            panel.set_reminders([second, first])

            self.assertEqual(
                panel.list_widget.currentItem().data(Qt.ItemDataRole.UserRole),
                "first",
            )
            self.assertTrue(panel.cancel_button.isEnabled())
            panel.cancel_button.click()
            self.assertEqual(emitted, ["first"])
        finally:
            panel.close()

    def test_empty_and_invalid_snapshots_disable_cancel(self):
        panel = RemindersPanel()
        try:
            panel.set_reminders([
                None,
                {},
                {"id": "bad", "due": "not-a-timestamp", "message": "Bad"},
                {"id": "past", "due": 1, "message": "Expired"},
            ])
            self.assertEqual(panel.list_widget.count(), 1)
            self.assertEqual(panel.list_widget.item(0).text(), "No upcoming reminders.")
            self.assertFalse(panel.cancel_button.isEnabled())

            panel.set_reminders([self._reminder("valid", 15, "Valid reminder")])
            self.assertEqual(panel.list_widget.count(), 1)
            self.assertFalse(panel.cancel_button.isEnabled())
            panel.list_widget.setCurrentRow(0)
            self.assertTrue(panel.cancel_button.isEnabled())

            panel.set_reminders([])
            self.assertFalse(panel.cancel_button.isEnabled())
            self.assertIsNone(panel.list_widget.currentItem().data(Qt.ItemDataRole.UserRole))
        finally:
            panel.close()


if __name__ == "__main__":
    unittest.main()
