import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins import _video_core


class VideoLocalFileLimitTests(unittest.TestCase):
    def test_oversized_local_media_is_rejected_before_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.mp4"
            path.write_bytes(b"x" * 11)
            with patch.object(_video_core, "MAX_LOCAL_FILE", 10), \
                 patch.object(_video_core, "_probe_seconds") as probe:
                with self.assertRaises(_video_core.VideoError) as caught:
                    _video_core.read_file(str(path), max_seconds=0, engine_factory=lambda: object())
            self.assertIn("too large", str(caught.exception))
            probe.assert_not_called()

    def test_file_at_limit_is_allowed_to_continue(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "small.mp4"
            path.write_bytes(b"x" * 10)
            with patch.object(_video_core, "MAX_LOCAL_FILE", 10), \
                 patch.object(_video_core, "_probe_seconds", return_value=0), \
                 patch.object(_video_core, "transcribe", return_value=([], "en")):
                with self.assertRaises(_video_core.VideoError) as caught:
                    _video_core.read_file(str(path), max_seconds=0, engine_factory=lambda: object())
            self.assertIn("nothing was said", str(caught.exception))

    def test_missing_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.mp4"
            with self.assertRaises(_video_core.VideoError):
                _video_core.read_file(str(path), max_seconds=0, engine_factory=lambda: object())


if __name__ == "__main__":
    unittest.main()
