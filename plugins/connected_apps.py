"""JARVIS AI tool for Connected Apps. Backend remains optional."""
from __future__ import annotations
from core import outcome

PLUGIN = {
    "name": "connected_apps", "label": "Connected Apps", "level": "basic", "panel": "apps",
    "summary": "Discover, connect, configure and use external apps through MCP integrations.",
    "tier": "core",
    "description": "Use Connected Apps to discover tools from connected applications. "
                   "For app setup call action=connect. For tool discovery call action=list_tools. "
                   "For execution call action=execute with connection_id, tool and arguments. "
                   "Never invent tool names or arguments; discover them first. Read-only operations "
                   "may execute automatically. Write, send, delete or destructive actions require "
                   "human confirmation.",
    "parameters": {"type":"OBJECT","properties":{
        "action":{"type":"STRING","enum":["connect","list_apps","list_tools","execute","disconnect","remove"]},
        "app":{"type":"STRING"},"connection_id":{"type":"STRING"},
        "tool":{"type":"STRING"},"arguments":{"type":"OBJECT"},
        "query":{"type":"STRING"}},
        "required":["action"]}
}

def describe():
    try:
        from core.connected_apps import service, agent
        apps=service.connections()
        if not apps:
            return PLUGIN["description"]+" No apps are connected yet."
        connected=", ".join(row["display_name"] for row in apps if row.get("status")=="connected")
        return PLUGIN["description"]+f" Connected apps: {connected or 'none'}."
    except Exception:
        return PLUGIN["description"]

def run(parameters, player=None, session_memory=None):
    p=parameters or {}; action=str(p.get("action","")).lower()
    try:
        from core.connected_apps import service, agent
        if action=="connect":
            opener=getattr(player,"open_panel",None)
            if not callable(opener): return outcome.failed("connected_apps","Connected Apps UI is not available.")
            opener("apps",{"search":p.get("app","")})
            return outcome.done("connected_apps.connect",panel="apps",app=p.get("app"))
        if action=="list_apps":
            return outcome.done("connected_apps.list",apps=service.connections())
        if action=="list_tools":
            rows=agent.find_tools(query=p.get("query",""),app=p.get("app",""))
            return outcome.done("connected_apps.tools",tools=rows)
        if action=="execute":
            connection_id=str(p.get("connection_id","")); tool=str(p.get("tool",""))
            matches=[x for x in agent.describe_tools() if x["connection_id"]==connection_id and x["name"]==tool]
            if not matches: return outcome.failed("connected_apps.execute","Tool not found in connected app.")
            return agent.execute(connection_id,tool,p.get("arguments",{}),read_only=False)
        if action=="disconnect":
            return outcome.done("connected_apps.disconnect",success=service.disconnect(str(p.get("connection_id",""))))
        if action=="remove":
            return outcome.done("connected_apps.remove",success=service.remove(str(p.get("connection_id",""))))
        return outcome.failed("connected_apps",f"Unsupported action: {action}")
    except Exception as exc:
        return outcome.failed("connected_apps",f"{type(exc).__name__}: {exc}")
