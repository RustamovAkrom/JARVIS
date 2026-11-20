"""Task Engine desktop panel — UI-only adapter; engine ownership stays in Core."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QDateTime
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget, QDialog, QFormLayout, QLineEdit, QSpinBox, QTextEdit, QDialogButtonBox, QFileDialog, QDateTimeEdit, QCheckBox, QProgressBar, QTextBrowser

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

        self.details_title = QLabel("Select a task")
        self.details_title.setObjectName("task_details_title")
        root.addWidget(self.details_title)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        root.addWidget(self.progress_bar)
        self.details_browser = QTextBrowser()
        self.details_browser.setObjectName("task_details")
        self.details_browser.setOpenExternalLinks(False)
        self.details_browser.setMinimumHeight(130)
        root.addWidget(self.details_browser, 1)
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

    def _render_details(self, task):
        if not task:
            self.details_title.setText("Select a task")
            self.progress_bar.setValue(0)
            self.details_browser.clear()
            return

        title = str(task.get("title") or task.get("task") or task.get("id") or "Task")
        status = str(task.get("status") or "unknown").upper()
        used = task.get("used") if isinstance(task.get("used"), dict) else {}
        budget = task.get("budget") if isinstance(task.get("budget"), dict) else {}
        rounds_used = int(used.get("rounds") or 0)
        rounds_budget = int(budget.get("rounds") or 0)
        seconds_used = float(used.get("seconds") or 0)
        seconds_budget = float(budget.get("seconds") or 0)
        if rounds_budget:
            progress = min(100, round(rounds_used / rounds_budget * 100))
        elif seconds_budget:
            progress = min(100, round(seconds_used / seconds_budget * 100))
        else:
            progress = 0
        self.details_title.setText(f"{title} · {status}")
        self.progress_bar.setValue(progress)
        self.progress_bar.setFormat(f"{progress}% · {rounds_used}/{rounds_budget or '∞'} rounds")
        lines = [
            f"<b>Status:</b> {status}",
            f"<b>Agent:</b> {self._html(task.get('agent') or 'general')}",
            f"<b>Created:</b> {self._format_time(task.get('created'))}",
            f"<b>Started:</b> {self._format_time(task.get('started'))}",
            f"<b>Time used:</b> {seconds_used / 60:.1f} / {seconds_budget / 60:.1f} min" if seconds_budget else f"<b>Time used:</b> {seconds_used / 60:.1f} min",
            f"<b>Rounds:</b> {rounds_used} / {rounds_budget or 'unlimited'}",
        ]
        if task.get("error"):
            lines.append(f"<br><b>Error:</b> {self._html(task['error'])}")
        result = task.get("result") or task.get("report")
        if result:
            lines.append(f"<br><b>Result:</b><br>{self._html(result)}")
        events = task.get("events") or []
        if events:
            lines.append("<br><b>Event history:</b>")
            for event in events[-20:]:
                if not isinstance(event, dict):
                    continue
                stamp = self._format_time(event.get("t"))
                kind = self._html(event.get("kind") or "event")
                text = self._html(event.get("text") or "")
                lines.append(f"<br><span style='color:#8b9aa8'>[{stamp}]</span> <b>{kind}</b> — {text}")
        self.details_browser.setHtml("<br>".join(lines))

    @staticmethod
    def _html(value):
        import html
        return html.escape(str(value or "")).replace("\\n", "<br>")

    @staticmethod
    def _format_time(value):
        try:
            return __import__("datetime").datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError, OSError):
            return "—"

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
        task = self._tasks.get(tid, {})
        self._render_details(task)
        active = task.get("status") in _ACTIVE
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
