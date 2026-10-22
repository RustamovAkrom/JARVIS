"""
obsidian_vault.py — read, search and write your Obsidian vault by voice.

WHY THIS IS A PLUGIN AND NOT A BUNDLED ACTION
    Mark LV drew a line through actions/: everything in there drives the
    COMPUTER, and a skill that serves one person's tool belongs in plugins/.
    Not everyone keeps an Obsidian vault; everyone opens applications.

WHY IT NEEDS NO API, NO DAEMON, NO AUTH
    An Obsidian vault is a folder of Markdown files. There is nothing to log
    into and nothing to keep running — which makes this the exact shape the
    plugin contract wants: a request comes in, files are read or written, a
    sentence goes back. No new dependency; the standard library covers it.

WHAT IT DOES
    search     find notes by content and title, with the matching line quoted
    read       read one note back (panel gets the note, JARVIS gets the gist)
    list       recently modified notes, optionally inside one folder
    create     make a new note — refuses to overwrite an existing one
    append     add to the end of a note, creating it if it isn't there
    daily      append to today's daily note, made from your date format
    tasks      every unchecked "- [ ]" in the vault, newest file first
    backlinks  which notes link to this one, via [[wikilinks]]
    open       open a note in the Obsidian app itself

SAFETY
    1. Vault confinement.  Every path is resolved and checked with
                           is_relative_to(vault) before anything is read or
                           written, the same guard file_controller uses. A note
                           name of "../../../Windows/System32/drivers/etc/hosts"
                           resolves outside the vault and is refused.
    2. Markdown only.      Only *.md is ever read or written. .obsidian/,
                           .trash/ and .git/ are never walked — your workspace
                           config and plugin settings are not searchable content
                           and must never be edited by voice.
    3. It cannot delete.   There is deliberately no delete action. Notes are
                           written by appending or by creating something new;
                           create REFUSES to overwrite. Deleting a file is
                           file_controller's job, where it goes to the trash and
                           can be undone — not something a misheard note title
                           should be able to reach.
    4. Undo (optional).    create, append and daily register with the shared
                           undo stack when this Mark has one, so "undo"
                           reverses them like any other file change: an append
                           restores the exact previous text, a created note is
                           removed again. core/undo.py arrived with the LII-LV
                           foundation update; on an older Mark this file still
                           loads and works, and says once that it cannot take a
                           write back rather than being rejected at startup.
    5. Budgeted walks.     Search and tasks stop at a file cap AND a time
                           budget, so a 20,000-note vault cannot hang a turn.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime
from difflib import get_close_matches
from pathlib import Path

# A plugin is shared as ONE file and dropped into whatever Mark the person is
# running. core/undo.py arrived with the LII–LV foundation update, so on an
# older Mark it simply is not there — and an import error would get this file
# rejected outright, over a feature that only makes writing nicer. Undo is
# therefore optional: present, notes are reversible; absent, everything else
# still works and the plugin says so rather than refusing to load.
try:
    from core.undo import push_undo

    _HAS_UNDO = True
except ImportError:
    _HAS_UNDO = False

    def push_undo(label, undo_fn):  # type: ignore[misc]
        """No undo stack in this Mark — recording a reversal would be a lie."""
        return


# The per-plugin settings store. Unlike undo this one IS load-bearing: without
# it there is nowhere to keep the vault path. Still not worth a rejection — the
# plugin loads and explains itself instead of telling somebody to install a
# package that does not exist.
try:
    from memory.config_manager import get_plugin_config

    _HAS_CONFIG = True
except ImportError:
    _HAS_CONFIG = False

    def get_plugin_config(namespace):  # type: ignore[misc]
        return {}


_NS = "obsidian_vault"

_SKIP_DIRS = {".obsidian", ".trash", ".git", ".stfolder", "node_modules", "__pycache__"}
_MAX_FILES = 6000  # files walked before a scan gives up
_TIME_BUDGET = 3.0  # seconds a scan may spend walking
_MAX_READ = 400_000  # refuse to read a "note" bigger than this
_MODEL_CHARS = 4000  # how much of a note is handed to the model
_SNIPPET = 160
_MAX_HITS = 8
_MAX_TASKS = 40
_MAX_LIST = 20

_DATE_FORMATS = {
    "YYYY-MM-DD": "%Y-%m-%d",
    "DD-MM-YYYY": "%d-%m-%Y",
    "YYYY-MM-DD dddd": "%Y-%m-%d %A",
    "YYYY/MM/YYYY-MM-DD": "%Y/%m/%Y-%m-%d",
}


# ── vault resolution ─────────────────────────────────────────────────────────


def _cfg() -> dict:
    return get_plugin_config(_NS)


def _vault() -> Path | None:
    raw = str(_cfg().get("vault_path") or "").strip().strip('"')
    if not raw:
        return None
    try:
        p = Path(os.path.expandvars(os.path.expanduser(raw))).resolve()
    except Exception:
        return None
    return p if p.is_dir() else None


def _detect_vault() -> Path | None:
    """Best-effort: find a folder containing .obsidian near the user's home.

    Only ever called from the settings button, never on a command path — a
    filesystem hunt is not something a spoken request should pay for.
    """
    home = Path.home()
    roots = [
        home,
        home / "Documents",
        home / "Desktop",
        home / "OneDrive",
        home / "OneDrive" / "Documents",
        home / "Dropbox",
    ]
    seen: set[Path] = set()
    for root in roots:
        try:
            if not root.is_dir() or root in seen:
                continue
            seen.add(root)
            for depth1 in root.iterdir():
                if not depth1.is_dir() or depth1.name.startswith("."):
                    continue
                if (depth1 / ".obsidian").is_dir():
                    return depth1.resolve()
                for depth2 in depth1.iterdir():
                    if depth2.is_dir() and (depth2 / ".obsidian").is_dir():
                        return depth2.resolve()
        except PermissionError, OSError:
            continue
    return None


def _inside(vault: Path, target: Path) -> bool:
    """The one guard that matters: nothing is touched outside the vault."""
    try:
        resolved = target.resolve()
    except Exception:
        return False
    try:
        return resolved == vault or resolved.is_relative_to(vault)
    except AttributeError:  # pragma: no cover (py<3.9)
        return str(resolved).startswith(str(vault) + os.sep)


def _walk(vault: Path):
    """Every .md in the vault, skipping the folders that are not notes.

    Bounded by both a file cap and a wall-clock budget: a vault is somebody's
    life's notes and can be very large, but a spoken turn cannot wait on it.
    """
    started = time.monotonic()
    count = 0
    for root, dirs, files in os.walk(vault):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if not name.lower().endswith(".md"):
                continue
            count += 1
            if count > _MAX_FILES or time.monotonic() - started > _TIME_BUDGET:
                return
            yield Path(root) / name


def _read_text(path: Path) -> str:
    try:
        if path.stat().st_size > _MAX_READ:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _mtime(path: Path) -> float:
    """A note can be deleted by Obsidian between the walk and the sort — a
    vanished file must not take down the whole answer."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _rel(vault: Path, path: Path) -> str:
    try:
        return str(path.relative_to(vault)).replace(os.sep, "/")
    except Exception:
        return path.name


