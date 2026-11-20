"""Task Engine desktop panel — UI-only adapter; engine ownership stays in Core."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QDateTime
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget, QDialog, QFormLayout, QLineEdit, QSpinBox, QTextEdit, QDialogButtonBox

_ACTIVE = {"queued", "scheduled", "running", "waiting_quota"}


class TaskPanel(QWidget):
    """Render task snapshots and emit commands; never owns worker threads."""

    cancel_requested = pyqtSignal(str)
    finish_requested = pyqtSignal(str)
    steer_requested = pyqtSignal(str)
    task_selected = pyqtSignal(str)
    refresh_requested = pyqtSignal()
    create_requested = pyqtSignal()
    create_task_requested = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TaskPanel")
        self._tasks = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(9, 9, 9, 9)
        root.setSpacing(7)
        root.addWidget(QLabel("TASKS"))
        controls = QHBoxLayout()
        self.filter_box = QComboBox()
        for label, value in (("All", "all"), ("Active", "active"), ("Completed", "done"), ("Failed", "failed")):
            self.filter_box.addItem(label, value)
        controls.addWidget(self.filter_box, 1)
        self.new_button = QPushButton("New Task")
        controls.addWidget(self.new_button)
        self.refresh_button = QPushButton("Refresh")
        controls.addWidget(self.refresh_button)
        root.addLayout(controls)
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("task_list")
        root.addWidget(self.list_widget, 1)
        self.empty_label = QLabel("No tasks yet.")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.empty_label)
        actions = QHBoxLayout()
        self.cancel_button = QPushButton("Cancel")
        self.finish_button = QPushButton("Finish & Report")
        self.steer_button = QPushButton("Steer")
        for button in (self.cancel_button, self.finish_button, self.steer_button):
            actions.addWidget(button)
        root.addLayout(actions)
        self.new_button.clicked.connect(self._create_task_dialog)
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        self.filter_box.currentIndexChanged.connect(self.render)
        self.list_widget.currentItemChanged.connect(self._selection_changed)
        self.cancel_button.clicked.connect(self._cancel_selected)
        self.finish_button.clicked.connect(self._finish_selected)
        self.steer_button.clicked.connect(self._steer_selected)
        self.render()

    def set_agent_profiles(self, profiles):
        self._profiles = dict(profiles or {})
        self._profile_names = sorted(self._profiles)

    def _create_task_dialog(self):
        profiles = getattr(self, "_profiles", {})
        dialog = QDialog(self)
        dialog.setWindowTitle("Create Task")
        dialog.setMinimumWidth(460)
        form = QFormLayout(dialog)
        title = QLineEdit()
        title.setPlaceholderText("Optional task title")
        description = QTextEdit()
        description.setPlaceholderText("Describe what JARVIS should accomplish...")
        description.setMinimumHeight(110)
        agent = QComboBox()
        for name, profile in sorted(profiles.items()):
            agent.addItem(getattr(profile, "title", name), name)
        if agent.count() == 0:
            agent.addItem("General", "general")
        minutes = QSpinBox()
        minutes.setRange(1, 720)
        minutes.setValue(10)
        rounds = QSpinBox()
        rounds.setRange(1, 400)
        rounds.setValue(40)
        delivery = QComboBox()
        delivery.addItem("Voice notification", "voice")
        delivery.addItem("Panel only", "panel")
        delivery.addItem("Silent", "silent")
        schedule_enabled = QCheckBox("Schedule for later")
        schedule = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        schedule.setCalendarPopup(True)
        schedule.setDisplayFormat("yyyy-MM-dd HH:mm")
        schedule.setEnabled(False)
        schedule_enabled.toggled.connect(schedule.setEnabled)
        files_label = QLabel("No attachments")
        files = []
        attach_button = QPushButton("Attach files")
        attach_button.clicked.connect(lambda: self._choose_task_files(files, files_label))
        file_row = QHBoxLayout()
        file_row.addWidget(attach_button)
        file_row.addWidget(files_label, 1)
        form.addRow("Title", title)
        form.addRow("Task", description)
        form.addRow("Agent", agent)
        form.addRow("Time budget (minutes)", minutes)
        form.addRow("Round budget", rounds)
        form.addRow("Delivery", delivery)
        form.addRow(schedule_enabled, schedule)
        form.addRow("Attachments", file_row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        task_text = description.toPlainText().strip()
        if not task_text:
            return
        self.create_task_requested.emit({
            "task": task_text,
            "title": title.text().strip(),
            "agent": agent.currentData(),
            "minutes": minutes.value(),
            "rounds": rounds.value(),
            "deliver": delivery.currentData(),
            "origin": "desk",
            "not_before": schedule.dateTime().toSecsSinceEpoch() if schedule_enabled.isChecked() else 0.0,
            "files": list(files),
        })

    def _choose_task_files(self, files, label):
        selected, _ = QFileDialog.getOpenFileNames(
            self, "Attach task files", "", "All files (*.*)"
        )
        if selected:
            files[:] = selected
            label.setText(f"{len(files)} file(s) selected")

    def set_tasks(self, tasks):
        rows = {}
        for task in tasks or []:
            if isinstance(task, dict) and str(task.get("id") or "").strip():
                rows[str(task["id"])] = dict(task)
        self._tasks = rows
        self.render()

    def selected_task_id(self):
        item = self.list_widget.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def render(self, *_):
        selected = self.selected_task_id()
        mode = self.filter_box.currentData()
        rows = sorted(self._tasks.values(), key=lambda t: float(t.get("created") or 0), reverse=True)
        if mode == "active":
            rows = [t for t in rows if t.get("status") in _ACTIVE]
        elif mode == "done":
            rows = [t for t in rows if t.get("status") == "done"]
        elif mode == "failed":
            rows = [t for t in rows if t.get("status") in {"failed", "cancelled"}]
        self.list_widget.clear()
        for task in rows:
            status = str(task.get("status") or "unknown").upper()
            title = str(task.get("title") or task.get("task") or task.get("id"))
            used, budget = task.get("used") or {}, task.get("budget") or {}
            item = QListWidgetItem(f"{status} · {title}\n{used.get('rounds', 0)}/{budget.get('rounds', '?')} rounds")
            item.setToolTip(str(task.get("task") or title))
            item.setData(Qt.ItemDataRole.UserRole, str(task.get("id")))
            self.list_widget.addItem(item)
            if str(task.get("id")) == selected:
                self.list_widget.setCurrentItem(item)
        self.empty_label.setVisible(self.list_widget.count() == 0)
        self.list_widget.setVisible(self.list_widget.count() > 0)
        self._selection_changed(self.list_widget.currentItem(), None)

    def _selection_changed(self, current, _previous):
        tid = str(current.data(Qt.ItemDataRole.UserRole) or "") if current else ""
        active = self._tasks.get(tid, {}).get("status") in _ACTIVE
        for button in (self.cancel_button, self.finish_button, self.steer_button):
            button.setEnabled(active)
        if tid:
            self.task_selected.emit(tid)

    def _cancel_selected(self):
        tid = self.selected_task_id()
        if tid:
            self.cancel_requested.emit(tid)

    def _finish_selected(self):
        tid = self.selected_task_id()
        if tid:
            self.finish_requested.emit(tid)

    def _steer_selected(self):
        tid = self.selected_task_id()
        if tid:
            self.steer_requested.emit(tid)
