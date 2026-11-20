import ast
import hashlib
import secrets
from pathlib import Path
from types import SimpleNamespace


def _load_overlay_methods():
    source = Path("gui/app/main_window.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    overlay = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PluginSettingsOverlay"
    )
    wanted = {"_style_toggle", "_gather"}
    methods = [
        node for node in overlay.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    isolated = ast.Module(
        body=[ast.ClassDef(name="Overlay", bases=[], keywords=[], body=methods, decorator_list=[])],
        type_ignores=[],
    )
    namespace = {
        "QPushButton": object,
        "C": SimpleNamespace(
            PRI_GHO="primary-glow", PRI="primary", PRI_DIM="primary-dim",
            BORDER="border", TEXT_MED="text-medium",
        ),
        "secrets": secrets,
        "hashlib": hashlib,
    }
    exec(compile(ast.fix_missing_locations(isolated), "<plugin-settings-methods>", "exec"), namespace)
    return namespace["Overlay"]


class _Widget:
    def __init__(self, value):
        self.value = value

    def text(self):
        return self.value

    def currentText(self):
        return self.value

    def isChecked(self):
        return self.value


class _Toggle:
    def __init__(self, checked):
        self.checked = checked
        self.text = ""
        self.style = ""

    def isChecked(self):
        return self.checked

    def setText(self, text):
        self.text = text

    def setStyleSheet(self, style):
        self.style = style


def test_plugin_settings_toggle_uses_local_labels_without_translation_method():
    overlay = _load_overlay_methods()
    toggle = _Toggle(True)
    overlay._style_toggle(toggle)
    assert toggle.text == "ON"
    assert "primary" in toggle.style

    toggle.checked = False
    overlay._style_toggle(toggle)
    assert toggle.text == "OFF"


def test_blank_plugin_passcode_is_omitted_to_preserve_saved_value():
    overlay = _load_overlay_methods()
    instance = overlay.__new__(overlay)
    instance._widgets = {("printer", "passcode"): _Widget("")}
    instance._types = {("printer", "passcode"): "passcode"}
    assert instance._gather("printer") == {}


def test_plugin_text_setting_is_saved_normally():
    overlay = _load_overlay_methods()
    instance = overlay.__new__(overlay)
    instance._widgets = {("printer", "host"): _Widget("  localhost  ")}
    instance._types = {("printer", "host"): "text"}
    assert instance._gather("printer") == {"host": "localhost"}


def test_qt_widgets_wrapper_exports_selected_binding():
    from core.qt.QtWidgets import QApplication, QWidget, QPushButton

    assert QApplication is not None
    assert QWidget is not None
    assert QPushButton is not None
