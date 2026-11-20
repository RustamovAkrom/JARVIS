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
    def test_camera_opens_preferred_device_and_releases_capture(self):
        cameras = importlib.import_module("core.cameras")
        fake_capture = object()
        camera = cameras.Camera(preferred=0)
        with patch.object(cameras, "open_index", return_value=fake_capture), patch.object(cameras, "films", return_value=True):
            self.assertTrue(camera.open())
            self.assertIs(camera.cap, fake_capture)
            self.assertEqual(camera.index, 0)
        with patch.object(fake_capture, "release", create=True):
            camera.release()
        self.assertIsNone(camera.cap)


class CameraCaptureBackendTests(unittest.TestCase):
    def test_open_index_uses_platform_backend_and_releases_failed_capture(self):
        cameras = importlib.import_module("core.cameras")

        class FakeCapture:
            def __init__(self, opened):
                self.opened = opened
                self.released = False
            def isOpened(self):
                return self.opened
            def release(self):
                self.released = True

        capture = FakeCapture(False)
        calls = []
        class FakeCV2:
            CAP_ANY = 0
            CAP_DSHOW = 700
            CAP_V4L2 = 800
            @staticmethod
            def VideoCapture(*args):
                calls.append(args)
                return capture

        with patch.object(cameras, "cv2", FakeCV2, create=True), patch.object(cameras.sys, "platform", "darwin"):
            self.assertIsNone(cameras.open_index(3))
        self.assertEqual(calls, [(3, FakeCV2.CAP_ANY)])
        self.assertTrue(capture.released)


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
