"""Interactive live context and execution-insight panel for JARVIS."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)
from gui.themes.tokens import C


class ContextPanel(QWidget):
    """Live context dashboard with navigation into the owning JARVIS surfaces."""

    action_requested = pyqtSignal(str)

    SECTIONS = (
        ("SESSION", ("Provider", "Model", "Environment", "Selection policy", "Execution engine", "Computer mode", "Access policy")),
        ("TOKEN USAGE", ("Input tokens", "Output tokens", "Total tokens", "Usage source", "Context window")),
        ("WORKSPACE", ("Active project", "Project path", "Current attachment")),
        ("CONNECTED APPS", ("Connection summary", "Connections")),
        ("PERMISSIONS", ("Computer permissions",)),
        ("AI ACTIONS", ("Recent actions", "Recent warnings and errors", "Action count")),
        ("MEMORY & CHAT", ("Saved memory entries", "Active chat messages", "Conversation size (estimate)")),
    )

    def __init__(
        self,
        snapshot_provider: Callable[[], Mapping[str, Any]],
        parent=None,
        *,
        refresh_interval_ms: int = 10000,
        action_callbacks: Mapping[str, Callable[[], None]] | None = None,
    ):
        super().__init__(parent)
        self._snapshot_provider = snapshot_provider
        self._action_callbacks = dict(action_callbacks or {})
        self._value_labels: dict[tuple[str, str], QLabel] = {}
        self.setObjectName("ContextPanel")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(7)

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
        self.refresh_button.setToolTip("Refresh live context and token estimates")
        self.refresh_button.setAccessibleName("Refresh session context")
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.refresh_button)
        root.addLayout(header)

        self._status_label = QLabel("Reading runtime state…")
        self._status_label.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent;")
        root.addWidget(self._status_label)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.activity_button = QPushButton("Open Activity")
        self.activity_button.setToolTip("View the full runtime event and AI action log")
        self.activity_button.clicked.connect(lambda: self._dispatch_action("activity"))
        actions.addWidget(self.activity_button)
        self.providers_button = QPushButton("Provider Settings")
        self.providers_button.setToolTip("Configure the active AI provider")
        self.providers_button.clicked.connect(lambda: self._dispatch_action("providers"))
        actions.addWidget(self.providers_button)
        self.permissions_button = QPushButton("Permissions")
        self.permissions_button.setToolTip("Review computer access permissions")
        self.permissions_button.clicked.connect(lambda: self._dispatch_action("permissions"))
        actions.addWidget(self.permissions_button)
        for button in (self.activity_button, self.providers_button, self.permissions_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(
                f"QPushButton {{ color:{C.PRI}; background:{C.PANEL2}; "
                f"border:1px solid {C.BORDER}; border-radius:6px; padding:6px 8px; }}"
                f"QPushButton:hover {{ border-color:{C.PRI_DIM}; background:{C.PRI_GHO}; }}"
            )
        root.addLayout(actions)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("ContextScrollArea")
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

    def _dispatch_action(self, action: str) -> None:
        callback = self._action_callbacks.get(action)
        if callback is not None:
            try:
                callback()
                self._status_label.setText(f"Opened {action.replace('_', ' ')}")
            except Exception as exc:
                self._status_label.setText(f"Action unavailable · {type(exc).__name__}")
        else:
            self.action_requested.emit(action)

    def _build_sections(self) -> None:
        for section, fields in self.SECTIONS:
            card = QFrame()
            card.setObjectName("ContextSection")
            card.setStyleSheet(
                f"QFrame#ContextSection {{ background:{C.PANEL2}; "
                f"border:1px solid {C.BORDER}; border-radius:8px; }}"
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
                key.setMinimumWidth(104)
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
            self._status_label.setText("Live runtime context loaded")
        except Exception as exc:
            snapshot = {}
            self._status_label.setText(f"Context unavailable · {type(exc).__name__}")

        for (section, field), label in self._value_labels.items():
            values = snapshot.get(section, {})
            value = values.get(field) if isinstance(values, Mapping) else None
            label.setText(self._display(value))

        self.updated_label.setText(datetime.now().astimezone().strftime("Updated %H:%M:%S"))

    def closeEvent(self, event):
        self._refresh_timer.stop()
        super().closeEvent(event)
