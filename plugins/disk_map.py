"""
Disk Map — what is filling a drive or a folder, as a live treemap on the HUD.

    "what's taking up space on my computer?"  → the system drive is scanned; the
    "scan my D drive"                           map fills in while it is read, then
    "how big is my Downloads folder?"           every block can be opened
    "show me the Windows folder"              → the map zooms to that folder
    "go back" / "close it"                    → one folder up / the HUD goes back

THE MAP IS LIVE (_disk_map_core.py)
    Every block is a folder or a file; its area is its size and its colour the
    kind of files that fill it (video, images, programs…). Click a folder to
    zoom in, right-click or Backspace to come back, a part of the path at the
    top to jump there. Hover anything for its size and share. EXPLORER shows
    the selected item in Windows Explorer; RESCAN reads the drive again.
    Nothing is ever deleted or moved.

INSTALL
    Drop disk_map.py and _disk_map_core.py into plugins/ next to Live Panels'
    _live_panels_core.py, and restart. No key, no account, no new package: the
    scan uses only Python's standard library, the map PyQt6, which the assistant
    already has.

NOTHING HERE KNOWS A LANGUAGE
    Results are facts — names and sizes as they are on disk — and the assistant
    says them in the user's language.
"""
from __future__ import annotations

import types

PLUGIN = {
    # For the plugin manager (core/plugin_loader): never sent to a model.
    "level": "basic",
    "label": "Disk map",
    # Inline, not behind use_feature (core/tool_budget): behind it the model
    # saw one line and answered computer and disk questions with the plain
    # numbers tool — measured, 2 of 12 opened the panel; inline, 11 of 12.
    "tier": "core",
    "summary": "Shows what fills your disk or a folder, as a map you can click into. Deletes nothing.",
    "setup": [
        "Say: \"What's taking up space on my computer?\" or \"How big is my Downloads folder?\"",
        "Click a block to zoom in; right-click to go back.",
        "It only looks: nothing is ever deleted or moved.",
    ],
    "name": "disk_map",
    "brief": ("A live, clickable map of what fills a drive or folder (disk space, biggest "
              "files and folders). Read-only."),
    "description": (
        "Shows a LIVE, clickable disk-space map (treemap) on the HUD and returns what takes the "
        "most space. action='scan': scan a drive or folder — pass 'path' as a drive letter "
        "('C', 'D'), a known folder ('downloads', 'desktop', 'documents', 'pictures', 'videos', "
        "'music', 'home', 'appdata', 'temp', 'program files') or a full path; empty = the system "
        "drive. action='focus': zoom the open map to a folder or file by name ('folder'). "
        "action='up': one folder back. action='close': close the map. Use this for any question "
        "about disk space, storage, what fills the disk, the biggest files or folders, or how big "
        "a folder is. It never deletes anything: suggest what could be cleaned, the user decides."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "'scan', 'focus', 'up' or 'close'",
            },
            "path": {
                "type": "STRING",
                "description": "For 'scan': drive letter, known folder name or full path; empty = system drive",
            },
            "folder": {
                "type": "STRING",
                "description": "For 'focus': the folder or file name to show, e.g. 'Windows', 'node_modules'",
            },
            "fresh": {
                "type": "BOOLEAN",
                "description": "For 'scan': true to scan again even if a recent scan exists",
            },
        },
        "required": ["action"],
    },
}

_WAIT_S = 12          # answer with the facts if the scan is this quick, report later otherwise


def _log(player, text: str) -> None:
    # The log file only: the activity panel shows what the assistant says.
    print(f"[DiskMap] {text}")


_NEEDS_LIVE_PANELS = (
    "Disk Map needs the Live Panels plugin (its _live_panels_core.py), which is not in the "
    "plugins folder. Put it there and restart the assistant."
)


def _has_live_panels() -> bool:
    """The map is drawn in Live Panels' frame; checked by file so the user hears what to
    install instead of an import error."""
    # By module, not by file: inside a build the plugins are compiled into the
    # program and no .py sits beside it (core/bundle).
    import importlib.util
    try:
        return importlib.util.find_spec("plugins._live_panels_core") is not None
    except (ImportError, ValueError):
        return False


def _load():
    try:
        from plugins import _disk_map_core as dm
        from plugins import _live_panels_core as lb
    except ImportError:  # loaded outside the package
        import _disk_map_core as dm  # type: ignore
        import _live_panels_core as lb  # type: ignore
    return lb, dm


