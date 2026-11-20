import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins import dev_agent


class DevAgentEditorLaunchTests(unittest.TestCase):
    def test_batch_editor_is_launched_through_cmd_without_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            command = r"C:\tools\editor.cmd"
            with patch.object(dev_agent, "_setting", side_effect=lambda key, default=None:
                             command if key == "editor_command" else True), \
                 patch.object(dev_agent.subprocess, "Popen") as popen, \
                 patch.object(dev_agent.time, "sleep"):
                self.assertTrue(dev_agent._open_vscode(project))
            popen.assert_called_once()
            args, kwargs = popen.call_args
            self.assertEqual(args[0], ["cmd.exe", "/c", command, str(project)])
            self.assertFalse(kwargs["shell"])

    def test_executable_editor_is_launched_directly(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            command = "editor"
            with patch.object(dev_agent, "_setting", side_effect=lambda key, default=None:
                             command if key == "editor_command" else True), \
                 patch.object(dev_agent.subprocess, "Popen") as popen, \
                 patch.object(dev_agent.time, "sleep"):
                self.assertTrue(dev_agent._open_vscode(project))
            args, kwargs = popen.call_args
            self.assertEqual(args[0], [command, str(project)])
            self.assertFalse(kwargs["shell"])


if __name__ == "__main__":
    unittest.main()
