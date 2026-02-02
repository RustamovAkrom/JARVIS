import os
import platform
import pyttsx3


def lock_screen():
    if platform.system() == "Windows":
        pyttsx3.speak("Блокирую экран")
        os.system("rundll32.exe user32.dll,LockWorkStation")
