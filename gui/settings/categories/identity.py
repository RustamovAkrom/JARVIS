"""Assistant and user identity settings with explicit Apply/Cancel behavior."""

from collections.abc import Callable, Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QLabel, QLineEdit, QPushButton, QPlainTextEdit, QVBoxLayout, QWidget

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
        apply_changes: Callable[[str, str, str, str, str, str], bool],
        personality: str = "professional",
        response_style: str = "balanced",
        custom_instructions: str = "",

        parent=None,
    ):
        super().__init__(parent)
        self._initial = (assistant_name, user_name, voice, personality, response_style, custom_instructions)
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

        behavior = SettingsSection(
            "Behavior profile",
            "Tune JARVIS response behavior. These preferences apply to live model instructions.",
        )
        self.personality_combo = QComboBox()
        self.personality_combo.setObjectName("identity_personality")
        self.personality_combo.setAccessibleName("Assistant personality")
        for label, value in (
            ("Professional", "professional"),
            ("Friendly", "friendly"),
            ("Technical", "technical"),
            ("Minimal", "minimal"),
        ):
            self.personality_combo.addItem(label, value)
        personality_index = self.personality_combo.findData(personality)
        if personality_index >= 0:
            self.personality_combo.setCurrentIndex(personality_index)
        behavior.add_row(SettingRow(
            "Personality",
            "Choose the default communication attitude. Safety rules remain unchanged.",
            self.personality_combo,
        ))

        self.response_style_combo = QComboBox()
        self.response_style_combo.setObjectName("identity_response_style")
        self.response_style_combo.setAccessibleName("Response detail")
        for label, value in (
            ("Concise", "concise"),
            ("Balanced", "balanced"),
            ("Detailed", "detailed"),
        ):
            self.response_style_combo.addItem(label, value)
        style_index = self.response_style_combo.findData(response_style)
        if style_index >= 0:
            self.response_style_combo.setCurrentIndex(style_index)
        behavior.add_row(SettingRow(
            "Response detail",
            "Set the default explanation depth; JARVIS can adapt to each request.",
            self.response_style_combo,
        ))

        self.custom_instructions = QPlainTextEdit()
        self.custom_instructions.setObjectName("identity_custom_instructions")
        self.custom_instructions.setAccessibleName("Custom assistant instructions")
        self.custom_instructions.setPlaceholderText(
            "Example: Prefer practical examples. Explain technical trade-offs. Use metric units."
        )
        self.custom_instructions.setMaximumHeight(112)
        self.custom_instructions.setPlainText(str(custom_instructions or "")[:1500])
        behavior.add_row(SettingRow(
            "Custom instructions",
            "Optional preferences for tone, formatting, and workflow. Maximum 1,500 characters.",
            self.custom_instructions,
        ))
        layout.addWidget(behavior)

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
                    str(self.personality_combo.currentData() or "professional"),
                    str(self.response_style_combo.currentData() or "balanced"),
                    self.custom_instructions.toPlainText().strip()[:1500],
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
            str(self.personality_combo.currentData() or "professional"),
            str(self.response_style_combo.currentData() or "balanced"),
            self.custom_instructions.toPlainText().strip()[:1500],
        )
        self.feedback.setText("Profile saved. Behavior preferences apply to the next live session; voice changes reconnect the session.")
        self.feedback.setStyleSheet(f"color:{C.GREEN};")
        self.feedback.show()
        return True

    def cancel(self):
        name, user, voice, personality, response_style, instructions = self._initial
        self.assistant_name_input.setText(name)
        self.user_name_input.setText(user)
        voice_index = self.voice_combo.findData(voice)
        if voice_index >= 0:
            self.voice_combo.setCurrentIndex(voice_index)
        personality_index = self.personality_combo.findData(personality)
        if personality_index >= 0:
            self.personality_combo.setCurrentIndex(personality_index)
        style_index = self.response_style_combo.findData(response_style)
        if style_index >= 0:
            self.response_style_combo.setCurrentIndex(style_index)
        self.custom_instructions.setPlainText(instructions)
        self.feedback.setText("Unsaved edits were discarded.")
        self.feedback.setStyleSheet(f"color:{C.TEXT_MED};")
        self.feedback.show()
