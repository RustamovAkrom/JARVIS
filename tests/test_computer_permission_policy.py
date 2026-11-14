"""Regression tests for the single computer-action permission policy."""

import unittest

from core.computer_permissions import (
    normalize_computer_action,
    required_permissions_for_action,
)


class ComputerPermissionPolicyTests(unittest.TestCase):
    def test_reload_aliases_all_require_keyboard_permission(self):
        for action in ("refresh_page", "reload", "reload_n", "refresh_n", "reload_page_n"):
            with self.subTest(action=action):
                self.assertEqual(
                    required_permissions_for_action("computer_settings", action),
                    ("type",),
                )

    def test_close_application_spellings_share_one_canonical_policy(self):
        for alias in ("close_app", "close_application", "quit_app"):
            with self.subTest(alias=alias):
                self.assertEqual(
                    normalize_computer_action("computer_control", alias),
                    "close_app",
                )
                self.assertEqual(
                    required_permissions_for_action("computer_control", alias),
                    ("click",),
                )

    def test_unknown_computer_actions_do_not_gain_implicit_capabilities(self):
        self.assertEqual(
            required_permissions_for_action("computer_settings", "unknown-action"),
            (),
        )
        self.assertEqual(
            required_permissions_for_action("computer_control", "unknown-action"),
            (),
        )


if __name__ == "__main__":
    unittest.main()
