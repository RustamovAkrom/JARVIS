import time
import pyttsx3
import random

from core.command_router import CommandRouter
from core.wakeword import WakeWordListener
from core.stt import SpeachToText
from core.state import AssistantMode
from core.ai.gemini import GeminiClient
from core import config

import socket




class Assistant:

    def __init__(
        self, stt: SpeachToText, wakeword: WakeWordListener, commands: dict[str, any]
    ):
        self.stt = stt
        self.wakeword = wakeword

        self.router = CommandRouter(commands, config.CONFIDENCE_THRESHOLD)

        self.mode = AssistantMode.SYSTEM
        self.gemini = GeminiClient(config.GEMINI_API_KEY)

        self.miss_phrases = ["Я вас не понял", "Повторите пожалуйста", "Не расслышал"]

    def speak(self, text):
        pyttsx3.speak(text)

    def internet_available(self, timeout=2) -> bool:
        try:
            socket.create_connection(("8.8.8.8", 53), timeout=timeout)
            return True
        except socket.error:
            return False

    def run(self):

        self.speak("Приветствую. Джарвис готов к работе.")
        print("✅ Assistant running")

        while True:

            # WAIT WAKEWORD
            self.wakeword.listen()

            print("🔔 Wake word detected")

            self.speak("Да, сэр")

            # active session time
            last_activity = time.time()
            unknown_count = 0

            # ACTIVE SESSION
            while True:

                if time.time() - last_activity > config.COMMAND_TIMEOUT:
                    print("⏲ Session timeout -> standby")
                    break

                # Listen user
                user_text = self.stt.listen(
                    timeout=2
                )  # wait 2 seconds for detect commands

                if not user_text:
                    continue

                print("🗣️ User said:", user_text)

                # Refresh activity timer
                last_activity = time.time()

                # Wakeword inside session    
                normalized_text = self.router.normalize(user_text)

                if config.WAKEWORD in normalized_text:

                    self.speak("Слушаю")

                    # remove wakeword
                    normalized_text = normalized_text.replace(config.WAKEWORD, "").strip()

                    # if user said only "Jarvis"
                    if not normalized_text:
                        continue

                # AI mode toggling
                if any(phrase in normalized_text for phrase in config.AI_ON_PHRASES):
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

                if any(phrase in normalized_text for phrase in config.AI_OFF_PHRASES):
                    self.mode = AssistantMode.SYSTEM
                    pyttsx3.speak("Возвращаюсь в обычный режим.")
                    print("🔄 Returned to system mode")
                    continue

                # AI (Chat) MODE
                if self.mode == AssistantMode.AI:

                    if not self.internet_available():
                        self.speak("Интернет недоступен. Выключаю режим ИИ.")
                        self.mode = AssistantMode.SYSTEM
                        print("❌ Internet not available. Exiting AI mode.")
                        continue

                    self.handle_ai(user_text)
                    continue

                # SYSTEM MODE
                commands_found = self.router.detect(normalized_text)
                
                print("Commands found:", commands_found)
                if commands_found:

                    unknown_count = 0
                    self.speak("Выполняю")

                    for action, score, phrase in commands_found:
                        print(f"▶ {action.__name__} | {score:.1f}% | '{phrase}'")

                        try:
                            action()
                        except Exception as e:
                            print("❌ Error executing command:", e)
                            self.speak("Произошла ошибка при выполнении команды.")

                else:
                    unknown_count += 1

                    if unknown_count == 1:
                        continue

                    if unknown_count == 2:
                        self.speak(random.choice(self.miss_phrases))
                        print(
                            "⚠️ Multiple unrecognized commands. Returning to standby mode."
                        )

                    if unknown_count >= 3:
                        self.speak("Возвращаюсь в режим ожидания")
                        break

    # AI HANDLER
    def handle_ai(self, text):
        if not self.gemini.available:
            self.speak("ИИ недоступен")
            self.mode = AssistantMode.SYSTEM
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
