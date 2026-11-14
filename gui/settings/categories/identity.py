"""Assistant and user identity settings with explicit Apply/Cancel behavior."""

from collections.abc import Callable, Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from gui.components.settings import SettingRow, SettingsSection
from gui.themes.tokens import C, Space
from gui.themes.typography import TypeScale, ui_font


class AssistantIdentityPage(QWidget):
    """Edits names and voice as one Apply transaction through the owner callback."""

    def __init__(
        self,
        assistant_name: str,
        user_name: str,
        voice: str,
        voices: Sequence[str],
        apply_changes: Callable[[str, str, str], bool],
        parent=None,
    ):
        super().__init__(parent)
        self._initial = (assistant_name, user_name, voice)
        self._apply_changes = apply_changes
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.LG)

        section = SettingsSection("Assistant profile", "Choose how JARVIS identifies itself and addresses you.")
        self.assistant_name_input = QLineEdit(assistant_name)
        self.assistant_name_input.setObjectName("assistant_name")
        self.assistant_name_input.setAccessibleName("Assistant name")
        self.assistant_name_input.setMaxLength(80)
        section.add_row(SettingRow("Assistant name", "Name shown throughout the interface.", self.assistant_name_input))

        self.user_name_input = QLineEdit(user_name)
        self.user_name_input.setObjectName("user_name")
        self.user_name_input.setAccessibleName("Your name")
        self.user_name_input.setMaxLength(80)
        section.add_row(SettingRow("Your name", "Leave blank to use the assistant's default form of address.", self.user_name_input))

        self.voice_combo = QComboBox()
        self.voice_combo.setObjectName("voice")
        self.voice_combo.setAccessibleName("Assistant voice")
        for name in voices:
            self.voice_combo.addItem(name, name)
        voice_index = self.voice_combo.findData(voice)
        self.voice_combo.setCurrentIndex(max(0, voice_index))
        section.add_row(SettingRow("Assistant voice", "Changing voice rebuilds the live session after Apply.", self.voice_combo))
        layout.addWidget(section)

        self.feedback = QLabel("")
        self.feedback.setWordWrap(True)
        self.feedback.setFont(ui_font(TypeScale.SECONDARY))
        self.feedback.setStyleSheet(f"color:{C.TEXT_MED};")
        self.feedback.hide()
        layout.addWidget(self.feedback)

        actions = SettingsSection("Changes", "Apply saves the profile. Cancel discards edits made on this page.")
        self.apply_button = QPushButton("Apply changes")
        self.apply_button.setObjectName("apply_identity")
        self.apply_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_button.clicked.connect(self.apply)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("cancel_identity")
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(self.cancel)
        actions.add_row(SettingRow("Save profile", "", self.apply_button))
        actions.add_row(SettingRow("Discard edits", "", self.cancel_button))
        layout.addWidget(actions)
        layout.addStretch(1)

    def apply(self):
        try:
            saved = bool(
                self._apply_changes(
                    self.assistant_name_input.text().strip() or "JARVIS",
                    self.user_name_input.text().strip(),
                    str(self.voice_combo.currentData() or ""),
                )
            )
        except Exception as exc:
            self.feedback.setText(f"Could not save the assistant profile: {exc}")
            self.feedback.setStyleSheet(f"color:{C.RED};")
            self.feedback.show()
            return False
        if not saved:
            self.feedback.setText("The profile could not be saved. Check the diagnostic log and try again.")
            self.feedback.setStyleSheet(f"color:{C.RED};")
            self.feedback.show()
            return False
        self._initial = (
            self.assistant_name_input.text().strip() or "JARVIS",
            self.user_name_input.text().strip(),
            str(self.voice_combo.currentData() or ""),
        )
        self.feedback.setText("Profile saved. A voice change takes effect after the live session reconnects.")
        self.feedback.setStyleSheet(f"color:{C.GREEN};")
        self.feedback.show()
        return True

    def cancel(self):
        name, user, voice = self._initial
        self.assistant_name_input.setText(name)
        self.user_name_input.setText(user)
        voice_index = self.voice_combo.findData(voice)
        if voice_index >= 0:
            self.voice_combo.setCurrentIndex(voice_index)
        self.feedback.setText("Unsaved edits were discarded.")
        self.feedback.setStyleSheet(f"color:{C.TEXT_MED};")
        self.feedback.show()