def _hook_close(player, lb) -> None:
    """Make "close it" (the UI's stop_video) close the panels too — once, on this UI.
    The same flag as Live Panels', so the two plugins never wrap it twice."""
    if player is None or getattr(player, "_live_panels_hooked", False):
        return
    original = getattr(player, "stop_video", None)
    if not callable(original):
        return

    def stop_video(self, *a, **kw):
        try:
            lb.close_all()
        except Exception:
            pass
        return original(*a, **kw)

    try:
        player.stop_video = types.MethodType(stop_video, player)
        player._live_panels_hooked = True
    except Exception:
        pass


def _scan(player, dm, target: str, fresh: bool) -> str:
    root = dm.resolve(target)
    if not root:
        return (f"status=failed reason=no drive or folder called '{target}' was found. "
                "Ask the user which drive or folder they mean.")
    sc = dm.get_scan(root, fresh=fresh)
    shown = bool(player) and dm.show(player, sc)
    _log(player, f"Disk map: scanning {root}" if sc.state == "running" else f"Disk map: {root}")
    lead = ("A live disk map is now on the HUD (the user can click folders to zoom in). "
            if shown else "The map could not be shown. ")
    if sc.done.wait(_WAIT_S):
        if sc.state != "done":
            return f"status=failed reason=the scan of {root} stopped: {sc.error}"
        return (lead + dm.describe(sc) + " Answer briefly in the user's language: what takes the "
                "most space and, if useful, where space could be freed. Never offer to delete "
                "files yourself.")

    def report(done_sc) -> None:
        if done_sc.state != "done" or not dm.visible():
            return                              # closed meanwhile: the result waits on the map
        say = getattr(player, "request_say", None)
        if callable(say):
            say("[Disk Map] The disk scan has finished. " + dm.describe(done_sc)
                + " Tell the user briefly, in their language, what takes the most space and "
                  "where space could be freed. Do not call any tool for this.")

    if player is not None:
        sc.when_done(report)
    with sc.lock:
        files = sc.prog.get("files", 0)
    return (lead + f"status=running Scanning {root} — {files:,} files read so far; the map fills "
            "in live and the results will be reported when it finishes. Tell the user briefly "
            "that the scan is underway.")


def _focus(dm, folder: str) -> str:
    if not folder:
        return "status=ask question=which folder the user wants to see"
    if not dm.visible():
        return "status=failed reason=no disk map is open; scan first (action='scan')"
    found = dm.focus(folder)
    if not found:
        return f"status=failed reason=no folder or file called '{folder}' is in the scanned part"
    node, path = found
    b = dm.board()
    sc = b.scan if b else None
    if node[dm.KIND] == 0 and node[dm.KIDS] is None:
        return (f"status=done Showing {path} ({dm.fmt_size(node[dm.SIZE])}); its contents are "
                "being scanned now and appear on the map in a moment.")
    if node[dm.KIND] == 0 and sc is not None:
        return ("status=done " + dm.describe(sc, node, path)
                + " Answer briefly in the user's language.")
    return f"status=done Selected the file {path}: {dm.fmt_size(node[dm.SIZE])}."


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = str(parameters.get("action") or "scan").strip().lower()
    path = str(parameters.get("path") or "").strip()
    folder = str(parameters.get("folder") or "").strip()
    fresh = str(parameters.get("fresh") or "").strip().lower() in ("true", "1", "yes")
    if not _has_live_panels():
        _log(player, _NEEDS_LIVE_PANELS)
        return f"status=failed reason={_NEEDS_LIVE_PANELS} Tell the user exactly this."
    try:
        lb, dm = _load()
    except ImportError as e:
        return f"status=failed reason={_NEEDS_LIVE_PANELS} ({e})"
    try:
        _hook_close(player, lb)
        if action in ("close", "hide", "stop"):
            lb.close_all()
            return "status=done closed=disk map"
        if action in ("up", "back"):
            dm.up()
            return "status=done went one folder up"
        if action in ("focus", "open", "show", "zoom"):
            if folder or not path:
                return _focus(dm, folder or path)
        return _scan(player, dm, path, fresh)
    except Exception as e:
        return f"status=failed reason=disk_map: {type(e).__name__}: {e}"
