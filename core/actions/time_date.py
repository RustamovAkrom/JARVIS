from datetime import datetime
import pyttsx3


def say_time():
    now = datetime.now().strftime("%H:%M")
    pyttsx3.speak(f"Сейчас {now}")
    print(f"Сейчас {now}")


def say_date(*args, **kwargs):
    today = datetime.now().strftime("%d %B %Y")
    pyttsx3.speak(f"Сегодня {today}")
    print(f"Сегодня {today}")
