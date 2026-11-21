"""AI-facing Connected Apps tool selection and guarded execution."""
from __future__ import annotations

from core import confirm
from core.connected_apps.service import service

class ConnectedAppsAgent:
    def describe_tools(self):
        out=[]
        for connection in service.connections():
            if connection.get("status") != "connected" or not connection.get("enabled", True):
                continue
            for tool in service.tools(connection["id"]):
                out.append({"app": connection["display_name"], "app_id": connection["app_id"],
                    "connection_id": connection["id"], "name": tool["name"],
                    "description": tool.get("description",""), "input_schema": tool.get("input_schema",{})})
        return out

    def find_tools(self, query="", app=""):
        query=(query or "").casefold()
        app=(app or "").casefold()
        return [t for t in self.describe_tools() if (not app or app in t["app"].casefold() or app in t["app_id"].casefold())
                and (not query or query in (t["name"]+" "+t["description"]+" "+t["app"]).casefold())]

    def execute(self, connection_id, tool_name, arguments=None, *, read_only=False, destructive=False):
        args=arguments or {}
        def run():
            return service.call_tool(connection_id, tool_name, args)
        if read_only and not destructive:
            return run()
        return confirm.request(key=f"connected_apps:{connection_id}:{tool_name}",
            title=f"Connected App — {tool_name}",
            detail=f"Execute tool {tool_name} with arguments:\n\n{args!r}",
            run=lambda: str(run()))

agent=ConnectedAppsAgent()
