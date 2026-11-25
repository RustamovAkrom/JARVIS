"""
JARVIS plugin — 1-Click Video & Reel Downloader (media_downloader.py).

Downloads high-quality video (MP4) from YouTube, Instagram Reels, TikTok, Twitter/X,
Facebook, Reddit, and other supported video sites into the user's Videos folder.
If no URL is given in the command, it automatically detects any video URL copied
to the Windows clipboard.
"""
from __future__ import annotations

import ctypes
import os
import re
import threading
from pathlib import Path
from typing import Any, Optional

try:
    from core.user_dirs import folder as _user_folder
    _VIDEOS_DIR = Path(_user_folder("Videos") or (Path.home() / "Videos"))
except Exception:
    _VIDEOS_DIR = Path.home() / "Videos"

_VIDEOS_DIR.mkdir(parents=True, exist_ok=True)


def _get_clipboard_url() -> Optional[str]:
    """Read URL from Windows clipboard using pure user32 ctypes."""
    CF_UNICODETEXT = 13
    u32 = ctypes.windll.user32
    k32 = ctypes.windll.kernel32
    if not u32.OpenClipboard(None):
        return None
    try:
        h = u32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = k32.GlobalLock(h)
        if not p:
            return None
        try:
            text = ctypes.wstring_at(p).strip()
            if text.startswith("http://") or text.startswith("https://"):
                return text
        finally:
            k32.GlobalUnlock(h)
    except Exception:
        pass
    finally:
        u32.CloseClipboard()
    return None


PLUGIN = {
    "name": "media_downloader",
    "description": (
        "Downloads video (MP4) from YouTube, Instagram Reels, TikTok, Twitter/X, Facebook, "
        "Reddit, and other video sites directly into the Videos folder. If the user doesn't specify "
        "a link, it automatically downloads the URL currently copied to the clipboard. Use for: "
        "'download this video', 'download this reel', 'download video from link X', or 'save this reel'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "url": {
                "type": "STRING",
                "description": "Video URL to download. If omitted, checks the clipboard for a copied link.",
            },
            "quality": {
                "type": "STRING",
                "enum": ["best", "1080p", "720p", "480p"],
                "description": "Desired video resolution/quality. Default is 'best'.",
            },
        },
        "required": [],
    },
}


def _download_task(url: str, quality: str, out_dir: Path, player=None) -> None:
    try:
        import yt_dlp
    except ImportError:
        if player and hasattr(player, "request_say"):
            player.request_say("yt-dlp is not installed. Please run pip install yt-dlp.")
        return

    fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
    if quality == "1080p":
        fmt = "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]"
    elif quality == "720p":
        fmt = "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]"
    elif quality == "480p":
        fmt = "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480]"

    ydl_opts = {
        "format": fmt,
        "outtmpl": str(out_dir / "%(title).100s [%(id)s].%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "Video")
            if player and hasattr(player, "request_say"):
                player.request_say(f"Download complete: {title}. Saved to your Videos folder.")
            if player and hasattr(player, "write_log"):
                player.write_log(f"Media Downloader: Saved '{title}' to {out_dir}")
    except Exception as e:
        if player and hasattr(player, "request_say"):
            player.request_say(f"Sorry, video download failed: {str(e)[:100]}")


def run(parameters: dict[str, Any], player=None) -> str:
    url = (parameters.get("url") or "").strip()
    quality = (parameters.get("quality") or "best").strip().lower()

    if not url:
        url = _get_clipboard_url() or ""

    if not url:
        return "Please provide a video link or copy a link to your clipboard first."

    # Launch download in background thread so JARVIS UI stays responsive
    t = threading.Thread(target=_download_task, args=(url, quality, _VIDEOS_DIR, player), daemon=True)
    t.start()

    return f"Downloading video from {url} in {quality} quality to your Videos folder. I will notify you when it's done."
