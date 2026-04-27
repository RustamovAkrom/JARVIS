import os
import subprocess
import pyttsx3
from .base import BaseMode



class GamingMode(BaseMode):

    name = "gaming"

    def __init__(self):
        self.prev_power_plan = None

    def enable(self):
        pyttsx3.speak("Включаю игровой режим")

        self.save_current_power_plan()
        self.enable_high_performance()
        self.disable_notifications()
        self.kill_background_apps()

        pyttsx3.speak("Игровой режим активирован")

    