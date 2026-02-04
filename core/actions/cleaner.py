import shutil
import ctypes
from pathlib import Path
import tempfile
import pyttsx3


def clear_temp_folder(*args, **kwargs):
    temp_path = Path(tempfile.gettempdir())

    for item in temp_path.iterdir():
        try:
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)
        except:
            pass


def clear_recycle_bin(*args, **kwargs):
    try:
        ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x0007)
    except:
        pass


def clean_all_files(*args, **kwargs):
    pyttsx3.speak("очистка корзины пожалуйста подождите")
    clear_temp_folder()
    clear_recycle_bin()
    pyttsx3.speak("корзинка успешно очищен")
