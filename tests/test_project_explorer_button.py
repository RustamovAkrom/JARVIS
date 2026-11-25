import ast
from pathlib import Path


def test_project_explorer_open_folder_button_is_widget():
    source = Path("gui/app/main_window.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    main_window = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
    )
    method = next(
        node for node in main_window.body
        if isinstance(node, ast.FunctionDef) and node.name == "_build_project_explorer"
    )
    assignment = next(
        node for node in ast.walk(method)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Attribute)
            and target.attr == "_open_folder_btn"
            for target in node.targets
        )
    )
    assert isinstance(assignment.value, ast.Call)
    assert isinstance(assignment.value.func, ast.Name)
    assert assignment.value.func.id == "QPushButton"
