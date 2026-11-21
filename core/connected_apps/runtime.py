"""Optional MCP runtime. The MCP SDK is imported lazily; core JARVIS remains usable without it."""
from __future__ import annotations

import asyncio
import threading
from typing import Any

class RuntimeUnavailable(RuntimeError):
    pass

class MCPRuntime:
    """Manages MCP sessions and exposes normalized tool metadata.

    Runtime calls are isolated on a private asyncio loop/thread so synchronous
    callers (including Qt/plugin dispatch) never block the UI event loop.
    """
    def __init__(self):
        self._lock = threading.RLock()
        self._loop = None
        self._thread = None
        self._sessions: dict[str, Any] = {}
        self._tools: dict[str, list[dict]] = {}

    def _ensure_loop(self):
        with self._lock:
            if self._loop and self._loop.is_running():
                return
            ready = threading.Event()
            def run():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
                ready.set()
                loop.run_forever()
                loop.close()
            self._thread = threading.Thread(target=run, name="connected-apps-mcp", daemon=True)
            self._thread.start()
            ready.wait(5)
            if not self._loop:
                raise RuntimeUnavailable("Could not start MCP runtime thread")

    def _submit(self, coroutine, timeout=60):
        self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        return future.result(timeout=timeout)

    async def _connect_stdio(self, connection_id: str, command: str, args: list[str], env: dict[str,str]):
        try:
            from mcp import ClientSession
            from mcp.client.stdio import stdio_client, StdioServerParameters
        except ImportError as exc:
            raise RuntimeUnavailable("MCP SDK is not installed. Install the optional MCP dependency.") from exc
        params = StdioServerParameters(command=command, args=args, env=env or None)
        transport = await stdio_client(params).__aenter__()
        session = ClientSession(*transport)
        await session.__aenter__()
        await session.initialize()
        result = await session.list_tools()
        tools = [{"name": t.name, "description": t.description or "", "input_schema": t.inputSchema or {}} for t in result.tools]
        self._sessions[connection_id] = (session, transport)
        self._tools[connection_id] = tools
        return tools

    async def _call(self, connection_id: str, tool: str, arguments: dict):
        session = self._sessions.get(connection_id)
        if not session:
            raise RuntimeUnavailable("MCP connection is not active")
        result = await session[0].call_tool(tool, arguments=arguments)
        if getattr(result, "isError", False):
            raise RuntimeError("MCP tool returned an error")
        return [{"type": getattr(item, "type", "text"), "text": getattr(item, "text", "")}
                for item in getattr(result, "content", [])]

    async def _close(self, connection_id: str):
        session = self._sessions.pop(connection_id, None)
        self._tools.pop(connection_id, None)
        if session:
            try: await session[0].__aexit__(None, None, None)
            finally:
                try: await session[1].__aexit__(None, None, None)
                except Exception: pass

    def connect_stdio(self, connection_id: str, command: str, args=None, env=None):
        return self._submit(self._connect_stdio(connection_id, command, list(args or []), dict(env or {})))

    def tools(self, connection_id: str):
        with self._lock:
            return list(self._tools.get(connection_id, []))

    def call(self, connection_id: str, tool: str, arguments: dict | None = None):
        return self._submit(self._call(connection_id, tool, arguments or {}))

    def disconnect(self, connection_id: str):
        try: self._submit(self._close(connection_id), timeout=15)
        except Exception: pass

    def shutdown(self):
        with self._lock:
            ids = list(self._sessions)
        for connection_id in ids:
            self.disconnect(connection_id)
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

runtime = MCPRuntime()
