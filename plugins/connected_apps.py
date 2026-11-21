"""JARVIS AI tool for Connected Apps. Backend remains optional."""
from __future__ import annotations
from core import outcome

PLUGIN = {
    "name": "connected_apps", "label": "Connected Apps", "level": "basic", "panel": "apps",
    "summary": "Discover, connect, configure and use external apps through MCP integrations.",
    "tier": "core",
    "description": "Manage and use connected MCP applications. Discover apps and tools before execution. Read-only tools may run automatically only when the MCP server marks them read-only. All other actions require confirmation.",
    "parameters": {"type":"OBJECT","properties":{
        "action":{"type":"STRING","enum":["connect","list_apps","list_tools","execute","smart_execute","disconnect","remove"]},
        "app":{"type":"STRING"},"connection_id":{"type":"STRING"},
        "tool":{"type":"STRING"},"arguments":{"type":"OBJECT"},"query":{"type":"STRING"},
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
        return "Connected Apps: "+PLUGIN["description"]+" Backend status is temporarily unavailable; existing JARVIS functions remain operational."

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
        if action=="smart_execute":
            query=str(p.get("query","")).strip()
            if not query:return outcome.failed("connected_apps.smart_execute","A task query is required.")
            try:
                result,tool=agent.execute_best(query,p.get("arguments",{}),app=p.get("app",""),auto_read_only=True)
                return outcome.done("connected_apps.smart_execute",app=tool["app"],tool=tool["name"],result=result)
            except Exception as exc:
                return outcome.failed("connected_apps.smart_execute",f"{type(exc).__name__}: {exc}")
        if action=="execute":
            connection_id=str(p.get("connection_id","")); tool=str(p.get("tool",""))
            matches=[x for x in agent.describe_tools() if x["connection_id"]==connection_id and x["name"]==tool]
            if not matches: return outcome.failed("connected_apps.execute","Tool not found in connected app.")
            return agent.execute(connection_id,tool,p.get("arguments",{}),read_only=True)
        if action=="disconnect":
            return outcome.done("connected_apps.disconnect",success=service.disconnect(str(p.get("connection_id",""))))
        if action=="remove":
            return outcome.done("connected_apps.remove",success=service.remove(str(p.get("connection_id",""))))
        return outcome.failed("connected_apps",f"Unsupported action: {action}")
    except Exception as exc:
        return outcome.failed("connected_apps",f"{type(exc).__name__}: {exc}")
