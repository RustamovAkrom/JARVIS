import re
import pyttsx3
import random
import time
import os
import socket

from rapidfuzz import fuzz

from core.stt import SpeachToText
from core.wakeword import WakeWordListener

from core import actions
from ai.gemini import GeminiClient
from core import config


SEPARATORS = [
    "и",
    "потом",
    "затем",
    "после этого",
    "а потом",
    "далее",
    "следующим образом"
]

COMMANDS = {

    # 🌐 Internet
    ("открой браузер", "запусти браузер", "открыть интернет"): actions.open_browser,
    ("открой ютуб", "включи youtube", "запусти видео"): actions.open_youtube,
    ("открой гугл", "google"): actions.open_google,

    # 🕒 Time & Date
    ("который час", "скажи время", "текущее время"): actions.say_time,
    ("какое сегодня число", "сегодняшняя дата"): actions.say_date,

    # 💻 System
    ("выключи компьютер", "выруби пк", "shutdown"): actions.shutdown_pc,
    ("перезагрузи компьютер", "restart"): actions.restart_pc,
    ("заблокируй экран", "lock pc"): actions.lock_pc,

    # 🔊 Volume
    ("максимальная громкость", "громкость на максимум"): actions.set_volume_max,
    ("средняя громкость", "половина громкости"): actions.set_volume_mid,
    ("минимальная громкость", "убавь до нуля"): actions.set_volume_min,

    # 📁 Files
    ("открой проводник", "мои файлы"): actions.open_explorer,
    # 🎮 Fun
    ("скажи анекдот", "пошути"): actions.tell_joke,
    ("выключись", "заверши работу", "стоп", "отключись"): actions.exit_assistant,
}


AI_ON_PHRASES = [
    "включи и ай",
    "режим эй",
    "поговорим",
    "активируй ии",
    "эй ай",
    "включи помощника",
    "активируй помощника"
]

AI_OFF_PHRASES = [
    "выключи и ай",
    "выйди из режима ай",
    "отключи ай",
    "вернись в обычный режим",
    "обычный режим"
]


def internet_available(timeout=2) -> bool:
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=timeout)
        return True
    except socket.error:
        return False


def normalize(text: str) -> str:

    text = text.lower()
    text = text.replace("ё", "е")

    # remove garbage
    text = re.sub(r"[^\w\s]", " ", text)

    # remove filler words
    fillers = ["пожалуйста", "ну", "давай", "короче", "типа"]
    for f in fillers:
        text = text.replace(f, "")

    return re.sub(r"\s+", " ", text).strip()


def split_into_phrases(text):

    parts = [text]

    for sep in SEPARATORS:
        new_parts = []
        for p in parts:
            new_parts.extend(p.split(sep))
        parts = new_parts

    return [p.strip() for p in parts if len(p.strip()) > 2]


def find_commands(user_text, threshold=70):

    user_text = normalize(user_text)

    phrases = split_into_phrases(user_text)

    detected = []

    for phrase in phrases:

        best_score = 0
        best_action = None

        for variants, action in COMMANDS.items():

            for variant in variants:

                variant = normalize(variant)

                score = fuzz.token_set_ratio(phrase, variant)

                if score > best_score:
                    best_score = score
                    best_action = action

        if best_score >= threshold:
            detected.append((best_action, best_score, phrase))

    return detected


def main():
    print("🟢 Initializing modules...")

    stt = SpeachToText("models/small/vosk-ru")
    wake = WakeWordListener(config.WAKEWORD)

    assistant_mode = "SYSTEM"

    gemini = GeminiClient(config.GEMINI_API_KEY)

    pyttsx3.speak("Приветствую. Джарвис готов к работе.")

    print("✅ Assistant ready")

    while True:
        # Wait for "jarvis" wake word
        wake.listen()

        print("🔔 Wake word detected")

        pyttsx3.speak("Да, сэр")

        last_activity = time.time()
        unknown_count = 0

        soff_miss_phrases = [
            "Я вас не понял",
            "Повторите пожалуйста",
            "Не расслышал"
        ]

        while True:
            if time.time() - last_activity > config.COMMAND_TIMEOUT:
                print("⏲️ Session timeout. Returning to standby mode.")
                break

            # listen command
            user_text = stt.listen(timeout=2)

            if not user_text:
                continue
                
            print("🗣️ User said:", user_text)

            # reset timer inside command mode
            last_activity = time.time()

            if config.WAKEWORD in user_text.lower():
                pyttsx3.speak("Слушаю")
                continue
            
            normalized_text = normalize(user_text)

            # AI mode toggling
            if any(phrase in normalized_text for phrase in AI_ON_PHRASES):
                if not internet_available():
                    pyttsx3.speak("Интернет недоступен. Невозможно включить режим ИИ.")
                    print("❌ Internet not available for AI mode.")
                    continue

                if not gemini.available:
                    pyttsx3.speak("Сервис ИИ недоступен. Попробуйте позже.")
                    print("❌ Gemini AI service not available.")
                    continue

                assistant_mode = "AI"
                pyttsx3.speak("Режим искусственного интеллекта активирован.")
                print("🤖 AI mode activated")
                continue

            if any(phrase in normalized_text for phrase in AI_OFF_PHRASES):
                assistant_mode = "SYSTEM"
                pyttsx3.speak("Возвращаюсь в обычный режим.")
                print("🔄 Returned to system mode")
                continue

            # AI mode chat

            if assistant_mode == "AI":
                if not internet_available():
                    pyttsx3.speak("Интернет недоступен. Выключаю режим ИИ.")
                    assistant_mode = "SYSTEM"
                    print("❌ Internet not available. Exiting AI mode.")
                    continue

                pyttsx3.speak("Думаю")
                answer = gemini.ask(user_text)

                if answer:
                    pyttsx3.speak(answer[:400]) # limit to 4000 chars
                else:
                    pyttsx3.speak("Не удалось получить ответ от ИИ.")
            
            # System command mode
            commands_found = find_commands(user_text, config.CONFIDENCE_THRESHOLD)
            
            if  commands_found:

                unknown_count = 0

                print("🔍 Commands detected:", len(commands_found))

                pyttsx3.speak("Выполняю")

                for action, score, phrase in commands_found:
                    print(f"▶ {action.__name__} | {score:.1f}% | '{phrase}'")

                    try:
                        action()
                    except Exception as e:
                        print("❌ Error executing command:", e)
                        pyttsx3.speak("Произошла ошибка при выполнении команды.")
            else:
                unknown_count += 1

                if unknown_count == 1:
                    continue

                if unknown_count == 2:
                    pyttsx3.speak(random.choice(soff_miss_phrases))
                    print("⚠️ Multiple unrecognized commands. Returning to standby mode.")

                if unknown_count >= 3:
                    pyttsx3.speak("Возвращаюсь в режим ожидания")
                    break
            

if __name__ == "__main__":

    try:
        main()

    except KeyboardInterrupt:
        print("\n🔴 Assistant stopped")
