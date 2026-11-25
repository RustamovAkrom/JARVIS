import tempfile
import unittest
from pathlib import Path

from core.tasks.engine import TaskEngine


class DurableToolLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.tasks_dir = root / "tasks"
        self.reports_dir = root / "reports"
        self.engines = [self._make_engine()]
        self.task = self.engines[0].start("Test durable tool execution")

    def _make_engine(self):
        return TaskEngine(
            root=self.tasks_dir,
            reports_dir=self.reports_dir,
            agents_dirs=[],
            autostart=False,
            log=lambda *_args: None,
        )

    def tearDown(self):
        for engine in self.engines:
            engine.shutdown(wait=0)
        self.tmp.cleanup()

    def test_completed_result_is_reused_after_engine_restart(self):
        calls = []

        def execute():
            calls.append("ran")
            return "file was written"

        result = self.engines[0].execute_tool_once(
            self.task["id"],
            "main:3",
            "write_file",
            {"path": "notes/result.txt", "content": "hello"},
            execute,
        )
        restarted = self._make_engine()
        self.engines.append(restarted)
        replay = restarted.execute_tool_once(
            self.task["id"],
            "main:3",
            "write_file",
            {"path": "notes/result.txt", "content": "hello"},
            lambda: calls.append("duplicate") or "wrong result",
        )

        self.assertEqual(result, "file was written")
        self.assertEqual(replay, "file was written")
        self.assertEqual(calls, ["ran"])

    def test_ambiguous_in_flight_call_is_not_reexecuted(self):
        calls = []

        def interrupted():
            calls.append("first")
            raise RuntimeError("interrupted after side effect")

        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            self.engines[0].execute_tool_once(
                self.task["id"],
                "main:4",
                "write_file",
                {"path": "notes/result.txt", "content": "hello"},
                interrupted,
            )

        replay = self.engines[0].execute_tool_once(
            self.task["id"],
            "main:4",
            "write_file",
            {"path": "notes/result.txt", "content": "hello"},
            lambda: calls.append("duplicate") or "wrong result",
        )

        self.assertIn("NOT RE-EXECUTED", replay)
        self.assertEqual(calls, ["first"])
        ledger = self.engines[0].get(self.task["id"])["tool_ledger"]
        self.assertEqual(next(iter(ledger.values()))["status"], "uncertain")


if __name__ == "__main__":
    unittest.main()
