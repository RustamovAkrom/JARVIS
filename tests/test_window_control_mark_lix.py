"""Regression tests for the Mark LIX window-control integration.

These tests exercise the action contract and its safety behavior without touching
real desktop windows. OS-specific backends are mocked.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def window_control_module():
    return importlib.import_module("actions.window_control")


def test_tool_declaration_is_valid(window_control_module):
    tool = window_control_module.TOOL
    assert tool["name"] == "window_control"
    assert callable(tool["handler"])
    assert tool["parameters"]["type"] == "OBJECT"
    assert "action" in tool["parameters"]["properties"]


def test_invalid_action_is_rejected(window_control_module):
    result = window_control_module.window_control({"action": "destroy"})
    assert "status=failed" in result
    assert "valid_actions" in result


def test_window_action_never_targets_jarvis_when_no_external_window(
    window_control_module, monkeypatch
):
    module = window_control_module
    monkeypatch.setattr(module, "_OS", "Windows")
    monkeypatch.setattr(module, "_windows_of", lambda app: [])
    result = module.window_control({"action": "minimize"})
    assert "status=failed" in result
    assert "assistant's own" in result


def test_window_action_reports_verified_success(
    window_control_module, monkeypatch
):
    module = window_control_module
    monkeypatch.setattr(module, "_OS", "Windows")
    row = {"hwnd": 123, "product": "Test App", "process": "test.exe"}
    monkeypatch.setattr(module, "_windows_of", lambda app: [row])
    monkeypatch.setattr(module, "_win_do", lambda action, hwnd, monitor="": (True, "minimized"))
    result = module.window_control({"action": "minimize", "app": "Test App"})
    assert "status=done" in result
    assert "verified=true" in result


def test_partial_window_failure_is_unverified(
    window_control_module, monkeypatch
):
    module = window_control_module
    monkeypatch.setattr(module, "_OS", "Windows")
    rows = [
        {"hwnd": 1, "product": "Test App", "process": "test.exe"},
        {"hwnd": 2, "product": "Test App", "process": "test.exe"},
    ]
    monkeypatch.setattr(module, "_windows_of", lambda app: rows)
    outcomes = iter([(True, "minimized"), (False, "minimized")])
    monkeypatch.setattr(module, "_win_do", lambda action, hwnd, monitor="": next(outcomes))
    result = module.window_control({"action": "minimize", "app": "Test App"})
    assert "status=unverified" in result
