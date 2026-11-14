"""General settings migrated from the legacy controls drawer."""

from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QComboBox, QLabel, QVBoxLayout, QWidget

from gui.components.settings import SettingRow, SettingsSection
from gui.themes.tokens import C, Space
from gui.themes.typography import TypeScale, ui_font


class GeneralSettingsPage(QWidget):
    """Uses injected state readers and existing owner handlers."""

    def __init__(
        self,
        state: dict[str, Callable[[], bool]],
        actions: dict[str, Callable[[], None]],
        languages: dict[str, str],
        current_language: Callable[[], str],
        language_changed: Callable,
        parent=None,
    ):
        super().__init__(parent)
        self._state = state
        self._actions = actions
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.LG)
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color:{C.RED};")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        basics = SettingsSection("Application behavior", "Startup and everyday application preferences.")
        layout.addWidget(basics)
        self._add_toggle(
            basics, "Auto-start", "Launch JARVIS when you sign in to this computer.", "autostart", "autostart"
        )
        self._add_toggle(
            basics, "Morning brief", "Enable the scheduled daily brief.", "morning_brief", "morning_brief"
        )
        self._add_toggle(
            basics, "Fullscreen", "Use the full screen for the JARVIS workspace. Shortcut: F11.", "fullscreen", "fullscreen"
        )

        language = SettingsSection("Language", "The interface language applies across JARVIS panels after restart.")
        self.language_combo = QComboBox()
        self.language_combo.setObjectName("app_language")
        self.language_combo.setAccessibleName("Interface language")
        self.language_combo.setAccessibleDescription("Choose the interface language. Restart JARVIS to apply it throughout the interface.")
        for code, name in languages.items():
            self.language_combo.addItem(name, code)
        self.language_combo.currentIndexChanged.connect(language_changed)
        language.add_row(
            SettingRow(
                "Interface language",
                "Choose a supported language. Restart JARVIS to apply it throughout the interface.",
                self.language_combo,
            )
        )
        self._current_language = current_language
        layout.addWidget(language)

        shortcuts = SettingsSection("Keyboard shortcuts", "Available throughout the main JARVIS window.")
        for name, shortcut in (("Mute microphone", "F4"), ("Fullscreen", "F11"), ("Interrupt response", "Esc"), ("Command palette", "Ctrl+K")):
            value = QLabel(shortcut)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            value.setStyleSheet(f"color:{C.TEXT}; background:{C.PANEL2}; border:1px solid {C.BORDER}; border-radius:5px; padding:5px 9px;")
            shortcuts.add_row(SettingRow(name, "", value))
        layout.addWidget(shortcuts)
        layout.addStretch(1)
        self.refresh_state()

    def _add_toggle(self, section, title, description, setting_id, state_id):
        control = QCheckBox()
        control.setObjectName(setting_id)
        control.setCursor(Qt.CursorShape.PointingHandCursor)
        control.setAccessibleName(title)
        control.setAccessibleDescription(description)
        control.setStyleSheet(
            f"QCheckBox {{ spacing:6px; color:{C.TEXT}; }}"
            f"QCheckBox::indicator {{ width:18px; height:18px; border:1px solid {C.BORDER_B}; border-radius:5px; background:{C.PANEL2}; }}"
            f"QCheckBox::indicator:checked {{ background:{C.PRI}; border-color:{C.PRI}; }}"
            f"QCheckBox:focus {{ color:{C.PRI}; }}"
        )
        control.toggled.connect(lambda checked, key=state_id, action=setting_id: self._apply_toggle(key, action))
        section.add_row(SettingRow(title, description, control))

    def _apply_toggle(self, state_id: str, action_id: str):
        control = self.findChild(QCheckBox, action_id)
        desired = bool(control.isChecked()) if control is not None else False
        action = self._actions.get(action_id)
        error = ""
        try:
            if action:
                action()
        except Exception as exc:
            error = f"Could not update {action_id.replace('_', ' ')}: {exc}"
        actual = bool(self._state[state_id]())
        if control is not None:
            control.blockSignals(True)
            control.setChecked(actual)
            control.blockSignals(False)
        if not error and actual != desired:
            error = "The requested change was not applied. Check the system permissions and diagnostic log."
        self.error_label.setText(error)
        self.error_label.setVisible(bool(error))

    def refresh_state(self):
        for setting_id, control in (
            ("autostart", "autostart"),
            ("morning_brief", "morning_brief"),
            ("fullscreen", "fullscreen"),
        ):
            widget = self.findChild(QCheckBox, control)
            if widget is not None:
                widget.blockSignals(True)
                widget.setChecked(bool(self._state[setting_id]()))
                widget.blockSignals(False)
        language_index = self.language_combo.findData(self._current_language())
        if language_index >= 0 and language_index != self.language_combo.currentIndex():
            self.language_combo.blockSignals(True)
            self.language_combo.setCurrentIndex(language_index)
            self.language_combo.blockSignals(False)
