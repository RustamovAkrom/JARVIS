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
