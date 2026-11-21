"""AI-facing Connected Apps tool discovery and guarded execution."""
from __future__ import annotations
import threading
from typing import Any
from core import confirm
from core.connected_apps.service import service

def _annotation(tool: dict[str, Any], name: str) -> bool:
    annotations=tool.get("annotations")
    if isinstance(annotations,dict): return annotations.get(name) is True
    return getattr(annotations,name,False) is True

def _validate_arguments(schema: dict[str,Any], args: dict[str,Any]) -> None:
    if not isinstance(args,dict): raise ValueError("Tool arguments must be an object")
    required=schema.get("required",[])
    missing=[key for key in required if key not in args]
    if missing: raise ValueError("Missing required tool arguments: "+", ".join(missing))
    properties=schema.get("properties",{})
    if schema.get("additionalProperties") is False:
        unknown=[key for key in args if key not in properties]
        if unknown: raise ValueError("Unknown tool arguments: "+", ".join(unknown))

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
                return f"status=failed reason=start_error error={exc}"
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
        out=[]
        for connection in service.connections():
            if connection.get("status")!="connected" or not connection.get("enabled",True): continue
            for tool in service.tools(connection["id"]):
                out.append({"app":connection["display_name"],"app_id":connection["app_id"],
                    "connection_id":connection["id"],"name":tool["name"],
                    "description":tool.get("description",""),"input_schema":tool.get("input_schema",{}),
                    "annotations":tool.get("annotations")})
        return out

    def find_tools(self,query="",app=""):
        query=(query or "").casefold().strip();app=(app or "").casefold().strip()
        return [t for t in self.describe_tools()
            if (not app or app in t["app"].casefold() or app in t["app_id"].casefold())
            and (not query or query in (t["name"]+" "+t["description"]+" "+t["app"]).casefold())]

    def execute(self,connection_id,tool_name,arguments=None,*,read_only=False,destructive=False):
        args=arguments or {}
        if not isinstance(args,dict): raise ValueError("Arguments must be a JSON object")
        tool=next((t for t in self.describe_tools()
            if t["connection_id"]==connection_id and t["name"]==tool_name),None)
        if tool is None: raise ValueError("Tool is not available on an enabled connected app")
        _validate_arguments(tool.get("input_schema") or {},args)
        # Automatic execution is permitted only when the MCP server explicitly
        # marks the operation read-only. All other calls fail closed.
        safe_read=_annotation(tool,"readOnlyHint") and not _annotation(tool,"destructiveHint")
        if read_only and safe_read and not destructive:
            return service.call_tool(connection_id,tool_name,args)
        return confirm.request(key=f"connected_apps:{connection_id}:{tool_name}",
            title=f"Connected App — {tool_name}",
            detail=f"Execute tool {tool_name} with arguments:\n\n{args!r}",
            run=lambda: str(service.call_tool(connection_id,tool_name,args)))

agent=ConnectedAppsAgent(service)
