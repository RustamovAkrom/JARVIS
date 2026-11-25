import json
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from actions import reminder


class ReminderStaleScriptCleanupTests(unittest.TestCase):
    def _prepare(self, root: Path):
        databases = root / "databases"
        config = root / "config"
        scripts = databases / "reminders"
        scripts.mkdir(parents=True)
        config.mkdir(parents=True)
        row = {
            "id": "old-reminder",
            "due": time.time() - 31 * 24 * 60 * 60,
            "message": "Old reminder",
            "job_id": "at:123",
            "platform": "linux",
        }
        (config / "ui_reminders.json").write_text(
            json.dumps({"reminders": [row]}), encoding="utf-8"
        )
        script = scripts / "old-reminder.py"
        script.write_text("stale", encoding="utf-8")
        paths = types.SimpleNamespace(
            databases_dir=databases,
            config_dir=config,
        )
        return paths, config, script

    @staticmethod
    def _result(code=0, stdout="", stderr=""):
        return types.SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)

    def test_old_reminder_is_removed_when_at_scheduler_confirms_absence(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, config, script = self._prepare(Path(tmp))
            with (
                patch.object(reminder, "PATHS", paths),
                patch("actions.reminder.subprocess.run", side_effect=[
                    self._result(1, stderr="job not found"),
                    self._result(0, stdout=""),
                ]),
            ):
                rows = reminder.list_reminders()

            self.assertEqual(rows, [])
            self.assertFalse(script.exists())
            data = json.loads((config / "ui_reminders.json").read_text(encoding="utf-8"))
            self.assertEqual(data["reminders"], [])

    def test_old_reminder_is_preserved_when_at_scheduler_still_has_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, config, script = self._prepare(Path(tmp))
            with (
                patch.object(reminder, "PATHS", paths),
                patch("actions.reminder.subprocess.run", side_effect=[
                    self._result(1, stderr="job still exists"),
                    self._result(0, stdout="123 2026-11-21 12:00 a user"),
                ]),
            ):
                rows = reminder.list_reminders()

            self.assertEqual(len(rows), 1)
            self.assertTrue(script.exists())
            data = json.loads((config / "ui_reminders.json").read_text(encoding="utf-8"))
            self.assertEqual(len(data["reminders"]), 1)


if __name__ == "__main__":
    unittest.main()
