import os
import random
import subprocess
import sys
from pathlib import Path
from core import config


def play_music(*args, **kwargs):
    """
    Находит случайный музыкальный файл в папке Music (или её подпапках)
    и воспроизводит его через системный проигрыватель.
    Работает на Windows, Linux и macOS.
    """
    try:
        # Папка пользователя "Музыка"
        if config.MUSICS_DIR:
            music_dir = config.MUSICS_DIR
        else:
            music_dir = Path.home() / "Music"
            
        if not music_dir.exists():
            return "Папка 'Music' не найдена."

        # Собираем список всех музыкальных файлов
        extensions = (".mp3", ".wav", ".flac", ".m4a", ".ogg")
        all_music = [file for file in music_dir.rglob("*") if file.suffix.lower() in extensions]

        if not all_music:
            return "Я не нашёл ни одного музыкального файла в вашей папке 'Music'."

        # Выбираем случайный файл
        random_song = random.choice(all_music)

        # Открываем файл системным проигрывателем
        if sys.platform.startswith("win"):
            os.startfile(random_song)
        elif sys.platform == "darwin":  # macOS
            subprocess.Popen(["open", random_song])
        else:  # Linux
            subprocess.Popen(["xdg-open", random_song])

        return f"🎵 Воспроизводится: {random_song.name}"

    except Exception as e:
        return f"Ошибка при воспроизведении музыки: {e}"
