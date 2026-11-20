"""Typed sensor registry for optional JARVIS data sources.

The registry is deliberately dependency-free and does not start hardware itself.
Sensor providers own their resource lifecycle; the registry coordinates state,
capabilities, observability and safe stop requests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import threading
import time
from typing import Callable, Iterable


class SensorState(str, Enum):
    REGISTERED = "registered"
    STARTING = "starting"
    ACTIVE = "active"
    STOPPING = "stopping"
    ERROR = "error"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class SensorSpec:
    sensor_id: str
    kind: str
    capabilities: frozenset[str] = field(default_factory=frozenset)
    optional: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        if not self.sensor_id or not self.sensor_id.strip():
            raise ValueError("sensor_id must be a non-empty string")
        if not self.kind or not self.kind.strip():
            raise ValueError("kind must be a non-empty string")
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))


@dataclass(frozen=True, slots=True)
class SensorSnapshot:
    spec: SensorSpec
    state: SensorState
    registered_at: float
    changed_at: float
    error: str | None = None


@dataclass(slots=True)
class _Sensor:
    spec: SensorSpec
    stop_callback: Callable[[], object] | None
    state: SensorState = SensorState.REGISTERED
    registered_at: float = field(default_factory=time.time)
    changed_at: float = field(default_factory=time.time)
    error: str | None = None


class SensorRegistry:
    """Thread-safe registry with lifecycle transitions and observer support."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sensors: dict[str, _Sensor] = {}
        self._listeners: set[Callable[[tuple[SensorSnapshot, ...]], object]] = set()

    def register(
        self,
        spec: SensorSpec,
        *,
        stop: Callable[[], object] | None = None,
        replace: bool = False,
    ) -> SensorSnapshot:
        with self._lock:
            if spec.sensor_id in self._sensors and not replace:
                raise ValueError(f"Sensor already registered: {spec.sensor_id}")
            previous = self._sensors.get(spec.sensor_id)
            if previous and previous.state in {SensorState.ACTIVE, SensorState.STARTING, SensorState.STOPPING}:
                raise RuntimeError(f"Cannot replace running sensor: {spec.sensor_id}")
            sensor = _Sensor(spec=spec, stop_callback=stop)
            self._sensors[spec.sensor_id] = sensor
            snapshot = self._snapshot(sensor)
        self._notify()
        return snapshot

    def unregister(self, sensor_id: str) -> bool:
        with self._lock:
            sensor = self._sensors.get(sensor_id)
            if sensor is None:
                return False
            if sensor.state in {SensorState.ACTIVE, SensorState.STARTING, SensorState.STOPPING}:
                raise RuntimeError(f"Stop sensor before unregistering: {sensor_id}")
            del self._sensors[sensor_id]
        self._notify()
        return True

    def transition(self, sensor_id: str, state: SensorState, *, error: str | None = None) -> SensorSnapshot:
        allowed = {
            SensorState.REGISTERED: {SensorState.STARTING, SensorState.DISABLED, SensorState.ERROR},
            SensorState.STARTING: {SensorState.ACTIVE, SensorState.REGISTERED, SensorState.ERROR, SensorState.DISABLED},
            SensorState.ACTIVE: {SensorState.STOPPING, SensorState.ERROR, SensorState.DISABLED},
            SensorState.STOPPING: {SensorState.REGISTERED, SensorState.DISABLED, SensorState.ERROR},
            SensorState.ERROR: {SensorState.REGISTERED, SensorState.STARTING, SensorState.DISABLED},
            SensorState.DISABLED: {SensorState.REGISTERED, SensorState.STARTING},
        }
        with self._lock:
            sensor = self._sensors.get(sensor_id)
            if sensor is None:
                raise KeyError(sensor_id)
            if state not in allowed[sensor.state]:
                raise ValueError(f"Invalid sensor transition: {sensor.state.value} -> {state.value}")
            sensor.state = state
            sensor.error = str(error)[:1000] if error else None
            sensor.changed_at = time.time()
            snapshot = self._snapshot(sensor)
        self._notify()
        return snapshot

    def request_stop(self, sensor_id: str) -> bool:
        with self._lock:
            sensor = self._sensors.get(sensor_id)
            if sensor is None or sensor.state not in {SensorState.ACTIVE, SensorState.STARTING}:
                return False
            callback = sensor.stop_callback
            sensor.state = SensorState.STOPPING
            sensor.changed_at = time.time()
        self._notify()
        if callback is None:
            self.transition(sensor_id, SensorState.REGISTERED)
            return True
        try:
            callback()
        except Exception as exc:
            self.transition(sensor_id, SensorState.ERROR, error=f"{type(exc).__name__}: {exc}")
            return False
        return True

    def stop_all(self, *, kind: str | None = None) -> tuple[str, ...]:
        with self._lock:
            ids = tuple(
                sensor.spec.sensor_id
                for sensor in self._sensors.values()
                if sensor.state in {SensorState.ACTIVE, SensorState.STARTING}
                and (kind is None or sensor.spec.kind == kind)
            )
        stopped: list[str] = []
        for sensor_id in ids:
            if self.request_stop(sensor_id):
                stopped.append(sensor_id)
        return tuple(stopped)

    def get(self, sensor_id: str) -> SensorSnapshot | None:
        with self._lock:
            sensor = self._sensors.get(sensor_id)
            return self._snapshot(sensor) if sensor else None

    def snapshot(self, *, kind: str | None = None) -> tuple[SensorSnapshot, ...]:
        with self._lock:
            rows = [self._snapshot(sensor) for sensor in self._sensors.values()]
        if kind is not None:
            rows = [row for row in rows if row.spec.kind == kind]
        return tuple(rows)

    def find_by_capability(self, capability: str) -> tuple[SensorSnapshot, ...]:
        with self._lock:
            rows = [
                self._snapshot(sensor)
                for sensor in self._sensors.values()
                if capability in sensor.spec.capabilities
            ]
        return tuple(rows)

    def subscribe(self, listener: Callable[[tuple[SensorSnapshot, ...]], object]) -> Callable[[], None]:
        with self._lock:
            self._listeners.add(listener)
        def unsubscribe() -> None:
            with self._lock:
                self._listeners.discard(listener)
        return unsubscribe

    @staticmethod
    def _snapshot(sensor: _Sensor) -> SensorSnapshot:
        return SensorSnapshot(sensor.spec, sensor.state, sensor.registered_at, sensor.changed_at, sensor.error)

    def _notify(self) -> None:
        snapshot = self.snapshot()
        with self._lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(snapshot)
            except Exception:
                # Observers must not break sensor lifecycle.
                continue
