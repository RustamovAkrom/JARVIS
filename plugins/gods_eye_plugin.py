"""J.A.R.V.I.S. GOD'S EYE plugin for the Mark-LIII/Mark-54 plugin loader.
Keep this file beside gods_eye_view.html in the JARVIS plugins directory.
"""
from __future__ import annotations

from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import threading
import webbrowser
import os
import subprocess
import sys

# Match the PLUGIN schema used by other registered JARVIS plugins.
PLUGIN = {
    "name": "gods_eye_view",
    "description": (
        "Opens the GOD'S EYE Earth observation HUD with daytime Earth, "
        "location map, public camera discovery, and a selectable YouTube results list containing only streams explicitly marked currently live. "
        "Actions: open, gods_eye, search, status, close. Say “search <topic> on Gods Eye” to run a query inside the HUD."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["open", "gods_eye", "search", "status", "close"],
                "description": "Open God's Eye, check its local server, or close it."
            },
            "query": {
                "type": "STRING",
                "description": "Search query or location to run inside GOD'S EYE, e.g. Tokyo live cameras, Eiffel Tower, weather in London, or a topic to search."
            }
        },
        "required": ["action"]
    }
}

_ROOT = Path(__file__).resolve().parent
_HTML = _ROOT / "gods_eye_view.html"
_SERVER = None
_SERVER_THREAD = None
_SERVER_LOCK = threading.Lock()


def _search_youtube(query: str):
    """No-key YouTube search endpoint used by the HUD. yt-dlp installs lazily."""
    try:
        import yt_dlp
    except ImportError:
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet", "-U", "yt-dlp"],
                check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            import yt_dlp
        except Exception as exc:
            return {"ok": False, "error": "yt-dlp unavailable: " + str(exc)[:180], "results": []}
    query = (query or "public live webcam").strip()[:180]
    opts = {"quiet": True, "no_warnings": True, "skip_download": True,
            "extract_flat": True, "ignoreerrors": True, "socket_timeout": 15,
            "playlistend": 20}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info("ytsearch20:" + query, download=False)
        results = []
        for item in (info or {}).get("entries") or []:
            if not item or not item.get("id"):
                continue
            title = item.get("title") or "YouTube video"
            status = item.get("live_status") or "unknown"
            # Strict allow-list: only items explicitly marked as currently live.
            # Title text containing "live" is not proof of a live broadcast.
            is_live = bool(item.get("is_live")) or status == "is_live"
            if not is_live:
                continue
            vid = str(item["id"])
            results.append({"id": vid, "title": title,
                            "uploader": item.get("uploader") or item.get("channel") or "",
                            "live_status": "is_live", "is_live": True,
                            "thumbnail": item.get("thumbnail") or "",
                            "url": "https://www.youtube.com/watch?v=" + vid})
        return {"ok": True, "results": results[:20]}
    except Exception as exc:
        return {"ok": False, "error": "YouTube search failed: " + str(exc)[:220], "results": []}


class _QuietHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(_ROOT), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/youtube-search":
            query = parse_qs(parsed.query).get("q", ["public live webcam"])[0]
            body = json.dumps(_search_youtube(query), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()

    def log_message(self, *_args):
        pass


def _start_server():
    global _SERVER, _SERVER_THREAD
    with _SERVER_LOCK:
        if _SERVER is not None:
            return _SERVER
        _SERVER = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
        _SERVER.daemon_threads = True
        _SERVER_THREAD = threading.Thread(target=_SERVER.serve_forever, daemon=True,
                                          name="GodsEyeLocalServer")
        _SERVER_THREAD.start()
        return _SERVER


def _launch_hud(url: str) -> str:
    """Prefer a clean app-style window over opening a Chrome tab."""
    # Edge app mode opens a standalone window without Chrome tabs/address bar.
    candidates = [
        os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
    ]
    for exe in candidates:
        if exe and os.path.isfile(exe):
            subprocess.Popen([exe, "--app=" + url, "--new-window"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             close_fds=True)
            return "edge-app"
    # If Edge is unavailable, try a native WebView window when pywebview exists.
    try:
        import webview
        threading.Thread(target=lambda: webview.create_window("J.A.R.V.I.S. // GOD'S EYE", url, width=1440, height=900),
                         daemon=True, name="GodsEyeWebViewWindow").start()
        # pywebview requires webview.start() to run its GUI loop.
        threading.Thread(target=lambda: webview.start(), daemon=True,
                         name="GodsEyeWebViewLoop").start()
        return "webview"
    except Exception:
        webbrowser.open(url)
        return "browser-fallback"


def run(parameters=None, player=None, session_memory=None) -> str:
    parameters = parameters or {}
    action = str(parameters.get("action", "open") or "open").strip().lower()
    query = str(parameters.get("query", "") or "").strip()
    if action == "status":
        return "GOD'S EYE plugin is registered. HTML file: " + ("found" if _HTML.is_file() else "MISSING")
    if action == "close":
        global _SERVER
        if _SERVER is not None:
            try:
                _SERVER.shutdown()
                _SERVER.server_close()
            except Exception:
                pass
            _SERVER = None
            return "GOD'S EYE local server closed."
        return "GOD'S EYE server is not running."
    if action not in ("open", "gods_eye", "search"):
        return "Unknown action. Use open, gods_eye, search, status, or close."
    if not _HTML.is_file():
        return "GOD'S EYE could not start: gods_eye_view.html must be beside gods_eye_plugin.py in the plugins folder."
    try:
        server = _start_server()
        url = f"http://127.0.0.1:{server.server_port}/gods_eye_view.html?v=jarvis-search-20261021"
        if query:
            fragment_key = "search" if action == "search" else "location"
            url += "#" + fragment_key + "=" + __import__("urllib.parse", fromlist=["quote"]).quote(query)
        launch_mode = _launch_hud(url)
        if launch_mode == "edge-app":
            return ("GOD'S EYE opened and is searching inside the HUD for: " + query) if action == "search" and query else "GOD'S EYE opened in a standalone Microsoft Edge app window (not a Chrome tab)."
        if launch_mode == "webview":
            return ("GOD'S EYE opened and is searching inside the HUD for: " + query) if action == "search" and query else "GOD'S EYE is opening in a native WebView window."
        return ("GOD'S EYE opened and is searching inside the HUD for: " + query) if action == "search" and query else "GOD'S EYE opened in your default browser because an app-style browser window was unavailable."
    except Exception as exc:
        return "GOD'S EYE launch failed: " + str(exc)[:220]


if __name__ == "__main__":
    print(run({"action": "open"}))
