import unittest
from unittest.mock import patch

from plugins import video_summarizer


class VideoSummarizerWorkerTests(unittest.TestCase):
    def test_background_answer_worker_start_failure_returns_false(self):
        transcript = object()
        with patch("threading.Thread.start", side_effect=RuntimeError("thread limit")):
            started = video_summarizer._finish_on_screen(
                None, transcript, "summarize", ""
            )
        self.assertFalse(started)


if __name__ == "__main__":
    unittest.main()
