import os
import pyttsx3
import ctypes


def shutdown_pc():
    pyttsx3.speak("Выключаю компьютер через пять секунд")
    # os.system("shutdown /s /t 5")


def cancel_shutdown():
    pyttsx3.speak("Отмена выключения")
    # os.system("shutdown /a")


def restart_pc(*args, **kwargs):
    pyttsx3.speak("Перезагружаю компьютер через пять секунд")
    # os.system("shutdown /r /t 5")


def lock_pc(*args, **kwargs):
    pyttsx3.speak("Блокирую экран")
    ctypes.windll.user32.LockWorkStation()
