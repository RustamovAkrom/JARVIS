import json
import tempfile
import unittest
from pathlib import Path

from core.tasks.engine import TaskEngine
from core.tasks.store import TaskStore


class TaskCheckpointRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "tasks"
        self.store = TaskStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def _task(self, status="running", attempts=0):
        task = {
            "id": "task-recovery",
            "agent": "general",
            "title": "Recovery test",
            "task": "Resume from checkpoint",
            "status": status,
            "created": 1,
            "started": 2,
            "finished": 0,
            "not_before": 0,
            "budget": {"minutes": 10, "rounds": 10},
            "used": {"rounds": 2, "seconds": 15},
            "attempts": attempts,
            "quota_until": 0,
            "wrap_up": False,
            "notes_in": [],
            "events": [],
            "files": [],
            "result": "",
            "report": "",
            "error": "",
        }
        self.store.create(task)
        return task

    def test_checkpoint_turns_survive_store_reconstruction(self):
        self._task(status="queued")
        turns = [
            {"role": "user", "text": "Research the topic"},
            {"role": "assistant", "text": "Initial findings"},
        ]
        self.store.save_turns("task-recovery", turns)

        reopened = TaskStore(self.root)
        self.assertEqual(reopened.load_turns("task-recovery"), turns)
        self.assertEqual(reopened.get("task-recovery")["status"], "queued")

    def test_interrupted_running_task_is_requeued_and_attempt_counted(self):
        self._task(status="running", attempts=1)
        self.store.save_turns("task-recovery", [{"role": "assistant", "text": "checkpoint"}])

        engine = TaskEngine(
            root=self.root,
            reports_dir=self.root / "reports",
            agents_dirs=[],
            autostart=False,
            log=lambda *_: None,
        )
        recovered = engine.get("task-recovery")
        self.assertEqual(recovered["status"], "queued")
        self.assertEqual(recovered["attempts"], 2)
        self.assertEqual(engine.store.load_turns("task-recovery"), [
            {"role": "assistant", "text": "checkpoint"}
        ])
        self.assertTrue(any("resuming after an interruption" in event["text"]
                           for event in recovered["events"]))
        engine.shutdown(wait=0)

    def test_cancel_queued_task_is_terminal_and_idempotent(self):
        self._task(status="queued")
        engine = TaskEngine(
            root=self.root,
            reports_dir=self.root / "reports",
            agents_dirs=[],
            autostart=False,
            log=lambda *_: None,
        )
        self.assertTrue(engine.cancel("task-recovery"))
        self.assertEqual(engine.get("task-recovery")["status"], "cancelled")
        self.assertFalse(engine.cancel("task-recovery"))
        engine.shutdown(wait=0)

    def test_steer_appends_note_to_active_task(self):
        self._task(status="queued")
        engine = TaskEngine(
            root=self.root,
            reports_dir=self.root / "reports",
            agents_dirs=[],
            autostart=False,
            log=lambda *_: None,
        )
        self.assertTrue(engine.steer("task-recovery", "Focus on official sources"))
        task = engine.get("task-recovery")
        self.assertEqual(task["notes_in"], ["Focus on official sources"])
        self.assertTrue(any(event["kind"] == "note" for event in task["events"]))
        self.assertFalse(engine.steer("task-recovery", "   "))
        engine.shutdown(wait=0)

    def test_recovery_fails_after_max_interrupted_attempts(self):
        self._task(status="running", attempts=3)
        engine = TaskEngine(
            root=self.root,
            reports_dir=self.root / "reports",
            agents_dirs=[],
            autostart=False,
            log=lambda *_: None,
        )
        recovered = engine.get("task-recovery")
        self.assertEqual(recovered["status"], "failed")
        self.assertIn("interrupted", recovered["error"])
        engine.shutdown(wait=0)


if __name__ == "__main__":
    unittest.main()
