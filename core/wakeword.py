import pvporcupine
import pyaudio
import struct
import time
from core import config


class WakeWordListener:

    def __init__(self, keyword="jarvis", device_index=None):

        self.keyword = keyword
        self.device_index = device_index

        # Init Porcupine
        self.porcupine = pvporcupine.create(
            access_key=config.PORCUPINE_ACCESS_KEY,
            keywords=[keyword],
        )

        # Init PyAudio
        self.pa = pyaudio.PyAudio()

        self.stream = None

        # Anti double-trigger protection
        self.last_trigger_time = 0
        self.cooldown = 1.5   # seconds

        self.running = False


    # START MIC STREAM
    def start(self):

        if self.running:
            return

        self.stream = self.pa.open(
            rate=self.porcupine.sample_rate,
            channels=1,
            format=pyaudio.paInt16,
            input=True,
            frames_per_buffer=self.porcupine.frame_length,
            input_device_index=self.device_index
        )

        self.running = True
        print("🎧 Wakeword mic stream started")


    # STOP MIC STREAM
    def stop(self):

        if not self.running:
            return

        try:
            self.stream.stop_stream()
            self.stream.close()
        except Exception:
            pass

        self.stream = None
        self.running = False

        print("🛑 Wakeword mic stream stopped")


    # LISTEN LOOP
    def listen(self):

        print("🔊 Waiting for wake word...")

        self.start()

        while self.running:

            try:

                pcm = self.stream.read(
                    self.porcupine.frame_length,
                    exception_on_overflow=False
                )

                pcm = struct.unpack_from(
                    "h" * self.porcupine.frame_length,
                    pcm
                )

                result = self.porcupine.process(pcm)

                if result >= 0:

                    now = time.time()

                    # debounce protection
                    if now - self.last_trigger_time < self.cooldown:
                        continue

                    self.last_trigger_time = now

                    print("🔥 Wake word detected")
                    return True

            except Exception as e:
                print("❗ Wakeword error:", e)
                time.sleep(0.1)


    # FULL CLEANUP
    def close(self):

        self.stop()

        try:
            self.pa.terminate()
            self.porcupine.delete()
        except Exception:
            pass

        print("✅ Wakeword engine closed")
