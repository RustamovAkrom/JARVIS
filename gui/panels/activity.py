"""Compact Activity view backed by MainWindow's existing log/event sink."""

from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gui.themes.tokens import C
from gui.themes.typography import FONT_MONO, FONT_UI


class ActivityPanel(QWidget):
    """Filterable, bounded activity display; it does not own or persist logs."""

    _incoming = pyqtSignal(str)
    _MAX_EVENTS = 500

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ActivityPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        self.setStyleSheet(
            f"QWidget#ActivityPanel {{ background:{C.PANEL}; border:0; }}"
        )
        self._events: list[dict[str, str]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(9, 9, 9, 9)
        root.setSpacing(7)

        heading = QLabel("ACTIVITY LOG")
        heading.setFont(QFont(FONT_MONO, 9, QFont.Weight.Bold))
        heading.setStyleSheet(f"color:{C.PRI}; background:transparent;")
        root.addWidget(heading)

        controls = QHBoxLayout()
        controls.setSpacing(5)
        self.filter_box = QComboBox()
        self.filter_box.setObjectName("activity_filter")
        self.filter_box.setFont(QFont(FONT_UI, 9))
        for label, value in (
            ("All", "all"),
            ("Actions", "actions"),
            ("Conversations", "conversations"),
            ("System", "system"),
            ("Warnings", "warnings"),
            ("Errors", "errors"),
        ):
            self.filter_box.addItem(label, value)
        self.filter_box.setMinimumWidth(0)
        self.filter_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.filter_box.setStyleSheet(
            f"QComboBox {{ background:{C.PANEL2}; color:{C.TEXT}; "
            f"border:1px solid {C.BORDER}; border-radius:5px; padding:4px 7px; }}"
            f"QComboBox QAbstractItemView {{ background:{C.PANEL2}; color:{C.TEXT}; "
            f"selection-background-color:{C.PRI_GHO}; }}"
        )
        controls.addWidget(self.filter_box, 1)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("activity_search")
        self.search_input.setPlaceholderText("Search activity")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumWidth(0)
        self.search_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.search_input.setStyleSheet(
            f"QLineEdit {{ background:{C.PANEL2}; color:{C.TEXT}; "
            f"border:1px solid {C.BORDER}; border-radius:5px; padding:5px 7px; }}"
            f"QLineEdit:focus {{ border-color:{C.PRI_DIM}; }}"
        )
        controls.addWidget(self.search_input, 2)

        clear_button = QPushButton("×")
        clear_button.setFixedSize(27, 27)
        clear_button.setToolTip("Clear this Activity view")
        clear_button.setAccessibleName("Clear Activity view")
        clear_button.setStyleSheet(
            f"QPushButton {{ color:{C.TEXT_DIM}; background:transparent; "
            f"border:1px solid {C.BORDER}; border-radius:5px; }}"
            f"QPushButton:hover {{ color:{C.RED}; border-color:{C.RED}; }}"
        )
        clear_button.clicked.connect(self.clear)
        controls.addWidget(clear_button)
        root.addLayout(controls)

        self._content_stack = QStackedWidget()
        self._content_stack.setObjectName("activity_content")
        self._content_stack.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._content_stack.setAutoFillBackground(True)
        self._content_stack.setStyleSheet(
            f"QStackedWidget#activity_content {{ background:{C.PANEL}; border:0; }}"
        )
        self._content_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        self.event_list = QListWidget()
        self.event_list.setObjectName("activity_events")
        self.event_list.setWordWrap(True)
        self.event_list.setUniformItemSizes(False)
        self.event_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.event_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.event_list.setHorizontalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.event_list.setFrameShape(QListWidget.Shape.NoFrame)
        self.event_list.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        # Style the scroll area, viewport and both scrollbars explicitly.
        # Styling only QListWidget leaves the native Windows scrollbar gutter
        # and horizontal scrollbar with the platform's default dark palette.
        self.event_list.viewport().setAutoFillBackground(True)
        self.event_list.viewport().setStyleSheet(
            f"background:{C.PANEL2}; border:0;"
        )
        self.event_list.setStyleSheet(
            f"QListWidget {{ background:{C.PANEL2}; color:{C.TEXT}; "
            f"border:1px solid {C.BORDER}; border-radius:7px; padding:3px; outline:0; }}"
            f"QListWidget::item {{ padding:6px 5px; border-bottom:1px solid {C.BORDER}; }}"
            f"QListWidget::item:selected {{ background:{C.PRI_GHO}; }}"
            f"QScrollBar:vertical {{ background:{C.PANEL}; width:10px; "
            f"margin:3px 2px 3px 0; border:0; border-radius:5px; }}"
            f"QScrollBar::handle:vertical {{ background:{C.BORDER_B}; "
            f"min-height:28px; border-radius:4px; margin:1px; }}"
            f"QScrollBar::handle:vertical:hover {{ background:{C.PRI_DIM}; }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ "
            f"height:0; border:0; background:transparent; }}"
            f"QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ "
            f"background:{C.PANEL}; }}"
            f"QScrollBar:horizontal {{ background:{C.PANEL}; height:8px; "
            f"border:0; margin:0; }}"
            f"QScrollBar::handle:horizontal {{ background:{C.BORDER_B}; "
            f"min-width:28px; border-radius:4px; }}"
            f"QScrollBar::handle:horizontal:hover {{ background:{C.PRI_DIM}; }}"
            f"QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ "
            f"width:0; border:0; background:transparent; }}"
            f"QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ "
            f"background:{C.PANEL}; }}"
        )
        self._content_stack.addWidget(self.event_list)

        empty_state = QWidget()
        empty_state.setObjectName("activity_empty_state")
        empty_state.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        empty_state.setAutoFillBackground(True)
        empty_state.setStyleSheet(
            f"QWidget#activity_empty_state {{ background:{C.PANEL}; border:0; }}"
        )
        empty_state.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        empty_layout = QVBoxLayout(empty_state)
        empty_layout.setContentsMargins(12, 12, 12, 12)
        empty_layout.addStretch(1)
        self.empty_label = QLabel("No activity recorded in this session.")
        self.empty_label.setObjectName("activity_empty_label")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        self.empty_label.setStyleSheet(
            f"color:{C.TEXT_DIM}; background:transparent; padding:10px;"
        )
        empty_layout.addWidget(self.empty_label)
        empty_layout.addStretch(1)
        self._content_stack.addWidget(empty_state)
        root.addWidget(self._content_stack, 1)

        self.filter_box.currentIndexChanged.connect(self._render)
        self.search_input.textChanged.connect(self._render)
        self._incoming.connect(self._append)

    @property
    def event_count(self) -> int:
        return len(self._events)

    @staticmethod
    def classify(message: str) -> str:
        text = str(message or "").strip()
        lowered = text.lower()
        if lowered.startswith(("err:", "error:", "[stderr]", "traceback")) or any(
            token in lowered for token in ("exception", "failed", "failure", "could not")
        ):
            return "errors"
        if lowered.startswith(("warn:", "warning:")) or "warning" in lowered:
            return "warnings"
        if lowered.startswith(("you:", "jarvis:", "j.a.r.v.i.s:")):
            return "conversations"
        if any(
            token in lowered
            for token in ("tool", "plugin", "command", "executing", "operation", "action:")
        ):
            return "actions"
        return "system"

    def append_event(self, message: str) -> None:
        text = str(message or "").strip()
        if text:
            self._incoming.emit(text)

    @pyqtSlot(str)
    def _append(self, message: str) -> None:
        self._events.append(
            {
                "time": datetime.now().strftime("%H:%M:%S"),
                "category": self.classify(message),
                "message": message,
            }
        )
        if len(self._events) > self._MAX_EVENTS:
            del self._events[: len(self._events) - self._MAX_EVENTS]
        self._render()

    @pyqtSlot()
    def clear(self) -> None:
        """Clear only the in-memory Activity view, not the application log/history."""
        self._events.clear()
        self._render()

    def _render(self, *_args) -> None:
        selected = str(self.filter_box.currentData() or "all")
        query = self.search_input.text().strip().casefold()
        self.event_list.clear()
        visible = [
            event
            for event in self._events
            if (selected == "all" or event["category"] == selected)
            and (not query or query in event["message"].casefold())
        ]
        colours = {
            "errors": C.RED,
            "warnings": C.ACC,
            "actions": C.GREEN,
            "conversations": C.PRI,
            "system": C.TEXT_MED,
        }
        labels = {
            "errors": "ERROR",
            "warnings": "WARN",
            "actions": "ACTION",
            "conversations": "CHAT",
            "system": "SYSTEM",
        }
        for event in reversed(visible):
            category = event["category"]
            item = QListWidgetItem(
                f"{event['time']}  {labels[category]}\n{event['message']}"
            )
            item.setForeground(QColor(colours[category]))
            item.setToolTip(event["message"])
            item.setData(Qt.ItemDataRole.UserRole, event.copy())
            self.event_list.addItem(item)
        if visible:
            self._content_stack.setCurrentWidget(self.event_list)
        else:
            if not self._events:
                self.empty_label.setText("No activity recorded in this session.")
            elif query:
                self.empty_label.setText("No activity matches your search.")
            elif selected != "all":
                self.empty_label.setText("No events in this category.")
            else:
                self.empty_label.setText("No activity matches the current filters.")
            self._content_stack.setCurrentWidget(self._content_stack.widget(1))
