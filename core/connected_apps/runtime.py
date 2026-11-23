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
        self._connection_locks = {}
        self._shutting_down = False

    def _connection_lock(self, cid):
        with self._lock:
            return self._connection_locks.setdefault(str(cid), threading.RLock())

    def _ensure_loop(self):
        with self._lock:
            if self._loop and self._loop.is_running():
                return
            self._shutting_down = False
            ready = threading.Event()

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

    async def _connection_worker(self, cid, factory, ready, stop_event):
        """Own a transport/session for its complete lifetime in one asyncio task.

        AnyIO task groups and cancel scopes must be exited by the same task that
        entered them. Keeping AsyncExitStack inside this long-lived task avoids
        cross-task cleanup when the UI later requests disconnect.
        """
        task = asyncio.current_task()
        stack = AsyncExitStack()
        failure = None
        try:
            from mcp import ClientSession

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
                self._sessions[cid] = (task, session, stop_event)
                self._tools[cid] = tools
            if not ready.done():
                ready.set_result(tools)

            # This task deliberately stays alive while the transport is active.
            # Disconnect/reconnect/shutdown signal stop_event; the owner task
            # then unwinds ClientSession and transport contexts itself.
            await stop_event.wait()
        except BaseException as exc:
            failure = exc
            if not ready.done():
                ready.set_exception(exc)
        finally:
            with self._lock:
                current = self._sessions.get(cid)
                if current is not None and current[0] is task:
                    self._sessions.pop(cid, None)
                    self._tools.pop(cid, None)
            try:
                await stack.aclose()
            except BaseException as cleanup_error:
                if failure is None:
                    failure = cleanup_error
                else:
                    logger.warning(
                        "MCP transport cleanup failed after session error for %s",
                        cid,
                        exc_info=True,
                    )

        if failure is not None:
            raise failure

    async def _connect(self, cid, factory):
        """Start a dedicated owner task and return after MCP initialization."""
        # A reconnect must fully close the previous owner task before a new
        # transport is created for the same connection id.
        await self._close(cid)

        loop = asyncio.get_running_loop()
        ready = loop.create_future()
        stop_event = asyncio.Event()
        task = asyncio.create_task(
            self._connection_worker(cid, factory, ready, stop_event),
            name=f"connected-apps-mcp:{cid}",
        )
        try:
            return await asyncio.wait_for(ready, timeout=85)
        except BaseException:
            # If startup fails or its caller times out/cancels, don't leave a
            # subprocess or HTTP transport running in the background.
            stop_event.set()
            try:
                await task
            except BaseException:
                pass
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

        def factory():
            from mcp.client.stdio import stdio_client, StdioServerParameters
            params = StdioServerParameters(command=str(command).strip(), args=list(args or []), env=dict(env or {}) or None)
            return stdio_client(params)

        with self._connection_lock(cid):
            return self._submit(self._connect(cid, factory), 90)

    def connect_http(self, cid, url, headers=None):
        url = _validate_http_endpoint(url)
        headers = _validate_headers(headers or {})

        def factory():
            from mcp.client.streamable_http import streamablehttp_client
            return streamablehttp_client(url, headers=headers)

        with self._connection_lock(cid):
            return self._submit(self._connect(cid, factory), 90)

    def connect_sse(self, cid, url, headers=None):
        url = _validate_http_endpoint(url)
        headers = _validate_headers(headers or {})

        def factory():
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
        with self._connection_lock(cid):
            return self._submit(self._ping(cid), timeout=timeout)

    async def _close(self, cid):
        with self._lock:
            entry = self._sessions.get(cid)
            if entry is None:
                self._tools.pop(cid, None)
                return

        owner_task, _session, stop_event = entry
        stop_event.set()
        if owner_task is not asyncio.current_task():
            # The owner task exits its own AsyncExitStack in its finally block.
            await owner_task

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

        # Ask each dedicated owner task to exit its own AsyncExitStack. Never
        # close transport stacks from the shutdown caller's task.
        for _cid, entry in entries:
            entry[2].set()
        if entries:
            results = await asyncio.gather(
                *(entry[0] for _cid, entry in entries),
                return_exceptions=True,
            )
            for (cid, _entry), result in zip(entries, results):
                if isinstance(result, BaseException):
                    logger.warning(
                        "MCP session cleanup failed during shutdown for %s: %s",
                        cid,
                        type(result).__name__,
                    )

        with self._lock:
            self._sessions.clear()
            self._tools.clear()

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
