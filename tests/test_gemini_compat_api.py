"""Run from the JARVIS repository root after applying the compatibility fix."""
import unittest
from unittest.mock import patch

from core import gemini


class GeminiBuildGuideCompatibilityTests(unittest.TestCase):
    def test_usable_rungs_preserves_order_and_skips_cooling_models(self):
        with patch.object(
            gemini, "_cooling", side_effect=lambda name: name == "cooling-model"
        ):
            self.assertEqual(
                ["gemini-strong", "gemini-lite"],
                gemini.usable_rungs(
                    ["gemini-strong", "cooling-model", "gemini-lite"]
                ),
            )

    def test_usable_rungs_accepts_single_model_string(self):
        with patch.object(gemini, "_cooling", return_value=False):
            self.assertEqual(["gemini-test"], gemini.usable_rungs("gemini-test"))

    def test_usable_rungs_handles_empty_input(self):
        self.assertEqual([], gemini.usable_rungs(None))
        self.assertEqual([], gemini.usable_rungs([]))

    def test_invalid_api_key_is_classified_as_key_error(self):
        self.assertTrue(
            gemini.is_key_error(
                "400 INVALID_ARGUMENT: API key not valid. Please pass a valid API key."
            )
        )
        self.assertTrue(gemini.is_key_error("401 UNAUTHENTICATED"))
        self.assertTrue(gemini.is_key_error("API_KEY_INVALID"))

    def test_model_access_denial_is_not_a_shared_key_error(self):
        self.assertFalse(
            gemini.is_key_error("403 PERMISSION_DENIED: this model is not enabled")
        )
        self.assertFalse(gemini.is_key_error("404 NOT_FOUND: model not found"))


if __name__ == "__main__":
    unittest.main()
