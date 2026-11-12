"""Durable, unbounded lecture recording to the user's Documents folder.

The WAV is written incrementally by a worker thread. The audio callback only
copies device buffers into a bounded queue, so slow storage is reported instead
of silently growing RAM or dropping lecture audio.
"""

from __future__ import annotations

import queue
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

import sounddevice as sd
from core.runtime_paths import PATHS


def recordings_dir() -> Path:
    """A persistent location outside the source or replaceable app bundle."""
    return PATHS.lectures_dir


def latest_recording_file() -> Path | None:
    """Find the last saved audio part, including recordings from earlier runs."""
    roots = [recordings_dir(), Path.home() / "Documents" / "JARVIS" / "Lectures"]
    sessions = sorted(
        (p for root in roots if root.is_dir() for p in root.glob("Lecture_*") if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for session in sessions:
        parts = sorted(session.glob("Part_*.wav"), key=lambda p: p.name, reverse=True)
        if parts:
            return parts[0]
    return None


class LectureRecorder:
    RATE = 48_000
    BLOCK = 2_048
    QUEUE_BLOCKS = 128
    SEGMENT_SECONDS = 45 * 60  # stay well below RIFF/WAV's 4 GiB size ceiling

    @staticmethod
    def input_sources() -> list[tuple[int, str]]:
        """Return operating-system input endpoints, including virtual/line-in devices."""
        devices = sd.query_devices()
        try:
            host_apis = sd.query_hostapis()
        except Exception:
            host_apis = []
        result = []
        for index, device in enumerate(devices):
            if int(device.get("max_input_channels", 0)) < 1:
                continue
            api_index = int(device.get("hostapi", -1))
            api = (
                host_apis[api_index].get("name", "Audio")
                if 0 <= api_index < len(host_apis)
                else "Audio"
            )
            result.append((index, f"{device.get('name', 'Input')} · {api}"))
        return result

    def __init__(self):
        self._stream = None
        self._queue: queue.Queue[bytes | None] | None = None
        self._writer: threading.Thread | None = None
        self._wave = None
        self._error: str | None = None
        self._path: Path | None = None
        self._segment_path: Path | None = None
        self._part_number = 0
        self._segment_frames = 0
        self._channels = 1
        self._sample_rate = self.RATE
        self._stopping = False
        self.started_at: datetime | None = None

    @property
    def recording(self) -> bool:
        return self._stream is not None and not self._stopping

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def latest_file(self) -> Path | None:
        """Most recently completed/open segment inside this lecture session."""
        return self._segment_path

    @property
    def error(self) -> str | None:
        return self._error

    def start(self, device_index: int | None = None) -> Path:
        if self.recording:
            raise RuntimeError("A lecture recording is already in progress.")

        directory = recordings_dir()
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = directory / f"Lecture_{stamp}"
        suffix = 1
        while path.exists():
            path = directory / f"Lecture_{stamp}_{suffix}"
            suffix += 1
        path.mkdir()

        self._queue = queue.Queue(maxsize=self.QUEUE_BLOCKS)
        self._path = path
        self._error = None
        self._stopping = False
        try:
            device = (
                sd.query_devices(device_index, "input")
                if device_index is not None
                else sd.query_devices(kind="input")
            )
            max_channels = max(1, int(device.get("max_input_channels", 1)))
            last_error = None
            candidates = [
                (rate, channels)
                for rate in (self.RATE, 44_100, 32_000, 16_000)
                for channels in (min(2, max_channels), 1)
            ]
            seen = set()
            for rate, channels in candidates:
                if (rate, channels) in seen:
                    continue
                seen.add((rate, channels))
                stream = None
                try:
                    self._open_segment(rate, channels)
                    self._sample_rate = rate
                    self._channels = channels
                    stream = sd.RawInputStream(
                        samplerate=rate,
                        blocksize=self.BLOCK,
                        channels=channels,
                        dtype="int16",
                        callback=self._audio_callback,
                        device=device_index,
                    )
                    stream.start()
                    self._stream = stream
                    break
                except Exception as exc:
                    last_error = exc
                    try:
                        if stream is not None:
                            stream.close()
                    except Exception:
                        pass
                    if self._wave is not None:
                        self._wave.close()
                        self._wave = None
                    if self._segment_path is not None:
                        self._segment_path.unlink(missing_ok=True)
                        self._segment_path = None
                        self._part_number = max(0, self._part_number - 1)
            if self._stream is None:
                raise RuntimeError(f"No supported input format found: {last_error}")
            self._writer = threading.Thread(
                target=self._write_loop, name="lecture-wav-writer", daemon=True
            )
            self._writer.start()
            self.started_at = datetime.now()
        except Exception:
            self._finalize_writer()
            # Keep a valid partial WAV if any audio arrived; otherwise avoid
            # leaving an empty file that looks like a successful recording.
            if path.exists() and not any(path.iterdir()):
                path.rmdir()
            self._stream = None
            raise
        return path

    def _open_segment(self, rate: int, channels: int):
        self._part_number += 1
        segment = self._path / f"Part_{self._part_number:03d}.wav"
        self._wave = wave.open(str(segment), "wb")
        self._wave.setnchannels(channels)
        self._wave.setsampwidth(2)
        self._wave.setframerate(rate)
        self._segment_path = segment
        self._segment_frames = 0

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            # PortAudio reports transient input overflows here. Preserve a note
            # for the UI instead of pretending the recording is pristine.
            self._error = f"Audio input warning: {status}"
        if self._queue is None:
            return
        try:
            self._queue.put_nowait(bytes(indata))
        except queue.Full:
            self._error = (
                "Audio writer fell behind; recording stopped to prevent missing audio."
            )
            raise sd.CallbackAbort

    def _write_loop(self):
        assert self._queue is not None and self._wave is not None
        checkpoint = time.monotonic()
        try:
            while True:
                block = self._queue.get()
                if block is None:
                    break
                frames = len(block) // (2 * self._channels)
                if (
                    self._segment_frames + frames
                    > self._sample_rate * self.SEGMENT_SECONDS
                ):
                    self._wave.close()
                    self._open_segment(self._sample_rate, self._channels)
                self._wave.writeframesraw(block)
                self._segment_frames += frames
                # Keep a valid WAV header during long recordings. If the app or
                # machine exits unexpectedly, completed audio remains readable.
                if time.monotonic() - checkpoint >= 10:
                    self._wave.writeframes(b"")
                    self._wave._file.flush()
                    checkpoint = time.monotonic()
        except Exception as exc:
            self._error = f"Could not write recording: {exc}"

    def _finalize_writer(self):
        if (
            self._queue is not None
            and self._writer is not None
            and self._writer.is_alive()
        ):
            try:
                self._queue.put(None, timeout=30)
            except queue.Full:
                self._error = self._error or "Could not flush the recording buffer."
        if self._writer is not None:
            self._writer.join(timeout=10)
            if self._writer.is_alive():
                self._error = self._error or "Audio writer did not finish cleanly."
        if self._wave is not None:
            try:
                self._wave.close()  # writes the final WAV header
            except Exception as exc:
                self._error = self._error or f"Could not finalize WAV file: {exc}"
        self._queue = None
        self._writer = None
        self._wave = None

    def stop(self) -> Path | None:
        stream, self._stream = self._stream, None
        self._stopping = True
        stop_error = None
        if stream is not None:
            try:
                stream.stop()
            except Exception as exc:
                stop_error = f"Audio stream stop warning: {exc}"
            finally:
                try:
                    stream.close()
                except Exception as exc:
                    stop_error = stop_error or f"Audio stream close warning: {exc}"
        self._finalize_writer()
        self._stopping = False
        if stop_error:
            self._error = self._error or stop_error
        return self._path
