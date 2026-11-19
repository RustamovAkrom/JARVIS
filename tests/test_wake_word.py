import sys
import types
import threading
import time

import numpy as np
import pytest

from core.wake_word import WakeWordDetector


def test_threshold_validation():
    detector = WakeWordDetector(lambda: None)
    detector.set_threshold(0.72)
    assert detector.threshold == 0.72
    with pytest.raises(ValueError):
        detector.set_threshold(1.0)


def test_feed_keeps_fresh_frames_when_queue_full():
    detector = WakeWordDetector(lambda: None)
    detector._running = True
    for value in range(51):
        detector.feed(np.array([value], dtype=np.int16))
    assert detector._queue.qsize() == 50
    assert detector._queue.get_nowait()[0] == 1
    assert detector._queue.get_nowait()[0] == 2


def test_start_stop_lifecycle(monkeypatch):
    calls = []
    detected = threading.Event()

    class FakeModel:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))
        def predict(self, frame):
            return {"hey_jarvis": 0.0}

    package = types.ModuleType("openwakeword")
    package.__path__ = []
    model_module = types.ModuleType("openwakeword.model")
    model_module.Model = FakeModel
    monkeypatch.setitem(sys.modules, "openwakeword", package)
    monkeypatch.setitem(sys.modules, "openwakeword.model", model_module)

    detector = WakeWordDetector(detected.set)
    assert detector.start()
    assert detector.ready
    detector.feed(np.zeros(1024, dtype=np.int16))
    time.sleep(0.05)
    detector.stop()
    assert not detector.ready
    assert detector._thread is None
    assert calls


def test_detection_uses_threshold_and_cooldown(monkeypatch):
    calls = []
    detected = threading.Event()

    class FakeModel:
        def __init__(self, **kwargs): pass
        def predict(self, frame): return {"hey_jarvis": 0.9}

    package = types.ModuleType("openwakeword")
    package.__path__ = []
    model_module = types.ModuleType("openwakeword.model")
    model_module.Model = FakeModel
    monkeypatch.setitem(sys.modules, "openwakeword", package)
    monkeypatch.setitem(sys.modules, "openwakeword.model", model_module)

    detector = WakeWordDetector(lambda: (calls.append(1), detected.set()), threshold=0.8)
    assert detector.start()
    detector.feed(np.zeros(1024, dtype=np.int16))
    assert detected.wait(1)
    detector.feed(np.zeros(1024, dtype=np.int16))
    time.sleep(0.05)
    detector.stop()
    assert len(calls) == 1
