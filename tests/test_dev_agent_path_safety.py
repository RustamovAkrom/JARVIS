import tempfile
import unittest
from pathlib import Path

from plugins.dev_agent import _safe_project_path


class DevAgentPathSafetyTests(unittest.TestCase):
    def test_resolves_normal_nested_project_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = _safe_project_path(root, "src/main.py")
            self.assertEqual(target, (root / "src" / "main.py").resolve())

    def test_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for path in ("../outside.py", "src/../../outside.py", "..\\outside.py"):
                with self.subTest(path=path):
                    with self.assertRaises(ValueError):
                        _safe_project_path(root, path)

    def test_rejects_absolute_and_drive_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for path in ("/tmp/outside.py", "C:\\outside.py"):
                with self.subTest(path=path):
                    with self.assertRaises(ValueError):
                        _safe_project_path(root, path)

    def test_rejects_symlink_escape_when_supported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root.parent / (root.name + "-outside")
            outside.mkdir()
            try:
                (root / "link").symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is unavailable")
            try:
                with self.assertRaises(ValueError):
                    _safe_project_path(root, "link/escaped.py")
            finally:
                outside.rmdir()


if __name__ == "__main__":
    unittest.main()
