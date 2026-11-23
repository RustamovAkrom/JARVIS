"""AI-facing Connected Apps tool discovery and guarded execution."""
from __future__ import annotations

import threading
import re
from typing import Any

from core.connected_apps.service import service


def _annotation(tool: dict[str, Any], name: str) -> bool:
    """Treat MCP annotations as trusted only when explicitly boolean true."""
    annotations = tool.get("annotations")
    if isinstance(annotations, dict):
        return annotations.get(name) is True
    try:
        return getattr(annotations, name, False) is True
    except Exception:
        return False


def _validate_value(schema: dict[str, Any], value: Any, path: str) -> None:
    if not isinstance(schema, dict):
        raise ValueError(f"{path} has an invalid schema")
    # Unsupported schema features must not silently weaken argument validation.
    unsupported = {"$ref", "$dynamicRef", "dependencies", "dependentSchemas",
                   "if", "then", "else", "propertyNames"}
    present = unsupported.intersection(schema)
    if present:
        raise ValueError(f"{path} uses unsupported schema validation: {sorted(present)[0]}")

    if "allOf" in schema:
        branches = schema["allOf"]
        if not isinstance(branches, list):
            raise ValueError(f"{path} has an invalid allOf schema")
        for branch in branches:
            _validate_value(branch, value, path)
    if "anyOf" in schema:
        branches = schema["anyOf"]
        if not isinstance(branches, list) or not branches:
            raise ValueError(f"{path} has an invalid anyOf schema")
        errors = []
        for branch in branches:
            try:
                _validate_value(branch, value, path)
                break
            except ValueError as exc:
                errors.append(str(exc))
        else:
            raise ValueError(f"{path} does not match any allowed schema")
    if "oneOf" in schema:
        branches = schema["oneOf"]
        if not isinstance(branches, list) or not branches:
            raise ValueError(f"{path} has an invalid oneOf schema")
        matches = 0
        for branch in branches:
            try:
                _validate_value(branch, value, path)
                matches += 1
            except ValueError:
                pass
        if matches != 1:
            raise ValueError(f"{path} must match exactly one allowed schema")
    if "not" in schema:
        try:
            _validate_value(schema["not"], value, path)
        except ValueError:
            pass
        else:
            raise ValueError(f"{path} matches a forbidden schema")

    expected = schema.get("type")
    types = {
        "object": dict, "array": list, "string": str,
        "integer": int, "number": (int, float), "boolean": bool, "null": type(None),
    }
    if expected is not None:
        expected_types = expected if isinstance(expected, list) else [expected]
        if not expected_types or any(item not in types for item in expected_types):
            raise ValueError(f"{path} has an invalid schema type")
        valid = any(
            isinstance(value, types[item]) and not (
                item in {"integer", "number"} and isinstance(value, bool)
            ) and not (item == "integer" and isinstance(value, float))
            for item in expected_types
        )
        if not valid:
            raise ValueError(f"{path} has an invalid type")

    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list) or value not in enum:
            raise ValueError(f"{path} must be one of the allowed values")
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{path} must match the required value")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        for keyword, predicate, message in (
            ("minimum", lambda x: value >= x, "must be >="),
            ("maximum", lambda x: value <= x, "must be <="),
            ("exclusiveMinimum", lambda x: value > x, "must be >"),
            ("exclusiveMaximum", lambda x: value < x, "must be <"),
        ):
            if keyword in schema:
                bound = schema[keyword]
                if not isinstance(bound, (int, float)) or isinstance(bound, bool):
                    raise ValueError(f"{path} has an invalid {keyword}")
                if not predicate(bound):
                    raise ValueError(f"{path} {message} {bound}")
        multiple = schema.get("multipleOf")
        if multiple is not None:
            if not isinstance(multiple, (int, float)) or isinstance(multiple, bool) or multiple <= 0:
                raise ValueError(f"{path} has an invalid multipleOf")
            quotient = value / multiple
            if abs(quotient - round(quotient)) > 1e-9:
                raise ValueError(f"{path} must be a multiple of {multiple}")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ValueError(f"{path} is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValueError(f"{path} is too long")
        pattern = schema.get("pattern")
        if pattern:
            try:
                if re.search(pattern, value) is None:
                    raise ValueError(f"{path} does not match the required pattern")
            except re.error:
                raise ValueError(f"{path} has an invalid validation pattern") from None

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list):
            raise ValueError(f"{path} has an invalid object schema")
        missing = [key for key in required if key not in value]
        if missing:
            raise ValueError(f"{path} missing required arguments: {', '.join(missing)}")
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            raise ValueError(f"{path} has too few properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            raise ValueError(f"{path} has too many properties")
        additional = schema.get("additionalProperties", True)
        pattern_properties = schema.get("patternProperties", {})
        if not isinstance(pattern_properties, dict):
            raise ValueError(f"{path} has an invalid patternProperties schema")
        for key, item in value.items():
            child = properties.get(key)
            if child is not None:
                _validate_value(child, item, f"{path}.{key}")
                continue
            matched = False
            for pattern, pattern_schema in pattern_properties.items():
                try:
                    if re.search(pattern, key):
                        matched = True
                        _validate_value(pattern_schema, item, f"{path}.{key}")
                except re.error:
                    raise ValueError(f"{path} has an invalid validation pattern") from None
            if matched:
                continue
            if additional is False:
                raise ValueError(f"{path} contains unknown arguments: {key}")
            if isinstance(additional, dict):
                _validate_value(additional, item, f"{path}.{key}")
    elif isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise ValueError(f"{path} has too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ValueError(f"{path} has too many items")
        if schema.get("uniqueItems") is True and len({repr(item) for item in value}) != len(value):
            raise ValueError(f"{path} items must be unique")
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                _validate_value(items, item, f"{path}[{index}]")
        elif isinstance(items, list):
            for index, item_schema in enumerate(items):
                if index < len(value):
                    _validate_value(item_schema, value[index], f"{path}[{index}]")
            if len(value) > len(items) and schema.get("additionalItems") is False:
                raise ValueError(f"{path} contains too many items")


def _validate_arguments(schema: dict[str, Any], arguments: Any) -> None:
    """Validate tool arguments against a JSON Schema object, failing closed."""
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be a JSON object")
    if not isinstance(schema, dict):
        raise ValueError("Tool input schema must be an object")
    # Bound validation work for schemas and payloads received from remote MCP
    # servers. JSON serialization also rejects non-JSON Python objects.
    import json
    try:
        schema_bytes = len(json.dumps(schema, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        argument_bytes = len(json.dumps(arguments, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, RecursionError):
        raise ValueError("Tool schema or arguments are not valid JSON data") from None
    if schema_bytes > 256 * 1024:
        raise ValueError("Tool input schema is too large")
    if argument_bytes > 1024 * 1024:
        raise ValueError("Tool arguments are too large")
    try:
        _validate_value(schema, arguments, "arguments")
    except (TypeError, KeyError, OverflowError, RecursionError):
        # Malformed/untrusted schemas must fail closed with a stable error.
        raise ValueError("Tool schema contains invalid validation constraints") from None


class ConnectedAppsAgent:
    def __init__(self, app_service=None):
        self._service = app_service or service
        self._lock = threading.RLock()
        self._tasks = {}

    def start(self, app, task, *, say=None):
        adapter = self._service.get(app)
        if adapter is None:
            return f"status=failed app={app} reason=not_connected"
        key = self._service.normalize(getattr(adapter, "name", app))
        cancel = threading.Event()

        def worker():
            try:
                result = adapter.execute(task, cancel)
                if say and not cancel.is_set():
                    say(str(result or "Task completed"))
            except Exception as exc:
                if say and not cancel.is_set():
                    say(f"Connected Apps task failed: {exc}")
            finally:
                with self._lock:
                    if self._tasks.get(key) is cancel:
                        self._tasks.pop(key, None)

        classifier = getattr(adapter, "requires_confirmation", None)
        try:
            requires_confirmation = bool(classifier(task)) if callable(classifier) else True
        except Exception:
            requires_confirmation = True

        def launch():
            with self._lock:
                if key in self._tasks:
                    return "status=failed reason=task_already_running"
                self._tasks[key] = cancel
            thread = threading.Thread(target=worker, daemon=True, name=f"connected-app-{key}")
            try:
                thread.start()
            except Exception as exc:
                with self._lock:
                    self._tasks.pop(key, None)
                return f"status=failed reason=start_error error={type(exc).__name__}"
            return "status=done task=started"

        if requires_confirmation:
            from core import confirm
            return confirm.request(
                key=f"connected_apps.{key}",
                title=f"CONNECTED APP — {app}",
                detail=f"Allow this task in {app}?\n\n{task}",
                run=launch,
            )
        return launch()

    def stop(self, app=""):
        key = self._service.normalize(app)
        with self._lock:
            if app:
                adapter = self._service.get(app)
                key = self._service.normalize(getattr(adapter, "name", app)) if adapter else key
                cancel = self._tasks.get(key)
                if not cancel:
                    return "status=failed reason=no_running_task"
                cancel.set()
            else:
                for cancel in self._tasks.values():
                    cancel.set()
        return "status=done cancellation_requested"

    def describe_tools(self):
        out = []
        for connection in self._service.connections():
            if connection.get("status") != "connected" or not connection.get("enabled", True):
                continue
            if (connection.get("metadata") or {}).get("native"):
                for tool in self._service.native_tools(connection["id"]):
                    out.append({
                        "app": connection["display_name"], "app_id": connection["app_id"],
                        "connection_id": connection["id"], "name": tool["name"],
                        "description": tool["description"], "input_schema": tool["input_schema"],
                        "annotations": tool.get("annotations"),
                        "connection_metadata": dict(connection.get("metadata") or {}),
                        "native": True,
                    })
                continue
            if connection.get("status") != "connected" or not connection.get("enabled", True):
                continue
            for tool in self._service.tools(connection["id"]):
                out.append({
                    "app": connection["display_name"], "app_id": connection["app_id"],
                    "connection_id": connection["id"], "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("input_schema", {}),
                    "annotations": tool.get("annotations"),
                    "connection_metadata": dict(connection.get("metadata") or {}),
                })
        return out

    def find_tools(self, query="", app=""):
        query = (query or "").casefold().strip()
        app = (app or "").casefold().strip()
        return [t for t in self.describe_tools()
                if (not app or app in t["app"].casefold() or app in t["app_id"].casefold())
                and (not query or query in (t["name"] + " " + t["description"] + " " + t["app"]).casefold())]

    def rank_tools(self, query, app="", limit=8):
        """Rank tools by keyword overlap for AI-assisted discovery."""
        import re
        terms = set(re.findall(r"[a-z0-9_]{2,}", (query or "").casefold()))
        rows = self.find_tools(app=app)
        scored = []
        for row in rows:
            name_tokens = set(re.findall(r"[a-z0-9_]{2,}", row["name"].casefold().replace("_", " ")))
            desc_tokens = set(re.findall(r"[a-z0-9_]{2,}", row["description"].casefold()))
            app_tokens = set(re.findall(r"[a-z0-9_]{2,}", row["app"].casefold()))
            overlap = len(terms & (name_tokens | desc_tokens | app_tokens))
            phrase = (query or "").casefold().strip()
            exact_name = phrase and phrase in row["name"].casefold()
            exact_desc = phrase and phrase in row["description"].casefold()
            score = overlap * 2 + (5 if exact_name else 0) + (2 if exact_desc else 0)
            if score:
                row = dict(row)
                row["match_score"] = score
                scored.append(row)
        scored.sort(key=lambda item: (item["match_score"], item["app"], item["name"]), reverse=True)
        return scored[:max(1, min(int(limit), 20))]

    def execute_best(self, query, arguments=None, *, app="", auto_read_only=True):
        candidates = self.rank_tools(query, app=app, limit=5)
        if not candidates:
            raise ValueError("No connected app tool matches this request")
        top = candidates[0]
        if len(candidates) > 1 and candidates[1]["match_score"] == top["match_score"]:
            choices = ", ".join(f"{x['app']}.{x['name']}" for x in candidates
                               if x["match_score"] == top["match_score"])
            raise ValueError("Ambiguous tool match; specify app/tool: " + choices)
        return self.execute(top["connection_id"], top["name"], arguments or {},
                           read_only=auto_read_only), top

    def execute(self, connection_id, tool_name, arguments=None, *, read_only=False, destructive=False):
        args = arguments or {}
        if not isinstance(args, dict):
            raise ValueError("Arguments must be a JSON object")
        tool = next((t for t in self.describe_tools()
                     if t["connection_id"] == connection_id and t["name"] == tool_name), None)
        if tool is None:
            raise ValueError("Tool is not available on an enabled connected app")
        if tool.get("native"):
            return self._service.call_native_tool(connection_id, tool_name, args)
        blocked = set(tool.get("connection_metadata", {}).get("blocked_tools") or [])
        if tool_name in blocked:
            raise PermissionError("This MCP tool is blocked by Connected Apps permissions")
        _validate_arguments(tool.get("input_schema") or {}, args)
        # Automatic execution is permitted only when the MCP server explicitly
        # marks the operation read-only and non-destructive. All other calls fail closed.
        from core.connected_apps.service import _auto_approve_read_only
        safe_read = _auto_approve_read_only(
            tool.get("connection_metadata") or {},
            read_only=_annotation(tool, "readOnlyHint"),
            destructive=_annotation(tool, "destructiveHint"),
            requires_confirmation=False,
        )
        if read_only and safe_read and not destructive:
            return self._service.call_tool(connection_id, tool_name, args)
        from core import confirm
        return confirm.request(
            key=f"connected_apps:{connection_id}:{tool_name}",
            title=f"Connected App — {tool_name}",
            detail=f"Execute tool {tool_name} with arguments:\n\n{args!r}",
            run=lambda: str(self._service.call_tool(connection_id, tool_name, args)),
        )


agent = ConnectedAppsAgent(service)
