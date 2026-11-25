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



def test_delete_all_only_removes_local_recall_history(monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QMessageBox
    from gui.settings.categories import conversations as settings_module

    app = QApplication.instance() or QApplication([])
    recall_dir = tmp_path / "recall"
    recall_dir.mkdir()
    recall_file = recall_dir / "2026-11-21.jsonl"
    recall_file.write_text('{"user":"recall"}\\n', encoding="utf-8")

    monkeypatch.setattr(conversations, "DIR", recall_dir)
    monkeypatch.setattr(conversations, "flush", lambda timeout=10.0: True)
    monkeypatch.setattr(
        conversations,
        "forget",
        lambda everything=True: (recall_file.unlink(), 1)[1],
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    page = ConversationMemoryPage()
    page._delete_all()

    assert not recall_file.exists()
    assert "Chat archive is unchanged" in page.status.text()
