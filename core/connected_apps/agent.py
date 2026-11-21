"""AI-facing Connected Apps tool discovery and guarded execution."""
from __future__ import annotations
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
