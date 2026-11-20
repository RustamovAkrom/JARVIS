import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins import shopping_list


class ShoppingListConcurrencyTests(unittest.TestCase):
    def test_concurrent_adds_do_not_overwrite_each_other(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "shopping_list.json"
            with patch.object(shopping_list, "STATE_FILE", state_file), \
                 patch.object(shopping_list, "_remember"):
                barrier = threading.Barrier(3)
                results = []

                def add(item):
                    barrier.wait()
                    results.append(shopping_list.run({"action": "add", "items": item}))

                threads = [
                    threading.Thread(target=add, args=("milk",)),
                    threading.Thread(target=add, args=("eggs",)),
                ]
                for thread in threads:
                    thread.start()
                barrier.wait()
                for thread in threads:
                    thread.join(2)
                    self.assertFalse(thread.is_alive())

                data = json.loads(state_file.read_text(encoding="utf-8"))
                names = {item["name"].lower() for item in data["items"]}
                self.assertEqual(names, {"milk", "eggs"})
                self.assertEqual(len(data["items"]), 2)
                self.assertEqual(len(results), 2)


if __name__ == "__main__":
    unittest.main()
