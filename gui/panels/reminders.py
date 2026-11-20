"""Cross-platform reminder panel backed by the OS scheduler."""
from __future__ import annotations

from datetime import datetime
from PyQt6.QtCore import Qt, pyqtSignal, QDateTime
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
        self.setStyleSheet("""
        QWidget#RemindersPanel { background: transparent; color: #c9e2e2; }
        QWidget#RemindersPanel QLineEdit, QWidget#RemindersPanel QDateTimeEdit {
            background: rgba(3,18,23,220); color: #d8eeee; border: 1px solid #245b60;
            border-radius: 5px; padding: 7px;
        }
        QWidget#RemindersPanel QListWidget {
            background: rgba(2,12,16,180); border: 1px solid #204c52; border-radius: 6px;
        }
        QWidget#RemindersPanel QListWidget::item { padding: 8px; border-bottom: 1px solid #18383c; }
        QWidget#RemindersPanel QListWidget::item:selected { background: #0c5f59; }
        QWidget#RemindersPanel QPushButton { background: #092327; color: #bce9e4;
            border: 1px solid #28666a; border-radius: 5px; padding: 7px; }
        QWidget#RemindersPanel QPushButton:hover { background: #12494b; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 9, 9, 9)
        layout.setSpacing(8)
        layout.addWidget(QLabel("REMINDERS"))
        self.message = QLineEdit()
        self.message.setPlaceholderText("What should JARVIS remind you about?")
        layout.addWidget(self.message)
        self.when = QDateTimeEdit(QDateTime.currentDateTime().addSecs(600))
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.when.setMinimumDateTime(QDateTime.currentDateTime().addSecs(60))
        layout.addWidget(self.when)
        row = QHBoxLayout()
        self.create_button = QPushButton("+ Schedule Reminder")
        self.refresh_button = QPushButton("Refresh")
        row.addWidget(self.create_button, 1)
        row.addWidget(self.refresh_button)
        layout.addLayout(row)
        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget, 1)
        self.cancel_button = QPushButton("Cancel Selected Reminder")
        layout.addWidget(self.cancel_button)
        self.info = QLabel("Reminders are registered with the operating system and persist after JARVIS closes.")
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color: #6e9a9c;")
        layout.addWidget(self.info)
        self.create_button.clicked.connect(self._create)
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        self.cancel_button.clicked.connect(self._cancel)
        self.set_reminders([])

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

    def _cancel(self):
        item = self.list_widget.currentItem()
        if item:
            reminder_id = item.data(Qt.ItemDataRole.UserRole)
            if reminder_id:
                self.cancel_requested.emit(str(reminder_id))

    def set_reminders(self, reminders):
        self.list_widget.clear()
        now = datetime.now().timestamp()
        for row in reminders or []:
            try:
                due = float(row.get("due", 0))
                if due <= now:
                    continue
                stamp = datetime.fromtimestamp(due).strftime("%Y-%m-%d %H:%M")
                item = QListWidgetItem(f"{stamp}  |  {row.get('message', 'Reminder')}")
                item.setData(Qt.ItemDataRole.UserRole, row.get("id"))
                self.list_widget.addItem(item)
            except (TypeError, ValueError, OSError):
                continue
        if not self.list_widget.count():
            item = QListWidgetItem("No upcoming reminders.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list_widget.addItem(item)
