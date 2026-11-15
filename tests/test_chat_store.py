"""Regression tests for persistent chat session state."""

import tempfile
import unittest
from pathlib import Path

from gui.app.chat_store import ChatStore


class ChatStoreResumeHandleTests(unittest.TestCase):
    def test_save_chat_preserves_newer_resume_handle_from_store(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ChatStore(Path(directory) / "chats.json")
            chat = store.get_active_chat()
            chat_id = chat["id"]

            self.assertTrue(store.save_resume_handle("fresh-handle", chat_id))

            # Simulate a stale UI snapshot created before the receive loop
            # persisted the latest session handle.
            stale_snapshot = store.get_chat(chat_id)
            self.assertIsNotNone(stale_snapshot)
            stale_snapshot["resume_handle"] = ""
            stale_snapshot["turns"].append(
                {"id": "turn-1", "request": "hello", "result": "world"}
            )

            store.save_chat(stale_snapshot, activate=False)

            self.assertEqual(store.get_resume_handle(chat_id), "fresh-handle")
            self.assertEqual(len(store.get_chat(chat_id)["turns"]), 1)

    def test_save_chat_can_explicitly_invalidate_resume_handle(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ChatStore(Path(directory) / "chats.json")
            chat = store.get_active_chat()
            chat_id = chat["id"]
            store.save_resume_handle("old-handle", chat_id)

            snapshot = store.get_chat(chat_id)
            snapshot["resume_handle"] = ""
            store.save_chat(
                snapshot,
                activate=False,
                preserve_resume_handle=False,
            )

            self.assertIsNone(store.get_resume_handle(chat_id))

    def test_clear_chat_invalidates_resume_handle(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ChatStore(Path(directory) / "chats.json")
            chat = store.get_active_chat()
            store.save_resume_handle("old-handle", chat["id"])

            cleared = store.clear_chat(chat["id"])

            self.assertIsNotNone(cleared)
            self.assertIsNone(store.get_resume_handle(chat["id"]))
            self.assertEqual(store.get_context(chat["id"]), [])


if __name__ == "__main__":
    unittest.main()
