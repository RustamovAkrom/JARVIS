import sounddevice as sd
import queue
import json
import time
import os
import sys
import numpy as np

from vosk import Model, KaldiRecognizer


# AUDIO QUEUE
q = queue.Queue(maxsize=30)


# GLOBAL MIC STATS
noise_floor = 10.0  # initial guess
alpha = 0.05  # smoothing factor (LOW = STABLE)


def callback(indata, frames, time_info, status):

    global noise_floor

    # RMS loudness
    volume = np.linalg.norm(indata) * 10

    # Adaptive noise calibration (EMA smoothing)
    noise_floor = (1 - alpha) * noise_floor + alpha * volume

    # Push audio frame + current volume
    if not q.full():
        q.put((bytes(indata), volume))


def resource_path(relative):

    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative)

    return relative


# STT CLASS
class SpeachToText:

    def __init__(self, model_path):

        self.model = Model(resource_path(model_path))
        self.recognizer = KaldiRecognizer(self.model, 16000)

        self.stream = None
        self.active = False

    # START MIC
    def start(self):

        if self.active:
            return

        self.stream = sd.RawInputStream(
            samplerate=16000,
            blocksize=8000,
            dtype="int16",
            channels=1,
            callback=callback,
        )

        self.stream.start()
        self.active = True

    # STOP MIC
    def stop(self):

        if not self.active:
            return

        self.stream.stop()
        self.stream.close()

        self.stream = None
        self.active = False

    # SMART LISTEN (ADAPTIVE)
    def listen(self, timeout=10, silence_timeout=1.2):

        global noise_floor

        self.start()

        print("🎤 Listening...")

        start_time = time.time()
        last_voice_time = time.time()

        while True:

            # absolute safety timeout
            if time.time() - start_time > timeout:
                return None

            if not q.empty():

                data, volume = q.get()

                # Dynamic voice threshold
                voice_threshold = max(15, noise_floor * 2.5)

                # Voice detected
                if volume > voice_threshold:
                    last_voice_time = time.time()

                # Vosk recognition
                if self.recognizer.AcceptWaveform(data):

                    result = json.loads(self.recognizer.Result())
                    self.recognizer.Reset()

                    text = result.get("text", "")

                    if text.strip():
                        return text

            # Silence end detection
            if time.time() - last_voice_time > silence_timeout:
                return None
