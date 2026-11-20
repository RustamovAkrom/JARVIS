"""
Plugin — Shopping list.

A persistent shopping list you manage entirely by voice: "add milk, eggs
and bread", "I bought the milk" (check it off), "what's on the list?", "clear the
list". It lives in memory/shopping_list.json, so it's still there tomorrow
and across sessions — the small remembered detail that makes JARVIS feel
like it's actually keeping track for you, which a stateless model can't.

Pure Python, no third-party dependency, cross-OS, all languages (item
names are stored exactly as spoken).
"""

import json
import re
from pathlib import Path

try:
    from core.paths import data_dir as _data_dir
except ImportError:          # a core older than core/paths.py
    _data_dir = lambda: Path(__file__).resolve().parent.parent

STATE_FILE = _data_dir() / "memory" / "shopping_list.json"

PLUGIN = {
    # For the plugin manager (core/plugin_loader): never sent to a model.
    "level": "basic",
    "label": "Shopping list",
    "summary": "Keeps your shopping list.",
    "setup": [
        "Say: \"Add milk to my shopping list\" or \"What's on my shopping list?\"",
    ],
    "name": "shopping_list",
    "description": (
        "Manages a persistent SHOPPING / GROCERY list by voice and remembers it "
        "across sessions. Use for: 'add milk and eggs to my shopping list', "
        "'add bread, eggs and cheese', 'I bought the milk / I got this' (check "
        "an item off), 'remove sugar from the list' (remove an item), "
        "'what's on my shopping list', 'clear the list'. This is a stored "
        "to-buy list — do NOT use the "
        "'reminder' tool or memory-saving for these requests."
    ),
    # Used in place of `description` only when a small model is on a tool
    # budget - Gemini still gets the full text. See providers/schema.py.
    "brief": (
        "Adds to, reads and clears the shopping list."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "'add' add item(s), 'remove' delete item(s), 'check' mark "
                    "item(s) as bought, 'list' read the list, 'clear' empty it. "
                    "Default 'list'."
                ),
            },
            "items": {
                "type": "STRING",
                "description": (
                    "The item or items involved, in the user's own words. For "
                    "several items, separate them with COMMAS (e.g. 'milk, eggs, "
                    "bread') — do not join them with a conjunction word."
                ),
            },
            "quantity": {
                "type": "STRING",
                "description": "Optional quantity for a single added item, e.g. '2 kg', '3 adet'.",
            },
            "which": {
                "type": "STRING",
                "description": "For 'clear': 'all' (default) or 'done' to remove only bought items.",
            },
        },
        "required": [],
    },
}


# ── persistence ──────────────────────────────────────────────────────────────

def _load() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data.setdefault("items", [])  # each: {"name": str, "qty": str, "done": bool}
    return data


def _save(data: dict) -> bool:
    """Persist atomically. A failed write must never be reported as saved or
    mirrored into long-term memory as though the disk state had changed."""
    import os
    import tempfile

    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=1)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{STATE_FILE.name}.", suffix=".tmp", dir=str(STATE_FILE.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, STATE_FILE)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
        _remember(data)
        return True
    except Exception as exc:
        print(f"[ShoppingList] persistence failed: {type(exc).__name__}: {exc}")
        return False


def _remember(data: dict) -> None:
    """Mirror the current to-buy list into a single rolling memory note, so JARVIS
    can answer 'what's on my shopping list' in a later conversation. One fixed key
    is overwritten each time; when the list empties, the note is removed — so
    memory never accumulates stale lists."""
    try:
        from datetime import date
        todo = [it["name"] for it in data.get("items", []) if not it.get("done")]
        if todo:
            from memory.memory_manager import remember
            names = ", ".join(todo[:15])
            remember("shopping_list",
                     f"Shopping list as of {date.today().isoformat()}: "
                     f"{len(todo)} item(s) to buy — {names}.", "notes")
        else:
            from memory.memory_manager import forget
            forget("shopping_list", "notes")
    except Exception:
        pass


# ── parsing / matching ────────────────────────────────────────────────────────

# Language-neutral: split only on punctuation separators, never on
# language-specific conjunction words (Gemini is asked to comma-separate).
_SPLIT = re.compile(r"\s*[,;/&\n]+\s*")


def _parse_items(text: str) -> list:
    if not text:
        return []
    parts = _SPLIT.split(text)
    seen, out = set(), []
    for p in parts:
        p = p.strip().strip(".").strip()
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p[:60])
    return out


def _find(data: dict, name: str) -> dict | None:
    name = name.lower().strip()
    # exact (case-insensitive) first, then substring both ways
    for it in data["items"]:
        if it["name"].lower() == name:
            return it
    for it in data["items"]:
        n = it["name"].lower()
        if name in n or n in name:
            return it
    return None


# ── formatting ─────────────────────────────────────────────────────────────────

