"""Compact, actionable runtime context dashboard for JARVIS."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)
from gui.themes.tokens import C


class ContextPanel(QWidget):
    """Human-readable live runtime, token and AI activity overview."""

    action_requested = pyqtSignal(str)

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
        self._labels: dict[str, QLabel] = {}
        self.setObjectName("ContextPanel")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(7)

        header = QHBoxLayout()
        title = QLabel("CONTEXT")
        title.setStyleSheet(f"color:{C.TEXT_MED}; font-weight:700; letter-spacing:1px;")
        header.addWidget(title)
        header.addStretch(1)
        self.updated_label = QLabel("Not refreshed")
        self.updated_label.setStyleSheet(f"color:{C.TEXT_DIM}; font-size:9px;")
        header.addWidget(self.updated_label)
        self.refresh_button = QPushButton("↻")
        self.refresh_button.setFixedSize(28, 26)
        self.refresh_button.setToolTip("Refresh context")
        self.refresh_button.setAccessibleName("Refresh context")
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.refresh_button)
        root.addLayout(header)

        self._status_label = QLabel("Reading runtime state…")
        self._status_label.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent;")
        root.addWidget(self._status_label)

        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        self.activity_button = self._action_button("Activity", "activity")
        self.providers_button = self._action_button("AI Providers", "providers")
        self.permissions_button = self._action_button("Permissions", "permissions")
        action_row.addWidget(self.activity_button)
        action_row.addWidget(self.providers_button)
        action_row.addWidget(self.permissions_button)
        root.addLayout(action_row)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("ContextScrollArea")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(7)
        self._scroll.setWidget(body)
        root.addWidget(self._scroll, 1)

        self._build_cards()
        self.refresh()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(max(3000, int(refresh_interval_ms)))
        self._refresh_timer.timeout.connect(self._refresh_if_visible)
        self._refresh_timer.start()

    def _action_button(self, title: str, action: str) -> QPushButton:
        button = QPushButton(title)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(f"Open {title}")
        button.clicked.connect(lambda: self._dispatch_action(action))
        button.setStyleSheet(
            f"QPushButton {{ color:{C.PRI}; background:{C.PANEL2}; "
            f"border:1px solid {C.BORDER}; border-radius:6px; padding:6px 7px; }}"
            f"QPushButton:hover {{ border-color:{C.PRI_DIM}; background:{C.PRI_GHO}; }}"
        )
        return button

    def _card(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("ContextCard")
        card.setStyleSheet(
            f"QFrame#ContextCard {{ background:{C.PANEL2}; "
            f"border:1px solid {C.BORDER}; border-radius:8px; }}"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(11, 9, 11, 10)
        layout.setSpacing(7)
        heading = QLabel(title)
        heading.setStyleSheet(f"color:{C.PRI}; font-size:9px; font-weight:700;")
        layout.addWidget(heading)
        self._body.addWidget(card)
        return card, layout

    def _value_row(self, layout: QVBoxLayout, key: str, *, strong: bool = False) -> QLabel:
        row = QGridLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setHorizontalSpacing(9)
        name = QLabel(key)
        name.setMinimumWidth(82)
        name.setWordWrap(True)
        name.setStyleSheet(f"color:{C.TEXT_DIM};")
        value = QLabel("—")
        value.setWordWrap(True)
        value.setMinimumWidth(0)
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        value.setStyleSheet(
            f"color:{C.TEXT};" + (" font-weight:600;" if strong else "")
        )
        row.addWidget(name, 0, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(value, 0, 1)
        row.setColumnStretch(1, 1)
        layout.addLayout(row)
        self._labels[key] = value
        return value

    def _build_cards(self) -> None:
        _, layout = self._card("ACTIVE MODEL")
        self._value_row(layout, "Provider", strong=True)
        self._value_row(layout, "Model")
        self._value_row(layout, "Environment")

        _, layout = self._card("TOKEN USAGE")
        total_row = QHBoxLayout()
        total_caption = QLabel("Session total")
        total_caption.setStyleSheet(f"color:{C.TEXT_DIM};")
        total_row.addWidget(total_caption)
        total_row.addStretch(1)
        self.token_total = QLabel("—")
        self.token_total.setStyleSheet(f"color:{C.PRI}; font-size:20px; font-weight:700;")
        total_row.addWidget(self.token_total)
        layout.addLayout(total_row)

        self.input_caption = QLabel("Prompt · —")
        self.input_caption.setStyleSheet(f"color:{C.TEXT_MED};")
        layout.addWidget(self.input_caption)
        self.input_bar = QProgressBar()
        self._style_progress(self.input_bar)
        layout.addWidget(self.input_bar)

        self.output_caption = QLabel("Response · —")
        self.output_caption.setStyleSheet(f"color:{C.TEXT_MED};")
        layout.addWidget(self.output_caption)
        self.output_bar = QProgressBar()
        self._style_progress(self.output_bar)
        layout.addWidget(self.output_bar)
        self.usage_source = QLabel("Usage source unavailable")
        self.usage_source.setWordWrap(True)
        self.usage_source.setStyleSheet(f"color:{C.TEXT_DIM}; font-size:9px;")
        layout.addWidget(self.usage_source)
        self.context_capacity = QLabel("Context capacity: not reported by provider")
        self.context_capacity.setWordWrap(True)
        self.context_capacity.setStyleSheet(f"color:{C.TEXT_DIM}; font-size:9px;")
        layout.addWidget(self.context_capacity)

        _, layout = self._card("WORKSPACE")
        self._value_row(layout, "Project", strong=True)
        self._value_row(layout, "Attachment")

        _, layout = self._card("CONNECTED SERVICES")
        self._value_row(layout, "Status", strong=True)
        self._value_row(layout, "Details")

        _, layout = self._card("AI ACTIVITY")
        self._value_row(layout, "Recent actions")
        self._value_row(layout, "Warnings")

        self._body.addStretch(1)

    @staticmethod
    def _style_progress(bar: QProgressBar) -> None:
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(False)
        bar.setFixedHeight(8)
        bar.setStyleSheet(
            f"QProgressBar {{ background:{C.BG}; border:0; border-radius:4px; }}"
            f"QProgressBar::chunk {{ background:{C.PRI}; border-radius:4px; }}"
        )

    def _dispatch_action(self, action: str) -> None:
        callback = self._action_callbacks.get(action)
        if callback is None:
            self.action_requested.emit(action)
            return
        try:
            callback()
            self._status_label.setText(f"Opened {action.replace('_', ' ')}")
        except Exception as exc:
            self._status_label.setText(f"Could not open {action}: {type(exc).__name__}")

    @staticmethod
    def _text(value: Any, fallback: str = "—") -> str:
        if value is None or value == "":
            return fallback
        if isinstance(value, (list, tuple, set)):
            items = [str(item).strip() for item in value if str(item).strip()]
            return "\n".join(items) if items else fallback
        return str(value)

    @staticmethod
    def _integer(value: Any) -> int:
        try:
            number = int(value)
            return max(0, number)
        except (TypeError, ValueError, OverflowError):
            return 0

    def _refresh_if_visible(self) -> None:
        if self.isVisible():
            self.refresh()

    def refresh(self) -> None:
        try:
            snapshot = self._snapshot_provider()
            if not isinstance(snapshot, Mapping):
                raise TypeError("Context provider must return a mapping")
            self._status_label.setText("Live context")
        except Exception as exc:
            snapshot = {}
            self._status_label.setText(f"Context unavailable · {type(exc).__name__}")

        def section(name: str) -> Mapping:
            value = snapshot.get(name, {})
            return value if isinstance(value, Mapping) else {}

        session = section("SESSION")
        for key in ("Provider", "Model", "Environment"):
            self._labels[key].setText(self._text(session.get(key)))

        usage = section("TOKEN USAGE")
        input_tokens = self._integer(usage.get("Input tokens"))
        output_tokens = self._integer(usage.get("Output tokens"))
        total_tokens = self._integer(usage.get("Total tokens")) or input_tokens + output_tokens
        source = self._text(usage.get("Usage source"), "Usage not reported")
        self.token_total.setText(f"{total_tokens:,}")
        self.input_caption.setText(f"Prompt · {input_tokens:,} tokens")
        self.output_caption.setText(f"Response · {output_tokens:,} tokens")
        # Bars show the prompt/response composition of the observed session.
        # They are deliberately not labelled as context-window percentages.
        denominator = max(1, input_tokens + output_tokens)
        self.input_bar.setValue(round(input_tokens * 100 / denominator))
        self.output_bar.setValue(round(output_tokens * 100 / denominator))
        self.usage_source.setText(source)
        capacity = usage.get("Context window")
        capacity_value = self._integer(usage.get("Context window tokens"))
        if capacity_value:
            percent = min(100, round(total_tokens * 100 / capacity_value))
            self.context_capacity.setText(
                f"Context window · {total_tokens:,} / {capacity_value:,} tokens ({percent}%)"
            )
        else:
            self.context_capacity.setText(
                "Context window · capacity not reported by this provider"
            )

        workspace = section("WORKSPACE")
        self._labels["Project"].setText(self._text(workspace.get("Active project"), "No project"))
        self._labels["Attachment"].setText(self._text(workspace.get("Current attachment"), "None"))

        apps = section("CONNECTED APPS")
        self._labels["Status"].setText(self._text(apps.get("Connection summary"), "No connected services"))
        self._labels["Details"].setText(self._text(apps.get("Connections"), "No connection details"))

        actions = section("AI ACTIONS")
        self._labels["Recent actions"].setText(self._text(actions.get("Recent actions"), "No actions recorded"))
        self._labels["Warnings"].setText(self._text(actions.get("Recent warnings and errors"), "None"))

        self.updated_label.setText(datetime.now().astimezone().strftime("Updated %H:%M:%S"))

    def closeEvent(self, event):
        self._refresh_timer.stop()
        super().closeEvent(event)
