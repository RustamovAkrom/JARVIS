import unittest
from unittest.mock import patch

from actions.computer_control import computer_control
from memory.config_manager import DEFAULT_COMPUTER_PERMISSIONS


class ComputerControlPermissionTests(unittest.TestCase):
    def test_click_is_denied_by_default_without_calling_mouse(self):
        with (
            patch(
                "memory.config_manager.get_computer_permissions",
                return_value=dict(DEFAULT_COMPUTER_PERMISSIONS),
            ),
            patch("actions.computer_control._click") as click,
        ):
            result = computer_control({"action": "click", "x": 20, "y": 30})
        self.assertIn("Permission denied", result)
        click.assert_not_called()

    def test_screen_click_requires_discovery_and_click_permissions(self):
        permissions = dict(DEFAULT_COMPUTER_PERMISSIONS, click=True)
        with (
            patch(
                "memory.config_manager.get_computer_permissions",
                return_value=permissions,
            ),
            patch("actions.computer_control._screen_find") as find,
        ):
            result = computer_control(
                {"action": "screen_click", "description": "Save button"}
            )
        self.assertIn("Permission denied", result)
        find.assert_not_called()

    def test_explicitly_enabled_click_executes(self):
        permissions = dict(DEFAULT_COMPUTER_PERMISSIONS, click=True)
        with (
            patch(
                "memory.config_manager.get_computer_permissions",
                return_value=permissions,
            ),
            patch("actions.computer_control._click", return_value="clicked") as click,
            patch("builtins.print"),
        ):
            result = computer_control({"action": "click", "x": 20, "y": 30})
        self.assertEqual(result, "clicked")
        click.assert_called_once_with(20, 30, "left", 1)


    def test_quit_app_alias_cannot_bypass_close_permission(self):
        with (
            patch(
                "memory.config_manager.get_computer_permissions",
                return_value=dict(DEFAULT_COMPUTER_PERMISSIONS),
            ),
            patch("actions.computer_control._close_app") as close_app,
        ):
            result = computer_control(
                {"action": "quit_app", "app_name": "Example App"}
            )
        self.assertIn("Permission denied", result)
        self.assertIn("close_app", result)
        close_app.assert_not_called()


if __name__ == "__main__":
    unittest.main()