# ── note lookup ──────────────────────────────────────────────────────────────


def _note_path(vault: Path, name: str, must_exist: bool) -> tuple[Path | None, str]:
    """Turn a spoken note name into a real path inside the vault.

    Returns (path, error). Accepts "Meeting notes", "Projects/Mark LV",
    "meeting notes.md" — and refuses anything that resolves outside the vault.
    """
    raw = (name or "").strip().strip('"').replace("\\", "/")
    raw = re.sub(r"^\[\[|\]\]$", "", raw).strip()
    if not raw:
        return None, "Which note?"
    if raw.lower().endswith(".md"):
        raw = raw[:-3]
    # Windows-illegal and path-escaping characters have no place in a note name.
    if any(ch in raw for ch in '<>:"|?*') or ".." in raw.split("/"):
        return None, f"'{name}' isn't a usable note name."

    direct = vault / f"{raw}.md"
    if not _inside(vault, direct):
        return None, "That note name points outside the vault, so I've left it alone."
    if direct.exists():
        return direct.resolve(), ""
    if not must_exist:
        return direct, ""

    # Not an exact path — search the vault by stem, then by closeness.
    stem = raw.split("/")[-1].lower()
    everything = list(_walk(vault))
    for p in everything:
        if p.stem.lower() == stem:
            return p.resolve(), ""
    partial = [p for p in everything if stem in p.stem.lower()]
    if len(partial) == 1:
        return partial[0].resolve(), ""
    if partial:
        names = ", ".join(sorted(p.stem for p in partial)[:6])
        return None, f"Several notes match '{raw}': {names}. Which one?"
    close = get_close_matches(
        stem, [p.stem.lower() for p in everything], n=3, cutoff=0.7
    )
    if close:
        pretty = ", ".join(sorted({c for c in close}))
        return None, f"There's no note called '{raw}'. Did you mean: {pretty}?"
    return None, f"There's no note called '{raw}' in the vault."


