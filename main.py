import sys

from core.stt import SpeachToText
from core.assistant import Assistant
from core import config
from core.commands_loader import load_commands


def main():
    commands = load_commands()
    stt = SpeachToText("models/small/vosk-ru")
    assistant = Assistant(stt, commands)

    try:
        assistant.run()
    except KeyboardInterrupt:
        print("Stoped 🔴")
        sys.exit(0)

    finally:
        stt.stop()


if __name__ == "__main__":
    main()
