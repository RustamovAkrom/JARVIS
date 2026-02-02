import os
import sys
import time
import ctypes
import pyttsx3
import platform
import psutil


def open_explorer(*args, **kwargs):
    pyttsx3.speak("Открываю проводник")
    os.system("start explorer")


def restart_pc(*args, **kwargs):
    pyttsx3.speak("Перезагружаю компьютер через пять секунд")
    os.system("shutdown /r /t 5")


def lock_pc(*args, **kwargs):
    pyttsx3.speak("Блокирую экран")
    ctypes.windll.user32.LockWorkStation()


def exit_assistant(*args, **kwargs):
    pyttsx3.speak("Завершаю работу. До встречи.")
    time.sleep(0.5)
    sys.exit(0)


def get_system_info(*args, **kwargs):
    try:
        ram = round(psutil.virtual_memory().total / (1024**3), 1)
        cpu = platform.processor()

        text = f"Система Windows. Процессор {cpu}. Оперативная память {ram} гигабайт."

        pyttsx3.speak(text)

    except Exception as e:
        print("SYS INFO ERROR:", e)
        pyttsx3.speak("Не удалось получить информацию о системе")
