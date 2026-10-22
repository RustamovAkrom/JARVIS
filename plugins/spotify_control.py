"""
Spotify Web API integration for MARK LIV / JARVIS.
Search, play, pause, resume, skip, volume, status and device listing.
"""

from pathlib import Path
import time

PLUGIN = {
    "name": "spotify_control",
    "description": (
        "Control Spotify through the Spotify Web API. Search and play songs, "
        "pause, resume, skip tracks, change Spotify volume, show playback "
        "status, and list devices. Prefer the Windows PC named YAVUZCAN PC. "
        "Never claim playback succeeded unless it is verified."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": [
                    "status",
                    "search_play",
                    "pause",
                    "resume",
                    "next",
                    "previous",
                    "volume",
                    "devices",
                ],
                "description": "Spotify action to perform.",
            },
            "query": {
                "type": "STRING",
                "description": "Song or artist to search for.",
            },
            "volume_percent": {
                "type": "INTEGER",
                "description": "Spotify volume from 0 to 100.",
            },
        },
        "required": ["action"],
    },
}


PLUGIN_SETTINGS = {
    "namespace": "spotify_api",
    "title": "Spotify Web API",
    "fields": [
        {
            "key": "client_id",
            "label": "Spotify Client ID",
            "type": "text",
            "placeholder": "Spotify Developer Dashboard Client ID",
        },
        {
            "key": "client_secret",
            "label": "Spotify Client Secret",
            "type": "password",
            "placeholder": "Spotify Developer Dashboard Client Secret",
        },
        {
            "key": "redirect_uri",
            "label": "Redirect URI",
            "type": "text",
            "default": "http://127.0.0.1:8888/callback",
        },
    ],
}


SCOPES = (
    "user-read-playback-state "
    "user-read-currently-playing "
    "user-modify-playback-state"
)


def _track_description(track):
    title = track.get("name", "Unknown track")
    artists = ", ".join(artist.get("name", "") for artist in track.get("artists", []))
    return f"{title} - {artists}" if artists else title


def _find_pc_device(devices):
    valid = [d for d in devices if d.get("id") and not d.get("is_restricted", False)]

    # Prefer the exact Windows PC name.
    for device in valid:
        if device.get("name", "").strip().lower() == "yavuzcan pc":
            return device

    # Otherwise look for a likely computer device.
    for device in valid:
        name = device.get("name", "").strip().lower()
        device_type = str(device.get("type") or "").strip().lower()

        if device_type == "computer":
            if not any(
                word in name for word in ("phone", "redmi", "android", "iphone")
            ):
                return device

        if "windows pc" in name or "desktop" in name:
            return device

    return None


