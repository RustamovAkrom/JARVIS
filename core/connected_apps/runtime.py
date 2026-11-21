"""Optional MCP runtime with isolated asyncio loop and managed session lifetimes."""
from __future__ import annotations

import asyncio
import threading
from contextlib import AsyncExitStack
from typing import Any

class RuntimeUnavailable(RuntimeError):
    pass

class MCPRuntime:
    def __init__(self):
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._sessions: dict[str, tuple[AsyncExitStack, Any]] = {}
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
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.close()
            self._thread = threading.Thread(target=run, name="connected-apps-mcp", daemon=True)
            self._thread.start()
            if not ready.wait(5) or not self._loop:
                raise RuntimeUnavailable("Could not start MCP runtime thread")

    def _submit(self, coroutine, timeout=60):
        self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            future.cancel()
            raise RuntimeUnavailable(f"MCP operation timed out after {timeout}s")

    async def _connect_stdio(self, connection_id: str, command: str, args: list[str], env: dict[str,str]):
        if not command or not command.strip():
            raise ValueError("MCP command cannot be empty")
        try:
            from mcp import ClientSession
            from mcp.client.stdio import stdio_client, StdioServerParameters
        except ImportError as exc:
            raise RuntimeUnavailable("MCP SDK is not installed. Install the optional MCP dependency.") from exc
        stack = AsyncExitStack()
        try:
            params = StdioServerParameters(command=command, args=args, env=env or None)
            transport = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(*transport))
            await session.initialize()
            result = await session.list_tools()
            tools = [{"name": t.name, "description": t.description or "",
                       "input_schema": t.inputSchema or {}} for t in result.tools]
            old = self._sessions.pop(connection_id, None)
            if old:
                await old[0].aclose()
            self._sessions[connection_id] = (stack, session)
            self._tools[connection_id] = tools
            return tools
        except BaseException:
            await stack.aclose()
            raise

    async def _call(self, connection_id: str, tool: str, arguments: dict):
        entry = self._sessions.get(connection_id)
        if not entry:
            raise RuntimeUnavailable("MCP connection is not active")
        allowed = {row["name"] for row in self._tools.get(connection_id, [])}
        if tool not in allowed:
            raise ValueError(f"Tool is not registered for this connection: {tool}")
        result = await entry[1].call_tool(tool, arguments=arguments)
        if getattr(result, "isError", False):
            raise RuntimeError("MCP tool returned an error")
        content=[]
        for item in getattr(result, "content", []):
            content.append({"type": getattr(item, "type", "unknown"),
                            "text": getattr(item, "text", "")})
        return content

    async def _close(self, connection_id: str):
        entry = self._sessions.pop(connection_id, None)
        self._tools.pop(connection_id, None)
        if entry:
            await entry[0].aclose()

    def connect_stdio(self, connection_id: str, command: str, args=None, env=None):
        return self._submit(self._connect_stdio(connection_id, command, list(args or []), dict(env or {})), timeout=90)

    def tools(self, connection_id: str):
        with self._lock:
            return list(self._tools.get(connection_id, []))

    def call(self, connection_id: str, tool: str, arguments=None):
        return self._submit(self._call(connection_id, tool, arguments or {}), timeout=120)

    def disconnect(self, connection_id: str):
        try:
            self._submit(self._close(connection_id), timeout=15)
        except Exception:
            pass

    def shutdown(self):
        with self._lock:
            ids = list(self._sessions)
        for connection_id in ids:
            self.disconnect(connection_id)
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

runtime = MCPRuntime()
