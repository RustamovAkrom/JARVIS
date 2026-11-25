"""Cross-platform reminder panel backed by the OS scheduler."""
from __future__ import annotations

from datetime import datetime
import math
from collections.abc import Mapping
from PyQt6.QtCore import Qt, pyqtSignal, QDateTime
from gui.themes.tokens import C

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QLineEdit, QDateTimeEdit, QMessageBox,
)


class RemindersPanel(QWidget):
    create_requested = pyqtSignal(dict)
    cancel_requested = pyqtSignal(str)
    refresh_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("RemindersPanel")
        self.setStyleSheet(self._stylesheet())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 9, 9, 9)
        layout.setSpacing(8)
        layout.addWidget(QLabel("REMINDERS"))
        self.message = QLineEdit()
        self.message.setObjectName("reminder_message")
        self.message.setAccessibleName("Reminder message")
        self.message.setPlaceholderText("What should JARVIS remind you about?")
        layout.addWidget(self.message)
        self.when = QDateTimeEdit(QDateTime.currentDateTime().addSecs(600))
        self.when.setObjectName("reminder_due_datetime")
        self.when.setAccessibleName("Reminder date and time")
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.when.setMinimumDateTime(QDateTime.currentDateTime().addSecs(60))
        layout.addWidget(self.when)
        row = QHBoxLayout()
        self.create_button = QPushButton("+ Schedule Reminder")
        self.create_button.setAccessibleName("Schedule reminder")
        self.create_button.setMinimumHeight(34)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setAccessibleName("Refresh reminders")
        self.refresh_button.setMinimumHeight(34)
        row.addWidget(self.create_button, 1)
        row.addWidget(self.refresh_button)
        layout.addLayout(row)
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("reminder_list")
        self.list_widget.setAccessibleName("Upcoming reminders")
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        layout.addWidget(self.list_widget, 1)
        self.cancel_button = QPushButton("Cancel Selected Reminder")
        self.cancel_button.setAccessibleName("Cancel selected reminder")
        self.cancel_button.setMinimumHeight(34)
        self.cancel_button.setEnabled(False)
        layout.addWidget(self.cancel_button)
        self.info = QLabel("Reminders are registered with the operating system and persist after JARVIS closes.")
        self.info.setWordWrap(True)
        self.info.setStyleSheet(f"color: {C.TEXT_DIM};")
        layout.addWidget(self.info)
        self.create_button.clicked.connect(self._create)
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        self.cancel_button.clicked.connect(self._cancel)
        self.list_widget.currentItemChanged.connect(self._update_cancel_state)
        self.set_reminders([])

    def _stylesheet(self):
        return f"""
        QWidget#RemindersPanel {{ background: transparent; color: {C.TEXT}; }}
        QWidget#RemindersPanel QLabel {{ color: {C.TEXT}; background: transparent; }}
        QWidget#RemindersPanel QLineEdit, QWidget#RemindersPanel QDateTimeEdit {{
            background: {C.PANEL2}; color: {C.TEXT}; border: 1px solid {C.BORDER};
            border-radius: 6px; padding: 7px;
        }}
        QWidget#RemindersPanel QListWidget {{
            background: {C.PANEL}; color: {C.TEXT}; border: 1px solid {C.BORDER}; border-radius: 7px;
        }}
        QWidget#RemindersPanel QListWidget::item {{ padding: 8px; border-bottom: 1px solid {C.BORDER}; }}
        QWidget#RemindersPanel QListWidget::item:selected {{ background: {C.PRI_GHO}; color: {C.TEXT}; }}
        QWidget#RemindersPanel QPushButton {{ background: {C.PANEL2}; color: {C.TEXT};
            border: 1px solid {C.BORDER}; border-radius: 6px; padding: 7px; }}
        QWidget#RemindersPanel QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        QWidget#RemindersPanel QPushButton:disabled {{ color: {C.TEXT_DIM}; }}
        """

    def refresh_theme(self):
        self.setStyleSheet(self._stylesheet())
        self.info.setStyleSheet(f"color: {C.TEXT_DIM};")
        self.update()

    def _create(self):
        message = self.message.text().strip()
        due = self.when.dateTime()
        if not message:
            QMessageBox.warning(self, "Reminder", "Enter reminder text.")
            return
        if due <= QDateTime.currentDateTime():
            QMessageBox.warning(self, "Reminder", "Choose a future date and time.")
            return
        self.create_requested.emit({
            "message": message,
            "date": due.toString("yyyy-MM-dd"),
            "time": due.toString("HH:mm"),
        })

    def _update_cancel_state(self, current=None, _previous=None):
        reminder_id = (
            current.data(Qt.ItemDataRole.UserRole)
            if current is not None
            else None
        )
        self.cancel_button.setEnabled(bool(reminder_id))

    def _cancel(self):
        item = self.list_widget.currentItem()
        reminder_id = (
            item.data(Qt.ItemDataRole.UserRole)
            if item is not None
            else None
        )
        if reminder_id:
            self.cancel_requested.emit(str(reminder_id))

    def set_reminders(self, reminders):
        # The panel refreshes periodically. Keep the selected reminder stable
        # across snapshots so the 15-second refresh does not interrupt cancel.
        current = self.list_widget.currentItem()
        selected_id = (
            current.data(Qt.ItemDataRole.UserRole)
            if current is not None
            else None
        )
        now = datetime.now().timestamp()
        valid_rows = []
        for row in reminders or []:
            if not isinstance(row, Mapping):
                continue
            try:
                reminder_id = row.get("id")
                due = float(row.get("due", 0))
                if not reminder_id or not math.isfinite(due) or due <= now:
                    continue
                stamp = datetime.fromtimestamp(due).strftime("%Y-%m-%d %H:%M")
                valid_rows.append((
                    due,
                    str(reminder_id),
                    str(row.get("message") or "Reminder"),
                    stamp,
                ))
            except (TypeError, ValueError, OverflowError, OSError):
                continue

        valid_rows.sort(key=lambda entry: (entry[0], entry[1]))
        self.list_widget.blockSignals(True)
        self.list_widget.setUpdatesEnabled(False)
        try:
            self.list_widget.clear()
            restored_item = None
            for _due, reminder_id, message, stamp in valid_rows:
                item = QListWidgetItem(f"{stamp}  |  {message}")
                item.setData(Qt.ItemDataRole.UserRole, reminder_id)
                item.setToolTip(message)
                self.list_widget.addItem(item)
                if reminder_id == selected_id:
                    restored_item = item

            if not valid_rows:
                item = QListWidgetItem("No upcoming reminders.")
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                self.list_widget.addItem(item)

            if restored_item is not None:
                self.list_widget.setCurrentItem(restored_item)
        finally:
            self.list_widget.setUpdatesEnabled(True)
            self.list_widget.blockSignals(False)
        self._update_cancel_state(self.list_widget.currentItem())