def _format_devices(devices):
    if not devices:
        return "Spotify returned no available Connect devices."

    output = []

    for device in devices:
        output.append(
            f"{device.get('name', 'Unknown')} "
            f"(type={device.get('type', 'unknown')}, "
            f"active={device.get('is_active', False)}, "
            f"restricted={device.get('is_restricted', False)})"
        )

    return "; ".join(output)


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = str(parameters.get("action", "status")).strip().lower()

    try:
        from memory.config_manager import get_plugin_config
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth

        settings = get_plugin_config("spotify_api") or {}

        client_id = str(settings.get("client_id", "")).strip()
        client_secret = str(settings.get("client_secret", "")).strip()
        redirect_uri = str(
            settings.get(
                "redirect_uri",
                "http://127.0.0.1:8888/callback",
            )
        ).strip()

        if not client_id or not client_secret:
            return (
                "Spotify API ayarları eksik. JARVIS Plugin Settings > "
                "Spotify Web API bölümündeki bilgileri kontrol et."
            )

        cache_dir = Path.home() / ".mark_liv"
        cache_dir.mkdir(parents=True, exist_ok=True)

        auth = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scope=SCOPES,
            cache_path=str(cache_dir / "spotify_token_cache.json"),
            open_browser=True,
        )

        sp = spotipy.Spotify(auth_manager=auth)

        # -----------------------------------------------------
        # DEVICES
        # -----------------------------------------------------
        if action == "devices":
            devices = sp.devices().get("devices", [])
            return _format_devices(devices)

        # -----------------------------------------------------
        # STATUS
        # -----------------------------------------------------
        if action == "status":
            current = sp.current_playback()

            if not current:
                return (
                    "Spotify şu anda oynatma bilgisi döndürmedi. "
                    "Windows Spotify uygulamasında bir şarkı başlat "
                    "ve tekrar dene."
                )

            track = current.get("item") or {}
            device = current.get("device") or {}

            if not track:
                return (
                    f"Spotify cihazı: {device.get('name', 'unknown')}. "
                    "Şu anda parça bilgisi alınamadı."
                )

            state = "çalıyor" if current.get("is_playing") else "duraklatılmış"

            return (
                f"Spotify {state}: {_track_description(track)}. "
                f"Cihaz: {device.get('name', 'unknown')}."
            )

        # -----------------------------------------------------
        # SEARCH AND PLAY
        # -----------------------------------------------------
        if action == "search_play":
            query = str(parameters.get("query", "")).strip()

            if not query:
                return "Hangi şarkıyı veya sanatçıyı aramamı istiyorsun?"

            results = sp.search(
                q=query,
                type="track",
                limit=5,
            )

            tracks = results.get("tracks", {}).get("items", [])

            if not tracks:
                return f"Spotify'da '{query}' için şarkı bulunamadı."

            track = tracks[0]
            track_name = _track_description(track)

            devices = sp.devices().get("devices", [])

            if not devices:
                return (
                    f"'{track_name}' bulundu fakat Spotify kullanılabilir "
                    "bir Connect cihazı bildirmedi. Windows Spotify "
                    "uygulamasını açıp tekrar dene."
                )

            pc = _find_pc_device(devices)

            if not pc:
                return (
                    "YAVUZCAN PC kullanılabilir cihazlar arasında "
                    "bulunamadı. Spotify cihazları: " + _format_devices(devices)
                )

            # Spotify sometimes acknowledges a playback request without
            # the expected track actually starting. Try transferring the
            # session and playing the track within its album context.
            album = track.get("album") or {}
            album_uri = album.get("uri")
            track_uri = track.get("uri")

            if not track_uri:
                return "Spotify arama sonucu geçerli bir parça URI'si içermiyor."

            try:
                sp.transfer_playback(
                    device_id=pc["id"],
                    force_play=True,
                )

                time.sleep(1)

                if album_uri:
                    sp.start_playback(
                        device_id=pc["id"],
                        context_uri=album_uri,
                        offset={"uri": track_uri},
                    )
                else:
                    sp.start_playback(
                        device_id=pc["id"],
                        uris=[track_uri],
                    )

            except Exception as play_error:
                return (
                    f"Spotify '{track_name}' parçasını "
                    f"{pc.get('name', 'PC')} cihazında başlatamadı. "
                    f"Hata: {type(play_error).__name__}: "
                    f"{str(play_error)[:300]}. "
                    "Spotify masaüstü uygulamasını ve Connect cihazını kontrol et."
                )

            # Poll playback status instead of trusting the HTTP command alone.
            last_current = None

            for _ in range(5):
                time.sleep(1)

                try:
                    last_current = sp.current_playback()
                except Exception:
                    continue

                if not last_current:
                    continue

                current_track = last_current.get("item") or {}
                current_device = last_current.get("device") or {}

                same_track = current_track.get("uri") == track_uri

                same_device = current_device.get("id") == pc.get("id") or (
                    current_device.get("name", "").strip().lower()
                    == pc.get("name", "").strip().lower()
                )

                if last_current.get("is_playing") and same_track and same_device:
                    return (
                        f"Spotify doğruladı: {track_name} "
                        f"{pc.get('name')} cihazında çalıyor."
                    )

            # Do not falsely claim that playback succeeded.
            try:
                latest_devices = sp.devices().get("devices", [])
                latest_current = sp.current_playback()
            except Exception as verify_error:
                return (
                    f"Komut gönderildi fakat oynatma doğrulanamadı. "
                    f"Doğrulama hatası: {type(verify_error).__name__}: "
                    f"{str(verify_error)[:250]}"
                )

            if latest_current:
                actual_device = latest_current.get("device") or {}
                actual_track = latest_current.get("item") or {}

                return (
                    f"Spotify komutu kabul etti fakat istenen oynatma "
                    f"doğrulanamadı. İstenen: {track_name}. "
                    f"Raporlanan parça: "
                    f"{_track_description(actual_track) if actual_track else 'yok'}. "
                    f"Cihaz: {actual_device.get('name', 'unknown')}. "
                    f"Çalıyor: {latest_current.get('is_playing', False)}. "
                    f"Cihaz listesi: {_format_devices(latest_devices)}"
                )

            return (
                f"Spotify '{track_name}' için komutu aldı fakat oynatma "
                "durumu döndürmedi. Spotify masaüstü uygulamasının açık "
                "olduğunu ve Connect menüsünde YAVUZCAN PC'nin seçili "
                "olduğunu kontrol et. Cihazlar: "
                f"{_format_devices(latest_devices)}"
            )

        # -----------------------------------------------------
        # PAUSE
        # -----------------------------------------------------
        if action == "pause":
            sp.pause_playback()
            return "Spotify duraklatma komutu gönderildi."

        # -----------------------------------------------------
        # RESUME
        # -----------------------------------------------------
        if action == "resume":
            current = sp.current_playback()

            if current and current.get("device"):
                device_id = current["device"].get("id")
            else:
                devices = sp.devices().get("devices", [])
                pc = _find_pc_device(devices)

                if not pc:
                    return (
                        "Devam ettirmek için kullanılabilir PC Spotify "
                        "cihazı bulunamadı. Spotify'ı bilgisayarda aç."
                    )

                device_id = pc["id"]

            sp.start_playback(device_id=device_id)
            return "Spotify devam ettirme komutu gönderildi."

        # -----------------------------------------------------
        # NEXT
        # -----------------------------------------------------
        if action == "next":
            sp.next_track()
            return "Spotify sonraki parça komutunu aldı."

        # -----------------------------------------------------
        # PREVIOUS
        # -----------------------------------------------------
        if action == "previous":
            sp.previous_track()
            return "Spotify önceki parça komutunu aldı."

        # -----------------------------------------------------
        # VOLUME
        # -----------------------------------------------------
        if action == "volume":
            value = parameters.get("volume_percent")

            if value is None:
                return "Spotify ses seviyesini 0 ile 100 arasında belirt."

            value = max(0, min(100, int(value)))
            sp.volume(value)

            return f"Spotify ses seviyesi {value} olarak ayarlandı."

        return (
            "Bilinmeyen Spotify işlemi. Desteklenen işlemler: "
            "status, search_play, pause, resume, next, previous, "
            "volume, devices."
        )

    except Exception as exc:
        message = str(exc)
        low = message.lower()

        try:
            if player:
                player.write_log(
                    "JARVIS Spotify plugin error: " f"{type(exc).__name__}: {message}"
                )
        except Exception:
            pass

        if "premium" in low or "403" in low:
            return (
                "Spotify isteği reddetti. Premium hesabını, API izinlerini "
                f"ve cihaz durumunu kontrol et. Ayrıntı: {message[:250]}"
            )

        if "404" in low:
            return (
                "Spotify oynatma cihazını veya oturumunu bulamadı. "
                f"Ayrıntı: {message[:250]}"
            )

        if "401" in low or "token" in low:
            return (
                "Spotify kimlik doğrulaması başarısız. Spotify hesabını "
                "yeniden bağlamayı dene. Ayrıntı: {message[:250]}"
            )

        if "invalid_client" in low or "client_id" in low:
            return (
                "Spotify Client ID veya Client Secret kabul edilmedi. "
                "Eklenti ayarlarını kontrol et."
            )

        if "redirect_uri" in low or "redirect uri" in low:
            return (
                "Spotify Redirect URI uyuşmuyor. Developer Dashboard ve "
                "JARVIS ayarındaki adresler birebir aynı olmalı."
            )

        return f"Spotify API hatası: {type(exc).__name__}: {message[:300]}"
