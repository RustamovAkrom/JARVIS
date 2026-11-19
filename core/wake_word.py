"""
Local wake-word detection for JARVIS ("Hey Jarvis").

Design goals:
  • ZERO cost when the feature is off — openwakeword is imported ONLY inside
    start()/install helpers, never at module load. If the user never enables
    wake word, none of this touches the app.
  • ZERO latency on the audio path — the microphone callback only ever does a
    cheap, non-blocking queue push (feed()); the actual model inference runs in
    this module's own background thread, so the real-time audio thread and the
    Gemini stream are never slowed.
  • Fully local & offline — audio fed here never leaves the machine; there is no
    network call except the one-time model download the user triggers from the UI.

openwakeword ships small ONNX models (a few MB each) and runs comfortably on a
CPU. The pretrained wake phrase used here is "Hey Jarvis".
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

# Pretrained openwakeword model that listens for "Hey Jarvis".
WAKE_MODEL = "hey_jarvis"
# Score in [0,1]; above this counts as a detection. Tunable per environment.
DEFAULT_THRESHOLD = 0.5
# Mic frames arrive at 16 kHz int16; this is just the detector's input rate.
SAMPLE_RATE = 16000


def is_installed() -> bool:
    """True if the openwakeword package is importable (no model check)."""
    try:
        import importlib.util

        return importlib.util.find_spec("openwakeword") is not None
    except Exception:
        return False


def is_ready() -> bool:
    """True if openwakeword is installed AND its model files are present on disk.

    This is a cheap, DETERMINISTIC file-existence check. It deliberately does NOT
    construct a Model to probe readiness — doing that is slow and, worse, can clash
    with the detector's own Model when it's already running, which intermittently
    returned False and made the UI flicker to 'not downloaded'. Never raises.
    """
    if not is_installed():
        return False
    try:
        import importlib.util

        spec = importlib.util.find_spec("openwakeword")
        locations = tuple(spec.submodule_search_locations or ()) if spec else ()
        if not locations:
            return False
        # Do not import the package just to inspect its model files. Importing
        # openwakeword.model can race this check during startup and observe the
        # partially initialized package namespace.
        models_dir = Path(locations[0]).resolve() / "resources" / "models"
        if not models_dir.is_dir():
            return False
        has_wake = any(models_dir.glob(f"{WAKE_MODEL}*.onnx")) or any(
            models_dir.glob(f"{WAKE_MODEL}*.tflite")
        )
        has_mel = any(models_dir.glob("melspectrogram*.onnx")) or any(
            models_dir.glob("melspectrogram*.tflite")
        )
        has_emb = any(models_dir.glob("embedding_model*.onnx")) or any(
            models_dir.glob("embedding_model*.tflite")
        )
        return bool(has_wake and has_mel and has_emb)
    except Exception:
        return False


def install_and_download(
    logger: Callable[[str], None] = print, notify: Callable[[str], None] | None = None
) -> tuple[bool, str]:
    """
    One-click setup for the UI button: pip-install openwakeword if missing, then
    download the wake model. Returns (ok, message). Never raises — every failure
    is reported through the returned message and the logger.
    """
    _tell = notify or (lambda _msg: None)
    try:
        if not is_installed():
            if getattr(sys, "frozen", False):
                return (
                    False,
                    "Wake word support is not included in this build. Install an updated JARVIS release.",
                )
            logger("Wake word: installing openwakeword (one-time)…")
            _tell("Wake word: installing openwakeword (one-time)…")
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", "openwakeword"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if r.returncode != 0:
                tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [""]
                return False, f"pip install failed: {tail[0][:160]}"
        # Download the pretrained melspectrogram/embedding + wake models.
        logger("Wake word: downloading models…")
        _tell("Wake word: downloading models…")
        try:
            import openwakeword.utils as _u

            try:
                _u.download_models([WAKE_MODEL])
            except TypeError:
                _u.download_models()  # older signature downloads the default set
        except Exception as e:
            return False, f"model download failed: {e}"

        if not is_ready():
            return False, "installed, but the wake model could not be loaded."
        logger("Wake word: ready.")
        return True, "Wake word installed and ready."
    except Exception as e:
        return False, f"setup error: {e}"


class WakeWordDetector:
    """
    Runs the wake model in a dedicated thread. The mic thread calls feed() with
    raw int16 frames; detections invoke on_detect() (called from this thread —
    the callback must marshal to whatever loop/UI it needs).
    """

    def __init__(
        self,
        on_detect: Callable[[], None],
        threshold: float = DEFAULT_THRESHOLD,
        logger: Callable[[str], None] = print,
        notify: Callable[[str], None] | None = None,
    ):
        self._on_detect = on_detect
        self._threshold = self._validate_threshold(threshold)
        self._cooldown_seconds = 2.0
        self._last_detection = 0.0
        self._logger = logger
        # See PluginRegistry: `logger` is the console and gets everything,
        # `notify` is the activity log and gets only what the user must act on.
        self._notify = notify or (lambda _msg: None)
        self._queue: queue.Queue = queue.Queue(maxsize=50)
        self._thread: threading.Thread | None = None
        self._running = False
        self._model = None
        self._ready = False

    @staticmethod
    def _validate_threshold(value: float) -> float:
        value = float(value)
        if not 0.1 <= value <= 0.95:
            raise ValueError("Wake word threshold must be between 0.10 and 0.95")
        return value

    @property
    def threshold(self) -> float:
        return self._threshold

    def set_threshold(self, value: float) -> None:
        self._threshold = self._validate_threshold(value)

    def start(self) -> bool:
        """Load the model and spawn the inference thread. Returns True on success.
        Safe to call again — a no-op if already running. Never raises."""
        if self._running:
            return True
        if self._thread is not None and self._thread.is_alive():
            self._logger("Wake word: previous detector thread is still stopping.")
            return False
        try:
            from openwakeword.model import Model

            model = Model(
                wakeword_models=[WAKE_MODEL], inference_framework="onnx"
            )
            self._model = model
        except Exception as e:
            self._logger(f"Wake word: could not load model — {e}")
            self._notify("Wake word unavailable — use the WAKE NOW button.")
            self._model = None
            return False
        self._running = True
        self._ready = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="WakeWordThread"
        )
        self._thread.start()
        self._logger("Wake word: listening for 'Hey Jarvis'.")
        return True

    def stop(self) -> None:
        self._running = False
        # Wake the worker without blocking the caller.
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(None)
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        if thread is not None and thread.is_alive():
            self._logger("Wake word: detector is still stopping; model retained until worker exits.")
            self._ready = False
            return
        self._model = None
        self._thread = None
        self._ready = False
        self._drain()

    @property
    def ready(self) -> bool:
        return self._ready

    def feed(self, frame_int16) -> None:
        """Called from the mic callback (real-time thread). Must stay cheap and
        never block — the frame is copied and dropped if the queue is backed up."""
        if not self._running:
            return
        try:
            # frame_int16 is a numpy int16 array (possibly 2-D mono) — flatten to 1-D
            data = (
                frame_int16[:, 0].copy()
                if getattr(frame_int16, "ndim", 1) > 1
                else frame_int16.copy()
            )
            try:
                self._queue.put_nowait(data)
            except queue.Full:
                # Prefer fresh microphone audio over stale queued frames.
                self._queue.get_nowait()
                self._queue.put_nowait(data)
        except queue.Full:
            pass
        except Exception:
            pass

    def _loop(self) -> None:
        try:
            import numpy as np
        except Exception as exc:
            self._logger(f"Wake word: numpy unavailable — {exc}")
            self._running = False
            return
        model = self._model
        while self._running:
            try:
                frame = self._queue.get()
                if frame is None or not self._running:
                    break
                if model is None:
                    break
                scores = model.predict(np.asarray(frame, dtype=np.int16))
                score = 0.0
                if isinstance(scores, dict):
                    # match the jarvis model regardless of exact key suffix
                    for k, v in scores.items():
                        if "jarvis" in k.lower():
                            score = max(score, float(v))
                now = time.monotonic()
                if score >= self._threshold and now - self._last_detection >= self._cooldown_seconds:
                    # Drain backlog and apply cooldown to suppress repeated scores
                    # from the same utterance.
                    self._drain()
                    self._last_detection = now
                    try:
                        self._on_detect()
                    except Exception as e:
                        self._logger(f"Wake word: on_detect error — {e}")
            except Exception as e:
                self._logger(f"Wake word: inference error — {e}")

    def _drain(self) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except Exception:
            pass
