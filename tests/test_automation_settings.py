import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QWidget
import memory.config_manager as config
from core.routines import runtime
from gui.settings.categories.automation import AutomationSettingsPage


class FakeEngine:
    def __init__(self):
        self.rows=[{"id":"r1","name":"Morning","kind":"say","enabled":True,"trigger":{"type":"time"},"text":"Good morning"}]
        self.quiet=""
    def quiet_hours(self): return self.quiet
    def list(self): return [dict(x) for x in self.rows]
    def get(self,rid): return next((dict(x) for x in self.rows if x["id"]==rid),None)
    def update(self,rid,**fields):
        row=next(x for x in self.rows if x["id"]==rid); row.update(fields); return dict(row)
    def remove(self,rid):
        before=len(self.rows); self.rows=[x for x in self.rows if x["id"]!=rid]; return len(self.rows)<before
    def set_quiet_hours(self,value): self.quiet=value; return value


def test_presence_settings_apply_and_persist(monkeypatch,tmp_path):
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(config,"CONFIG_FILE",tmp_path/"api_keys.json")
    monkeypatch.setattr(config,"CONFIG_DIR",tmp_path)
    applied=[]
    page=AutomationSettingsPage(presence_changed=applied.append)
    enabled=page.findChild(QWidget,"presence_enabled")
    minutes=page.findChild(QWidget,"presence_away_minutes")
    greet=page.findChild(QWidget,"presence_greet")
    enabled.setChecked(False)
    minutes.setValue(15)
    greet.setChecked(False)
    cfg=config.get_presence_settings()
    assert cfg=={"enabled":False,"away_minutes":15,"greet":False}
    assert applied[-1]==cfg


def test_routine_settings_list_toggle_remove_and_quiet(monkeypatch):
    app=QApplication.instance() or QApplication([])
    engine=FakeEngine()
    monkeypatch.setattr(runtime,"_engine",engine)
    page=AutomationSettingsPage()
    assert page.routine_list.count()==1
    page.routine_list.setCurrentRow(0)
    page.toggle_selected()
    assert engine.rows[0]["enabled"] is False
    page.routine_list.setCurrentRow(0)
    page.remove_selected()
    assert engine.rows==[]
    page.quiet_hours.setText("22:00-07:00")
    page.save_quiet_hours()
    assert engine.quiet=="22:00-07:00"


def test_clap_launcher_missing_executable_disables_toggle(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AutomationSettingsPage, "_clap_executable", lambda self: None)
    monkeypatch.setattr(AutomationSettingsPage, "_clap_running", lambda self: False)

    page = AutomationSettingsPage()

    assert not page.clap_enabled.isEnabled()
    assert page.clap_status.text().startswith("Not installed.")
    assert page.clap_download.isEnabled()


def test_clap_launcher_toggle_starts_and_stops(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    executable = tmp_path / "JARVISClapLauncher.exe"
    executable.touch()
    monkeypatch.setattr(AutomationSettingsPage, "_clap_executable", lambda self: executable)
    monkeypatch.setattr(AutomationSettingsPage, "_clap_running", lambda self: False)

    import gui.settings.categories.automation as automation
    started = []
    monkeypatch.setattr(automation.subprocess, "Popen", lambda *args, **kwargs: started.append((args, kwargs)))
    monkeypatch.setattr("core.clap_ipc.ask", lambda command, timeout_ms=700: "ok")

    page = AutomationSettingsPage()
    page._toggle_clap_launcher(True)
    assert started
    assert "Starting" in page.clap_status.text()

    page._toggle_clap_launcher(False)
    assert page.clap_status.text() != "Could not stop the launcher. Try quitting it from the system tray."
