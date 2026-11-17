"""Shared webcam manager for JARVIS plugins.

A single physical OpenCV capture is shared by all Camera clients. Frames are
read on a dedicated thread and only the newest frame is retained, avoiding
latency caused by stale queued frames.

Public API intentionally kept small:
    camera = Camera(index=None)
    frame = camera.read()       # BGR ndarray or None
    camera.close()              # release this client's lease

Camera instances are context managers. OpenCV is imported only when the first
camera is acquired. No plugin should create its own cv2.VideoCapture.
"""
from __future__ import annotations

import platform
import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from core.lazyimport import LazyModule

cv2 = LazyModule("cv2")

# Capture/reconnect policy. Keep timeouts finite so shutdown never hangs.
_READ_INTERVAL = 0.001
_RECONNECT_DELAY = 0.75
_OPEN_READ_TIMEOUT_MS = 3000
_OPEN_WRITE_TIMEOUT_MS = 3000
_DEFAULT_WIDTH = 1280
_DEFAULT_HEIGHT = 720
_DEFAULT_FPS = 30


@dataclass(frozen=True, slots=True)
class CameraInfo:
    index: int
    name: str
    width: int
    height: int
    fps: float
    opened: bool
    clients: int
    blocked_by: Optional[str] = None


class _Device:
    """One physical device and its latest frame, protected by a condition."""

    def __init__(self, index: int) -> None:
        self.index = index
        self._condition = threading.Condition()
        self._capture_lock = threading.RLock()
        self._capture = None
        self._frame: Optional[np.ndarray] = None
        self._frame_id = 0
        self._last_read_at = 0.0
        self._last_error: Optional[str] = None
        self._blocked_by: Optional[str] = None
        self._clients = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._closed = False
        self._width = 0
        self._height = 0
        self._fps = 0.0
        self._open_capture()
        self._thread = threading.Thread(
            target=self._reader,
            name=f"JARVIS-Camera-{index}",
            daemon=True,
        )
        self._thread.start()

    @staticmethod
    def _backend() -> int:
        system = platform.system()
        if system == "Windows":
            return cv2.CAP_DSHOW
        if system == "Darwin":
            return cv2.CAP_AVFOUNDATION
        return cv2.CAP_V4L2 if hasattr(cv2, "CAP_V4L2") else cv2.CAP_ANY

    def _open_capture(self) -> bool:
        """Open/reopen the camera. Caller serializes access via _condition."""
        cap = None
        try:
            backend = self._backend()
            # The timeout properties are supported by recent OpenCV builds;
            # retry without them for older builds/backends.
            try:
                cap = cv2.VideoCapture(
                    self.index,
                    backend,
                    [
                        cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, _OPEN_READ_TIMEOUT_MS,
                        cv2.CAP_PROP_READ_TIMEOUT_MSEC, _OPEN_READ_TIMEOUT_MS,
                    ],
                )
            except Exception:
                # Older OpenCV builds and some backends reject parameterized open.
                cap = None

            if cap is None or not cap.isOpened():
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
                # Some backends reject timeout properties by returning a closed
                # capture instead of raising. Retry with the legacy signature.
                try:
                    cap = cv2.VideoCapture(self.index, backend)
                except Exception:
                    cap = None
                if cap is None or not cap.isOpened():
                    if cap is not None:
                        try:
                            cap.release()
                        except Exception:
                            pass
                    with self._condition:
                        self._last_error = f"Camera {self.index} could not be opened"
                        self._blocked_by = self._detect_blocking_process()
                        self._capture = None
                        self._condition.notify_all()
                    return False

            # MJPG commonly avoids USB bandwidth problems on Windows webcams.
            try:
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, _DEFAULT_WIDTH)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, _DEFAULT_HEIGHT)
                cap.set(cv2.CAP_PROP_FPS, _DEFAULT_FPS)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass

            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            with self._condition:
                old, self._capture = self._capture, cap
                self._width, self._height, self._fps = width, height, fps
                self._last_error = None
                self._blocked_by = None
                self._condition.notify_all()
            if old is not None and old is not cap:
                old.release()
            return True
        except Exception as exc:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            with self._condition:
                self._capture = None
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._condition.notify_all()
            return False

    @staticmethod
    def _detect_blocking_process() -> Optional[str]:
        """Best-effort diagnostic; never make camera availability depend on it."""
        # OpenCV does not expose the process holding a device. Returning None is
        # more honest than guessing. Platform-specific process inspection can
        # be added later behind an explicit permission-aware implementation.
        return None

    def _reader(self) -> None:
        retry_at = 0.0
        failures = 0
        while not self._stop.is_set():
            with self._condition:
                cap = self._capture
                closed = self._closed
            if closed:
                break
            if cap is None:
                now = time.monotonic()
                if now < retry_at:
                    self._stop.wait(min(0.1, retry_at - now))
                    continue
                self._open_capture()
                retry_at = time.monotonic() + _RECONNECT_DELAY
                continue

            try:
                with self._capture_lock:
                    ok, frame = cap.read()
            except Exception as exc:
                ok, frame = False, None
                error = f"{type(exc).__name__}: {exc}"
            else:
                error = None

            if ok and frame is not None and getattr(frame, "size", 0):
                # Do not expose a buffer that OpenCV may reuse on the next read.
                owned = frame.copy()
                with self._condition:
                    self._frame = owned
                    self._frame_id += 1
                    self._last_read_at = time.monotonic()
                    self._last_error = None
                    self._blocked_by = None
                    self._condition.notify_all()
                failures = 0
                continue

            failures += 1
            with self._condition:
                self._last_error = error or f"Camera {self.index} returned no frame"
                self._condition.notify_all()
            if failures >= 5:
                with self._condition:
                    if self._capture is cap:
                        self._capture = None
                        self._frame = None
                try:
                    cap.release()
                except Exception:
                    pass
                failures = 0
                retry_at = time.monotonic() + _RECONNECT_DELAY
            else:
                self._stop.wait(_READ_INTERVAL)

    def configure(self, width: int, height: int, fps: float) -> None:
        """Best-effort capture configuration without exposing VideoCapture."""
        with self._condition:
            cap = self._capture
            if cap is None or self._closed:
                return
            try:
                with self._capture_lock:
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                    cap.set(cv2.CAP_PROP_FPS, fps)
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    self._width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or width)
                    self._height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or height)
                    self._fps = float(cap.get(cv2.CAP_PROP_FPS) or fps)
            except Exception as exc:
                self._last_error = f"Camera configuration failed: {type(exc).__name__}: {exc}"

    def acquire(self) -> None:
        with self._condition:
            if self._closed:
                raise RuntimeError("Camera device has been closed")
            self._clients += 1
            self._condition.notify_all()

    def release(self) -> None:
        should_close = False
        with self._condition:
            if self._clients > 0:
                self._clients -= 1
            should_close = self._clients == 0
        if should_close:
            self.close()

    def read(self, last_frame_id: Optional[int] = None, timeout: float = 0.0):
        """Return (frame_copy, frame_id); optionally wait for a newer frame."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while True:
                if self._frame is not None and (
                    last_frame_id is None or self._frame_id != last_frame_id
                ):
                    return self._frame.copy(), self._frame_id
                if self._closed or self._stop.is_set():
                    return None, self._frame_id
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None, self._frame_id
                self._condition.wait(remaining)

    def info(self) -> CameraInfo:
        with self._condition:
            return CameraInfo(
                index=self.index,
                name=f"Camera {self.index}",
                width=self._width,
                height=self._height,
                fps=self._fps,
                opened=self._capture is not None and not self._closed,
                clients=self._clients,
                blocked_by=self._blocked_by,
            )

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._stop.set()
            cap, self._capture = self._capture, None
            self._frame = None
            self._condition.notify_all()
        if cap is not None:
            try:
                with self._capture_lock:
                    cap.release()
            except Exception:
                pass
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2.0)


class _CaptureProxy:
    """Narrow legacy adapter; never hands raw capture ownership to a plugin."""

    def __init__(self, device: _Device) -> None:
        self._device = device

    def set(self, prop: int, value: float) -> bool:
        with self._device._condition:
            cap = self._device._capture
            if cap is None or self._device._closed:
                return False
            try:
                with self._device._capture_lock:
                    result = bool(cap.set(prop, value))
                    current = float(cap.get(prop) or value)
                # Keep diagnostics in sync for commonly queried properties.
                if prop == cv2.CAP_PROP_FRAME_WIDTH:
                    self._device._width = int(current)
                elif prop == cv2.CAP_PROP_FRAME_HEIGHT:
                    self._device._height = int(current)
                elif prop == cv2.CAP_PROP_FPS:
                    self._device._fps = current
                return result
            except Exception as exc:
                self._device._last_error = f"Capture property update failed: {type(exc).__name__}: {exc}"
                return False

    def get(self, prop: int) -> float:
        with self._device._condition:
            cap = self._device._capture
            if cap is None or self._device._closed:
                return 0.0
            try:
                with self._device._capture_lock:
                    return float(cap.get(prop) or 0.0)
            except Exception:
                return 0.0

    def isOpened(self) -> bool:
        return self._device.info().opened


class Camera:
    """A lease on the shared camera stream.

    `index=None` uses the configured device. Explicit indices are useful for
    diagnostics/tests. Each instance owns one lease and must be closed.
    """

    def __init__(self, index: Optional[int] = None, *, fast: bool = False,
                 want: Optional[tuple[int, int, int]] = None) -> None:
        """Acquire a shared camera lease.

        ``fast`` is accepted for compatibility with the imported plugin API.
        The shared manager owns backend selection; it deliberately does not
        promise a separate Media Foundation fast-open path. ``want`` requests
        width, height and FPS on the shared capture (best effort).
        """
        self._closed = False
        self._last_frame_id: Optional[int] = None
        self.how = "shared"
        selected = _configured_index() if index is None else _validate_index(index)
        self._device = _manager.acquire(selected)
        self._cap_proxy = _CaptureProxy(self._device)
        if want is not None:
            if (not isinstance(want, (tuple, list)) or len(want) != 3
                    or any(isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0 for v in want)):
                self.close()
                raise ValueError("want must be a (width, height, fps) tuple of positive numbers")
            self._device.configure(int(want[0]), int(want[1]), float(want[2]))

    @property
    def cap(self):
        """Compatibility proxy for legacy ``cam.cap.set/get`` calls.

        It never exposes the raw VideoCapture object, preserving ownership by
        the shared camera manager.
        """
        return self._cap_proxy

    def open(self) -> bool:
        """Legacy readiness check; opening is performed when the lease is acquired."""
        return not self._closed and self._device.info().opened

    @property
    def index(self) -> int:
        return self._device.index

    @property
    def is_opened(self) -> bool:
        return self._device.info().opened

    @property
    def last_error(self) -> Optional[str]:
        return self._device._last_error

    @property
    def blocked_by(self) -> Optional[str]:
        return self._device.info().blocked_by

    def read(self, timeout: float = 0.0) -> Optional[np.ndarray]:
        """Get the newest BGR frame, or None while unavailable."""
        if self._closed:
            return None
        frame, frame_id = self._device.read(self._last_frame_id, timeout)
        if frame is not None:
            self._last_frame_id = frame_id
        return frame

    def read_latest(self) -> Optional[np.ndarray]:
        """Return the latest frame without requiring it to be newer."""
        if self._closed:
            return None
        frame, frame_id = self._device.read(None, 0.0)
        if frame is not None:
            self._last_frame_id = frame_id
        return frame

    def info(self) -> CameraInfo:
        return self._device.info()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _manager.release(self._device)

    release = close

    def __enter__(self) -> "Camera":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __del__(self):  # pragma: no cover - best-effort safety net
        try:
            self.close()
        except Exception:
            pass


class _CameraManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._devices: dict[int, _Device] = {}

    def acquire(self, index: int) -> _Device:
        with self._lock:
            device = self._devices.get(index)
            if device is None or device._closed:
                device = _Device(index)
                self._devices[index] = device
            device.acquire()
            return device

    def release(self, device: _Device) -> None:
        with self._lock:
            device.release()
            if device._closed and self._devices.get(device.index) is device:
                self._devices.pop(device.index, None)

    def close_all(self) -> None:
        with self._lock:
            devices = list(self._devices.values())
            self._devices.clear()
        for device in devices:
            device.close()

    def info(self) -> tuple[CameraInfo, ...]:
        with self._lock:
            return tuple(device.info() for device in self._devices.values())


_manager = _CameraManager()


def _validate_index(index: int) -> int:
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError("Camera index must be a non-negative integer")
    return index


def _configured_index() -> int:
    """Read the camera index used by JARVIS' existing webcam settings."""
    try:
        from core.runtime_paths import PATHS
        import json

        config = json.loads(PATHS.api_config_file.read_text(encoding="utf-8"))
        return _validate_index(int(config.get("camera_index", 0)))
    except FileNotFoundError:
        return 0
    except (OSError, ValueError, TypeError):
        return 0


