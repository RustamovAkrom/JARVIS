import sys

from core.stt import SpeachToText
from core.wakeword import WakeWordListener
from core.assistant import Assistant
from core import config
from core.commands import COMMANDS


def main():

    stt = SpeachToText("models/small/vosk-ru")
    wake = WakeWordListener(config.WAKEWORD)

    assistant = Assistant(stt, wake, COMMANDS)

    try:
        assistant.run()
    except KeyboardInterrupt:
        print("Stoped 🔴")
        sys.exit(0)

    finally:
        wake.close()
        stt.stop()


if __name__ == "__main__":
    main()
