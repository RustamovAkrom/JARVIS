"""Task Engine desktop panel — UI-only adapter; engine ownership stays in Core."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QDateTime
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget, QDialog, QFormLayout, QGridLayout, QLineEdit, QSpinBox, QTextEdit, QDialogButtonBox, QFileDialog, QDateTimeEdit, QCheckBox, QProgressBar, QTextBrowser, QSizePolicy, QMessageBox, QApplication

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
    delete_task_requested = pyqtSignal(str)
    delete_history_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TaskPanel")
        self.setStyleSheet(self._panel_stylesheet())
        self._tasks = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(9, 9, 9, 9)
        root.setSpacing(7)
        root.addWidget(QLabel("TASKS"))
        self.active_summary = QLabel("No active background tasks")
        self.active_summary.setObjectName("task_active_summary")
        self.active_summary.setWordWrap(True)
        self.active_summary.setMaximumHeight(62)
        light = self._is_light_theme()
        self.active_summary.setStyleSheet(
            "color: #087f70; background: #e8f7f4; border: 1px solid #b9ddd6; border-radius: 5px; padding: 6px;"
            if light else
            "color: #67e8d0; background: rgba(5, 24, 29, 150); border: 1px solid #245b60; border-radius: 5px; padding: 6px;"
        )
        root.addWidget(self.active_summary)
        self.search_box = QLineEdit()
        self.search_box.setObjectName("task_search")
        self.search_box.setPlaceholderText("Search tasks...")
        root.addWidget(self.search_box)
        controls = QVBoxLayout()
        controls.setSpacing(5)
        filter_row = QHBoxLayout()
        filter_row.setSpacing(5)
        self.filter_box = QComboBox()
        for label, value in (("Active", "active"), ("History", "history"), ("All", "all")):
            self.filter_box.addItem(label, value)
        self.filter_box.setToolTip("Active tasks are shown by default. Completed tasks are available in History.")
        self.agent_filter = QComboBox()
        self.agent_filter.setToolTip("Filter by agent profile")
        self.agent_filter.addItem("All Agents", "all")
        self.sort_box = QComboBox()
        for label, value in (("Newest", "newest"), ("Oldest", "oldest"), ("Status", "status"), ("Progress", "progress")):
            self.sort_box.addItem(label, value)
        self.sort_box.setToolTip("Sort tasks")
        self.sort_box.setCurrentIndex(self.sort_box.findData("newest"))
        for widget in (self.filter_box, self.agent_filter, self.sort_box):
            widget.setMinimumWidth(0)
            widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            filter_row.addWidget(widget, 1)
        controls.addLayout(filter_row)
        action_row = QHBoxLayout()
        action_row.setSpacing(5)
        self.new_button = QPushButton("+ New Task")
        self.new_button.setMinimumHeight(30)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setMinimumHeight(30)
        action_row.addWidget(self.new_button, 1)
        action_row.addWidget(self.refresh_button, 1)
        controls.addLayout(action_row)
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
        history_actions = QHBoxLayout()
        self.delete_button = QPushButton("Delete Task")
        self.clear_history_button = QPushButton("Delete History")
        self.delete_button.setToolTip("Permanently delete selected task and its task-owned files")
        self.clear_history_button.setToolTip("Permanently delete all completed, failed and cancelled tasks")
        history_actions.addWidget(self.delete_button)
        history_actions.addWidget(self.clear_history_button)
        root.addLayout(history_actions)
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
        self.delete_button.clicked.connect(self._delete_selected)
        self.clear_history_button.clicked.connect(self._delete_history)
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

    @staticmethod
    def _is_light_theme():
        return QApplication.palette().color(QPalette.ColorRole.Window).lightness() > 145

    def _panel_stylesheet(self):
        light = self._is_light_theme()
        bg = "#f7fafc" if light else "#071216"
        surface = "#ffffff" if light else "#0a1b20"
        text = "#18333a" if light else "#d8eeee"
        muted = "#526d75" if light else "#8baeb2"
        border = "#c5d8df" if light else "#245b60"
        accent = "#087f70" if light else "#13a88f"
        selected_bg = "#d8f2ed" if light else "#12494b"
        return f"""
        QWidget#TaskPanel {{ background: transparent; color: {text}; }}
        QWidget#TaskPanel QLabel {{ color: {text}; }}
        QWidget#TaskPanel QLabel#task_details_title {{ color: {accent}; font-weight: 600; padding: 5px 2px; }}
        QWidget#TaskPanel QLabel#task_empty {{ color: {muted}; padding: 18px; }}
        QWidget#TaskPanel QLabel#task_stats {{ color: {muted}; font-size: 10px; padding: 2px 4px; }}
        QWidget#TaskPanel QLineEdit, QWidget#TaskPanel QComboBox {{
            background: {surface}; color: {text}; border: 1px solid {border};
            border-radius: 6px; padding: 6px 8px; min-height: 22px;
        }}
        QWidget#TaskPanel QPushButton {{
            background: {surface}; color: {text}; border: 1px solid {border};
            border-radius: 6px; padding: 6px 8px; min-height: 24px;
        }}
        QWidget#TaskPanel QPushButton:hover {{ background: {selected_bg}; border-color: {accent}; }}
        QWidget#TaskPanel QPushButton:disabled {{ color: {muted}; border-color: {border}; }}
        QWidget#TaskPanel QListWidget {{ background: {bg}; border: 1px solid {border}; border-radius: 7px; padding: 4px; outline: none; }}
        QWidget#TaskPanel QListWidget::item {{ color: {text}; background: {surface}; border: 1px solid transparent; border-radius: 5px; padding: 8px; margin: 2px 0; }}
        QWidget#TaskPanel QListWidget::item:selected {{ color: {text}; background: {selected_bg}; border: 1px solid {accent}; }}
        QWidget#TaskPanel QProgressBar {{ background: {surface}; color: {text}; border: 1px solid {border}; border-radius: 5px; text-align: center; min-height: 18px; }}
        QWidget#TaskPanel QProgressBar::chunk {{ background: {accent}; border-radius: 4px; }}
        QWidget#TaskPanel QTextBrowser {{ background: {surface}; color: {text}; border: 1px solid {border}; border-radius: 7px; padding: 8px; }}
        """
    
    def _dialog_stylesheet(self):
        light = self._is_light_theme()
        bg = "#f7fafc" if light else "#071216"
        surface = "#ffffff" if light else "#0a1b20"
        text = "#18333a" if light else "#d8eeee"
        muted = "#526d75" if light else "#a9c5c7"
        border = "#c5d8df" if light else "#24565d"
        accent = "#087f70" if light else "#22d3b2"
        return f"""
        QDialog#CreateTaskDialog {{ background: {bg}; color: {text}; }}
        QDialog#CreateTaskDialog QLabel {{ color: {muted}; background: transparent; border: none; }}
        QDialog#CreateTaskDialog QLabel#dialog_heading {{ color: {accent}; font-size: 16px; font-weight: 600; }}
        QDialog#CreateTaskDialog QLineEdit, QDialog#CreateTaskDialog QTextEdit,
        QDialog#CreateTaskDialog QComboBox, QDialog#CreateTaskDialog QSpinBox,
        QDialog#CreateTaskDialog QDateTimeEdit {{ background: {surface}; color: {text}; border: 1px solid {border}; border-radius: 7px; padding: 8px 10px; min-height: 24px; }}
        QDialog#CreateTaskDialog QCheckBox {{ color: {muted}; spacing: 8px; }}
        QDialog#CreateTaskDialog QPushButton {{ background: {surface}; color: {text}; border: 1px solid {border}; border-radius: 7px; padding: 8px 14px; min-width: 82px; }}
        QDialog#CreateTaskDialog QPushButton:hover {{ border-color: {accent}; }}
        QDialog#CreateTaskDialog QPushButton:default {{ background: {accent}; color: white; }}
        """
    
    def _create_task_dialog(self):
        profiles = getattr(self, "_profiles", {})
        dialog = QDialog(self)
        dialog.setObjectName("CreateTaskDialog")
        dialog.setWindowTitle("Create Task")
        dialog.setMinimumSize(560, 520)
        dialog.resize(640, 620)
        dialog.setSizeGripEnabled(True)
        dialog.setStyleSheet(self._dialog_stylesheet())
        root = QVBoxLayout(dialog)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(14)

        heading = QLabel("Create a background task")
        heading.setObjectName("dialog_heading")
        root.addWidget(heading)
        subtitle = QLabel("Configure the task, agent and execution limits.")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        title = QLineEdit()
        title.setPlaceholderText("Optional task title")
        title.setClearButtonEnabled(True)

        description = QTextEdit()
        description.setPlaceholderText("Describe what JARVIS should accomplish…")
        description.setMinimumHeight(130)

        agent = QComboBox()
        for name, profile in sorted(profiles.items()):
            agent.addItem(getattr(profile, "title", name), name)
        if agent.count() == 0:
            agent.addItem("General assistant", "general")

        minutes = QSpinBox()
        minutes.setRange(1, 720)
        minutes.setValue(10)
        minutes.setSuffix(" min")
        rounds = QSpinBox()
        rounds.setRange(1, 400)
        rounds.setValue(40)
        rounds.setSuffix(" rounds")

        delivery = QComboBox()
        delivery.addItem("Voice notification", "voice")
        delivery.addItem("Panel only", "panel")
        delivery.addItem("Silent", "silent")

        schedule_enabled = QCheckBox("Schedule for later")
        schedule = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        schedule.setCalendarPopup(True)
        schedule.setDisplayFormat("dd MMM yyyy  HH:mm")
        schedule.setEnabled(False)
        schedule_enabled.toggled.connect(schedule.setEnabled)

        files_label = QLabel("No attachments")
        files_label.setWordWrap(True)
        files_label.setMinimumWidth(0)
        files = []
        attach_button = QPushButton("Attach files")
        attach_button.clicked.connect(lambda: self._choose_task_files(files, files_label))

        root.addWidget(QLabel("Task title"))
        root.addWidget(title)
        root.addWidget(QLabel("Task description"))
        root.addWidget(description)

        settings = QGridLayout()
        settings.setHorizontalSpacing(12)
        settings.setVerticalSpacing(10)
        settings.setColumnStretch(0, 1)
        settings.setColumnStretch(1, 1)
        settings.addWidget(QLabel("Agent"), 0, 0)
        settings.addWidget(QLabel("Delivery"), 0, 1)
        settings.addWidget(agent, 1, 0)
        settings.addWidget(delivery, 1, 1)
        settings.addWidget(QLabel("Time budget"), 2, 0)
        settings.addWidget(QLabel("Round budget"), 2, 1)
        settings.addWidget(minutes, 3, 0)
        settings.addWidget(rounds, 3, 1)
        root.addLayout(settings)

        schedule_row = QHBoxLayout()
        schedule_row.addWidget(schedule_enabled)
        schedule_row.addStretch(1)
        schedule_row.addWidget(schedule)
        root.addLayout(schedule_row)

        attachment_row = QHBoxLayout()
        attachment_row.addWidget(attach_button)
        attachment_row.addWidget(files_label, 1)
        root.addLayout(attachment_row)

        root.addStretch(1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Create task")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setDefault(True)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        root.addWidget(buttons)

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
        active = sorted(
            (task for task in rows.values() if task.get("status") in _ACTIVE),
            key=lambda task: float(task.get("started") or task.get("created") or 0),
            reverse=True,
        )
        if not active:
            self.active_summary.setText("No active background tasks")
        else:
            lines = [f"ACTIVE BACKGROUND TASKS · {len(active)}"]
            for task in active[:3]:
                status = str(task.get("status") or "active").upper()
                progress = int(self._progress(task))
                title = str(task.get("title") or task.get("task") or "Untitled task").replace("\n", " ")
                lines.append(f"• {title[:48]} — {status} · {progress}%")
            if len(active) > 3:
                lines.append(f"+ {len(active) - 3} more")
            self.active_summary.setText("\n".join(lines))
        self.render()

    def _render_details(self, task):
        if not task:
            self.details_title.setText("Select a task")
            self.progress_bar.setRange(0, 100)
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
        terminal = status.lower() in {"done", "failed", "cancelled"}
        if terminal:
            progress = 100 if status.lower() == "done" else 0
        elif rounds_budget:
            progress = min(99, round(rounds_used / rounds_budget * 100))
        elif seconds_budget:
            progress = min(99, round(seconds_used / seconds_budget * 100))
        else:
            progress = 0
        self.details_title.setText(f"{title}  |  {status}")
        self.progress_bar.setVisible(True)
        self.details_browser.setVisible(True)
        self.export_button.setEnabled(bool(task.get("report") or task.get("result")))
        if status.lower() in _ACTIVE:
            self.progress_bar.setRange(0, 0)  # Busy indicator: rounds/budget are not completion percentage.
        else:
            self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(progress)
        if terminal:
            self.progress_bar.setFormat("Completed" if status.lower() == "done" else status.title())
        else:
            self.progress_bar.setFormat(f"Running · {rounds_used}/{rounds_budget or '∞'} rounds")
        lines = [
            f"<b>Status:</b> {status}",
            f"<b>Agent:</b> {self._html(task.get('agent') or 'general')}",
            f"<b>Created:</b> {self._format_time(task.get('created'))}",
            f"<b>Started:</b> {self._format_time(task.get('started'))}",
            f"<b>Time used:</b> {seconds_used / 60:.1f} / {seconds_budget / 60:.1f} min" if seconds_budget else f"<b>Time used:</b> {seconds_used / 60:.1f} min",
            f"<b>Rounds:</b> {rounds_used} / {rounds_budget or 'unlimited'}",
            f"<b>Attempts:</b> {int(task.get('attempts') or 0)}",
        ]
        quota_until = float(task.get("quota_until") or 0)
        if quota_until > 0:
            lines.append(f"<b>Quota retry:</b> {self._format_time(quota_until)}")
        recovery_events = [
            event for event in (task.get("events") or [])
            if isinstance(event, dict) and "resuming after an interruption" in str(event.get("text") or "").lower()
        ]
        if recovery_events:
            lines.append("<br><b>Recovery:</b> Resumed after interruption")
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
            timestamp = float(value or 0)
            if timestamp <= 0:
                return "—"
            return __import__("datetime").datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
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
        terminal_statuses = {"done", "failed", "cancelled"}
        if mode == "active":
            rows = [t for t in rows if t.get("status") in _ACTIVE]
        elif mode == "history":
            rows = [t for t in rows if t.get("status") in terminal_statuses]
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
        queued = sum(t.get("status") in {"queued", "scheduled"} for t in all_tasks)
        waiting = sum(t.get("status") == "waiting_quota" for t in all_tasks)
        cancelled = sum(t.get("status") == "cancelled" for t in all_tasks)
        self.stats_label.setText(
            f"{len(all_tasks)} tasks · {queued} queued · {running} running · "
            f"{waiting} waiting · {done} done · {failed} failed · {cancelled} cancelled"
        )
        self.list_widget.clear()
        for task in rows:
            status = str(task.get("status") or "unknown").upper()
            title = str(task.get("title") or task.get("task") or task.get("id"))
            used, budget = task.get("used") or {}, task.get("budget") or {}
            attempts = int(task.get("attempts") or 0)
            scheduled = task.get("not_before")
            subtitle = f"{used.get('rounds', 0)}/{budget.get('rounds', '?')} rounds"
            if attempts:
                subtitle += f" · attempt {attempts}"
            if task.get("status") == "scheduled" and scheduled:
                subtitle += f" · scheduled {self._format_time(scheduled)}"
            item = QListWidgetItem(f"{status} · {title}\n{subtitle}")
            item.setToolTip(str(task.get("task") or title))
            item.setData(Qt.ItemDataRole.UserRole, str(task.get("id")))
            self.list_widget.addItem(item)
            if str(task.get("id")) == selected:
                self.list_widget.setCurrentItem(item)
        has_rows = self.list_widget.count() > 0
        self.empty_label.setVisible(not has_rows)
        self.list_widget.setVisible(has_rows)
        current = self.list_widget.currentItem()
        self._selection_changed(current, None)
        selected_task = self._tasks.get(self.selected_task_id(), {})
        terminal = selected_task.get("status") not in _ACTIVE
        self.delete_button.setEnabled(bool(selected_task) and terminal)
        self.clear_history_button.setEnabled(any(t.get("status") not in _ACTIVE for t in all_tasks))

    def _delete_selected(self):
        tid = self.selected_task_id()
        task = self._tasks.get(tid, {})
        if not tid or task.get("status") in _ACTIVE:
            return
        title = str(task.get("title") or task.get("task") or tid)
        answer = QMessageBox.question(
            self, "Delete task",
            f"Permanently delete '{title}' and its task data/report?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.delete_task_requested.emit(tid)

    def _delete_history(self):
        count = sum(1 for t in self._tasks.values() if t.get("status") not in _ACTIVE)
        if not count:
            return
        answer = QMessageBox.question(
            self, "Delete task history",
            f"Permanently delete {count} completed/failed/cancelled tasks and their task-owned data?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.delete_history_requested.emit()

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
