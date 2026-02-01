import os
import pyttsx3

def shutdown_pc():

    confirm = input("Вы уверены, что хотите выключить компьютер? (y/n): ")
    if confirm.lower() == "y":
        pyttsx3.speak("Выключаю компьютер")
        os.system("shutdown /s /t 5")
