import os
import pyttsx3


def open_browser(*args, **kwargs):
    os.system("start chrome")  # Example for Windows


def open_youtube(*args, **kwargs):
    os.system("start https://www.youtube.com")  # Example for Windows


def shutdown_pc(*args, **kwargs):
    print("Shutting down the PC...")


def say_time(*args, **kwargs):
    from datetime import datetime
    now = datetime.now()
    current_time = now.strftime("%H:%M")
    print(f"сегодняшниe {current_time}")
    pyttsx3.speak(f"сегодняшниe {current_time}")
