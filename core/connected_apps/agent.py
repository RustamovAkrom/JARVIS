"""AI-facing Connected Apps tool discovery and guarded execution."""
from __future__ import annotations

import threading
import re
from typing import Any

from core.connected_apps.service import service


def _annotation(tool: dict[str, Any], name: str) -> bool:
    annotations = tool.get("annotations")
    if isinstance(annotations, dict):
        return annotations.get(name) is True
    return getattr(annotations, name, False) is True


def _validate_value(schema: dict[str, Any], value: Any, path: str) -> None:
    if not isinstance(schema, dict):
        return
    expected = schema.get("type")
    types = {
        "object": dict, "array": list, "string": str,
        "integer": int, "number": (int, float), "boolean": bool,
    }
    if expected in types:
        target = types[expected]
        if expected in {"integer", "number"} and isinstance(value, bool):
            raise ValueError(f"{path} must be {expected}")
        if not isinstance(value, target):
            raise ValueError(f"{path} must be {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} must be one of the allowed values")
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{path} must match the required value")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{path} must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"{path} must be <= {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            raise ValueError(f"{path} must be > {schema['exclusiveMinimum']}")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            raise ValueError(f"{path} must be < {schema['exclusiveMaximum']}")
        if "multipleOf" in schema and schema["multipleOf"]:
            quotient = value / schema["multipleOf"]
            if abs(quotient - round(quotient)) > 1e-9:
                raise ValueError(f"{path} must be a multiple of {schema['multipleOf']}")
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
        if isinstance(required, list):
            missing = [key for key in required if key not in value]
            if missing:
                raise ValueError(f"{path} missing required arguments: {', '.join(missing)}")
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            raise ValueError(f"{path} has too few properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            raise ValueError(f"{path} has too many properties")
        if schema.get("additionalProperties") is False:
            unknown = [key for key in value if key not in properties]
            if unknown:
                raise ValueError(f"{path} contains unknown arguments: {', '.join(unknown)}")
        for key, item in value.items():
            child = properties.get(key) if isinstance(properties, dict) else None
            if child is not None:
                _validate_value(child, item, f"{path}.{key}")
    elif isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise ValueError(f"{path} has too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ValueError(f"{path} has too many items")
        if schema.get("uniqueItems") is True:
            try:
                if len({repr(item) for item in value}) != len(value):
                    raise ValueError(f"{path} items must be unique")
            except TypeError:
                pass
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                _validate_value(schema["items"], item, f"{path}[{index}]")


def _validate_arguments(schema: dict[str, Any], args: dict[str, Any]) -> None:
    if not isinstance(args, dict):
        raise ValueError("Tool arguments must be an object")
    _validate_value(schema or {"type": "object"}, args, "arguments")


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
        safe_read = (
            _annotation(tool, "readOnlyHint")
            and not _annotation(tool, "destructiveHint")
            and tool.get("connection_metadata", {}).get("auto_approve_read_only") is True
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
