"""Privacy controls for local conversation recall."""
from PyQt6.QtWidgets import QCheckBox, QLabel, QSpinBox, QPushButton, QVBoxLayout, QWidget, QMessageBox
from gui.components.settings import SettingRow, SettingsSection
from gui.themes.tokens import C, Space
from memory.config_manager import get_conversation_memory, save_conversation_memory
from memory import conversations

class ConversationMemoryPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout=QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(Space.LG)
        privacy=SettingsSection("Conversation recall", "Local transcript search is opt-in. Raw conversations are stored on this device and are not uploaded by this feature.")
        self.enabled=QCheckBox("Store new conversations locally")
        self.enabled.setObjectName("conversation_memory_enabled")
        self.enabled.setAccessibleName("Store new conversations locally")
        self.enabled.setChecked(get_conversation_memory()["enabled"])
        self.enabled.toggled.connect(self._toggle)
        privacy.add_row(SettingRow("Local history", "Save user and JARVIS transcript text for later search.", self.enabled))
        self.days=QSpinBox()
        self.days.setObjectName("conversation_memory_retention")
        self.days.setRange(1,3650)
        self.days.setSuffix(" days")
        self.days.setValue(get_conversation_memory()["days"])
        self.days.valueChanged.connect(self._retention)
        privacy.add_row(SettingRow("Retention period", "Older conversation files are automatically deleted.", self.days))
        layout.addWidget(privacy)
        management=SettingsSection("Stored history", "Review storage statistics or remove history. Deletion is permanent.")
        self.status=QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color:{C.TEXT_MED};")
        management.add_row(SettingRow("Storage", "", self.status))
        self.delete_button=QPushButton("Delete all conversation history")
        self.delete_button.setObjectName("conversation_memory_delete_all")
        self.delete_button.clicked.connect(self._delete_all)
        management.add_row(SettingRow("Privacy", "Remove every stored conversation file.", self.delete_button))
        layout.addWidget(management)
        self.refresh()
        layout.addStretch(1)
    def _toggle(self, enabled):
        save_conversation_memory(enabled=enabled)
        conversations.refresh_settings()
        self.refresh()
    def _retention(self, days):
        save_conversation_memory(days=days)
        conversations.refresh_settings()
        conversations.prune(days)
        self.refresh()
    def _delete_all(self):
        result=QMessageBox.question(self,"Delete conversation history","Delete all locally stored conversation history? This cannot be undone.",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
        if result != QMessageBox.StandardButton.Yes:
            return
        deleted=conversations.forget(everything=True)
        self.status.setText(f"Deleted {deleted} day-file(s).")
        self.refresh()
    def refresh(self):
        stats=conversations.stats()
        self.status.setText(f"{stats['exchanges']} exchanges across {stats['days_with_conversations']} days. " + (f"First: {stats['first']} · Last: {stats['last']}." if stats['first'] else "No stored history."))
