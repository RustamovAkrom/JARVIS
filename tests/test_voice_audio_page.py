"""Regression tests for the embedded Voice & Audio settings page."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget

from gui.settings.categories.audio import VoiceAudioPage


class VoiceAudioPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_live_voice_controls_are_mounted_in_audio_page(self):
        devices = QWidget()
        voice_controls = QWidget()

        page = VoiceAudioPage(
            devices,
            voice_controls_panel=voice_controls,
        )

        self.assertGreaterEqual(page.layout().indexOf(devices), 0)
        self.assertGreaterEqual(page.layout().indexOf(voice_controls), 0)
        self.assertNotIn(
            "voice_activation_controls",
            [widget.objectName() for widget in page.findChildren(QWidget)],
        )


if __name__ == "__main__":
    unittest.main()
