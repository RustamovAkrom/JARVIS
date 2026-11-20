import threading
import time
import unittest
from unittest.mock import patch

from plugins import printer_control


class PrinterLifecycleLockTests(unittest.TestCase):
    def _assert_serialized(self, wrapper_name, unlocked_name):
        active = 0
        maximum = 0
        guard = threading.Lock()

        def operation(*args, **kwargs):
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.03)
            with guard:
                active -= 1

        wrapper = getattr(printer_control, wrapper_name)
        with patch.object(printer_control, unlocked_name, side_effect=operation):
            threads = [threading.Thread(target=wrapper, args=(None,)) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(1)
                self.assertFalse(thread.is_alive())
        self.assertEqual(maximum, 1)

    def test_monitor_start_requests_are_serialized(self):
        self._assert_serialized("_autostart_monitor", "_autostart_monitor_unlocked")

    def test_camera_start_requests_are_serialized(self):
        self._assert_serialized("_start_camera", "_start_camera_unlocked")


if __name__ == "__main__":
    unittest.main()
