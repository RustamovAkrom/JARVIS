"""Reusable native widgets for the centralized settings experience."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from gui.themes.tokens import C, Radius, Space
from gui.themes.typography import TypeScale, ui_font


def _section_style() -> str:
    return (
        f"QFrame#SettingsSection {{ background:{C.PANEL}; border:1px solid {C.BORDER};"
        f" border-radius:{Radius.MD}px; }}"
        f"QLabel {{ color:{C.TEXT}; background:transparent; border:0; }}"
    )


def _row_style() -> str:
    return (
        "QFrame#SettingRow { background:transparent; border:0;"
        f" border-bottom:1px solid {C.BORDER}; }}"
        f"QLabel#SettingTitle {{ color:{C.WHITE}; background:transparent; border:0; }}"
        f"QLabel#SettingDescription {{ color:{C.TEXT_MED}; background:transparent; border:0; }}"
    )


class SettingRow(QFrame):
    """A readable label/description paired with its existing native control."""

    def __init__(self, title: str, description: str = "", control: QWidget | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingRow")
        self.setStyleSheet(_row_style())
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, Space.MD, 0, Space.MD)
        layout.setSpacing(Space.LG)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(Space.XS)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("SettingTitle")
        self.title_label.setFont(ui_font(TypeScale.BODY, QFont.Weight.DemiBold))
        self.title_label.setWordWrap(True)
        self.description_label = QLabel(description)
        self.description_label.setObjectName("SettingDescription")
        self.description_label.setFont(ui_font(TypeScale.SECONDARY))
        self.description_label.setWordWrap(True)
        self.description_label.setVisible(bool(description))
        text_column.addWidget(self.title_label)
        text_column.addWidget(self.description_label)
        layout.addLayout(text_column, 1)

        self.control = control
        if control is not None:
            control.setParent(self)
            control.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            control.setAccessibleName(title)
            if description:
                control.setAccessibleDescription(description)
            layout.addWidget(control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def refresh_theme(self) -> None:
        self.setStyleSheet(_row_style())


class SettingsSection(QFrame):
    """A titled settings group with consistent spacing and row alignment."""

    def __init__(self, title: str, description: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("SettingsSection")
        self.setStyleSheet(_section_style())
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.SM)
        self._layout.setSpacing(Space.XS)

        self.title_label = QLabel(title)
        self.title_label.setFont(ui_font(TypeScale.SECTION, QFont.Weight.DemiBold))
        self._layout.addWidget(self.title_label)
        self.description_label = QLabel(description)
        self.description_label.setFont(ui_font(TypeScale.SECONDARY))
        self.description_label.setWordWrap(True)
        self.description_label.setStyleSheet(f"color:{C.TEXT_MED};")
        self.description_label.setVisible(bool(description))
        self._layout.addWidget(self.description_label)

    def add_row(self, row: SettingRow) -> SettingRow:
        self._layout.addWidget(row)
        return row

    def refresh_theme(self) -> None:
        self.setStyleSheet(_section_style())
        self.description_label.setStyleSheet(f"color:{C.TEXT_MED};")
        for row in self.findChildren(SettingRow):
            row.refresh_theme()
