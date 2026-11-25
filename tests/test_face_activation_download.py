from __future__ import annotations

import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins import face_activation as face


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class CameraFrameValidationTests(unittest.TestCase):
    def test_valid_bgr_frame(self):
        import numpy as np
        self.assertTrue(face._valid_frame(np.zeros((480, 640, 3), dtype=np.uint8), np))

    def test_invalid_camera_frames(self):
        import numpy as np
        cases = [
            None,
            np.zeros((480, 640), dtype=np.uint8),
            np.zeros((480, 640, 4), dtype=np.uint8),
            np.zeros((1, 640, 3), dtype=np.uint8),
            np.zeros((480, 1, 3), dtype=np.uint8),
            np.empty((480, 640, 3), dtype=object),
        ]
        for frame in cases:
            with self.subTest(shape=getattr(frame, "shape", None)):
                self.assertFalse(face._valid_frame(frame, np))


class ModelDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.directory = Path(self.tempdir.name)
        self.data = b"onnx-model-payload" * 8
        self.urlopen_calls = 0
        self.counter_lock = threading.Lock()
        self.barrier = threading.Barrier(2)

    def tearDown(self):
        self.tempdir.cleanup()

    def _urlopen(self, _request, timeout=60):
        with self.counter_lock:
            self.urlopen_calls += 1
        # Force both invocations to be in the download path simultaneously.
        self.barrier.wait(timeout=3)
        return FakeResponse(self.data)

    def test_parallel_calls_use_private_download_files(self):
        results = []
        errors = []

        def worker():
            try:
                results.append(face._model_paths())
            except Exception as exc:  # pragma: no cover - assertion reports it
                errors.append(exc)

        with (
            patch.object(face, "_DATA_DIR", self.directory),
            patch.object(face, "_MODELS", {"test.onnx": ("https://example.invalid/model", 10)}),
            patch.object(face.urllib.request, "urlopen", side_effect=self._urlopen),
        ):
            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

        self.assertFalse(errors, errors)
        self.assertEqual(len(results), 2)
        final_path = self.directory / "test.onnx"
        self.assertTrue(final_path.is_file())
        self.assertEqual(final_path.read_bytes(), self.data)
        self.assertEqual(list(self.directory.glob("*.download")), [])
        self.assertEqual(list(self.directory.glob(".*.download")), [])
        self.assertEqual(self.urlopen_calls, 2)

    def test_valid_existing_model_is_reused(self):
        model = self.directory / "test.onnx"
        model.write_bytes(self.data)
        with (
            patch.object(face, "_DATA_DIR", self.directory),
            patch.object(face, "_MODELS", {"test.onnx": ("https://example.invalid/model", 10)}),
            patch.object(face.urllib.request, "urlopen") as urlopen,
        ):
            result = face._model_paths()
        self.assertEqual(result["test.onnx"], model)
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
