"""Focused tests for computer application rules and elevated-access expiry."""

import unittest
from unittest.mock import patch

from core.computer_permissions import application_rule_decision
from memory import config_manager


class ComputerSafetySettingsTests(unittest.TestCase):
    def test_microphone_gate_defaults_to_enabled(self):
        with patch.object(config_manager, "load_api_keys", return_value={}):
            settings = config_manager.get_computer_safety_settings()
        self.assertTrue(settings["microphone_during_screen_actions"])
        self.assertFalse(settings["application_rules_enabled"])

    def test_blocklist_matches_process_names_case_insensitively(self):
        allowed, reason = application_rule_decision(
            "chrome.exe",
            {
                "application_rules_enabled": True,
                "application_rule_mode": "blocklist",
                "applications": ["Chrome"],
            },
        )
        self.assertFalse(allowed)
        self.assertIn("blocked", reason)

    def test_allowlist_fails_closed_for_unknown_or_unlisted_apps(self):
        settings = {
            "application_rules_enabled": True,
            "application_rule_mode": "allowlist",
            "applications": ["code.exe", "discord"],
        }
        self.assertFalse(application_rule_decision("chrome.exe", settings)[0])
        self.assertFalse(application_rule_decision("", settings)[0])
        self.assertEqual(application_rule_decision("Code", settings), (True, ""))

    def test_disabled_rules_do_not_block_applications(self):
        self.assertEqual(
            application_rule_decision(
                "chrome.exe",
                {"application_rules_enabled": False, "applications": ["chrome"]},
            ),
            (True, ""),
        )

    def test_full_access_without_deadline_is_expired(self):
        legacy = {"computer_mode": "agent", "computer_access_level": "full"}
        with (
            patch.object(config_manager, "load_api_keys", return_value=legacy),
            patch.object(config_manager.time, "time", return_value=1_000),
        ):
            self.assertEqual(
                config_manager.get_computer_access_settings()["access_level"],
                "approve",
            )

    def test_full_access_expires_at_saved_deadline(self):
        config = {
            "computer_mode": "agent",
            "computer_access_level": "full",
            "computer_access_expires_at": 1_300,
        }
        with (
            patch.object(config_manager, "load_api_keys", return_value=config),
            patch.object(config_manager.time, "time", return_value=1_000),
        ):
            settings = config_manager.get_computer_access_settings()
        self.assertEqual(settings["access_level"], "full")
        self.assertEqual(settings["remaining_seconds"], 300)

    def test_full_access_grant_duration_is_clamped_to_sixty_minutes(self):
        persisted = {}
        with (
            patch.object(config_manager, "load_api_keys", return_value={"computer_access_level": "approve"}),
            patch.object(config_manager, "_patch_config", side_effect=lambda **values: persisted.update(values)),
            patch.object(config_manager.time, "time", return_value=1_000),
        ):
            config_manager.save_computer_access_settings(
                access_level="full",
                temporary_minutes=120,
            )
        self.assertEqual(persisted["computer_access_level"], "full")
        self.assertEqual(persisted["computer_access_expires_at"], 4_600)


if __name__ == "__main__":
    unittest.main()
