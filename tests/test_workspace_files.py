import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from actions import workspace_files


class WorkspaceFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "project"
        self.root.mkdir()
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text(
            "print('hello')\n", encoding="utf-8")
        (self.root / ".venv").mkdir()
        (self.root / ".venv" / "private.py").write_text(
            "ignored", encoding="utf-8")
        self.workspace_patch = patch.object(
            workspace_files, "_workspace", return_value=self.root)
        self.workspace_patch.start()

    def tearDown(self):
        self.workspace_patch.stop()
        self.temp_dir.cleanup()

    def test_lists_project_files_and_skips_generated_directories(self):
        result = workspace_files.workspace_files({"action": "list"})
        self.assertIn("src\\main.py", result.replace("/", "\\"))
        self.assertNotIn("private.py", result)

    def test_reads_project_relative_file(self):
        result = workspace_files.workspace_files(
            {"action": "read", "path": "src/main.py"})
        self.assertIn("print('hello')", result)

    def test_rejects_paths_outside_the_selected_project(self):
        outside = Path(self.temp_dir.name) / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        result = workspace_files.workspace_files(
            {"action": "read", "path": str(outside)})
        self.assertIn("outside the selected project", result)
        self.assertNotIn("secret", result)

    def test_reads_current_file_from_saved_workspace_relative_path(self):
        with patch(
            "memory.config_manager.get_active_project_file",
            return_value="src/main.py",
        ):
            result = workspace_files.workspace_files(
                {"action": "read_current"})
        self.assertIn("print('hello')", result)

    def test_reports_no_selected_project(self):
        with patch.object(workspace_files, "_workspace", return_value=None):
            result = workspace_files.workspace_files({"action": "list"})
        self.assertIn("No project folder is selected", result)


if __name__ == "__main__":
    unittest.main()
