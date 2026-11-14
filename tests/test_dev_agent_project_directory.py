"""Regression tests for non-destructive DevAgent project destinations."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from actions.dev_agent import _project_directory


class DevAgentProjectDirectoryTests(unittest.TestCase):
    def test_default_destination_uses_a_new_folder_when_name_exists(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            desktop = home / "Desktop"
            existing = desktop / "demo"
            existing.mkdir(parents=True)
            sentinel = existing / "keep.txt"
            sentinel.write_text("preserve", encoding="utf-8")

            with patch("actions.dev_agent.Path.home", return_value=home):
                destination = _project_directory("", "demo", "demo")

            self.assertEqual(destination, desktop / "demo-2")
            self.assertTrue(destination.is_dir())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_explicit_nonempty_destination_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            existing = Path(temporary) / "project"
            existing.mkdir()
            sentinel = existing / "important.py"
            sentinel.write_text("do_not_overwrite = True", encoding="utf-8")

            with self.assertRaisesRegex(FileExistsError, "not empty"):
                _project_directory(str(existing), "project", "project")

            self.assertEqual(
                sentinel.read_text(encoding="utf-8"),
                "do_not_overwrite = True",
            )

    def test_explicit_empty_destination_can_be_used(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "empty-project"
            destination.mkdir()

            selected = _project_directory(str(destination), "project", "project")

            self.assertEqual(selected, destination)
            self.assertEqual(list(destination.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
