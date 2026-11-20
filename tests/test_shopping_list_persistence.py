import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins import shopping_list


class ShoppingListPersistenceTests(unittest.TestCase):
    def test_save_writes_valid_json_atomically_and_remembers_after_success(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "shopping.json"
            data = {"items": [{"name": "milk", "qty": "", "done": False}]}
            with patch.object(shopping_list, "STATE_FILE", target), \
                 patch.object(shopping_list, "_remember") as remember:
                self.assertTrue(shopping_list._save(data))
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), data)
            remember.assert_called_once_with(data)
            self.assertFalse(list(Path(temp).glob("*.tmp")))

    def test_failed_replace_does_not_mirror_memory(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "shopping.json"
            data = {"items": [{"name": "milk", "qty": "", "done": False}]}
            with patch.object(shopping_list, "STATE_FILE", target), \
                 patch.object(shopping_list, "_remember") as remember, \
                 patch("os.replace", side_effect=OSError("disk unavailable")):
                self.assertFalse(shopping_list._save(data))
            remember.assert_not_called()
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
