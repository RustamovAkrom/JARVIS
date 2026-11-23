"""Telegram Personal native Connected App.

This adapter is intentionally separate from the Telegram Remote plugin.
It uses a local MTProto session and enforces permissions/chat allowlists before
every operation. Message content is never written to logs or audit metadata.
"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from core.connected_apps.native import NativeAppAdapter, NativeAppDescriptor, NativeTool

try:
    from telethon import TelegramClient
    from telethon.sessions import StringSession
except Exception:  # optional dependency
    TelegramClient = None
    StringSession = None


APP_ID = "telegram_personal"

DEFAULT_PERMISSIONS = {
    "list_chats": True,
    "search_messages": True,
    "read_recent_messages": False,
    "read_history": False,
    "read_media": False,
    "send_messages": True,
    "reply_messages": True,
    "forward_messages": False,
    "edit_messages": False,
    "delete_messages": False,
    "contacts": False,
    "ai_access": False,
}

TOOLS = (
    NativeTool("telegram_list_chats", "List chat metadata. Does not read message contents.", {"type":"object","properties":{"limit":{"type":"integer","minimum":1,"maximum":100}},"additionalProperties":False}, read_only=True),
    NativeTool("telegram_search_messages", "Search messages only in explicitly allowed chats.", {"type":"object","properties":{"query":{"type":"string","minLength":1,"maxLength":256},"chat_id":{"type":"string"},"limit":{"type":"integer","minimum":1,"maximum":50}},"required":["query"],"additionalProperties":False}, read_only=True),
    NativeTool("telegram_get_recent_messages", "Read recent messages from an explicitly allowed chat.", {"type":"object","properties":{"chat_id":{"type":"string"},"limit":{"type":"integer","minimum":1,"maximum":50}},"required":["chat_id"],"additionalProperties":False}, read_only=True),
    NativeTool("telegram_send_message", "Send a message. Confirmation is required by default.", {"type":"object","properties":{"chat_id":{"type":"string"},"text":{"type":"string","minLength":1,"maxLength":4096}},"required":["chat_id","text"],"additionalProperties":False}, read_only=False, requires_confirmation=True),
    NativeTool("telegram_reply_message", "Reply to a message. Confirmation is required by default.", {"type":"object","properties":{"chat_id":{"type":"string"},"message_id":{"type":"integer"},"text":{"type":"string","minLength":1,"maxLength":4096}},"required":["chat_id","message_id","text"],"additionalProperties":False}, read_only=False, requires_confirmation=True),
)

class TelegramPersonalAdapter(NativeAppAdapter):
    descriptor = NativeAppDescriptor(
        id=APP_ID, name="Telegram Personal",
        description="Personal Telegram account integration using a local MTProto session.",
        category="communication", icon="telegram",
        auth_provider="telegram-mtproto",
        scopes=("local-session",),
    )
    tools = TOOLS

    def __init__(self, store):
        super().__init__(store)
        self._clients: dict[str, Any] = {}
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.RLock()
        self._loop = None
        self._loop_thread = None

    def _lock(self, cid):
        with self._guard:
            return self._locks.setdefault(cid, threading.RLock())

    def _metadata(self, cid):
        row = self.store.get_connection(cid) or {}
        return row.get("metadata") or {}

    def _permissions(self, cid):
        meta = self._metadata(cid)
        perms = dict(DEFAULT_PERMISSIONS)
        perms.update(meta.get("telegram_permissions") or {})
        return perms

    def _allowed_chats(self, cid):
        return set(map(str, self._metadata(cid).get("telegram_allowed_chats") or []))

    def _require(self, cid, permission, chat_id=None):
        if not self._permissions(cid).get(permission, False):
            raise PermissionError(f"Telegram permission is disabled: {permission}")
        if chat_id is not None:
            if str(chat_id) not in self._allowed_chats(cid):
                raise PermissionError("This Telegram chat is not explicitly allowed")
    
    def _client(self, cid):
        if TelegramClient is None:
            raise RuntimeError("Telethon is not installed. Install the optional Telegram Personal dependency.")
        with self._lock(cid):
            client = self._clients.get(cid)
            if client is not None:
                return client
            secret = self.store.get_credential(cid, "mtproto_session")
            config = self.store.get_credential(cid, "telegram_api_config")
            if not secret or not config:
                raise RuntimeError("Telegram account is not authenticated")
            cfg = json.loads(config)
            client = TelegramClient(
                StringSession(secret), int(cfg["api_id"]), cfg["api_hash"],
                connection_retries=2, request_retries=2, auto_reconnect=True,
            )
            self._run(client.connect())
            if not self._run(client.is_user_authorized()):
                self._run(client.disconnect())
                raise RuntimeError("Telegram session is unauthorized; reconnect the account")
            self._clients[cid] = client
            return client

    def _run(self, coro):
        # Telethon requires one stable event loop for each client lifetime.
        with self._guard:
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                self._loop_thread = threading.Thread(
                    target=self._loop.run_forever, name="jarvis-telegram-loop", daemon=True
                )
                self._loop_thread.start()
            loop = self._loop
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=60)

    def health_check(self, cid):
        try:
            client = self._client(cid)
            self._run(client.get_me())
            return True
        except Exception:
            return False

    def execute(self, cid, tool_name, arguments):
        args = arguments or {}
        if tool_name == "telegram_list_chats":
            self._require(cid, "list_chats")
            client = self._client(cid)
            limit = min(max(int(args.get("limit", 30)), 1), 100)
            async def run():
                rows = []
                async for dialog in client.iter_dialogs(limit=limit):
                    rows.append({"chat_id": str(dialog.id), "name": dialog.name, "type": type(dialog.entity).__name__})
                return rows
            return self._run(run())

        if tool_name == "telegram_search_messages":
            self._require(cid, "search_messages")
            query = str(args.get("query") or "").strip()
            if not query:
                raise ValueError("Search query is required")
            chat_id = args.get("chat_id")
            allowed = self._allowed_chats(cid)
            if chat_id is not None:
                self._require(cid, "search_messages", chat_id)
                targets = [int(chat_id)]
            else:
                targets = [int(x) for x in allowed]
            if not targets:
                raise PermissionError("No Telegram chats are allowlisted")
            client = self._client(cid)
            limit = min(max(int(args.get("limit", 20)), 1), 50)
            async def run():
                results = []
                for target in targets:
                    async for msg in client.iter_messages(target, search=query, limit=limit):
                        results.append({"chat_id": str(target), "message_id": msg.id, "date": msg.date.isoformat() if msg.date else None, "text": msg.message or ""})
                        if len(results) >= limit:
                            return results
                return results
            return self._run(run())

        if tool_name == "telegram_get_recent_messages":
            chat_id = str(args["chat_id"])
            self._require(cid, "read_recent_messages", chat_id)
            client = self._client(cid)
            limit = min(max(int(args.get("limit", 20)), 1), 50)
            async def run():
                messages = []
                async for msg in client.iter_messages(int(chat_id), limit=limit):
                    messages.append({"message_id": msg.id, "date": msg.date.isoformat() if msg.date else None, "text": msg.message or ""})
                return messages
            return self._run(run())

        if tool_name in {"telegram_send_message", "telegram_reply_message"}:
            chat_id = str(args["chat_id"])
            self._require(cid, "send_messages" if tool_name == "telegram_send_message" else "reply_messages", chat_id)
            client = self._client(cid)
            text = str(args["text"])
            async def run():
                if tool_name == "telegram_reply_message":
                    return await client.send_message(int(chat_id), text, reply_to=int(args["message_id"]))
                return await client.send_message(int(chat_id), text)
            msg = self._run(run())
            return {"chat_id": chat_id, "message_id": msg.id, "sent": True}

        raise ValueError("Telegram tool is not supported")

    def disconnect(self, cid):
        with self._lock(cid):
            client = self._clients.pop(cid, None)
        if client is not None:
            try:
                self._run(client.disconnect())
            except Exception:
                pass
        return True
