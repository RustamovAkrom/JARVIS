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
