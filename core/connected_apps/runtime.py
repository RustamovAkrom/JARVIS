"""Optional MCP runtime with isolated asyncio loop and managed session lifetimes."""
from __future__ import annotations
import asyncio, threading
from contextlib import AsyncExitStack
from typing import Any

class RuntimeUnavailable(RuntimeError):
    pass

class MCPRuntime:
    def __init__(self):
        self._lock=threading.RLock()
        self._loop: asyncio.AbstractEventLoop|None=None
        self._thread: threading.Thread|None=None
        self._sessions: dict[str,tuple[AsyncExitStack,Any]]={}
        self._tools: dict[str,list[dict]]={}

    def _ensure_loop(self):
        with self._lock:
            if self._loop and self._loop.is_running(): return
            ready=threading.Event()
            def run():
                loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop); self._loop=loop; ready.set()
                loop.run_forever()
                pending=asyncio.all_tasks(loop)
                for task in pending: task.cancel()
                if pending: loop.run_until_complete(asyncio.gather(*pending,return_exceptions=True))
                loop.close()
            self._thread=threading.Thread(target=run,name="connected-apps-mcp",daemon=True)
            self._thread.start()
            if not ready.wait(5) or not self._loop: raise RuntimeUnavailable("Could not start MCP runtime")

    def _submit(self,coro,timeout=60):
        self._ensure_loop()
        future=asyncio.run_coroutine_threadsafe(coro,self._loop)
        try: return future.result(timeout=timeout)
        except TimeoutError:
            future.cancel(); raise RuntimeUnavailable(f"MCP operation timed out after {timeout}s")

    async def _connect(self,connection_id,transport_factory):
        try:
            from mcp import ClientSession
        except ImportError as exc: raise RuntimeUnavailable("MCP SDK is not installed. Install optional dependency 'mcp'.") from exc
        stack=AsyncExitStack()
        try:
            transport=await stack.enter_async_context(transport_factory())
            session=await stack.enter_async_context(ClientSession(*transport))
            await session.initialize()
            result=await session.list_tools()
            tools=[{"name":t.name,"description":t.description or "","input_schema":t.inputSchema or {}} for t in result.tools]
            old=self._sessions.pop(connection_id,None)
            if old: await old[0].aclose()
            self._sessions[connection_id]=(stack,session); self._tools[connection_id]=tools
            return tools
        except BaseException:
            await stack.aclose(); raise

    def connect_stdio(self,connection_id,command,args=None,env=None):
        if not command or not command.strip(): raise ValueError("MCP command cannot be empty")
        async def factory():
            from mcp.client.stdio import stdio_client,StdioServerParameters
            return stdio_client(StdioServerParameters(command=command,args=list(args or []),env=dict(env or {}) or None))
        return self._submit(self._connect(connection_id,factory),timeout=90)

    def connect_http(self,connection_id,url,headers=None):
        if not url.lower().startswith(("https://","http://")): raise ValueError("MCP URL must be HTTP(S)")
        async def factory():
            from mcp.client.streamable_http import streamablehttp_client
            client=streamablehttp_client(url,headers=dict(headers or {}))\n            async def adapter():\n                transport=await client.__aenter__()\n                return transport[:2]\n            return adapter()
        return self._submit(self._connect(connection_id,factory),timeout=90)

    async def _call(self,connection_id,tool,arguments):
        entry=self._sessions.get(connection_id)
        if not entry: raise RuntimeUnavailable("MCP connection is not active")
        if tool not in {x["name"] for x in self._tools.get(connection_id,[])}: raise ValueError("Tool is not registered for this connection")
        result=await entry[1].call_tool(tool,arguments=arguments)
        if getattr(result,"isError",False): raise RuntimeError("MCP tool returned an error")
        return [{"type":getattr(x,"type","unknown"),"text":getattr(x,"text","")} for x in getattr(result,"content",[])]

    async def _close(self,connection_id):
        entry=self._sessions.pop(connection_id,None); self._tools.pop(connection_id,None)
        if entry: await entry[0].aclose()

    def tools(self,connection_id):
        with self._lock: return list(self._tools.get(connection_id,[]))
    def call(self,connection_id,tool,arguments=None): return self._submit(self._call(connection_id,tool,arguments or {}),timeout=120)
    def disconnect(self,connection_id):
        try: self._submit(self._close(connection_id),timeout=15)
        except Exception: pass
    def shutdown(self):
        for cid in list(self._sessions): self.disconnect(cid)
        if self._loop and self._loop.is_running(): self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread and self._thread.is_alive(): self._thread.join(timeout=3)
runtime=MCPRuntime()
