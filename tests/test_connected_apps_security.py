from core.connected_apps.agent import _validate_arguments,_annotation

def test_tool_schema_rejects_missing_required():
    try:_validate_arguments({"required":["path"],"properties":{"path":{"type":"string"}}},{})
    except ValueError as exc:assert "path" in str(exc)
    else:raise AssertionError("missing required arg accepted")

def test_tool_schema_rejects_unknown_when_closed():
    try:_validate_arguments({"properties":{"path":{"type":"string"}},"additionalProperties":False},{"bad":1})
    except ValueError as exc:assert "bad" in str(exc)
    else:raise AssertionError("unknown arg accepted")

def test_readonly_annotation_fails_closed():
    assert _annotation({"annotations":{"readOnlyHint":True}},"readOnlyHint")
    assert not _annotation({"annotations":{"readOnlyHint":"true"}},"readOnlyHint")
    assert not _annotation({"annotations":None},"readOnlyHint")

def test_schema_validation_enforces_numeric_string_and_array_constraints():
    from core.connected_apps.agent import _validate_arguments
    schema={"type":"object","properties":{
        "limit":{"type":"integer","minimum":1,"maximum":10},
        "name":{"type":"string","minLength":2,"maxLength":5,"pattern":"^[a-z]+$"},
        "tags":{"type":"array","items":{"type":"string"},"maxItems":2,"uniqueItems":True},
    },"additionalProperties":False}
    _validate_arguments(schema,{"limit":5,"name":"jarv","tags":["a","b"]})
    for args in (
        {"limit":0,"name":"ok","tags":[]},
        {"limit":11,"name":"ok","tags":[]},
        {"limit":1,"name":"x","tags":[]},
        {"limit":1,"name":"123","tags":[]},
        {"limit":1,"name":"ok","tags":["a","a"]},
        {"limit":1,"name":"ok","tags":["a","b","c"]},
    ):
        try:
            _validate_arguments(schema,args)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Invalid arguments accepted: {args}")

def test_blocked_connected_app_tool_is_rejected_before_confirmation():
    from unittest.mock import Mock, patch
    from core.connected_apps.agent import ConnectedAppsAgent
    service=Mock()
    service.connections.return_value=[{
        "id":"c1","app_id":"demo","display_name":"Demo","status":"connected",
        "enabled":True,"metadata":{"blocked_tools":["delete_file"]},
    }]
    service.tools.return_value=[{"name":"delete_file","input_schema":{"type":"object"}}]
    agent=ConnectedAppsAgent(service)
    with patch("core.confirm.request") as confirm:
        try:
            agent.execute("c1","delete_file",{})
        except PermissionError as exc:
            assert "blocked" in str(exc).lower()
        else:
            raise AssertionError("Blocked tool was executed")
    confirm.assert_not_called()
    service.call_tool.assert_not_called()
