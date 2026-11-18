from __future__ import annotations

import importlib
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


class LazyImportTests(unittest.TestCase):
    def test_lazy_module_defers_import_and_resolves_attribute(self):
        lazy = importlib.import_module("core.lazyimport").LazyModule("math")
        self.assertIn("not yet imported", repr(lazy))
        self.assertEqual(lazy.sqrt(81), 9)
        self.assertIn("loaded", repr(lazy))

    def test_installed_does_not_raise_for_missing_module(self):
        module = importlib.import_module("core.lazyimport")
        self.assertFalse(module.installed("__jarvis_missing_test_package__"))


class InputLockTests(unittest.TestCase):
    def setUp(self):
        self.mod = importlib.import_module("core.inputlock")
        self.mod.clear()
        self.mod.configure_fallback(None)
        self.mod.unregister_interface()

    def tearDown(self):
        self.mod.clear()
        self.mod.unregister_interface()

    def test_held_alias_matches_is_held(self):
        self.assertFalse(self.mod.held())
        self.mod.hold("test", fallback=False)
        self.assertTrue(self.mod.held())
        self.assertEqual(self.mod.held(), self.mod.is_held())

    def test_interface_waiter_is_notified(self):
        result = []
        thread = threading.Thread(target=lambda: result.append(self.mod.interface(0.5)))
        thread.start()
        marker = object()
        self.mod.register_interface(marker)
        thread.join(1)
        self.assertEqual(result, [marker])


class CameraCompatibilityTests(unittest.TestCase):
    def test_legacy_constructor_open_and_cap_proxy(self):
        cameras = importlib.import_module("core.cameras")

        class FakeDevice:
            index = 0
            _closed = False
            _capture = object()
            _condition = threading.RLock()
            _capture_lock = threading.RLock()
            _width = _height = 0
            _fps = 0.0
            _last_error = None

            def acquire(self): pass
            def configure(self, width, height, fps):
                self.requested = (width, height, fps)
            def info(self):
                return cameras.CameraInfo(0, "Fake", 1280, 720, 30, True, 1)

        class FakeManager:
            def __init__(self): self.device = FakeDevice()
            def acquire(self, index): return self.device
            def release(self, device): pass

        class FakeCapture:
            def __init__(self): self.values = {}
            def set(self, prop, value): self.values[prop] = value; return True
            def get(self, prop): return self.values.get(prop, 0)

        fake_manager = FakeManager()
        fake_manager.device._capture = FakeCapture()
        with patch.object(cameras, "_manager", fake_manager), patch.object(cameras, "_configured_index", return_value=0):
            cam = cameras.Camera(None, fast=True, want=(1280, 720, 30))
            self.assertTrue(cam.open())
            self.assertEqual(cam.how, "shared")
            self.assertTrue(cam.cap.set(3, 640))
            self.assertEqual(cam.cap.get(3), 640.0)
            cam.close()



class CameraCaptureBackendTests(unittest.TestCase):
    def test_closed_timeout_capture_retries_without_open_parameters(self):
        cameras = importlib.import_module("core.cameras")

        class FakeCapture:
            def __init__(self, opened):
                self.opened = opened
                self.released = False
                self.values = {}

            def isOpened(self):
                return self.opened

            def release(self):
                self.released = True

            def set(self, prop, value):
                self.values[prop] = value
                return True

            def get(self, prop):
                return self.values.get(prop, 0)

        captures = [FakeCapture(False), FakeCapture(True)]
        calls = []

        class FakeCV2:
            CAP_AVFOUNDATION = 1200
            CAP_PROP_OPEN_TIMEOUT_MSEC = 1
            CAP_PROP_READ_TIMEOUT_MSEC = 2
            CAP_PROP_FOURCC = 3
            CAP_PROP_FRAME_WIDTH = 4
            CAP_PROP_FRAME_HEIGHT = 5
            CAP_PROP_FPS = 6
            CAP_PROP_BUFFERSIZE = 7
            error = RuntimeError

            @staticmethod
            def VideoWriter_fourcc(*_):
                return 0

            @staticmethod
            def VideoCapture(*args):
                calls.append(args)
                return captures[len(calls) - 1]

        device = object.__new__(cameras._Device)
        device.index = 0
        device._condition = threading.Condition()
        device._capture = None
        device._width = device._height = 0
        device._fps = 0.0
        device._last_error = None
        device._blocked_by = None

        with (
            patch.object(cameras, "cv2", FakeCV2),
            patch.object(cameras.platform, "system", return_value="Darwin"),
        ):
            self.assertTrue(device._open_capture())

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][:2], (0, FakeCV2.CAP_AVFOUNDATION))
        self.assertEqual(len(calls[0]), 3)  # timeout-parameterized AVFoundation open
        self.assertEqual(calls[1], (0, FakeCV2.CAP_AVFOUNDATION))  # legacy fallback
        self.assertTrue(captures[0].released)
        self.assertIs(device._capture, captures[1])


class SecretStoreTests(unittest.TestCase):
    def test_rejects_unrecognised_ciphertext(self):
        secretstore = importlib.import_module("core.secretstore")
        self.assertIsNone(secretstore.reveal("plain-text", "face_lock.profile"))

    def test_protect_fails_closed_without_windows_dpapi(self):
        import os
        if os.name != "nt":
            secretstore = importlib.import_module("core.secretstore")
            with self.assertRaises(secretstore.SecretStoreError):
                secretstore.protect("embedding", "face_lock.profile")


if __name__ == "__main__":
    unittest.main()
