"""Optional MCP runtime isolated from JARVIS' Qt/UI thread."""
from __future__ import annotations

import asyncio
import threading
import re
import logging

logger = logging.getLogger(__name__)
from contextlib import AsyncExitStack
from urllib.parse import urlsplit


class RuntimeUnavailable(RuntimeError):
    pass


def _normalize_annotations(value):
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return {
        key: getattr(value, key, None)
        for key in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
    }


def _validate_http_endpoint(url: str) -> str:
    value = str(url or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("MCP endpoint must be a valid HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("Credentials must not be embedded in the MCP URL")
    if parsed.fragment:
        raise ValueError("MCP endpoint URL must not contain a fragment")
    return value


def _validate_headers(headers):
    if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
        raise ValueError("MCP HTTP headers must be a string mapping")
    forbidden = {"host", "content-length", "transfer-encoding", "connection"}
    for key, value in headers.items():
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key):
            raise ValueError("MCP HTTP header name is invalid")
        if key.casefold() in forbidden:
            raise ValueError(f"MCP HTTP header '{key}' is managed by the transport")
        if "\r" in value or "\n" in value:
            raise ValueError("MCP HTTP header values cannot contain newline characters")
    return dict(headers)


class MCPRuntime:
    def __init__(self):
        self._lock = threading.RLock()
        self._loop = None
        self._thread = None
        self._sessions = {}
        self._tools = {}
        self._connection_locks = {}\n        self._shutting_down = False

    def _connection_lock(self, cid):
        with self._lock:
            return self._connection_locks.setdefault(str(cid), threading.RLock())

    def _ensure_loop(self):
        with self._lock:
            if self._loop and self._loop.is_running():
                return
            self._shutting_down = False\n            ready = threading.Event()

            def run():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                # Publish the loop before signaling readiness. Do not acquire
                # self._lock here: _ensure_loop holds it while waiting.
                self._loop = loop
                ready.set()
                loop.run_forever()
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.close()
                with self._lock:
                    if self._loop is loop:
                        self._loop = None

            self._thread = threading.Thread(target=run, name="connected-apps-mcp", daemon=True)
            self._thread.start()
            if not ready.wait(5):
                raise RuntimeUnavailable("Could not start MCP runtime")

    def _submit(self, coro, timeout=60):
        try:
            self._ensure_loop()
        except Exception:
            if hasattr(coro, "close"):
                coro.close()
            raise
        with self._lock:
            loop = self._loop
        if not loop or not loop.is_running():
            if hasattr(coro, "close"):
                coro.close()
            raise RuntimeUnavailable("MCP runtime event loop is not running")
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            future.cancel()
            raise RuntimeUnavailable(f"MCP operation timed out after {timeout}s") from None

    async def _connect(self, cid, factory):
        try:
            from mcp import ClientSession
        except ImportError as exc:
            raise RuntimeUnavailable(
                "MCP SDK is missing. Install JARVIS with the connected-apps extra."
            ) from exc

        stack = AsyncExitStack()
        try:
            transport = await stack.enter_async_context(factory())
            if isinstance(transport, (tuple, list)):
                transport = transport[:2]
            session = await stack.enter_async_context(ClientSession(*transport))
            await session.initialize()
            result = await session.list_tools()
            tools = [
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.inputSchema or {},
                    "annotations": _normalize_annotations(getattr(tool, "annotations", None)),
                }
                for tool in result.tools
            ]
            with self._lock:
                old = self._sessions.pop(cid, None)
                self._tools.pop(cid, None)
                self._sessions[cid] = (stack, session)
                self._tools[cid] = tools
            if old:
                # A failed cleanup of the previous session must not tear down
                # the newly established connection.
                try:
                    await old[0].aclose()
                except Exception:
                    pass
            return tools
        except BaseException:
            # Preserve the original connect/initialize failure. A transport
            # cleanup error must not replace the actionable root cause.
            try:
                await stack.aclose()
            except BaseException:
                logger.warning("MCP transport cleanup failed after connection error", exc_info=True)
            raise

    def connect_stdio(self, cid, command, args=None, env=None):
        if not command or not str(command).strip():
            raise ValueError("MCP command cannot be empty")
        if not isinstance(args, (list, tuple)) or not all(isinstance(x, str) for x in (args or [])):
            raise ValueError("MCP arguments must be a list of strings")
        if env is not None and (not isinstance(env, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in env.items()
        )):
            raise ValueError("MCP environment must be a string mapping")

        async def factory():
            from mcp.client.stdio import stdio_client, StdioServerParameters
            params = StdioServerParameters(command=str(command).strip(), args=list(args or []), env=dict(env or {}) or None)
            return stdio_client(params)

        with self._connection_lock(cid):
            return self._submit(self._connect(cid, factory), 90)

    def connect_http(self, cid, url, headers=None):
        url = _validate_http_endpoint(url)
        headers = _validate_headers(headers or {})

        async def factory():
            from mcp.client.streamable_http import streamablehttp_client
            return streamablehttp_client(url, headers=headers)

        with self._connection_lock(cid):
            return self._submit(self._connect(cid, factory), 90)

    def connect_sse(self, cid, url, headers=None):
        url = _validate_http_endpoint(url)
        headers = _validate_headers(headers or {})

        async def factory():
            from mcp.client.sse import sse_client
            return sse_client(url, headers=headers)

        with self._connection_lock(cid):
            return self._submit(self._connect(cid, factory), 90)

    async def _call(self, cid, tool, args):
        with self._lock:
            entry = self._sessions.get(cid)
            allowed = {row["name"] for row in self._tools.get(cid, [])}
        if not entry:
            raise RuntimeUnavailable("MCP connection is not active")
        if tool not in allowed:
            raise ValueError("Tool is not registered")
        if not isinstance(args, dict):
            raise ValueError("Tool arguments must be a JSON object")
        result = await entry[1].call_tool(tool, arguments=args)
        if getattr(result, "isError", False):
            detail = "; ".join(
                str(getattr(item, "text", "")).strip()
                for item in getattr(result, "content", [])
                if getattr(item, "text", None)
            )
            raise RuntimeError(detail or "MCP tool returned an error")
        content = []
        for item in getattr(result, "content", []):
            row = {"type": getattr(item, "type", "unknown")}
            text = getattr(item, "text", None)
            if text is not None:
                row["text"] = text
            mime = getattr(item, "mimeType", None)
            if mime:
                row["mime_type"] = mime
            content.append(row)
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            content.append({"type": "structured", "data": structured})
        return content

    async def _ping(self, cid):
        with self._lock:
            entry = self._sessions.get(cid)
        if not entry:
            return False
        ping = getattr(entry[1], "ping", None)
        if callable(ping):
            await ping()
            return True
        # MCP SDK versions/providers without a ping method can still be
        # checked by performing a lightweight tool-list request.
        await entry[1].list_tools()
        return True

    def health(self, cid, timeout=10):
        return self._submit(self._ping(cid), timeout=timeout)

    async def _close(self, cid):
        with self._lock:
            entry = self._sessions.pop(cid, None)
            self._tools.pop(cid, None)
        if entry:
            await entry[0].aclose()

    def tools(self, cid):
        with self._lock:
            return [dict(row) for row in self._tools.get(cid, [])]

    def call(self, cid, tool, args=None):
        with self._connection_lock(cid):
            return self._submit(self._call(cid, tool, args or {}), 120)

    def disconnect(self, cid):
        # Avoid creating a coroutine when the runtime is already stopped.
        with self._lock:
            loop = self._loop
        if not loop or not loop.is_running():
            raise RuntimeUnavailable("MCP runtime is not running; disconnect could not be confirmed")
        try:
            with self._connection_lock(cid):
                self._submit(self._close(cid), 15)
            return True
        except Exception as exc:
            logger.exception("Failed to disconnect MCP connection %s", cid)
            raise RuntimeUnavailable(f"Failed to disconnect MCP connection: {exc}") from exc

    async def _close_all(self):
        with self._lock:
            entries = list(self._sessions.items())
            self._sessions.clear()
            self._tools.clear()
        errors = []
        for cid, (stack, _session) in entries:
            try:
                await stack.aclose()
            except Exception as exc:
                errors.append((cid, exc))
        for cid, exc in errors:
            logger.warning("MCP session cleanup failed during shutdown for %s: %s", cid, type(exc).__name__)

    def shutdown(self):
        # Idempotent, best-effort teardown. Close every transport before
        # stopping the event loop so AsyncExitStack cleanup can run.
        with self._lock:
            loop = self._loop
            thread = self._thread
        if loop and loop.is_running():
            try:
                self._submit(self._close_all(), timeout=15)
            except Exception:
                logger.warning("MCP runtime shutdown cleanup failed", exc_info=True)
            if loop.is_running():
                loop.call_soon_threadsafe(loop.stop)
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3)
        with self._lock:
            if self._thread is thread and (not thread or not thread.is_alive()):
                self._thread = None


runtime = MCPRuntime()
