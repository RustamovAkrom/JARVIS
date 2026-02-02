import os
import pyttsx3


def shutdown_pc():
    pyttsx3.speak("Выключаю компьютер через пять секунд")
    os.system("shutdown /s /t 5")


def cancel_shutdown():
    pyttsx3.speak("Отмена выключения")
    os.system("shutdown /a")
