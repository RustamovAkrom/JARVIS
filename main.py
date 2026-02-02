from core.stt import SpeachToText
from core.wakeword import WakeWordListener
from core.assistant import Assistant
from core import config
from core.commands import COMMANDS


def main():

    stt = SpeachToText("models/small/vosk-ru")
    wake = WakeWordListener(config.WAKEWORD)

    assistant = Assistant(stt, wake, COMMANDS)

    assistant.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Stoped 🔴")
