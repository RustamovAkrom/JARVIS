"""Regression tests for background agent profiles and tool isolation."""

import tempfile
import unittest
from pathlib import Path

from core.tasks.agents import KNOWN_TOOLS, FALLBACK, load, parse
from core.tasks.tools import TaskTools


class AgentProfileTests(unittest.TestCase):
    def test_profile_rejects_unknown_tools(self):
        source = """---
name: custom
tools: search_web, shell
minutes: 5
rounds: 20
---
Do the task.
"""
        with self.assertRaisesRegex(ValueError, "unknown tools: shell"):
            parse(source)

    def test_profile_deduplicates_tools_and_clamps_budget(self):
        source = """---
name: custom
tools: search_web, search_web, notes
minutes: 9999
rounds: 9999
---
Do the task.
"""
        profile = parse(source)
        self.assertEqual(profile.tools, ["search_web", "notes"])
        self.assertEqual(profile.minutes, 720)
        self.assertEqual(profile.rounds, 400)

    def test_loader_skips_invalid_profiles_and_keeps_valid_ones(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "invalid.md").write_text(
                "---\\nname: bad\\ntools: shell\\n---\\nNo instructions.\\n",
                encoding="utf-8",
            )
            (root / "valid.md").write_text(
                "---\\nname: valid\\ntools: notes\\n---\\nKeep notes.\\n",
                encoding="utf-8",
            )
            logs = []
            profiles = load([root], log=logs.append)
            self.assertEqual(list(profiles), ["valid"])
            self.assertTrue(logs)
            self.assertNotIn(FALLBACK.name, profiles)

    def test_loader_uses_fallback_when_no_profile_is_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "invalid.md").write_text("not a profile", encoding="utf-8")
            profiles = load([root], log=lambda _message: None)
            self.assertEqual(profiles, {"general": FALLBACK})

    def test_user_profile_overrides_bundled_profile_by_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "bundled"
            user = Path(tmp) / "user"
            bundled.mkdir()
            user.mkdir()
            source = "---\\nname: custom\\ntools: notes\\n---\\nBundled.\\n"
            override = "---\\nname: custom\\ntools: write_file\\n---\\nUser override.\\n"
            (bundled / "custom.md").write_text(source, encoding="utf-8")
            (user / "custom.md").write_text(override, encoding="utf-8")
            profiles = load([bundled, user], log=lambda _message: None)
            self.assertEqual(profiles["custom"].tools, ["write_file"])
            self.assertEqual(profiles["custom"].instructions, "User override.")


class AgentToolIsolationTests(unittest.TestCase):
    def test_web_capable_agent_never_gets_memory_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = TaskTools(Path(tmp), ["search_web", "fetch_page", "recall_memory"])
            self.assertIn("search_web", tools.names)
            self.assertIn("fetch_page", tools.names)
            self.assertNotIn("recall_memory", tools.names)
            self.assertTrue(tools.has_web)

    def test_memory_only_agent_can_recall_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = TaskTools(Path(tmp), ["recall_memory", "notes"])
            self.assertIn("recall_memory", tools.names)
            self.assertFalse(tools.has_web)

    def test_unknown_requested_tools_are_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = []
            tools = TaskTools(Path(tmp), ["shell", "notes"], log=logs.append)
            self.assertEqual(tools.names, ["notes"])
            self.assertTrue(any("shell" in message for message in logs))


if __name__ == "__main__":
    unittest.main()
