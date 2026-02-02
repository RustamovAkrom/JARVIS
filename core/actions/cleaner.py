import shutil
import ctypes
from pathlib import Path
import tempfile


def clear_temp_folder():
    temp_path = Path(tempfile.gettempdir())

    for item in temp_path.iterdir():
        try:
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)
        except:
            pass


def clear_recycle_bin():
    try:
        ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x0007)
    except:
        pass


def clean_all_files():
    clear_temp_folder()
    clear_recycle_bin()
