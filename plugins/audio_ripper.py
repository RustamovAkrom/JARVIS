"""
JARVIS plugin — Video to MP3 Audio Extractor (audio_ripper.py).

Extracts high-quality MP3 audio from any YouTube, reel, clip, or video URL
(or clipboard link) and saves it directly to the user's Music folder.
"""
from __future__ import annotations

import ctypes
import os
import threading
from pathlib import Path
from typing import Any, Optional

try:
    from core.user_dirs import folder as _user_folder
    _MUSIC_DIR = Path(_user_folder("Music") or (Path.home() / "Music"))
except Exception:
    _MUSIC_DIR = Path.home() / "Music"

_MUSIC_DIR.mkdir(parents=True, exist_ok=True)


def _get_clipboard_url() -> Optional[str]:
    """Read URL from Windows clipboard using user32 ctypes."""
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
    "name": "audio_ripper",
    "description": (
        "Extracts high-quality MP3 audio from YouTube videos, Instagram Reels, music clips, or web links "
        "and saves them directly into your Music folder. Automatically grabs links from the clipboard if omitted. "
        "Use for: 'convert this video to mp3', 'extract audio from this link', 'download song from video', or 'rip audio from this reel'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "url": {
                "type": "STRING",
                "description": "Video or song URL to extract audio from. If omitted, checks the clipboard.",
            },
            "bitrate": {
                "type": "STRING",
                "enum": ["192", "256", "320"],
                "description": "Audio bitrate in kbps (default 320 for highest quality).",
            },
        },
        "required": [],
    },
}


def _rip_task(url: str, bitrate: str, out_dir: Path, player=None) -> None:
    try:
        import yt_dlp
    except ImportError:
        if player and hasattr(player, "request_say"):
            player.request_say("yt-dlp is not installed. Please run pip install yt-dlp.")
        return

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(out_dir / "%(title).100s.%(ext)s"),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": bitrate,
        }],
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "Song")
            if player and hasattr(player, "request_say"):
                player.request_say(f"Audio extracted: {title}. Saved to your Music folder.")
            if player and hasattr(player, "write_log"):
                player.write_log(f"Audio Ripper: Saved MP3 '{title}' to {out_dir}")
    except Exception as e:
        # If ffmpeg is not available, try saving bestaudio directly
        try:
            fallback_opts = {
                "format": "bestaudio/best",
                "outtmpl": str(out_dir / "%(title).100s.%(ext)s"),
                "quiet": True,
            }
            with yt_dlp.YoutubeDL(fallback_opts) as fallback_ydl:
                info = fallback_ydl.extract_info(url, download=True)
                title = info.get("title", "Audio")
                if player and hasattr(player, "request_say"):
                    player.request_say(f"Audio downloaded: {title}. Saved to your Music folder.")
        except Exception as e2:
            if player and hasattr(player, "request_say"):
                player.request_say(f"Audio extraction failed: {str(e2)[:100]}")


def run(parameters: dict[str, Any], player=None) -> str:
    url = (parameters.get("url") or "").strip()
    bitrate = str(parameters.get("bitrate") or "320").strip()

    if not url:
        url = _get_clipboard_url() or ""

    if not url:
        return "Please provide a video link or copy a link to your clipboard first."

    t = threading.Thread(target=_rip_task, args=(url, bitrate, _MUSIC_DIR, player), daemon=True)
    t.start()

    return f"Extracting MP3 audio from {url} at {bitrate} kbps to your Music folder. I will alert you when it's ready."
