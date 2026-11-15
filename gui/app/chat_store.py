"""Persistent, independent chat sessions for the desktop conversation UI."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from threading import RLock
from typing import Any

from core.user_data import update_json


class ChatStore:
    """Stores chat history and replayable context outside replaceable app files."""

    MAX_CHATS = 100
    MAX_TURNS = 200
    MAX_CONTEXT_MESSAGES = 80

    def __init__(
        self,
        path: Path,
        *,
        legacy_chat_path: Path | None = None,
        legacy_history_path: Path | None = None,
    ) -> None:
        self.path = Path(path)
        self._legacy_chat_path = Path(legacy_chat_path) if legacy_chat_path else None
        self._legacy_history_path = Path(legacy_history_path) if legacy_history_path else None
        self._lock = RLock()
        self._ensure_store()

    @staticmethod
    def _timestamp() -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _read_legacy(path: Path | None) -> list[dict[str, Any]]:
        if path is None:
            return []
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return []
        rows = value.get("turns", []) if isinstance(value, dict) else []
        return [dict(row) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    @staticmethod
    def _context_from_turns(turns: list[dict[str, Any]]) -> list[dict[str, str]]:
        context: list[dict[str, str]] = []
        for turn in turns:
            request = str(turn.get("request") or "").strip()
            result = str(turn.get("result") or "").strip()
            if request:
                context.append({"role": "user", "text": request})
            if result and str(turn.get("status") or "complete").lower() != "error":
                context.append({"role": "model", "text": result})
        return context[-ChatStore.MAX_CONTEXT_MESSAGES :]

    @staticmethod
    def _title(turns: list[dict[str, Any]], fallback: str = "New chat") -> str:
        for turn in turns:
            request = str(turn.get("request") or "").strip()
            if request:
                return (request[:52].rstrip() + ("…" if len(request) > 52 else ""))
        return fallback

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "active_chat_id": "", "chats": []}
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError(f"Could not read chat database {self.path.name}: {exc}") from exc
        if not isinstance(value, dict) or not isinstance(value.get("chats"), list):
            raise RuntimeError(f"Chat database {self.path.name} has an invalid structure")
        value.setdefault("version", 1)
        value.setdefault("active_chat_id", "")
        value["chats"] = [
            self._normalise_chat(chat)
            for chat in value["chats"]
            if isinstance(chat, dict) and chat.get("id")
        ]
        return value

    @staticmethod
    def _normalise_chat(chat: dict[str, Any]) -> dict[str, Any]:
        turns = chat.get("turns", [])
        turns = [dict(turn) for turn in turns if isinstance(turn, dict)] if isinstance(turns, list) else []
        context = chat.get("context", [])
        context = [
            {"role": str(item.get("role") or "user"), "text": str(item.get("text") or "")}
            for item in context
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ] if isinstance(context, list) else []
        if not context:
            context = ChatStore._context_from_turns(turns)
        return {
            "id": str(chat.get("id") or uuid.uuid4().hex),
            "title": str(chat.get("title") or ChatStore._title(turns)),
            "created_at": str(chat.get("created_at") or ChatStore._timestamp()),
            "updated_at": str(chat.get("updated_at") or chat.get("created_at") or ChatStore._timestamp()),
            "turns": turns[-ChatStore.MAX_TURNS :],
            "context": context[-ChatStore.MAX_CONTEXT_MESSAGES :],
            "resume_handle": str(chat.get("resume_handle") or ""),
        }

    def _write(self, data: dict[str, Any]) -> None:
        payload = {
            "version": 1,
            "active_chat_id": str(data.get("active_chat_id") or ""),
            "chats": [self._normalise_chat(chat) for chat in data.get("chats", [])],
        }
        def replace(current: dict) -> None:
            current.clear()
            current.update(payload)
        update_json(self.path, replace)

    def _legacy_chats(self) -> list[dict[str, Any]]:
        active = self._read_legacy(self._legacy_chat_path)
        archive = self._read_legacy(self._legacy_history_path)
        active_ids = {str(row.get("id")) for row in active if row.get("id")}
        archive = [row for row in archive if not row.get("id") or str(row.get("id")) not in active_ids]
        chats: list[dict[str, Any]] = []
        for title, turns in (("Previous conversation", active), ("Earlier conversations", archive)):
            if not turns:
                continue
            created = str(turns[0].get("time") or self._timestamp())
            chats.append({
                "id": uuid.uuid4().hex,
                "title": self._title(turns, title),
                "created_at": created,
                "updated_at": str(turns[-1].get("response_time") or turns[-1].get("time") or created),
                "turns": turns[-self.MAX_TURNS :],
                "context": self._context_from_turns(turns),
            })
        return chats

    def _ensure_store(self) -> None:
        with self._lock:
            if self.path.exists():
                data = self._read()
                if data["chats"]:
                    active_id = str(data.get("active_chat_id") or "")
                    if not any(chat["id"] == active_id for chat in data["chats"]):
                        data["active_chat_id"] = data["chats"][0]["id"]
                        self._write(data)
                    return
            else:
                data = {"version": 1, "active_chat_id": "", "chats": self._legacy_chats()}
            if not data["chats"]:
                chat = self._new_chat_record()
                data["chats"] = [chat]
                data["active_chat_id"] = chat["id"]
            self._write(data)

    def _new_chat_record(self, title: str = "New chat") -> dict[str, Any]:
        now = self._timestamp()
        return {
            "id": uuid.uuid4().hex,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "turns": [],
            "context": [],
            "resume_handle": "",
        }

    def list_chats(self) -> list[dict[str, Any]]:
        with self._lock:
            chats = self._read()["chats"]
            return sorted(chats, key=lambda chat: chat.get("updated_at", ""), reverse=True)

    def get_chat(self, chat_id: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            data = self._read()
            target = str(chat_id or data.get("active_chat_id") or "")
            for chat in data["chats"]:
                if chat["id"] == target:
                    return dict(chat)
            return None

    def get_active_chat(self) -> dict[str, Any]:
        chat = self.get_chat()
        if chat is not None:
            return chat
        return self.create_chat()

    def get_active_chat_id(self) -> str:
        with self._lock:
            return str(self._read().get("active_chat_id") or "")

    def get_resume_handle(self, chat_id: str | None = None) -> str | None:
        chat = self.get_chat(chat_id)
        handle = str(chat.get("resume_handle") or "").strip() if chat else ""
        return handle or None

    def save_resume_handle(
        self, handle: str | None, chat_id: str | None = None
    ) -> bool:
        with self._lock:
            data = self._read()
            target = str(chat_id or data.get("active_chat_id") or "")
            for chat in data["chats"]:
                if chat["id"] == target:
                    chat["resume_handle"] = str(handle or "")
                    self._write(data)
                    return True
            return False

    def get_context(
        self, chat_id: str | None = None, *, limit: int = MAX_CONTEXT_MESSAGES
    ) -> list[dict[str, str]]:
        chat = self.get_chat(chat_id)
        if not chat:
            return []
        count = max(0, min(self.MAX_CONTEXT_MESSAGES, int(limit)))
        return list(chat.get("context") or [])[-count:] if count else []

    def clear_chat(self, chat_id: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            data = self._read()
            target = str(chat_id or data.get("active_chat_id") or "")
            for chat in data["chats"]:
                if chat["id"] == target:
                    chat["turns"] = []
                    chat["context"] = []
                    chat["resume_handle"] = ""
                    chat["title"] = "New chat"
                    chat["updated_at"] = self._timestamp()
                    self._write(data)
                    return dict(chat)
            return None

    def start_new_chat(self, title: str = "New chat") -> dict[str, Any]:
        """Create a fresh chat context; previous chats remain available."""
        with self._lock:
            data = self._read()
            chat = self._new_chat_record(title)
            data["chats"].append(chat)
            data["chats"] = data["chats"][-self.MAX_CHATS :]
            data["active_chat_id"] = chat["id"]
            self._write(data)
            return dict(chat)

    def create_chat(self, title: str = "New chat") -> dict[str, Any]:
        return self.start_new_chat(title)

    def set_active(self, chat_id: str) -> dict[str, Any] | None:
        with self._lock:
            data = self._read()
            selected = next((chat for chat in data["chats"] if chat["id"] == str(chat_id)), None)
            if selected is None:
                return None
            data["active_chat_id"] = selected["id"]
            self._write(data)
            return dict(selected)

    def save_chat(self, chat: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            normalised = self._normalise_chat(chat)
            normalised["updated_at"] = self._timestamp()
            for index, existing in enumerate(data["chats"]):
                if existing["id"] == normalised["id"]:
                    data["chats"][index] = normalised
                    break
            else:
                data["chats"].append(normalised)
            data["active_chat_id"] = normalised["id"]
            data["chats"] = data["chats"][-self.MAX_CHATS :]
            self._write(data)
            return dict(normalised)

    def clear_all(self) -> dict[str, Any]:
        with self._lock:
            chat = self._new_chat_record()
            self._write({"active_chat_id": chat["id"], "chats": [chat]})
            return dict(chat)
