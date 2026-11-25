"""Polished, compact runtime context dashboard for JARVIS."""
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
    """Live overview of the active model, usage, workspace and AI activity."""

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
        self.setStyleSheet(f"QWidget#ContextPanel {{ background:{C.PANEL}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(7)

        header = QHBoxLayout()
        title = QLabel("SESSION OVERVIEW")
        title.setStyleSheet(f"color:{C.TEXT_MED}; font-weight:700; letter-spacing:1px;")
        header.addWidget(title)
        header.addStretch(1)
        self.updated_label = QLabel("Not refreshed")
        self.updated_label.setStyleSheet(f"color:{C.TEXT_DIM}; font-size:9px;")
        header.addWidget(self.updated_label)
        self.refresh_button = QPushButton("↻")
        self.refresh_button.setFixedSize(28, 26)
        self.refresh_button.setToolTip("Refresh live context")
        self.refresh_button.setAccessibleName("Refresh live context")
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.refresh_button)
        root.addLayout(header)

        self._status_label = QLabel("Reading runtime state…")
        self._status_label.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent;")
        root.addWidget(self._status_label)

        # A two-column action grid remains legible at the right panel's
        # minimum width. Four buttons in one row were compressed enough to
        # truncate labels such as "Connected Apps" on smaller workspaces.
        self._action_grid = QGridLayout()
        self._action_grid.setContentsMargins(0, 0, 0, 0)
        self._action_grid.setHorizontalSpacing(6)
        self._action_grid.setVerticalSpacing(5)
        self.activity_button = self._action_button("Activity", "activity")
        self.providers_button = self._action_button("AI Providers", "providers")
        self.permissions_button = self._action_button("Permissions", "permissions")
        self.connected_apps_button = self._action_button("Connected Apps", "connected_apps")
        for index, button in enumerate((
            self.activity_button,
            self.providers_button,
            self.permissions_button,
            self.connected_apps_button,
        )):
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._action_grid.addWidget(button, index // 2, index % 2)
        self._action_grid.setColumnStretch(0, 1)
        self._action_grid.setColumnStretch(1, 1)
        root.addLayout(self._action_grid)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("ContextScrollArea")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            f"QScrollArea, QScrollArea QWidget#ContextBody {{ background:{C.PANEL}; border:0; }}"
            f"QScrollBar:vertical {{ background:{C.PANEL}; width:9px; margin:2px; }}"
            f"QScrollBar::handle:vertical {{ background:{C.BORDER_B}; border-radius:4px; min-height:24px; }}"
            f"QScrollBar::handle:vertical:hover {{ background:{C.PRI_DIM}; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}"
        )
        body = QWidget()
        body.setObjectName("ContextBody")
        body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        body.setStyleSheet(f"background:{C.PANEL};")
        self._body = QVBoxLayout(body)
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(8)
        self._scroll.setWidget(body)
        self._scroll.viewport().setStyleSheet(f"background:{C.PANEL}; border:0;")
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
        button.setAccessibleName(f"Open {title}")
        button.setMinimumHeight(32)
        button.clicked.connect(lambda: self._dispatch_action(action))
        button.setStyleSheet(
            f"QPushButton {{ color:{C.PRI}; background:{C.PANEL2}; "
            f"border:1px solid {C.BORDER}; border-radius:7px; padding:6px 8px; }}"
            f"QPushButton:hover {{ border-color:{C.PRI_DIM}; background:{C.PRI_GHO}; }}"
            f"QPushButton:pressed {{ background:{C.BORDER}; }}"
        )
        return button

    def _card(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("ContextCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setStyleSheet(
            f"QFrame#ContextCard {{ background:{C.PANEL2}; "
            f"border:1px solid {C.BORDER}; border-radius:10px; }}"
        )
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 11)
        layout.setSpacing(8)
        heading = QLabel(title)
        heading.setStyleSheet(f"color:{C.PRI}; font-size:9px; font-weight:700; letter-spacing:.4px;")
        layout.addWidget(heading)
        self._body.addWidget(card)
        return card, layout

    def _value_row(self, layout: QVBoxLayout, key: str, *, strong: bool = False) -> QLabel:
        row = QGridLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setHorizontalSpacing(10)
        name = QLabel(key)
        name.setMinimumWidth(78)
        name.setWordWrap(True)
        name.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent;")
        value = QLabel("—")
        value.setWordWrap(True)
        value.setMinimumWidth(0)
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        value.setStyleSheet(
            f"color:{C.TEXT}; background:transparent;" + (" font-weight:600;" if strong else "")
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
        self._value_row(layout, "Mode")

        _, layout = self._card("TOKEN USAGE")
        total_row = QHBoxLayout()
        total_caption = QLabel("Observed session usage")
        total_caption.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent;")
        total_row.addWidget(total_caption)
        total_row.addStretch(1)
        self.token_total = QLabel("—")
        self.token_total.setStyleSheet(f"color:{C.PRI}; background:transparent; font-size:21px; font-weight:700;")
        total_row.addWidget(self.token_total)
        layout.addLayout(total_row)

        self.input_caption = QLabel("Prompt · —")
        self.input_caption.setStyleSheet(f"color:{C.TEXT_MED}; background:transparent;")
        layout.addWidget(self.input_caption)
        self.input_bar = QProgressBar()
        self._style_progress(self.input_bar, C.PRI)
        layout.addWidget(self.input_bar)

        self.output_caption = QLabel("Response · —")
        self.output_caption.setStyleSheet(f"color:{C.TEXT_MED}; background:transparent;")
        layout.addWidget(self.output_caption)
        self.output_bar = QProgressBar()
        self._style_progress(self.output_bar, C.ACC)
        layout.addWidget(self.output_bar)

        self.usage_source = QLabel("Usage source unavailable")
        self.usage_source.setWordWrap(True)
        self.usage_source.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent; font-size:9px;")
        layout.addWidget(self.usage_source)
        self.context_capacity = QLabel("Context window · not reported")
        self.context_capacity.setWordWrap(True)
        self.context_capacity.setStyleSheet(f"color:{C.TEXT_DIM}; background:transparent; font-size:9px;")
        layout.addWidget(self.context_capacity)

        _, layout = self._card("WORKSPACE")
        self._value_row(layout, "Project", strong=True)
        self._value_row(layout, "Attachment")

        _, layout = self._card("CONNECTED SERVICES")
        self.connection_status = self._value_row(layout, "Status", strong=True)
        self.connection_details = self._value_row(layout, "Services")

        _, layout = self._card("AI ACTIVITY")
        self._value_row(layout, "Recent actions")
        self._value_row(layout, "Issues")
        self._body.addStretch(1)

    @staticmethod
    def _style_progress(bar: QProgressBar, color: str) -> None:
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(False)
        bar.setFixedHeight(9)
        bar.setStyleSheet(
            f"QProgressBar {{ background:{C.BAR_BG}; border:0; border-radius:4px; }}"
            f"QProgressBar::chunk {{ background:{color}; border-radius:4px; }}"
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
            return max(0, int(value))
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
            self._status_label.setText("Live session · updating automatically")
        except Exception as exc:
            snapshot = {}
            self._status_label.setText(f"Context unavailable · {type(exc).__name__}")

        def section(name: str) -> Mapping:
            value = snapshot.get(name, {})
            return value if isinstance(value, Mapping) else {}

        session = section("SESSION")
        self._labels["Provider"].setText(self._text(session.get("Provider")))
        self._labels["Model"].setText(self._text(session.get("Model")))
        self._labels["Mode"].setText(self._text(session.get("Environment")))

        usage = section("TOKEN USAGE")
        input_tokens = self._integer(usage.get("Input tokens"))
        output_tokens = self._integer(usage.get("Output tokens"))
        total_tokens = self._integer(usage.get("Total tokens")) or input_tokens + output_tokens
        source = self._text(usage.get("Usage source"), "Usage not reported")
        estimated = "estimate" in source.casefold()
        prefix = "≈" if estimated else ""
        self.token_total.setText(f"{prefix}{total_tokens:,}")
        self.input_caption.setText(f"Prompt · {prefix}{input_tokens:,} tokens")
        self.output_caption.setText(f"Response · {prefix}{output_tokens:,} tokens")
        denominator = max(1, input_tokens + output_tokens)
        self.input_bar.setValue(round(input_tokens * 100 / denominator))
        self.output_bar.setValue(round(output_tokens * 100 / denominator))
        self.usage_source.setText(source)
        capacity_value = self._integer(usage.get("Context window tokens"))
        if capacity_value:
            percent = min(100, round(total_tokens * 100 / capacity_value))
            self.context_capacity.setText(
                f"Context window · {prefix}{total_tokens:,} / {capacity_value:,} ({percent}%)"
            )
        else:
            self.context_capacity.setText("Context window · capacity not reported by provider")

        workspace = section("WORKSPACE")
        self._labels["Project"].setText(self._text(workspace.get("Active project"), "No project"))
        self._labels["Attachment"].setText(self._text(workspace.get("Current attachment"), "No attachment"))

        apps = section("CONNECTED APPS")
        self.connection_status.setText(self._text(apps.get("Connection summary"), "No connected services"))
        self.connection_details.setText(self._text(apps.get("Connections"), "No service details"))
        details = self.connection_details.text().casefold()
        self.connection_details.setStyleSheet(
            f"color:{C.RED if 'error' in details or 'failed' in details else C.TEXT}; background:transparent;"
        )

        actions = section("AI ACTIONS")
        recent = self._text(actions.get("Recent actions"), "No actions recorded")
        issues = self._text(actions.get("Recent warnings and errors"), "No issues")
        # Connection failures are operational issues even when the Activity panel
        # has not emitted an event for them (for example, startup auth failures).
        connection_lines = self._text(apps.get("Connections"), "").splitlines()
        connection_issues = [
            line.strip() for line in connection_lines
            if line.strip() and any(marker in line.casefold() for marker in (
                "error", "failed", "failure", "unauthorized", "permissionerror",
                "bad credentials", "authentication", "authorization",
            ))
        ]
        issue_lines = [] if issues == "No issues" else issues.splitlines()
        for line in connection_issues:
            if line not in issue_lines:
                issue_lines.append(line)
        issues = "\n".join(issue_lines) if issue_lines else "No issues"
        self._labels["Recent actions"].setText(recent)
        self._labels["Issues"].setText(issues)
        self._labels["Issues"].setStyleSheet(
            f"color:{C.RED if issues != 'No issues' else C.TEXT}; background:transparent;"
        )
        self.updated_label.setText(datetime.now().astimezone().strftime("Updated %H:%M:%S"))

    def closeEvent(self, event):
        self._refresh_timer.stop()
        super().closeEvent(event)
