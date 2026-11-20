import ast
from pathlib import Path


def test_qt_widgets_wrapper_exports_selected_binding():
    from core.qt.QtWidgets import QApplication, QWidget, QPushButton

    assert QApplication is not None
    assert QWidget is not None
    assert QPushButton is not None


def test_plugin_settings_toggle_does_not_depend_on_window_translation_method():
    source = Path("gui/app/main_window.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    overlay = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PluginSettingsOverlay"
    )
    method = next(
        node for node in overlay.body
        if isinstance(node, ast.FunctionDef) and node.name == "_style_toggle"
    )
    assert not any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        and node.attr == "_tr"
        for node in ast.walk(method)
    )
