import unittest

from core.sensor_registry import SensorRegistry, SensorSpec, SensorState


class SensorRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = SensorRegistry()

    def test_register_and_capability_discovery(self):
        spec = SensorSpec("camera.front", "camera", {"video", "presence"})
        self.registry.register(spec)
        rows = self.registry.find_by_capability("presence")
        self.assertEqual([row.spec.sensor_id for row in rows], ["camera.front"])
        self.assertEqual(rows[0].state, SensorState.REGISTERED)

    def test_lifecycle_transitions_and_invalid_transition(self):
        self.registry.register(SensorSpec("mic", "audio"))
        self.registry.transition("mic", SensorState.STARTING)
        self.registry.transition("mic", SensorState.ACTIVE)
        with self.assertRaises(ValueError):
            self.registry.transition("mic", SensorState.REGISTERED)
        self.registry.transition("mic", SensorState.STOPPING)
        self.registry.transition("mic", SensorState.REGISTERED)

    def test_stop_callback_and_error_state(self):
        called = []
        self.registry.register(SensorSpec("screen", "screen"), stop=lambda: called.append(True))
        self.registry.transition("screen", SensorState.STARTING)
        self.registry.transition("screen", SensorState.ACTIVE)
        self.assertTrue(self.registry.request_stop("screen"))
        self.assertEqual(called, [True])
        self.assertEqual(self.registry.get("screen").state, SensorState.STOPPING)

    def test_stop_callback_failure_marks_error(self):
        def fail():
            raise RuntimeError("device failure")
        self.registry.register(SensorSpec("camera", "camera"), stop=fail)
        self.registry.transition("camera", SensorState.STARTING)
        self.registry.transition("camera", SensorState.ACTIVE)
        self.assertFalse(self.registry.request_stop("camera"))
        snapshot = self.registry.get("camera")
        self.assertEqual(snapshot.state, SensorState.ERROR)
        self.assertIn("device failure", snapshot.error)

    def test_duplicate_registration_is_rejected(self):
        spec = SensorSpec("sensor", "camera")
        self.registry.register(spec)
        with self.assertRaises(ValueError):
            self.registry.register(spec)

    def test_unregister_active_sensor_is_rejected(self):
        self.registry.register(SensorSpec("sensor", "camera"))
        self.registry.transition("sensor", SensorState.STARTING)
        self.registry.transition("sensor", SensorState.ACTIVE)
        with self.assertRaises(RuntimeError):
            self.registry.unregister("sensor")

    def test_listener_failure_does_not_break_registry(self):
        self.registry.subscribe(lambda _snapshot: (_ for _ in ()).throw(RuntimeError("observer")))
        self.registry.register(SensorSpec("sensor", "camera"))
        self.assertIsNotNone(self.registry.get("sensor"))

    def test_unsubscribe(self):
        calls = []
        unsubscribe = self.registry.subscribe(lambda _snapshot: calls.append(1))
        self.registry.register(SensorSpec("sensor", "camera"))
        unsubscribe()
        self.registry.transition("sensor", SensorState.STARTING)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
