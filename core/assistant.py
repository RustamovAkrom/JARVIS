import time
import pyttsx3
import random
import socket
import re

from core.command_router import CommandRouter
from core.stt import SpeachToText
from core.state import AssistantMode
from core.ai.gemini import GeminiClient
from core import config


class Assistant:

    def __init__(
        self, stt: SpeachToText, commands: dict[str, any]
    ):
        # ROUTERS
        self.stt = stt
        self.router = CommandRouter(commands, config.CONFIDENCE_THRESHOLD)

        # AI
        self.mode = AssistantMode.SYSTEM
        self.gemini = GeminiClient(config.GEMINI_API_KEY)

        # SESSION
        self.session_active = False
        self.session_timeout = 15
        self.last_activity = time.time()

        # WAKEWORD FROM CONFIG
        self.wakewords = ["джарвис", "jarvis", "чарльз", "джервис"]

        # FAIL SYSTEM
        self.fail_count = 0

        self.fail_lvl1 = [
            "Я вас не понял",
            "Повторите пожалуйста",
            "Не расслышал"
        ]

        self.fail_lvl2 = [
            "Попробуйте сказать иначе",
            "Вы говорите немного неразборчиво",
            "Команда не распознана"
        ]

        self.fail_lvl3 = [
            "Мы не понимаем друг друга",
            "Скажите точнее",
            "Попробуйте еще раз"
        ]


    # SPEAK
    def speak(self, text):
        pyttsx3.speak(text)

    # CHEKC INTERNET CONNECTION
    def internet_available(self, timeout=2) -> bool:
        try:
            socket.create_connection(("8.8.8.8", 53), timeout=timeout)
            return True
        except socket.error:
            return False

    # SESSION RESET
    def reset_session(self):
        self.session_active = False
        self.fail_count = 0

    # FAIL HANDLER
    def handle_fail(self):
        self.fail_count += 1

        if self.fail_count == 1:
            self.speak(random.choice(self.fail_lvl1))
        elif self.fail_count == 2:
            self.speak(random.choice(self.fail_lvl2))
        elif self.fail_count == 3:
            self.speak(random.choice(self.fail_lvl3))

        if self.fail_count >= 5:
            self.speak("Возвращаюсь в режим ожидания")
            self.reset_session()
    
    # WAKEWORD CHECK
    def wakeword_detect(self, text: str) -> str:
        words = text.split()
        return any(w in words for w in self.wakewords)
    
    # AI HANDLER
    def handle_ai(self, text):

        if not self.gemini.available:
            self.speak("Интернет недоступен. Выключаю режим ИИ.")
            self.mode = AssistantMode.SYSTEM
            self.reset_session()
            return
        
        if not self.gemini.available:
            self.speak("ИИ недоступен")
            self.mode = AssistantMode.SYSTEM
            self.reset_session()
            return

        self.speak("Думаю")

        try:
            answer = self.gemini.ask(text)

            if answer:
                self.speak(answer[:400])  # limit to 4000 chars
            else:
                self.speak("Ответ не получен")

        except Exception as e:
            print("❌ AI error:", e)
            self.speak("Ошибка связи с ИИ")

    # AI HANDLER
    def run(self):

        self.speak("Приветствую. Джарвис готов к работе.")
        print("✅ Assistant running")

        while True:
            try:

                # SESSION TIMEOUT
                if self.session_active:
                    if time.time() - self.last_activity > self.session_timeout:
                        print("⏱ Session timeout → standby")
                        self.reset_session()

                # LISTEN
                user_text = self.stt.listen(
                    timeout=2,
                    silence_timeout=config.SILENCE_TIMEOUT
                )

                if not user_text:
                    continue

                print("🗣️ User said:", user_text)
                normalized = self.router.normalize(user_text)
                
                # NOT ACTIVE -> WAIT WAKEWORD
                if not self.session_active:
                    if not self.wakeword_detect(normalized):
                        continue

                    self.session_active = True
                    self.last_activity = time.time()
                    self.fail_count = 0

                    self.speak("Слушаю")
                    
                    for w in self.wakewords:
                        normalized = normalized.replace(w, "").strip()
                        
                    command_text = normalized.strip()

                    if not command_text:
                        continue

                else:
                    command_text = normalized
                    self.last_activity = time.time()

                print("🎯 Command:", command_text)

                # AI ON
                if any(phrase in command_text for phrase in config.AI_ON_PHRASES):

                    if not config.AI_ENABLED:
                        self.speak("Режим ИИ отключен в настройках.")
                        print("❌ AI mode disabled in config.")
                        continue

                    if not self.internet_available():
                        pyttsx3.speak(
                            "Интернет недоступен. Невозможно включить режим ИИ."
                        )
                        print("❌ Internet not available for AI mode.")
                        continue

                    if not self.gemini.available:
                        pyttsx3.speak("Сервис ИИ недоступен. Попробуйте позже.")
                        print("❌ Gemini AI service not available.")
                        continue

                    self.mode = AssistantMode.AI
                    pyttsx3.speak("Режим искусственного интеллекта активирован.")
                    print("🤖 AI mode activated")
                    continue

                # AI OFF
                if any(phrase in command_text for phrase in config.AI_OFF_PHRASES):
                    self.mode = AssistantMode.SYSTEM
                    pyttsx3.speak("Возвращаюсь в обычный режим.")
                    print("🔄 Returned to system mode")
                    continue

                # AI MODE
                if self.mode == AssistantMode.AI:
                    self.handle_ai(command_text)
                    continue

                # SYSTEM COMMANDS
                commands_found = self.router.detect(command_text)

                print("Commands found:", commands_found)

                if commands_found:

                    self.fail_count = 0
                    self.speak("Выполняю")

                    for action, score, phrase in commands_found:
                        print(f"▶ {action.__name__} | {score:.1f}% | '{phrase}'")

                        try:
                            action()
                        except Exception as e:
                            print("❌ Error executing command:", e)
                            self.handle_fail()

                else:
                    self.handle_fail()

            except Exception as e:
                print("🔥 CRITICAL LOOP ERROR:", e)
                time.sleep(1)