def _fmt_line(it: dict) -> str:
    box = "☑" if it.get("done") else "☐"
    qty = f" ({it['qty']})" if it.get("qty") else ""
    return f"{box} {it['name']}{qty}"


def _panel(player, data: dict) -> None:
    if not player:
        return
    items = data["items"]
    if not items:
        body = "(empty)"
    else:
        body = "\n".join(_fmt_line(it) for it in items)
    remaining = sum(1 for it in items if not it.get("done"))
    try:
        player.show_content("🛒 SHOPPING LIST",
                            f"{body}\n\n{remaining} to buy · {len(items)} total")
    except Exception:
        pass


def _speak_list(data: dict) -> str:
    todo = [it for it in data["items"] if not it.get("done")]
    if not todo:
        if data["items"]:
            return "Everything on your shopping list is checked off."
        return "Your shopping list is empty."
    names = ", ".join((f"{it['name']} ({it['qty']})" if it.get("qty") else it["name"])
                      for it in todo)
    return f"You have {len(todo)} {'item' if len(todo) == 1 else 'items'} to buy: {names}."


# ── entry point ───────────────────────────────────────────────────────────────

def run(parameters: dict, player=None, session_memory=None) -> str:
    raw_items = (parameters.get("items") or "").strip()
    action = (parameters.get("action") or ("add" if raw_items else "list")).strip().lower()
    data = _load()

    # -------- ADD --------
    if action == "add":
        names = _parse_items(raw_items)
        if not names:
            return "What would you like me to add to the shopping list?"
        qty = (parameters.get("quantity") or "").strip()[:20]
        added, already = [], []
        for nm in names:
            if _find(data, nm):
                already.append(nm)
                continue
            data["items"].append({"name": nm, "qty": qty if len(names) == 1 else "", "done": False})
            added.append(nm)
        saved = _save(data)
        _panel(player, data)
        if not saved:
            return "The list changed in this session, but I could not save it to disk. Check storage permissions."
        if added and already:
            return (f"Added {', '.join(added)}. {', '.join(already)} "
                    f"{'was' if len(already) == 1 else 'were'} already on the list.")
        if added:
            return f"Added {', '.join(added)} to your shopping list. {len(data['items'])} items total."
        return f"{', '.join(already)} {'is' if len(already) == 1 else 'are'} already on your list."

    # -------- REMOVE --------
    if action in ("remove", "delete"):
        names = _parse_items(raw_items)
        if not names:
            return "Which item should I remove from the list?"
        removed, missing = [], []
        for nm in names:
            it = _find(data, nm)
            if it:
                data["items"].remove(it)
                removed.append(it["name"])
            else:
                missing.append(nm)
        saved = _save(data)
        _panel(player, data)
        if not saved:
            return "The list changed in this session, but I could not save it to disk. Check storage permissions."
        if removed and missing:
            return f"Removed {', '.join(removed)}. Couldn't find {', '.join(missing)}."
        if removed:
            return f"Removed {', '.join(removed)} from your shopping list."
        return f"I couldn't find {', '.join(missing)} on your list."

    # -------- CHECK (mark bought) --------
    if action in ("check", "done", "bought", "got"):
        names = _parse_items(raw_items)
        if not names:
            return "Which item did you get?"
        checked, missing = [], []
        for nm in names:
            it = _find(data, nm)
            if it:
                it["done"] = True
                checked.append(it["name"])
            else:
                missing.append(nm)
        saved = _save(data)
        _panel(player, data)
        if not saved:
            return "The list changed in this session, but I could not save it to disk. Check storage permissions."
        remaining = sum(1 for it in data["items"] if not it.get("done"))
        if checked:
            tail = (" Your list is complete!" if remaining == 0
                    else f" {remaining} left to buy.")
            miss = f" (couldn't find {', '.join(missing)})" if missing else ""
            return f"Checked off {', '.join(checked)}.{tail}{miss}"
        return f"I couldn't find {', '.join(missing)} on your list."

    # -------- CLEAR --------
    if action in ("clear", "empty", "reset"):
        which = (parameters.get("which") or "all").strip().lower()
        if which == "done":
            before = len(data["items"])
            data["items"] = [it for it in data["items"] if not it.get("done")]
            saved = _save(data)
            _panel(player, data)
            if not saved:
                return "The list changed in this session, but I could not save it to disk. Check storage permissions."
            return f"Cleared {before - len(data['items'])} bought items. {len(data['items'])} still on the list."
        n = len(data["items"])
        data["items"] = []
        saved = _save(data)
        _panel(player, data)
        if not saved:
            return "The list changed in this session, but I could not save it to disk. Check storage permissions."
        return "Your shopping list is now empty." if n else "Your shopping list was already empty."

    # -------- LIST (default) --------
    _panel(player, data)
    return _speak_list(data)

