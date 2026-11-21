from unittest.mock import patch
from plugins import connected_apps

def test_describe_survives_backend_error():
    with patch("core.connected_apps.service.service.connections",side_effect=RuntimeError("backend unavailable")):
        text=connected_apps.describe()
    assert "Connected Apps" in text

def test_run_reports_backend_error():
    with patch("core.connected_apps.service.service.connections",side_effect=RuntimeError("backend unavailable")):
        result=connected_apps.run({"action":"list_apps"})
    assert "failed" in result.lower()
    assert "backend unavailable" in result
