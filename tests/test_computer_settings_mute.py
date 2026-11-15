import unittest
from unittest.mock import patch

from actions import computer_settings as settings


class FakeEndpointVolume:
    def __init__(self, muted=False):
        self.muted = muted

    def GetMute(self):
        return int(self.muted)

    def SetMute(self, muted, _context):
        self.muted = bool(muted)


class ComputerSettingsMuteTests(unittest.TestCase):
    def setUp(self):
        self.endpoint = FakeEndpointVolume()
        self.patches = [
            patch.object(settings, "_OS", "Windows"),
            patch.object(settings, "_PYAUTOGUI", True),
            patch.object(settings, "_win_endpoint_volume", return_value=self.endpoint),
            patch.object(settings, "push_undo"),
        ]
        for active in self.patches:
            active.start()
        self.addCleanup(self._stop_patches)

    def _stop_patches(self):
        for active in reversed(self.patches):
            active.stop()

    def test_mute_sets_speaker_output_muted_and_verifies_state(self):
        result = settings.computer_settings({"action": "mute"})
        self.assertTrue(self.endpoint.muted)
        self.assertIn("muted=true", result.lower())
        self.assertIn("verified=true", result.lower())

    def test_unmute_sets_speaker_output_active_and_verifies_state(self):
        self.endpoint.muted = True
        result = settings.computer_settings({"action": "unmute"})
        self.assertFalse(self.endpoint.muted)
        self.assertIn("muted=false", result.lower())
        self.assertIn("verified=true", result.lower())

    def test_repeated_mute_is_idempotent(self):
        self.endpoint.muted = True
        result = settings.computer_settings({"action": "mute"})
        self.assertTrue(self.endpoint.muted)
        self.assertIn("verified=true", result.lower())

    def test_toggle_mute_inverts_the_current_mute_state(self):
        result = settings.computer_settings({"action": "toggle_mute"})
        self.assertTrue(self.endpoint.muted)
        self.assertIn("verified=true", result.lower())
        result = settings.computer_settings({"action": "toggle_mute"})
        self.assertFalse(self.endpoint.muted)
        self.assertIn("verified=true", result.lower())

    def test_unmute_alias_is_resolved_as_unmute(self):
        self.assertEqual(
            settings._detect_action("turn the sound back on")["action"],
            "unmute",
        )


if __name__ == "__main__":
    unittest.main()