def _panel(player, title: str, body: str) -> None:
    if not player:
        return
    try:
        player.show_content(title, body)
    except Exception:
        pass


def _log(player, text: str) -> None:
    if not player:
        return
    try:
        player.write_log(text)
    except Exception:
        pass


# ── settings ─────────────────────────────────────────────────────────────────


def _check_vault(values: dict) -> tuple[bool, str]:
    raw = str(values.get("vault_path") or "").strip().strip('"')
    if not raw:
        found = _detect_vault()
        if found:
            return False, f"No path set. Found a vault at: {found}"
        return False, "No vault path set, and I couldn't find one automatically."
    try:
        p = Path(os.path.expandvars(os.path.expanduser(raw))).resolve()
    except Exception as e:
        return False, f"That path can't be read: {e}"
    if not p.is_dir():
        return False, "That folder doesn't exist."
    notes = sum(1 for _ in _walk(p))
    marker = (
        ""
        if (p / ".obsidian").is_dir()
        else " (no .obsidian folder — is this the vault root?)"
    )
    if not notes:
        return False, f"No .md notes found in {p.name}{marker}"
    return True, f"{p.name}: {notes} note(s) found{marker}"


PLUGIN_SETTINGS = {
    "namespace": _NS,
    "title": "🗒️  OBSIDIAN VAULT",
    "fields": [
        {
            "key": "vault_path",
            "label": "Vault Folder",
            "type": "text",
            "placeholder": r"e.g. C:\Users\you\Documents\MyVault — press CHECK to find it",
        },
        {
            "key": "daily_folder",
            "label": "Daily Notes Folder (blank = vault root)",
            "type": "text",
            "placeholder": "e.g. Daily or Journal/2026",
        },
        {
            "key": "daily_format",
            "label": "Daily Note Filename",
            "type": "choice",
            "options": list(_DATE_FORMATS.keys()),
            "default": "YYYY-MM-DD",
        },
        {
            "key": "new_note_folder",
            "label": "New Notes Folder (blank = vault root)",
            "type": "text",
            "placeholder": "e.g. Inbox",
        },
    ],
    "action": {
        "label": "CHECK VAULT",
        "run": lambda values: _check_vault(values or {}),
    },
}

PLUGIN = {
    "name": "obsidian_vault",
    "description": (
        "Reads, searches and writes notes in the user's Obsidian vault. Use this "
        "whenever the user says note, notes, vault, Obsidian, daily note, journal, "
        "or asks what they wrote about something — 'what did I write about the "
        "avatar', 'add this to my notes', 'put it in today's note', 'what's left "
        "on my task list', 'which notes link to Mark LV'. "
        "Do NOT use this for ordinary files, documents, PDFs or anything outside "
        "the vault — reading or summarising a file on disk is file_processor, and "
        "moving, renaming or deleting a file is file_controller. This tool only "
        "ever touches Markdown inside the configured Obsidian vault, and it "
        "cannot delete anything."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "search, read, list, create, append, daily, tasks, "
                    "backlinks or open. Defaults to search when a query "
                    "is given, otherwise list."
                ),
            },
            "note": {
                "type": "STRING",
                "description": (
                    "Note title or vault-relative path, for read, create, "
                    "append, backlinks and open. E.g. 'Mark LV' or "
                    "'Projects/Mark LV'."
                ),
            },
            "content": {
                "type": "STRING",
                "description": "Markdown text to write, for create, append and daily.",
            },
            "query": {
                "type": "STRING",
                "description": "What to look for, for search.",
            },
            "folder": {
                "type": "STRING",
                "description": "Vault-relative folder to limit list to. Optional.",
            },
        },
        "required": [],
    },
}


