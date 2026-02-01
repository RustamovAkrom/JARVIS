import os
import sys
import time
import pyttsx3

def open_explorer():
    pyttsx3.speak("Открываю проводник")
    os.system("explorer")


def restart_pc(*args, **kwargs):
    os.system("shutdown /r /t 5")


def lock_pc(*args, **kwargs):
    os.system("rundll32.exe user32.dll,LockWorkStation")


def exit_assistant(*args, **kwargs):
    pyttsx3.speak("Завершаю работу. До встречи.")
    time.sleep(1)
    sys.exit(0)
