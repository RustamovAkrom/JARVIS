import os
import pyttsx3


def open_explorer(*args, **kwargs):
    pyttsx3.speak("Открываю проводник")
    os.system("start explorer")


#  ---


def open_settings(*args, **kwargs):
    pyttsx3.speak("Открываю настройки")
    os.system("start ms-settings:")


def open_control_panel(*args, **kwargs):
    pyttsx3.speak("Открываю панель управления")
    os.system("start control")


def open_task_manager(*args, **kwargs):
    pyttsx3.speak("Открываю диспетчер задач")
    os.system("start taskmgr")


def open_cmd(*args, **kwargs):
    pyttsx3.speak("Открываю командную строку")
    os.system("start cmd")


def open_powershell(*args, **kwargs):
    pyttsx3.speak("Открываю PowerShell")
    os.system("start powershell")


def open_notepad(*args, **kwargs):
    pyttsx3.speak("Открываю блокнот")
    os.system("start notepad")


def open_calculator(*args, **kwargs):
    pyttsx3.speak("Открываю калькулятор")
    os.system("start calc")


def open_paint(*args, **kwargs):
    pyttsx3.speak("Открываю Paint")
    os.system("start mspaint")


def open_snipping_tool(*args, **kwargs):
    pyttsx3.speak("Открываю ножницы")
    os.system("start snippingtool")


def open_camera(*args, **kwargs):
    pyttsx3.speak("Открываю камеру")
    os.system("start microsoft.windows.camera:")


def open_downloads(*args, **kwargs):
    pyttsx3.speak("Открываю загрузки")
    os.system('start "" "%USERPROFILE%\\Downloads"')


def open_documents(*args, **kwargs):
    pyttsx3.speak("Открываю документы")
    os.system('start "" "%USERPROFILE%\\Documents"')


def open_desktop_folder():
    pyttsx3.speak("Открываю рабочий стол")
    os.system('start "" "%USERPROFILE%\\Desktop"')


def open_pictures(*args, **kwargs):
    pyttsx3.speak("Открываю изображения")
    os.system('start "" "%USERPROFILE%\\Pictures"')


def open_music(*args, **kwargs):
    pyttsx3.speak("Открываю музыку")
    os.system('start "" "%USERPROFILE%\\Music"')


def open_videos(*args, **kwargs):
    pyttsx3.speak("Открываю видео")
    os.system('start "" "%USERPROFILE%\\Videos"')


def open_run_dialog(*args, **kwargs):
    pyttsx3.speak("Открываю окно выполнить")
    os.system("start Run")
