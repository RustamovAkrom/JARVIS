import webbrowser
import pyttsx3


def open_browser(*args, **kwargs):
    pyttsx3.speak("Открываю браузер")
    webbrowser.open("https://google.com")
    print("Browser opened.")


def open_youtube(*args, **kwargs):
    pyttsx3.speak("Открываю Ютуб")
    webbrowser.open("https://youtube.com")
    print("YouTube opened in your browser.")


def open_google(*args, **kwargs):
    pyttsx3.speak("Открываю Гугл")
    webbrowser.open("https://google.com")
    print("Google opened in your browser.")
