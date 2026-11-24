"""Live execution context panel for the JARVIS workspace."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)
from gui.themes.tokens import C


class ContextPanel(QWidget):
    """Read-only, periodically refreshed view of the active JARVIS session."""

    SECTIONS = (
        ("SESSION", ("Provider", "Model", "Environment", "Selection policy", "Execution engine", "Computer mode", "Access policy")),
        ("WORKSPACE", ("Active project", "Project path", "Current attachment")),
        ("CONNECTED APPS", ("Connection summary", "Connections")),
        ("PERMISSIONS", ("Computer permissions",)),
        ("MEMORY & CHAT", ("Saved memory entries", "Active chat messages", "Conversation size (estimate)")),
    )

    def __init__(
        self,
        snapshot_provider: Callable[[], Mapping[str, Any]],
        parent=None,
        *,
        refresh_interval_ms: int = 10000,
    ):
        super().__init__(parent)
        self._snapshot_provider = snapshot_provider
        self._value_labels: dict[tuple[str, str], QLabel] = {}
        self.setObjectName("ContextPanel")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("LIVE SESSION CONTEXT")
        title.setStyleSheet(f"color:{C.TEXT_MED}; font-weight:700; letter-spacing:1px;")
        header.addWidget(title)
        header.addStretch(1)
        self.updated_label = QLabel("Not refreshed")
        self.updated_label.setStyleSheet(f"color:{C.TEXT_DIM}; font-size:9px;")
        header.addWidget(self.updated_label)
        self.refresh_button = QPushButton("↻")
        self.refresh_button.setFixedSize(28, 26)
        self.refresh_button.setToolTip("Refresh session context")
        self.refresh_button.setAccessibleName("Refresh session context")
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.refresh_button)
        root.addLayout(header)

        self._status_label = QLabel("Reading runtime state…")
        self._status_label.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent;")
        root.addWidget(self._status_label)

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

        self._build_sections()
        self.refresh()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(max(3000, int(refresh_interval_ms)))
        self._refresh_timer.timeout.connect(self._refresh_if_visible)
        self._refresh_timer.start()

    def _build_sections(self) -> None:
        for section, fields in self.SECTIONS:
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
            for field in fields:
                row = QGridLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setHorizontalSpacing(8)
                key = QLabel(field)
                key.setStyleSheet(f"color:{C.TEXT_DIM};")
                key.setMinimumWidth(92)
                key.setWordWrap(True)
                value = QLabel("Not available")
                value.setWordWrap(True)
                value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                value.setStyleSheet(f"color:{C.TEXT};")
                value.setMinimumWidth(0)
                row.addWidget(key, 0, 0, Qt.AlignmentFlag.AlignTop)
                row.addWidget(value, 0, 1)
                row.setColumnStretch(1, 1)
                layout.addLayout(row)
                self._value_labels[(section, field)] = value
            self._body_layout.addWidget(card)
        self._body_layout.addStretch(1)

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
            return "\n".join(f"{key}: {item}" for key, item in value.items()) or "None"
        return str(value)

    def _refresh_if_visible(self) -> None:
        if self.isVisible():
            self.refresh()

    def refresh(self) -> None:
        try:
            snapshot = self._snapshot_provider()
            if not isinstance(snapshot, Mapping):
                raise TypeError("Context provider must return a mapping")
            self._status_label.setText("Runtime snapshot loaded")
        except Exception as exc:
            snapshot = {}
            self._status_label.setText(
                f"Context unavailable · {type(exc).__name__}: {str(exc)[:120]}"
            )

        for (section, field), label in self._value_labels.items():
            values = snapshot.get(section, {})
            value = values.get(field) if isinstance(values, Mapping) else None
            label.setText(self._display(value))

        self.updated_label.setText(
            datetime.now().astimezone().strftime("Updated %H:%M:%S")
        )

    def closeEvent(self, event):
        self._refresh_timer.stop()
        super().closeEvent(event)
