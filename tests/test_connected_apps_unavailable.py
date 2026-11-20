import unittest
from unittest.mock import patch

from plugins import connected_apps


class ConnectedAppsUnavailableTests(unittest.TestCase):
    def test_describe_does_not_crash_when_backend_missing(self):
        with patch.object(connected_apps, "_backend_error", return_value="backend missing"):
            description = connected_apps.describe()
        self.assertIn("unavailable in this build", description)
        self.assertIn("backend/UI", description)

    def test_run_reports_missing_backend_without_import_crash(self):
        with patch.object(connected_apps, "_backend_error", return_value="backend missing"):
            result = connected_apps.run({"action": "connect", "app": "Notion"})
        self.assertIn("status=failed", result)
        self.assertIn("connected_apps", result)
        self.assertIn("backend missing", result)


if __name__ == "__main__":
    unittest.main()
