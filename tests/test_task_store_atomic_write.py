import json
import tempfile
import threading
import unittest
from pathlib import Path

from core.tasks.store import write_json


class AtomicTaskStoreWriteTests(unittest.TestCase):
    def test_concurrent_writes_never_leave_invalid_json_or_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "checkpoint.json"
            errors = []

            def writer(index):
                try:
                    for sequence in range(30):
                        write_json(target, {"writer": index, "sequence": sequence,
                                            "payload": "x" * 4096})
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

            self.assertFalse(errors)
            self.assertTrue(target.exists())
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertIn(payload["writer"], range(4))
            self.assertIn(payload["sequence"], range(30))
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