def get_camera(index: Optional[int] = None) -> Camera:
    """Factory alias for callers that prefer an explicit function."""
    return Camera(index=index)


def list_cameras(max_index: int = 5) -> list[CameraInfo]:
    """Probe camera indices and return metadata for devices that open.

    Probing is intentionally synchronous and should only be used from a
    settings/diagnostic action, never from the frame-processing loop.
    """
    max_index = max(0, min(int(max_index), 16))
    found: list[CameraInfo] = []
    for index in range(max_index + 1):
        probe = Camera(index=index)
        try:
            deadline = time.monotonic() + 1.5
            frame = None
            while time.monotonic() < deadline and frame is None:
                frame = probe.read(timeout=0.1)
            if frame is not None:
                found.append(probe.info())
        finally:
            probe.close()
    return found


def chosen() -> Optional[int]:
    """Return the camera selected in JARVIS device settings.

    Camera indices are used by the current AUDIO & CAMERA configuration.
    If a future device picker stores a stable camera name, it should resolve
    that name to an index before saving it; OpenCV itself does not enumerate
    stable device names portably.
    """
    try:
        from core.runtime_paths import PATHS
        import json

        config = json.loads(PATHS.api_config_file.read_text(encoding="utf-8"))
        value = config.get("camera_index")
        if value in (None, ""):
            return None
        return _validate_index(int(value))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return None


def in_use_by() -> Optional[str]:
    """Return a known process name holding a selected camera, if available.

    OpenCV cannot identify an external process that owns a webcam handle.
    Never guess a process name; platform-specific ownership diagnostics can
    populate this in a later implementation.
    """
    for info in _manager.info():
        if info.blocked_by:
            return info.blocked_by
    return None


def active_cameras() -> tuple[CameraInfo, ...]:
    return _manager.info()


def close_all() -> None:
    """Release all shared devices; call during application shutdown."""
    _manager.close_all()