# ── undo helpers ─────────────────────────────────────────────────────────────

_undo_warned = [False]


def _undo_caveat() -> str:
    """Say once, on the first write, that this Mark cannot take it back.

    Silence would be worse than the missing feature: somebody who has read that
    notes are reversible would find out they are not at the moment they need it.
    """
    if _HAS_UNDO or _undo_warned[0]:
        return ""
    _undo_warned[0] = True
    return " (This Mark has no undo stack, so I can't take that back — update to Mark LII or newer for that.)"


def _undo_created(target: Path):
    def _fn():
        if not target.exists():
            return f"'{target.stem}' is already gone."
        target.unlink()
        return f"Removed the note '{target.stem}'."

    return _fn


def _undo_restore(target: Path, previous: str):
    def _fn():
        if not target.exists():
            return f"'{target.stem}' is no longer there — nothing to restore."
        target.write_text(previous, encoding="utf-8")
        return f"Restored '{target.stem}' to what it said before."

    return _fn


# ── actions ──────────────────────────────────────────────────────────────────


def _do_search(vault: Path, query: str, player) -> str:
    q = (query or "").strip()
    if not q:
        return "What should I look for in the vault?"
    needle = q.lower()
    words = [w for w in re.split(r"\W+", needle) if len(w) > 2] or [needle]

    hits = []
    for path in _walk(vault):
        stem_l = path.stem.lower()
        text = _read_text(path)
        low = text.lower()
        score = 0
        if needle in stem_l:
            score += 50
        score += sum(8 for w in words if w in stem_l)
        score += sum(min(low.count(w), 10) for w in words)
        if not score:
            continue
        line = ""
        for w in words:
            idx = low.find(w)
            if idx >= 0:
                start = max(0, idx - _SNIPPET // 2)
                line = " ".join(text[start : start + _SNIPPET].split())
                break
        hits.append((score, path, line))

    if not hits:
        return f"Nothing in the vault mentions '{q}'."

    hits.sort(key=lambda h: (-h[0], str(h[1])))
    top = hits[:_MAX_HITS]

    lines, spoken = [], []
    for _, path, line in top:
        rel = _rel(vault, path)
        lines.append(f"📄 {rel}\n   {line}" if line else f"📄 {rel}")
        spoken.append(f"{rel}: {line}" if line else rel)
    _panel(player, f"🗒 VAULT — {q[:28]}", "\n\n".join(lines))

    more = f" ({len(hits)} in total)" if len(hits) > len(top) else ""
    return f"{len(top)} note(s) mention '{q}'{more}:\n" + "\n".join(spoken)


def _do_read(vault: Path, note: str, player) -> str:
    path, err = _note_path(vault, note, must_exist=True)
    if err:
        return err
    try:
        if path.stat().st_size > _MAX_READ:
            return (
                f"'{path.stem}' is too large to read out — "
                f"{path.stat().st_size // 1024} KB. Open it in Obsidian instead."
            )
    except OSError:
        pass
    text = _read_text(path)
    if not text.strip():
        return f"'{path.stem}' is empty."
    _panel(player, f"🗒 {path.stem[:40]}", text)
    body = text[:_MODEL_CHARS]
    cut = (
        "\n\n[…the note continues; this is the beginning.]"
        if len(text) > _MODEL_CHARS
        else ""
    )
    return f"'{_rel(vault, path)}':\n\n{body}{cut}"


def _do_list(vault: Path, folder: str, player) -> str:
    root = vault
    if (folder or "").strip():
        cand = vault / folder.strip().strip("/").replace("\\", "/")
        if not _inside(vault, cand):
            return "That folder is outside the vault, so I've left it alone."
        if not cand.is_dir():
            return f"There's no folder called '{folder}' in the vault."
        root = cand.resolve()

    notes = sorted(_walk(root), key=_mtime, reverse=True)[:_MAX_LIST]
    if not notes:
        return "There are no notes there."
    lines = [
        f"📄 {_rel(vault, p)}  ·  {datetime.fromtimestamp(_mtime(p)):%d %b %H:%M}"
        for p in notes
    ]
    _panel(player, "🗒 RECENT NOTES", "\n".join(lines))
    return (
        "Most recently edited: "
        + ", ".join(p.stem for p in notes[:8])
        + (f" — {len(notes)} shown on the panel." if len(notes) > 8 else ".")
    )


def _do_create(vault: Path, note: str, content: str, player) -> str:
    name = (note or "").strip()
    if not name:
        return "What should the note be called?"
    folder = str(_cfg().get("new_note_folder") or "").strip().strip("/")
    if folder and "/" not in name.replace("\\", "/"):
        name = f"{folder}/{name}"

    path, err = _note_path(vault, name, must_exist=False)
    if err:
        return err
    if path.exists():
        return (
            f"'{path.stem}' already exists — I haven't touched it. "
            f"Say 'add it to {path.stem}' if you want it appended instead."
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = (content or "").strip()
        path.write_text(
            f"# {path.stem}\n\n{body}\n" if body else f"# {path.stem}\n",
            encoding="utf-8",
        )
    except Exception as e:
        return f"Sir, I couldn't create that note: {e}"

    push_undo(f"created note {path.stem}", _undo_created(path))
    _log(player, f"JARVIS: New note — {_rel(vault, path)}")
    return f"Created '{_rel(vault, path)}' in the vault." + _undo_caveat()


def _append_to(vault: Path, path: Path, content: str, player, what: str) -> str:
    body = (content or "").strip()
    if not body:
        return "What should I write in it?"
    existed = path.exists()
    previous = _read_text(path) if existed else ""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if existed:
            sep = (
                ""
                if previous.endswith("\n\n")
                else ("\n" if previous.endswith("\n") else "\n\n")
            )
            path.write_text(previous + sep + body + "\n", encoding="utf-8")
        else:
            path.write_text(f"# {path.stem}\n\n{body}\n", encoding="utf-8")
    except Exception as e:
        return f"Sir, I couldn't write to that note: {e}"

    if existed:
        push_undo(f"added to {path.stem}", _undo_restore(path, previous))
    else:
        push_undo(f"created note {path.stem}", _undo_created(path))
    _log(
        player, f"JARVIS: {'Appended to' if existed else 'Created'} {_rel(vault, path)}"
    )
    return (
        f"Added it to {what}."
        if existed
        else f"'{_rel(vault, path)}' didn't exist, so I made it and wrote that in."
    ) + _undo_caveat()


def _do_append(vault: Path, note: str, content: str, player) -> str:
    if not (note or "").strip():
        return "Which note should I add that to?"
    path, err = _note_path(vault, note, must_exist=True)
    if err and "no note called" not in err.lower():
        return err
    if err:  # not there — create it where new notes go
        return _do_create(vault, note, content, player)
    return _append_to(vault, path, content, player, f"'{path.stem}'")


def _do_daily(vault: Path, content: str, player) -> str:
    cfg = _cfg()
    fmt = _DATE_FORMATS.get(str(cfg.get("daily_format") or "YYYY-MM-DD"), "%Y-%m-%d")
    folder = str(cfg.get("daily_folder") or "").strip().strip("/").replace("\\", "/")
    stamp = datetime.now().strftime(fmt)
    rel = f"{folder}/{stamp}" if folder else stamp

    path, err = _note_path(vault, rel, must_exist=False)
    if err:
        return err
    stamped = f"- {datetime.now():%H:%M} — {(content or '').strip()}"
    return _append_to(vault, path, stamped, player, "today's note")


def _do_tasks(vault: Path, player) -> str:
    found = []
    for path in sorted(_walk(vault), key=_mtime, reverse=True):
        for line in _read_text(path).splitlines():
            s = line.strip()
            if s.startswith(("- [ ]", "* [ ]", "+ [ ]")):
                found.append((path, s[5:].strip()))
                if len(found) >= _MAX_TASKS:
                    break
        if len(found) >= _MAX_TASKS:
            break
    if not found:
        return "There's nothing unchecked in the vault — every box is ticked."
    _panel(
        player,
        "🗒 OPEN TASKS",
        "\n".join(f"☐ {t}\n   {_rel(vault, p)}" for p, t in found),
    )
    spoken = "; ".join(t for _, t in found[:8])
    more = f" and {len(found) - 8} more on the panel" if len(found) > 8 else ""
    return f"{len(found)} open task(s): {spoken}{more}."


def _do_backlinks(vault: Path, note: str, player) -> str:
    path, err = _note_path(vault, note, must_exist=True)
    if err:
        return err
    stem = path.stem.lower()
    pattern = re.compile(r"\[\[([^\]\|#]+)", re.IGNORECASE)
    linking = []
    for other in _walk(vault):
        if other.resolve() == path:
            continue
        for target in pattern.findall(_read_text(other)):
            if target.strip().split("/")[-1].lower() == stem:
                linking.append(other)
                break
    if not linking:
        return f"No note links to '{path.stem}'."
    _panel(
        player,
        f"🗒 LINKS TO {path.stem[:30]}",
        "\n".join(f"📄 {_rel(vault, p)}" for p in linking),
    )
    return (
        f"{len(linking)} note(s) link to '{path.stem}': "
        + ", ".join(p.stem for p in linking[:8])
        + "."
    )


def _do_open(vault: Path, note: str, player) -> str:
    path, err = _note_path(vault, note, must_exist=True)
    if err:
        return err
    uri = (
        "obsidian://open?vault="
        + urllib.parse.quote(vault.name)
        + "&file="
        + urllib.parse.quote(_rel(vault, path)[:-3])
    )
    try:
        if sys.platform.startswith("win"):
            os.startfile(uri)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.run(["open", uri], check=True, timeout=10)
        else:
            subprocess.Popen(
                ["xdg-open", uri], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
    except Exception as e:
        return f"Sir, I couldn't open Obsidian: {e}"
    return f"Opening '{path.stem}' in Obsidian."


# ── entry point ──────────────────────────────────────────────────────────────

_ALIASES = {
    "find": "search",
    "look": "search",
    "query": "search",
    "get": "read",
    "show": "read",
    "note": "read",
    "add": "append",
    "write": "append",
    "add_to": "append",
    "new": "create",
    "make": "create",
    "today": "daily",
    "journal": "daily",
    "daily_note": "daily",
    "todo": "tasks",
    "todos": "tasks",
    "task": "tasks",
    "links": "backlinks",
    "linked": "backlinks",
    "ls": "list",
    "recent": "list",
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    if not _HAS_CONFIG:
        return (
            "This Mark is too old for the vault plugin — it has no plugin "
            "settings store, so there's nowhere to keep the vault path. "
            "Update to Mark LII or newer and it will work as it is."
        )

    vault = _vault()
    if vault is None:
        return (
            "No Obsidian vault is set up yet. Open the settings, put the vault "
            "folder into OBSIDIAN VAULT and press CHECK VAULT — it can find it "
            "for you if you leave the box empty."
        )

    note = str(parameters.get("note") or "").strip()
    content = str(parameters.get("content") or "")
    query = str(parameters.get("query") or "").strip()
    folder = str(parameters.get("folder") or "").strip()

    action = str(parameters.get("action") or "").strip().lower().replace(" ", "_")
    action = _ALIASES.get(action, action)
    if not action:
        action = "search" if query else ("read" if note else "list")

    try:
        if action == "search":
            return _do_search(vault, query or note, player)
        if action == "read":
            return _do_read(vault, note or query, player)
        if action == "list":
            return _do_list(vault, folder, player)
        if action == "create":
            return _do_create(vault, note, content, player)
        if action == "append":
            return _do_append(vault, note, content, player)
        if action == "daily":
            return _do_daily(vault, content or query, player)
        if action == "tasks":
            return _do_tasks(vault, player)
        if action == "backlinks":
            return _do_backlinks(vault, note, player)
        if action == "open":
            return _do_open(vault, note, player)
    except Exception as e:
        return f"Sir, the vault operation failed: {e}"

    known = "search, read, list, create, append, daily, tasks, backlinks, open"
    close = get_close_matches(action, known.split(", "), n=1, cutoff=0.6)
    if close:
        return f"I don't have '{action}' for the vault — did you mean {close[0]}?"
    return f"I can't '{action}' in the vault. I can: {known}."
