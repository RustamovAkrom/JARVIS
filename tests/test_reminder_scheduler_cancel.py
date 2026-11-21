import unittest
from types import SimpleNamespace
from unittest.mock import patch

from actions import reminder


class ReminderSchedulerCancellationTests(unittest.TestCase):
    def setUp(self):
        self.row = {
            "id": "reminder-1",
            "job_id": "jarvis-reminder-1",
            "platform": "mac",
        }

    def _run_result(self, returncode=0, stdout="", stderr=""):
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    def test_macos_keeps_local_reminder_when_launchd_still_has_job(self):
        with (
            patch.object(reminder, "list_reminders", return_value=[self.row]),
            patch("actions.reminder.subprocess.run", side_effect=[
                self._run_result(1, stderr="unload failed"),
                self._run_result(0, stdout="123 0 com.jarvis.reminder-1"),
            ]) as run,
        ):
            cancelled, message = reminder.cancel_reminder("reminder-1")

        self.assertFalse(cancelled)
        self.assertIn("still reports", message)
        self.assertEqual(run.call_count, 2)

    def test_linux_keeps_local_reminder_when_systemd_still_active(self):
        row = dict(self.row, platform="linux", job_id="systemd:jarvis-reminder-1")
        with (
            patch.object(reminder, "list_reminders", return_value=[row]),
            patch("actions.reminder.subprocess.run", side_effect=[
                self._run_result(1, stderr="stop failed"),
                self._run_result(0, stdout="active\ninactive\n"),
            ]) as run,
        ):
            cancelled, message = reminder.cancel_reminder("reminder-1")

        self.assertFalse(cancelled)
        self.assertIn("still reports", message)
        self.assertEqual(run.call_count, 2)

    def test_linux_reports_unknown_scheduler_state_as_failure(self):
        row = dict(self.row, platform="linux", job_id="systemd:jarvis-reminder-1")
        with (
            patch.object(reminder, "list_reminders", return_value=[row]),
            patch("actions.reminder.subprocess.run", side_effect=[
                self._run_result(1, stderr="stop failed"),
                self._run_result(1, stderr="system bus unavailable"),
            ]),
        ):
            cancelled, message = reminder.cancel_reminder("reminder-1")

        self.assertFalse(cancelled)
        self.assertIn("Could not verify", message)


if __name__ == "__main__":
    unittest.main()
