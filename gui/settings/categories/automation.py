"""Settings for privacy-preserving presence and routine automation."""
from __future__ import annotations
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QSpinBox, QVBoxLayout, QWidget, QLabel,
)
from gui.components.settings import SettingRow, SettingsSection
from gui.themes.tokens import C, Space
from memory.config_manager import get_presence_settings, save_presence_settings


class AutomationSettingsPage(QWidget):
    def __init__(self, presence_changed=None, parent=None):
        super().__init__(parent)
        self._presence_changed = presence_changed
        self._loading = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.LG)

        cfg = get_presence_settings()
        presence = SettingsSection(
            "Presence awareness",
            "Uses keyboard/mouse idle and supported screen-lock signals. No camera or recording is used.",
        )
        self.enabled = QCheckBox("Enable presence awareness")
        self.enabled.setObjectName("presence_enabled")
        self.enabled.setChecked(cfg["enabled"])
        self.away_minutes = QSpinBox()
        self.away_minutes.setObjectName("presence_away_minutes")
        self.away_minutes.setRange(1, 240)
        self.away_minutes.setSuffix(" minutes")
        self.away_minutes.setValue(cfg["away_minutes"])
        self.greet = QCheckBox("Welcome me back")
        self.greet.setObjectName("presence_greet")
        self.greet.setChecked(cfg["greet"])
        presence.add_row(SettingRow("Detection", "Enable or disable automatic away/back detection.", self.enabled))
        presence.add_row(SettingRow("Away threshold", "Minutes without input before JARVIS considers you away.", self.away_minutes))
        presence.add_row(SettingRow("Return greeting", "Speak a brief greeting or queued notification digest when you return.", self.greet))
        layout.addWidget(presence)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color:{C.TEXT_MED};")
        presence.add_row(SettingRow("Status", "", self.status))

        routines = SettingsSection(
            "Routines",
            "Manage saved schedules and quiet hours. Create routines by voice or with the routines action.",
        )
        self.routine_list = QListWidget()
        self.routine_list.setObjectName("automation_routine_list")
        self.routine_list.setMinimumHeight(150)
        routines.add_row(SettingRow("Saved routines", "", self.routine_list))
        controls = QWidget()
        row = QVBoxLayout(controls)
        row.setContentsMargins(0, 0, 0, 0)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("automation_refresh")
        self.toggle_button = QPushButton("Enable / disable selected")
        self.toggle_button.setObjectName("automation_toggle")
        self.remove_button = QPushButton("Remove selected")
        self.remove_button.setObjectName("automation_remove")
        row.addWidget(self.refresh_button)
        row.addWidget(self.toggle_button)
        row.addWidget(self.remove_button)
        routines.add_row(SettingRow("Routine actions", "", controls))

        quiet = QWidget()
        quiet_row = QVBoxLayout(quiet)
        quiet_row.setContentsMargins(0, 0, 0, 0)
        self.quiet_hours = QLineEdit()
        self.quiet_hours.setObjectName("automation_quiet_hours")
        self.quiet_hours.setPlaceholderText("HH:MM-HH:MM  (leave empty to disable)")
        self.save_quiet = QPushButton("Save quiet hours")
        self.save_quiet.setObjectName("automation_save_quiet")
        quiet_row.addWidget(self.quiet_hours)
        quiet_row.addWidget(self.save_quiet)
        routines.add_row(SettingRow("Quiet hours", "Routine notifications can be deferred during this interval.", quiet))
        self.routine_status = QLabel("")
        self.routine_status.setWordWrap(True)
        self.routine_status.setStyleSheet(f"color:{C.TEXT_MED};")
        routines.add_row(SettingRow("Status", "", self.routine_status))
        layout.addWidget(routines)
        layout.addStretch(1)

        self.enabled.toggled.connect(self._save_presence)
        self.away_minutes.valueChanged.connect(self._save_presence)
        self.greet.toggled.connect(self._save_presence)
        self.refresh_button.clicked.connect(self.refresh_routines)
        self.toggle_button.clicked.connect(self.toggle_selected)
        self.remove_button.clicked.connect(self.remove_selected)
        self.save_quiet.clicked.connect(self.save_quiet_hours)
        self._loading = False
        self.refresh_routines()
        self._refresh_presence_status()

    def _save_presence(self, *_):
        if self._loading:
            return
        try:
            cfg = save_presence_settings(
                enabled=self.enabled.isChecked(),
                away_minutes=self.away_minutes.value(),
                greet=self.greet.isChecked(),
            )
            if self._presence_changed:
                self._presence_changed(cfg)
            self.status.setText("Presence settings saved and applied.")
        except Exception as exc:
            self.status.setText(f"Could not save presence settings: {exc}")

    def _refresh_presence_status(self):
        cfg = get_presence_settings()
        self.status.setText(
            f"Presence {'enabled' if cfg['enabled'] else 'disabled'} · away after {cfg['away_minutes']} minutes · "
            f"welcome back {'on' if cfg['greet'] else 'off'}"
        )

    def _engine(self):
        try:
            from core.routines.runtime import get_engine
            return get_engine()
        except Exception:
            return None

    def refresh_routines(self):
        self.routine_list.clear()
        engine = self._engine()
        if engine is None:
            self.routine_status.setText("Routine engine is not available in this session.")
            return
        try:
            quiet = engine.quiet_hours()
            self.quiet_hours.setText(quiet or "")
            rows = engine.list()
            for routine in rows:
                trigger = routine.get("trigger", {})
                typ = trigger.get("type", "unknown")
                label = f"{'●' if routine.get('enabled') else '○'} {routine.get('name')}  ·  {routine.get('kind')}  ·  {typ}"
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, routine.get("id"))
                item.setToolTip(str(routine.get("text") or routine.get("builtin") or ""))
                self.routine_list.addItem(item)
            self.routine_status.setText(f"{len(rows)} routine(s) · quiet hours: {quiet or 'off'}")
        except Exception as exc:
            self.routine_status.setText(f"Could not load routines: {exc}")

    def _selected_id(self):
        item = self.routine_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def toggle_selected(self):
        rid = self._selected_id()
        engine = self._engine()
        if not rid or engine is None:
            self.routine_status.setText("Select a routine first.")
            return
        row = engine.get(rid)
        if row is None:
            self.refresh_routines()
            return
        try:
            engine.update(rid, enabled=not row.get("enabled", False))
            self.refresh_routines()
        except Exception as exc:
            self.routine_status.setText(f"Could not update routine: {exc}")

    def remove_selected(self):
        rid = self._selected_id()
        engine = self._engine()
        if not rid or engine is None:
            self.routine_status.setText("Select a routine first.")
            return
        try:
            removed = engine.remove(rid)
            self.routine_status.setText("Routine removed." if removed else "Routine not found.")
            self.refresh_routines()
        except Exception as exc:
            self.routine_status.setText(f"Could not remove routine: {exc}")

    def save_quiet_hours(self):
        engine = self._engine()
        if engine is None:
            self.routine_status.setText("Routine engine is not available.")
            return
        try:
            value = engine.set_quiet_hours(self.quiet_hours.text().strip())
            self.routine_status.setText(f"Quiet hours saved: {value or 'off'}.")
            self.refresh_routines()
        except Exception as exc:
            self.routine_status.setText(f"Invalid quiet hours: {exc}")
