import unittest
from unittest.mock import patch

from core import sensors
from core.sensor_registry import SensorState


class SensorsRegistryBridgeTests(unittest.TestCase):
    def tearDown(self):
        for key in list(sensors.active().get("camera", [])) + list(sensors.active().get("screen", [])):
            sensors.stop(key)

    def test_legacy_start_is_visible_in_typed_registry(self):
        sensors.start("test_screen", "screen")
        snapshot = sensors.registry_snapshot()
        row = next(item for item in snapshot if item.spec.sensor_id == "test_screen")
        self.assertEqual(row.state, SensorState.ACTIVE)
        self.assertIn("screen", row.spec.capabilities)
        self.assertEqual(sensors.active(), {"screen": ["test_screen"]})

    def test_legacy_stop_removes_registry_entry(self):
        sensors.start("test_camera", "camera")
        sensors.stop("test_camera")
        self.assertFalse(any(item.spec.sensor_id == "test_camera" for item in sensors.registry_snapshot()))

    def test_invalid_kind_remains_ignored(self):
        before = sensors.registry_snapshot()
        sensors.start("bad", "unknown")
        self.assertEqual(before, sensors.registry_snapshot())

    def test_restarting_key_replaces_registry_entry(self):
        sensors.start("watcher", "screen")
        sensors.start("watcher", "camera")
        row = next(item for item in sensors.registry_snapshot() if item.spec.sensor_id == "watcher")
        self.assertEqual(row.spec.kind, "camera")
        self.assertEqual(sensors.active(), {"camera": ["watcher"]})


if __name__ == "__main__":
    unittest.main()
