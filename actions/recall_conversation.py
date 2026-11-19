"""Search and manage opt-in local conversation history."""
from __future__ import annotations

TOOL = {
    "name": "recall_conversation",
    "description": (
        "Search previous conversations stored locally. Use when the user asks what was discussed "
        "earlier, what they mentioned, or refers to a past conversation. Actions: search, status, "
        "on, off, keep_days, forget. Raw history is opt-in. Never claim deletion unless confirmed."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "enum": ["search", "status", "on", "off", "keep_days", "forget"]},
            "query": {"type": "STRING"},
            "date_from": {"type": "STRING", "description": "Inclusive YYYY-MM-DD"},
            "date_to": {"type": "STRING", "description": "Inclusive YYYY-MM-DD"},
            "days": {"type": "INTEGER"},
            "everything": {"type": "BOOLEAN"},
            "confirmed": {"type": "BOOLEAN"},
        },
        "required": ["action"],
    },
    "handler": lambda parameters, **ctx: handle(parameters),
}

def handle(parameters: dict) -> str:
    from memory import conversations as conv
    from memory.config_manager import save_conversation_memory
    p = parameters or {}
    action = str(p.get("action", "search")).lower()
    try:
        if action == "search":
            return conv.search(p.get("query", ""), p.get("date_from"), p.get("date_to"))
        if action == "status":
            s = conv.stats()
            return (f"Conversation memory is {'on' if s['enabled'] else 'off'}. "
                    f"Stored {s['exchanges']} exchanges across {s['days_with_conversations']} days. "
                    f"Retention: {s['days_kept']} days. Local storage only.")
        if action in {"on", "off"}:
            cfg = save_conversation_memory(enabled=action == "on")
            conv.refresh_settings()
            if action == "on":
                return f"Conversation memory enabled. New exchanges will be stored locally for {cfg['days']} days."
            return "Conversation memory disabled. No new exchanges will be stored. Existing history remains until deleted or expired."
        if action == "keep_days":
            days = int(p.get("days") or 0)
            if not 1 <= days <= 3650:
                return "Retention must be between 1 and 3650 days."
            cfg = save_conversation_memory(days=days)
            conv.refresh_settings()
            deleted = conv.prune(days)
            return f"Retention set to {cfg['days']} days. Deleted {deleted} older day(s)."
        if action == "forget":
            if not p.get("confirmed"):
                return "Deletion not performed. Ask the user to confirm the date range or all history first."
            conv.flush()
            deleted = conv.forget(p.get("date_from"), p.get("date_to"), everything=bool(p.get("everything")))
            return f"Deleted {deleted} conversation day-file(s)."
        return "Unknown conversation memory action."
    except (ValueError, TypeError) as exc:
        return f"Conversation memory operation failed: {exc}"
