"""Regression tests for the centralized live theme controller."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication, QWidget

from gui.themes.manager import ThemeManager
from gui.components.widgets import BusySpinner
from gui.panels.system_monitor.widgets import MetricGauge

from gui.themes.tokens import (
    C,
    DEFAULT_UI_COLOR,
    apply_ui_accent,
    apply_ui_theme,
    current_palette,
)


def _app():
    return QApplication.instance() or QApplication([])


def test_theme_manager_renders_styles_from_each_current_palette_without_drift():
    app = _app()
    apply_ui_theme("dark")
    apply_ui_accent(DEFAULT_UI_COLOR)
    widget = QWidget()
    widget.setStyleSheet(
        f"QWidget {{ background:{C.PANEL}; color:{C.TEXT}; "
        f"border:1px solid {C.BORDER}; }}"
    )
    manager = ThemeManager.for_app(app)
    manager._capture_widget(widget)

    transitions = (
        ("dark", "#ff3355"),
        ("light", "#000000"),
        ("dark", "#ffffff"),
        ("dark", "#2764ff"),
        ("light", "#f05ac8"),
    )
    try:
        for mode, accent in transitions:
            assert manager.apply(mode=mode, accent=accent)
            style = widget.styleSheet().lower()
            assert C.PANEL.lower() in style
            assert C.TEXT.lower() in style
            assert C.BORDER.lower() in style
            assert app.palette().color(QPalette.ColorRole.Window).name() == C.BG
    finally:
        manager.apply(mode="dark", accent=DEFAULT_UI_COLOR)
        widget.close()
        widget.deleteLater()


def test_theme_manager_rejects_invalid_state_without_mutating_palette():
    app = _app()
    manager = ThemeManager.for_app(app)
    before = current_palette()
    assert not manager.apply(accent="not-a-colour")
    assert not manager.apply(mode="sepia")
    assert current_palette() == before


def test_theme_manager_runs_owner_refresh_and_custom_widget_hooks():
    app = _app()
    manager = ThemeManager.for_app(app)

    class ThemeAwareWidget(QWidget):
        def __init__(self):
            super().__init__()
            self.refresh_count = 0

        def apply_jarvis_theme(self):
            self.refresh_count += 1

    widget = ThemeAwareWidget()
    calls = []
    try:
        assert manager.apply(
            accent="#20a0d0",
            mode="dark",
            refresh_callbacks=(lambda: calls.append("owner"),),
        )
        assert calls == ["owner"]
        assert widget.refresh_count >= 1
    finally:
        manager.apply(mode="dark", accent=DEFAULT_UI_COLOR)
        widget.close()
        widget.deleteLater()



def test_custom_painted_controls_refresh_cached_semantic_colours():
    app = _app()
    apply_ui_theme("dark")
    apply_ui_accent(DEFAULT_UI_COLOR)
    manager = ThemeManager.for_app(app)
    primary_gauge = MetricGauge("NET", C.PRI, theme_token="PRI")
    status_gauge = MetricGauge("CPU", C.GREEN, theme_token="GREEN")
    spinner = BusySpinner(color=C.PRI)
    try:
        assert manager.apply(mode="dark", accent="#e34b72")
        assert primary_gauge._color == C.PRI
        assert status_gauge._color == C.GREEN
        assert spinner._color.name() == C.PRI
    finally:
        manager.apply(mode="dark", accent=DEFAULT_UI_COLOR)
        primary_gauge.close()
        status_gauge.close()
        spinner.close()



def test_theme_manager_recovers_semantic_roles_from_monochrome_stylesheets():
    app = _app()
    manager = ThemeManager.for_app(app)
    apply_ui_theme("dark")
    apply_ui_accent("#ffffff")
    widget = QWidget()
    widget.setStyleSheet(
        f"QWidget {{ background:{C.PANEL}; color:{C.TEXT}; "
        f"border:1px solid {C.BORDER}; }}"
    )
    manager._capture_widget(widget)
    try:
        assert manager.apply(mode="dark", accent="#a044dd")
        style = widget.styleSheet().lower()
        assert C.PANEL2.lower() in style
        assert C.TEXT.lower() in style
        assert C.BORDER.lower() in style
    finally:
        manager.apply(mode="dark", accent=DEFAULT_UI_COLOR)
        widget.close()
        widget.deleteLater()
