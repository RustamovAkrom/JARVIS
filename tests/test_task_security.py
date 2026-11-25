from unittest.mock import patch

from core.tasks.security import authorize_task_tool


def test_safe_research_tools_are_allowed():
    assert authorize_task_tool("search_web").allowed
    assert authorize_task_tool("fetch_page").allowed


def test_unknown_tool_fails_closed():
    decision = authorize_task_tool("send_message", {"text": "hello"})
    assert not decision.allowed
    assert "not approved" in decision.reason


def test_computer_action_requires_agent_mode():
    with patch("memory.config_manager.get_computer_access_settings", return_value={
        "mode": "chat", "access_level": "full"
    }), patch("memory.config_manager.get_computer_permissions", return_value={
        "click": True
    }):
        decision = authorize_task_tool("computer_control", {"action": "click"})
    assert not decision.allowed
    assert "Agent mode" in decision.reason


def test_computer_action_requires_full_access():
    with patch("memory.config_manager.get_computer_access_settings", return_value={
        "mode": "agent", "access_level": "approve"
    }), patch("memory.config_manager.get_computer_permissions", return_value={
        "click": True
    }):
        decision = authorize_task_tool("computer_control", {"action": "click"})
    assert not decision.allowed
    assert "Full Access" in decision.reason


def test_granular_permission_is_enforced():
    with patch("memory.config_manager.get_computer_access_settings", return_value={
        "mode": "agent", "access_level": "full"
    }), patch("memory.config_manager.get_computer_permissions", return_value={
        "click": False
    }):
        decision = authorize_task_tool("computer_control", {"action": "click"})
    assert not decision.allowed
    assert "click" in decision.reason


def test_authorized_computer_action_requires_all_policy_gates():
    with patch("memory.config_manager.get_computer_access_settings", return_value={
        "mode": "agent", "access_level": "full"
    }), patch("memory.config_manager.get_computer_permissions", return_value={
        "click": True
    }):
        decision = authorize_task_tool("computer_control", {"action": "click"})
    assert decision.allowed


def test_unknown_computer_action_fails_closed():
    decision = authorize_task_tool("computer_control", {"action": "shutdown"})
    assert not decision.allowed
