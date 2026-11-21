from PyQt6.QtWidgets import QWidget
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
import memory.config_manager as config
import memory.conversations as conversations
from gui.settings.categories.conversations import ConversationMemoryPage


def test_conversation_settings_ui_persists(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "api_keys.json")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(conversations, "DIR", tmp_path / "history")
    page = ConversationMemoryPage()
    toggle = page.findChild(QWidget, "conversation_memory_enabled")
    retention = page.findChild(QWidget, "conversation_memory_retention")
    assert toggle is not None
    assert retention is not None
    toggle.setChecked(True)
    assert config.get_conversation_memory()["enabled"] is True
    retention.setValue(30)
    assert config.get_conversation_memory()["days"] == 30


def test_delete_all_removes_chat_store_legacy_files_and_recall(monkeypatch, tmp_path):
    import json
    from pathlib import Path
    from PyQt6.QtWidgets import QMessageBox
    from gui.settings.categories import conversations as settings_module
    from gui.app.chat_store import ChatStore

    app = QApplication.instance() or QApplication([])
    store_path = tmp_path / "ui_chats.json"
    legacy_chat = tmp_path / "ui_chat.json"
    legacy_history = tmp_path / "ui_history.json"
    recall_dir = tmp_path / "recall"

    store = ChatStore(store_path)
    chat = store.get_active_chat()
    chat["turns"] = [{"id": "1", "request": "hello", "result": "world"}]
    chat["context"] = [{"role": "user", "text": "hello"}]
    store.save_chat(chat)

    legacy_chat.write_text(json.dumps({"turns": [{"request": "legacy"}]}), encoding="utf-8")
    legacy_history.write_text(json.dumps({"turns": [{"request": "archived"}]}), encoding="utf-8")
    recall_dir.mkdir()
    recall_file = recall_dir / "2026-11-21.jsonl"
    recall_file.write_text('{"user":"recall"}\\n', encoding="utf-8")

    monkeypatch.setattr(settings_module, "CHAT_STORE_FILE", store_path)
    monkeypatch.setattr(settings_module, "LEGACY_CHAT_FILE", legacy_chat)
    monkeypatch.setattr(settings_module, "LEGACY_HISTORY_FILE", legacy_history)
    monkeypatch.setattr(conversations, "DIR", recall_dir)
    monkeypatch.setattr(conversations, "flush", lambda timeout=3.0: True)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    page = ConversationMemoryPage()
    page._delete_all()

    cleared = ChatStore(store_path).list_chats()
    assert len(cleared) == 1
    assert cleared[0]["turns"] == []
    assert cleared[0]["context"] == []
    assert json.loads(legacy_chat.read_text(encoding="utf-8"))["turns"] == []
    assert json.loads(legacy_history.read_text(encoding="utf-8"))["turns"] == []
    assert not recall_file.exists()
    assert "All chats deleted" in page.status.text()
