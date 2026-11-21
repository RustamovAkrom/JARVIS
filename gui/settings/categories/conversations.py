"""Privacy controls for local conversation recall and chat history."""
from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtWidgets import (
    QCheckBox,
    QLabel,
    QSpinBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)

from gui.components.settings import SettingRow, SettingsSection
from gui.themes.tokens import C, Space
from memory.config_manager import get_conversation_memory, save_conversation_memory
from memory import conversations
from core.runtime_paths import PATHS
from core.user_data import update_json
from gui.app.chat_store import ChatStore


CHAT_STORE_FILE = PATHS.config_dir / "ui_chats.json"
LEGACY_CHAT_FILE = PATHS.config_dir / "ui_chat.json"
LEGACY_HISTORY_FILE = PATHS.config_dir / "ui_history.json"


def _clear_legacy_file(path: Path) -> None:
    """Clear a legacy transcript file without deleting unrelated JSON keys."""
    def replace(data: dict) -> None:
        data["turns"] = []
    update_json(path, replace)


class ConversationMemoryPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.LG)

        privacy = SettingsSection(
            "Conversation recall",
            "Local transcript storage is opt-in. Stored files remain on this device; "
            "when you ask JARVIS to recall something, matching excerpts are sent "
            "to the active AI provider to answer.",
        )
        self.enabled = QCheckBox("Store new conversations locally")
        self.enabled.setObjectName("conversation_memory_enabled")
        self.enabled.setAccessibleName("Store new conversations locally")
        self.enabled.setChecked(get_conversation_memory()["enabled"])
        self.enabled.toggled.connect(self._toggle)
        privacy.add_row(
            SettingRow(
                "Local history",
                "Save user and JARVIS transcript text for later search.",
                self.enabled,
            )
        )

        self.days = QSpinBox()
        self.days.setObjectName("conversation_memory_retention")
        self.days.setRange(1, 3650)
        self.days.setSuffix(" days")
        self.days.setValue(get_conversation_memory()["days"])
        self.days.valueChanged.connect(self._retention)
        privacy.add_row(
            SettingRow(
                "Retention period",
                "Older conversation files are automatically deleted.",
                self.days,
            )
        )
        layout.addWidget(privacy)

        management = SettingsSection(
            "Stored history",
            "Review storage statistics or permanently remove all conversation history.",
        )
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color:{C.TEXT_MED};")
        management.add_row(SettingRow("Storage", "", self.status))

        self.delete_button = QPushButton("Delete all conversation history")
        self.delete_button.setObjectName("conversation_memory_delete_all")
        self.delete_button.clicked.connect(self._delete_all)
        management.add_row(
            SettingRow(
                "Privacy",
                "Delete archived chats, the active chat, legacy history, and local recall transcripts. Saved memory is not affected.",
                self.delete_button,
            )
        )
        layout.addWidget(management)
        self.refresh()
        layout.addStretch(1)

    def _toggle(self, enabled: bool) -> None:
        save_conversation_memory(enabled=enabled)
        conversations.refresh_settings()
        self.refresh()

    def _retention(self, days: int) -> None:
        save_conversation_memory(days=days)
        conversations.refresh_settings()
        conversations.flush()
        conversations.prune(days)
        self.refresh()

    def _delete_all(self) -> None:
        result = QMessageBox.question(
            self,
            "Delete conversation history",
            "Permanently delete all archived chats, the active chat, legacy chat files, "
            "and local conversation-recall transcripts? Saved memories will remain.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if result != QMessageBox.StandardButton.Yes:
            return

        # Stop pending transcript writes before removing files. A timeout is
        # treated as a failure to avoid reporting a successful deletion while
        # a queued writer could recreate history afterward.
        if not conversations.flush(timeout=10.0):
            QMessageBox.warning(
                self,
                "Deletion incomplete",
                "Conversation writes are still pending. Please try again.",
            )
            return

        try:
            store = ChatStore(
                CHAT_STORE_FILE,
                legacy_chat_path=LEGACY_CHAT_FILE,
                legacy_history_path=LEGACY_HISTORY_FILE,
            )
            store.clear_all()
            _clear_legacy_file(LEGACY_CHAT_FILE)
            _clear_legacy_file(LEGACY_HISTORY_FILE)
            deleted_recall_files = conversations.forget(everything=True)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Could not delete conversation history",
                f"History deletion failed: {type(exc).__name__}: {exc}",
            )
            self.refresh()
            return

        self.refresh()
        self.status.setText(
            f"All chats deleted. Removed {deleted_recall_files} local recall file(s)."
        )

    def refresh(self) -> None:
        stats = conversations.stats()
        self.status.setText(
            f"{stats['exchanges']} recall exchanges across "
            f"{stats['days_with_conversations']} days; dropped writes: "
            f"{stats.get('dropped_writes', 0)}. "
            + (
                f"First: {stats['first']} · Last: {stats['last']}."
                if stats["first"]
                else "No stored history."
            )
        )
