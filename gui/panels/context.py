"""Live execution context panel for the JARVIS workspace."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)
from gui.themes.tokens import C


class ContextPanel(QWidget):
    """Read-only view of the state currently driving the assistant session."""

    SECTIONS = (
        ("SESSION", ("Provider", "Model", "Execution mode", "Active agent")),
        ("WORKSPACE", ("Active project", "Attached files")),
        ("TOOLS & ACCESS", ("Connected tools", "Permissions")),
        ("MEMORY", ("Memory status", "Context usage")),
    )

    def __init__(self, snapshot_provider: Callable[[], Mapping[str, Any]], parent=None):
        super().__init__(parent)
        self._snapshot_provider = snapshot_provider
        self.setObjectName("ContextPanel")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("CURRENT SESSION")
        title.setStyleSheet(f"color:{C.TEXT_MED}; font-weight:700; letter-spacing:1px;")
        header.addWidget(title)
        header.addStretch(1)
        self.refresh_button = QPushButton("↻")
        self.refresh_button.setFixedSize(28, 26)
        self.refresh_button.setToolTip("Refresh session context")
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.refresh_button)
        root.addLayout(header)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self._body_layout = QVBoxLayout(body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(7)
        self._scroll.setWidget(body)
        root.addWidget(self._scroll, 1)
        self.refresh()

    @staticmethod
    def _display(value: Any) -> str:
        if value is None or value == "":
            return "Not available"
        if isinstance(value, bool):
            return "Enabled" if value else "Disabled"
        if isinstance(value, (list, tuple, set)):
            values = [str(item).strip() for item in value if str(item).strip()]
            return "\n".join(values) if values else "None"
        if isinstance(value, Mapping):
            return ", ".join(f"{key}: {item}" for key, item in value.items()) or "None"
        return str(value)

    def refresh(self) -> None:
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        try:
            snapshot = self._snapshot_provider()
            if not isinstance(snapshot, Mapping):
                snapshot = {}
        except Exception as exc:
            snapshot = {"SESSION": {"Context status": f"Unavailable ({type(exc).__name__})"}}

        for section, labels in self.SECTIONS:
            values = snapshot.get(section, {})
            if not isinstance(values, Mapping):
                values = {}
            card = QFrame()
            card.setObjectName("ContextSection")
            card.setStyleSheet(
                f"QFrame#ContextSection {{ background:{C.PANEL2}; border:1px solid {C.BORDER}; border-radius:8px; }}"
            )
            layout = QVBoxLayout(card)
            layout.setContentsMargins(10, 8, 10, 9)
            layout.setSpacing(7)
            heading = QLabel(section)
            heading.setStyleSheet(f"color:{C.PRI}; font-size:9px; font-weight:700;")
            layout.addWidget(heading)
            for label in labels:
                row = QGridLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setHorizontalSpacing(8)
                key = QLabel(label)
                key.setStyleSheet(f"color:{C.TEXT_DIM};")
                key.setMinimumWidth(95)
                value = QLabel(self._display(values.get(label)))
                value.setWordWrap(True)
                value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                value.setStyleSheet(f"color:{C.TEXT};")
                row.addWidget(key, 0, 0, Qt.AlignmentFlag.AlignTop)
                row.addWidget(value, 0, 1)
                row.setColumnStretch(1, 1)
                layout.addLayout(row)
            self._body_layout.addWidget(card)
        self._body_layout.addStretch(1)
