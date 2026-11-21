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


def test_clap_launcher_controls_persist_config(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "api_keys.json")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(AutomationSettingsPage, "_clap_executable", lambda self: None)
    monkeypatch.setattr(AutomationSettingsPage, "_clap_running", lambda self: False)

    page = AutomationSettingsPage()
    assert not page.clap_enabled.isEnabled()
    assert page.clap_download.isVisible() is False  # page is not shown in this test

    page.clap_pattern.setCurrentIndex(page.clap_pattern.findData(1))
    page.clap_sensitivity.setCurrentIndex(page.clap_sensitivity.findData("high"))

    saved = config.get_clap_launcher()
    assert saved["claps"] == 1
    assert saved["sensitivity"] == "high"
