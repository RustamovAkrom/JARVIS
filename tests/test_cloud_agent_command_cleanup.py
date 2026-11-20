import unittest
from unittest.mock import Mock, patch

from plugins import cloud_agent


class CloudAgentCommandCleanupTests(unittest.TestCase):
    def test_phonegate_is_ended_when_command_handler_raises(self):
        player = Mock()
        player.on_text_command.side_effect = RuntimeError("handler failed")
        client = Mock()
        gate = Mock()

        with patch.object(cloud_agent, "_phone_control", return_value=True), \
             patch.object(cloud_agent, "_cfg", return_value={"accept_commands": True}), \
             patch.object(cloud_agent, "_live") as live, \
             patch.object(cloud_agent, "_post") as post, \
             patch("core.phonegate", gate):
            live.wait.return_value = True
            cloud_agent._run_command(player, client, {
                "kind": "text", "id": "cmd-1", "chat": "chat-1", "text": "do something"
            })

        gate.begin.assert_called_once_with("chat-1", "do something")
        gate.end.assert_called_once_with("chat-1")
        post.assert_called_once()
        self.assertTrue(post.call_args.kwargs.get("done"))
        self.assertIn("handler failed", post.call_args.kwargs.get("error", ""))


if __name__ == "__main__":
    unittest.main()
