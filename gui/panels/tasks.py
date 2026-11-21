"""Task Engine desktop panel — UI-only adapter; engine ownership stays in Core."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QDateTime
from gui.themes.tokens import C

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
        root.setContentsMargins(7, 7, 7, 7)
        root.setSpacing(4)
        root.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
        section_label = QLabel("TASKS")
        section_label.setObjectName("task_section_label")
        root.addWidget(section_label)
        self.active_summary = QLabel("No active background tasks")
        self.active_summary.setObjectName("task_active_summary")
        self.active_summary.setWordWrap(True)
        self.active_summary.setMaximumHeight(44)
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
        self.search_box.setMinimumHeight(30)
        self.search_box.setMaximumHeight(36)
        root.addWidget(self.search_box)
        controls = QVBoxLayout()
        controls.setSpacing(3)
        filter_row = QHBoxLayout()
        filter_row.setSpacing(4)
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
            widget.setMinimumHeight(30)
            widget.setMaximumHeight(36)
            filter_row.addWidget(widget, 1)
        controls.addLayout(filter_row)
        action_row = QHBoxLayout()
        action_row.setSpacing(4)
        self.new_button = QPushButton("+ New")
        self.new_button.setMinimumHeight(28)
        self.new_button.setMaximumHeight(32)
        self.refresh_button = QPushButton("↻")
        self.refresh_button.setToolTip("Refresh tasks")
        self.refresh_button.setMinimumHeight(28)
        self.refresh_button.setMaximumHeight(32)
        action_row.addWidget(self.new_button, 1)
        action_row.addWidget(self.refresh_button, 1)
        controls.addLayout(action_row)
        root.addLayout(controls)
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("task_list")
        self.list_widget.setMinimumHeight(0)
        self.list_widget.setMaximumHeight(78)
        self.list_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.list_widget.setUniformItemSizes(True)
        root.addWidget(self.list_widget, 0)

        self.details_title = QLabel("Select a task")
        self.details_title.setObjectName("task_details_title")
        self.details_title.setWordWrap(True)
        self.details_title.setMaximumHeight(24)
        self.details_title.setMinimumHeight(0)
        root.addWidget(self.details_title)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setMaximumHeight(18)
        root.addWidget(self.progress_bar, 0)
        self.details_browser = QTextBrowser()
        self.details_browser.setObjectName("task_details")
        self.details_browser.setOpenExternalLinks(False)
        self.details_browser.setMinimumHeight(0)
        self.details_browser.setMaximumHeight(120)
        self.details_browser.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        root.addWidget(self.details_browser, 1)

        report_actions = QHBoxLayout()
        report_actions.setSpacing(4)
        self.view_report_button = QPushButton("View")
        self.open_report_button = QPushButton("Open")
        self.export_button = QPushButton("Save As")
        self.show_report_folder_button = QPushButton("Folder")
        for button in (
            self.view_report_button, self.open_report_button,
            self.export_button, self.show_report_folder_button,
        ):
            button.setMinimumWidth(0)
            button.setMaximumHeight(28)
            button.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            report_actions.addWidget(button, 1)
        root.addLayout(report_actions)
        history_actions = QGridLayout()
        history_actions.setHorizontalSpacing(5)
        history_actions.setVerticalSpacing(2)
        self.delete_button = QPushButton("Delete")
        self.clear_history_button = QPushButton("Clear History")
        self.delete_button.setToolTip("Permanently delete selected task and its task-owned files")
        self.clear_history_button.setToolTip("Permanently delete all completed, failed and cancelled tasks")
        history_actions.addWidget(self.delete_button, 0, 0)
        history_actions.addWidget(self.clear_history_button, 0, 1)
        history_actions.setColumnStretch(0, 1)
        history_actions.setColumnStretch(1, 1)
        root.addLayout(history_actions, 0)
        self.stats_label = QLabel("0 tasks")
        self.stats_label.setObjectName("task_stats")
        self.stats_label.setWordWrap(True)
        self.stats_label.setMaximumHeight(22)
        root.addWidget(self.stats_label, 0)
        self.empty_label = QLabel("No tasks yet. Create your first task.")
        self.empty_label.setObjectName("task_empty")
        self.empty_label.setMaximumHeight(24)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.empty_label, 0)
        actions = QGridLayout()
        actions.setHorizontalSpacing(5)
        actions.setVerticalSpacing(2)
        self.retry_button = QPushButton("Retry")
        self.cancel_button = QPushButton("Cancel")
        self.finish_button = QPushButton("Finish")
        self.steer_button = QPushButton("Steer")
        for index, button in enumerate((self.retry_button, self.cancel_button, self.finish_button, self.steer_button)):
            actions.addWidget(button, index // 2, index % 2)
        actions.setColumnStretch(0, 1)
        actions.setColumnStretch(1, 1)
        root.addLayout(actions, 0)
        self.new_button.clicked.connect(self._create_task_dialog)
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        self.filter_box.currentIndexChanged.connect(self.render)
        self.sort_box.currentIndexChanged.connect(self.render)
        self.agent_filter.currentIndexChanged.connect(self.render)
        self.search_box.textChanged.connect(self.render)
        self.view_report_button.clicked.connect(self._view_selected_report)
        self.open_report_button.clicked.connect(self._open_selected_report)
        self.export_button.clicked.connect(self._export_selected_report)
        self.delete_button.clicked.connect(self._delete_selected)
        self.clear_history_button.clicked.connect(self._delete_history)
        self.show_report_folder_button.clicked.connect(self._show_selected_report_folder)
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
        return str(C.PANEL).strip().lower() in {"#ffffff", "#fff", "white"}

    def refresh_theme(self):
        """Rebuild panel and dialog styles from the live application palette."""
        self.setStyleSheet(self._panel_stylesheet())
        if hasattr(self, "active_summary"):
            self.active_summary.setStyleSheet(
                f"color: {C.PRI}; background: {C.PANEL2}; border: 1px solid {C.BORDER}; "
                "border-radius: 6px; padding: 7px;"
            )
        self.update()

    def _panel_stylesheet(self):
        bg, surface = C.PANEL, C.PANEL2
        text, muted, border, accent = C.TEXT, C.TEXT_DIM, C.BORDER, C.PRI
        selected_bg = C.PRI_GHO
        return f"""
        QWidget#TaskPanel {{ background: transparent; color: {text}; }}
        QWidget#TaskPanel QLabel {{ color: {text}; background: transparent; }}
        QWidget#TaskPanel QLabel#task_details_title {{ color: {accent}; font-weight: 600; padding: 2px; }}
        QWidget#TaskPanel QLabel#task_section_label {{ font-weight: 600; padding: 2px 4px; }}
        QWidget#TaskPanel QLabel#task_empty {{ color: {muted}; padding: 10px; }}
        QWidget#TaskPanel QLabel#task_stats {{ color: {muted}; font-size: 10px; padding: 2px 4px; }}
        QWidget#TaskPanel QLineEdit, QWidget#TaskPanel QComboBox {{
            background: {surface}; color: {text}; border: 1px solid {border};
            border-radius: 6px; padding: 3px 5px; min-height: 20px; max-height: 32px;
        }}
        QWidget#TaskPanel QComboBox QAbstractItemView {{
            background: {surface}; color: {text}; selection-background-color: {selected_bg};
            border: 1px solid {border};
        }}
        QWidget#TaskPanel QPushButton {{
            background: {surface}; color: {text}; border: 1px solid {border};
            border-radius: 6px; padding: 2px 4px; min-height: 18px; max-height: 28px;
        }}
        QWidget#TaskPanel QPushButton:hover {{ background: {selected_bg}; border-color: {accent}; }}
        QWidget#TaskPanel QPushButton:disabled {{ color: {muted}; border-color: {border}; }}
        QWidget#TaskPanel QListWidget {{ background: {bg}; border: 1px solid {border}; border-radius: 7px; padding: 3px; outline: none; }}
        QWidget#TaskPanel QListWidget::item {{ color: {text}; background: {surface}; border: 1px solid {border}; border-radius: 5px; padding: 4px 5px; margin: 1px 0; }}
        QWidget#TaskPanel QListWidget::item:selected {{ color: {text}; background: {selected_bg}; border: 1px solid {accent}; }}
        QWidget#TaskPanel QProgressBar {{ background: {surface}; color: {text}; border: 1px solid {border}; border-radius: 5px; text-align: center; min-height: 15px; max-height: 18px; }}
        QWidget#TaskPanel QProgressBar::chunk {{ background: {accent}; border-radius: 4px; }}
        QWidget#TaskPanel QTextBrowser {{ background: {surface}; color: {text}; border: 1px solid {border}; border-radius: 7px; padding: 6px; }}
        """

    def _dialog_stylesheet(self):
        bg, surface = C.PANEL, C.PANEL2
        text, muted, border, accent = C.TEXT, C.TEXT_DIM, C.BORDER, C.PRI
        return f"""
        QDialog#CreateTaskDialog {{ background: {bg}; color: {text}; }}
        QDialog#CreateTaskDialog QLabel {{ color: {muted}; background: transparent; border: none; }}
        QDialog#CreateTaskDialog QLabel#dialog_heading {{ color: {accent}; font-size: 16px; font-weight: 600; }}
        QDialog#CreateTaskDialog QLineEdit, QDialog#CreateTaskDialog QTextEdit,
        QDialog#CreateTaskDialog QComboBox, QDialog#CreateTaskDialog QSpinBox,
        QDialog#CreateTaskDialog QDateTimeEdit {{ background: {surface}; color: {text}; border: 1px solid {border}; border-radius: 7px; padding: 7px 9px; min-height: 22px; }}
        QDialog#CreateTaskDialog QCheckBox {{ color: {muted}; spacing: 8px; }}
        QDialog#CreateTaskDialog QPushButton {{ background: {surface}; color: {text}; border: 1px solid {border}; border-radius: 7px; padding: 7px 12px; min-width: 75px; }}
        QDialog#CreateTaskDialog QPushButton:hover {{ border-color: {accent}; }}
        QDialog#CreateTaskDialog QPushButton:default {{ background: {accent}; color: white; }}
        """

    def _create_task_dialog(self):
        profiles = getattr(self, "_profiles", {})
        dialog = QDialog(self)
        dialog.setObjectName("CreateTaskDialog")
        dialog.setWindowTitle("Create Task")
        dialog.setMinimumSize(440, 430)
        dialog.resize(560, 560)
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
            for button in (self.view_report_button, self.open_report_button,
                           self.export_button, self.show_report_folder_button):
                button.setEnabled(False)
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
        # The task list already displays title + status. Avoid repeating the
        # same status in a second heading above the progress bar.
        self.details_title.setText(title)
        self.details_title.setVisible(False)
        self.progress_bar.setVisible(True)
        self.details_browser.setVisible(True)
        self.export_button.setEnabled(bool(task))
        if status.lower() in _ACTIVE:
            self.progress_bar.setRange(0, 0)  # Busy indicator: rounds/budget are not completion percentage.
        else:
            self.progress_bar.setRange(0, 100)
        report_path = self._report_path(task)
        report_available = bool(report_path and report_path.is_file())
        self.view_report_button.setEnabled(report_available)
        self.open_report_button.setEnabled(report_available)
        self.show_report_folder_button.setEnabled(report_available)
        self.export_button.setEnabled(bool(task))
        self.progress_bar.setValue(progress)
        if terminal:
            self.progress_bar.setFormat("Completed" if status.lower() == "done" else status.title())
        else:
            self.progress_bar.setFormat(f"Running · {rounds_used}/{rounds_budget or '∞'} rounds")
        lines = [
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
        result = task.get("result")
        if result:
            lines.append(f"<br><b>Summary:</b><br>{self._html(result)}")
        report_path = self._report_path(task)
        if report_path:
            if report_path.is_file():
                lines.append("<br><b>Report:</b> Ready to view or export")
            else:
                lines.append(f"<br><b>Report:</b> Missing file — {self._html(report_path.name)}")
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

    def _report_path(self, task):
        from pathlib import Path
        value = task.get("report_path") or task.get("report") or ""
        if not value:
            return None
        path = Path(str(value))
        # Older task records stored the report path in the "report" field.
        return path if path.suffix.lower() in {".md", ".txt"} else None

    def _read_selected_report(self):
        task = self._tasks.get(self.selected_task_id(), {})
        path = self._report_path(task)
        if not path or not path.is_file():
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Report unavailable", "The report file is missing or unavailable.")
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Report unavailable", str(exc))
            return None

    def _view_selected_report(self):
        report = self._read_selected_report()
        if report is None:
            return
        task = self._tasks.get(self.selected_task_id(), {})
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Task Report — {task.get('title') or 'Report'}")
        dialog.resize(760, 620)
        layout = QVBoxLayout(dialog)
        viewer = QTextBrowser(dialog)
        viewer.setMarkdown(report)
        viewer.setOpenExternalLinks(True)
        layout.addWidget(viewer, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _open_selected_report(self):
        task = self._tasks.get(self.selected_task_id(), {})
        path = self._report_path(task)
        if not path or not path.is_file():
            self._read_selected_report()
            return
        try:
            import os
            import sys
            import subprocess
            if sys.platform.startswith("win"):
                os.startfile(str(path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Open report failed", str(exc))

    def _show_selected_report_folder(self):
        task = self._tasks.get(self.selected_task_id(), {})
        path = self._report_path(task)
        if not path or not path.is_file():
            self._read_selected_report()
            return
        try:
            import os
            import sys
            import subprocess
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select,", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])
        except Exception as exc:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Open folder failed", str(exc))

    def _export_selected_report(self):
        task = self._tasks.get(self.selected_task_id(), {})
        report_path = self._report_path(task)
        report = None
        if report_path and report_path.is_file():
            try:
                report = report_path.read_text(encoding="utf-8")
            except OSError as exc:
                QMessageBox.warning(self, "Report unavailable", str(exc))
                return
        if report is None:
            # Failed/cancelled tasks may not have a report. Export a useful
            # diagnostic summary instead of silently doing nothing.
            used = task.get("used") if isinstance(task.get("used"), dict) else {}
            budget = task.get("budget") if isinstance(task.get("budget"), dict) else {}
            events = task.get("events") if isinstance(task.get("events"), list) else []
            lines = [
                f"# {task.get('title') or task.get('task') or 'Background task'}",
                "",
                f"- Status: {str(task.get('status') or 'unknown').upper()}",
                f"- Agent: {task.get('agent') or 'general'}",
                f"- Created: {self._format_time(task.get('created'))}",
                f"- Started: {self._format_time(task.get('started'))}",
                f"- Time used: {float(used.get('seconds') or 0):.1f}s",
                f"- Rounds: {used.get('rounds', 0)} / {budget.get('rounds', 'unlimited')}",
                "",
                "## Task",
                str(task.get("task") or ""),
            ]
            if task.get("error"):
                lines.extend(["", "## Error", str(task.get("error"))])
            if events:
                lines.extend(["", "## Event history"])
                for event in events:
                    if isinstance(event, dict):
                        lines.append(
                            f"- [{self._format_time(event.get('t'))}] "
                            f"{event.get('kind') or 'event'}: {event.get('text') or ''}"
                        )
            report = "\\n".join(lines)

        suggested = str(task.get("title") or "task-report").strip() or "task-report"
        path, selected_filter = QFileDialog.getSaveFileName(
            self, "Save Task Report", f"{suggested}.md",
            "Markdown (*.md);;Text (*.txt)"
        )
        if not path:
            return
        try:
            from pathlib import Path
            destination = Path(path)
            if selected_filter.startswith("Text") and destination.suffix.lower() != ".txt":
                destination = destination.with_suffix(".txt")
            elif destination.suffix.lower() not in {".md", ".txt"}:
                destination = destination.with_suffix(".md")
            destination.write_text(report, encoding="utf-8")
            QMessageBox.information(self, "Report saved", f"Report saved to:\\n{destination}")
        except OSError as exc:
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
            item = QListWidgetItem(f"{title[:36]}\n{status} · {subtitle}")
            item.setSizeHint(item.sizeHint())
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
