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
    retry_requested = pyqtSignal(str)
    task_selected = pyqtSignal(str)
    refresh_requested = pyqtSignal()
    create_requested = pyqtSignal()
    create_task_requested = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TaskPanel")
        self.setStyleSheet("""
        QWidget#TaskPanel { background: transparent; color: #d8e8e8; }
        QWidget#TaskPanel QLabel { color: #c4d8d8; }
        QWidget#TaskPanel QLabel#task_details_title { color: #67e8d0; font-weight: 600; padding: 5px 2px; }\n        QWidget#TaskPanel QLabel#task_empty { color: #6e9a9c; padding: 18px; }\n        QWidget#TaskPanel QLabel#task_stats { color: #5f999a; font-size: 10px; padding: 2px 4px; }
        QWidget#TaskPanel QLineEdit, QWidget#TaskPanel QComboBox {
            background: rgba(3, 18, 23, 220); color: #d8eeee;
            border: 1px solid #245b60; border-radius: 5px; padding: 6px 8px;
        }
        QWidget#TaskPanel QPushButton {
            background: rgba(9, 35, 39, 230); color: #bce9e4;
            border: 1px solid #28666a; border-radius: 5px; padding: 6px 8px;
        }
        QWidget#TaskPanel QPushButton:hover { background: #12494b; border-color: #45b9a9; }
        QWidget#TaskPanel QPushButton:disabled { color: #527176; border-color: #254044; background: #101b1e; }
        QWidget#TaskPanel QListWidget {
            background: rgba(2, 12, 16, 180); border: 1px solid #204c52;
            border-radius: 6px; padding: 4px; outline: none;
        }
        QWidget#TaskPanel QListWidget::item {
            color: #c9e2e2; background: rgba(8, 30, 34, 160);
            border: 1px solid transparent; border-radius: 4px; padding: 7px 8px; margin: 2px 0;
        }
        QWidget#TaskPanel QListWidget::item:selected {
            color: #eaffff; background: rgba(12, 95, 89, 160); border: 1px solid #39b8a6;
        }
        QWidget#TaskPanel QProgressBar {
            background: #10272a; color: #bfe8e3; border: 1px solid #24545a;
            border-radius: 4px; text-align: center; min-height: 17px;
        }
        QWidget#TaskPanel QProgressBar::chunk { background: #13a88f; border-radius: 3px; }
        QWidget#TaskPanel QTextBrowser {
            background: rgba(3, 15, 19, 210); color: #c5dada;
            border: 1px solid #204c52; border-radius: 6px; padding: 8px;
        }
        """)
        self._tasks = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(9, 9, 9, 9)
        root.setSpacing(7)
        root.addWidget(QLabel("TASKS"))
        self.search_box = QLineEdit()
        self.search_box.setObjectName("task_search")
        self.search_box.setPlaceholderText("Search tasks...")
        root.addWidget(self.search_box)
        controls = QHBoxLayout()
        controls.setSpacing(6)
        self.filter_box = QComboBox()
        for label, value in (("All", "all"), ("Active", "active"), ("Completed", "done"), ("Failed", "failed")):
            self.filter_box.addItem(label, value)
        controls.addWidget(self.filter_box, 1)
        self.agent_filter = QComboBox()
        self.agent_filter.setToolTip("Filter by agent profile")
        self.agent_filter.addItem("All Agents", "all")
        controls.addWidget(self.agent_filter, 1)
        self.sort_box = QComboBox()
        for label, value in (("Newest", "newest"), ("Oldest", "oldest"), ("Status", "status"), ("Progress", "progress")):
            self.sort_box.addItem(label, value)
        self.sort_box.setToolTip("Sort tasks")
        controls.addWidget(self.sort_box, 1)
        self.new_button = QPushButton("New Task")
        controls.addWidget(self.new_button)
        self.refresh_button = QPushButton("Refresh")
        controls.addWidget(self.refresh_button)
        root.addLayout(controls)
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("task_list")
        root.addWidget(self.list_widget, 2)

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
        self.details_browser.setMinimumHeight(100)
        root.addWidget(self.details_browser, 2)
        self.export_button = QPushButton("Export Report")
        root.addWidget(self.export_button)
        self.stats_label = QLabel("0 tasks")
        self.stats_label.setObjectName("task_stats")
        root.addWidget(self.stats_label)
        self.empty_label = QLabel("No tasks yet. Create your first task.")
        self.empty_label.setObjectName("task_empty")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.empty_label)
        actions = QHBoxLayout()
        self.retry_button = QPushButton("Retry")
        self.cancel_button = QPushButton("Cancel")
        self.finish_button = QPushButton("Finish & Report")
        self.steer_button = QPushButton("Steer")
        for button in (self.retry_button, self.cancel_button, self.finish_button, self.steer_button):
            actions.addWidget(button)
        root.addLayout(actions)
        self.new_button.clicked.connect(self._create_task_dialog)
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        self.filter_box.currentIndexChanged.connect(self.render)
        self.sort_box.currentIndexChanged.connect(self.render)
        self.agent_filter.currentIndexChanged.connect(self.render)
        self.search_box.textChanged.connect(self.render)
        self.export_button.clicked.connect(self._export_selected_report)
        self.list_widget.currentItemChanged.connect(self._selection_changed)
        self.retry_button.clicked.connect(self._retry_selected)
        self.cancel_button.clicked.connect(self._cancel_selected)
        self.finish_button.clicked.connect(self._finish_selected)
        self.steer_button.clicked.connect(self._steer_selected)
        self.render()

    def set_agent_profiles(self, profiles):
        self._profiles = dict(profiles or {})
        self._profile_names = sorted(self._profiles)
        current = self.agent_filter.currentData() if hasattr(self, "agent_filter") else "all"
        self.agent_filter.blockSignals(True)
        self.agent_filter.clear()
        self.agent_filter.addItem("All Agents", "all")
        for name in self._profile_names:
            self.agent_filter.addItem(name.replace("_", " ").title(), name)
        idx = self.agent_filter.findData(current)
        self.agent_filter.setCurrentIndex(max(0, idx))
        self.agent_filter.blockSignals(False)

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
            self.progress_bar.setVisible(False)
            self.details_browser.setVisible(False)
            self.export_button.setEnabled(False)
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
        self.details_title.setText(f"{title}  |  {status}")
        self.progress_bar.setVisible(True)
        self.details_browser.setVisible(True)
        self.export_button.setEnabled(bool(task.get("report") or task.get("result")))
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

    def _export_selected_report(self):
        task = self._tasks.get(self.selected_task_id(), {})
        report = str(task.get("report") or task.get("result") or "")
        if not report:
            return
        from pathlib import Path
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Task Report", f"{task.get('title') or 'task-report'}.md",
            "Markdown (*.md);;Text (*.txt)"
        )
        if not path:
            return
        try:
            Path(path).write_text(report, encoding="utf-8")
        except OSError as exc:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Export failed", str(exc))

    def selected_task_id(self):
        item = self.list_widget.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    @staticmethod
    def _progress(task):
        used = task.get("used") if isinstance(task.get("used"), dict) else {}
        budget = task.get("budget") if isinstance(task.get("budget"), dict) else {}
        rounds = float(used.get("rounds") or 0)
        total = float(budget.get("rounds") or 0)
        seconds = float(used.get("seconds") or 0)
        time_budget = float(budget.get("seconds") or 0)
        if total:
            return min(100.0, rounds / total * 100)
        if time_budget:
            return min(100.0, seconds / time_budget * 100)
        return 0.0

    def render(self, *_):
        selected = self.selected_task_id()
        mode = self.filter_box.currentData()
        query = self.search_box.text().strip().casefold()
        agent = self.agent_filter.currentData()
        rows = list(self._tasks.values())
        if mode == "active":
            rows = [t for t in rows if t.get("status") in _ACTIVE]
        elif mode == "done":
            rows = [t for t in rows if t.get("status") == "done"]
        elif mode == "failed":
            rows = [t for t in rows if t.get("status") in {"failed", "cancelled"}]
        if agent and agent != "all":
            rows = [t for t in rows if str(t.get("agent") or "").lower() == str(agent).lower()]
        if query:
            rows = [t for t in rows if query in str(t.get("title") or "").casefold()
                    or query in str(t.get("task") or "").casefold()
                    or query in str(t.get("agent") or "").casefold()
                    or query in str(t.get("status") or "").casefold()]
        sort_mode = self.sort_box.currentData()
        if sort_mode == "oldest":
            rows.sort(key=lambda t: float(t.get("created") or 0))
        elif sort_mode == "status":
            rows.sort(key=lambda t: (str(t.get("status") or ""), -float(t.get("created") or 0)))
        elif sort_mode == "progress":
            rows.sort(key=lambda t: self._progress(t), reverse=True)
        else:
            rows.sort(key=lambda t: float(t.get("created") or 0), reverse=True)
        all_tasks = list(self._tasks.values())
        running = sum(t.get("status") == "running" for t in all_tasks)
        done = sum(t.get("status") == "done" for t in all_tasks)
        failed = sum(t.get("status") == "failed" for t in all_tasks)
        self.stats_label.setText(f"{len(all_tasks)} tasks · {running} running · {done} done · {failed} failed")
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
        has_rows = self.list_widget.count() > 0
        self.empty_label.setVisible(not has_rows)
        self.list_widget.setVisible(has_rows)
        self._selection_changed(self.list_widget.currentItem(), None)

    def _selection_changed(self, current, _previous):
        tid = str(current.data(Qt.ItemDataRole.UserRole) or "") if current else ""
        task = self._tasks.get(tid, {})
        self._render_details(task)
        has_task = bool(task)
        self.details_title.setVisible(has_task)
        self.progress_bar.setVisible(has_task)
        self.details_browser.setVisible(has_task)
        active = task.get("status") in _ACTIVE
        self.retry_button.setEnabled(task.get("status") in {"failed", "cancelled"})
        for button in (self.cancel_button, self.finish_button, self.steer_button):
            button.setEnabled(active)
        if tid:
            self.task_selected.emit(tid)

    def _retry_selected(self):
        tid = self.selected_task_id()
        if tid:
            self.retry_requested.emit(tid)

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
