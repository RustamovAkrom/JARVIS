"""Background system telemetry sampler used by the monitor and HUD."""

import ctypes
import platform
import threading
import time

import psutil

_OS = platform.system()


class _SysMetrics:
    def __init__(self):
        self.cpu = 0.0
        self.mem = 0.0
        self.net = 0.0
        self.gpu = -1.0
        self.tmp = -1.0
        self.process_count = 0
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        # Probe caches — GPU and temperature are refreshed less often than
        # CPU/memory/network metrics to keep the monitor lightweight.
        self._slow_tick = 0  # gpu/temp refreshed every 3rd cycle
        self._process_tick = 0
        self._pynvml = None  # cached pynvml module + device handle
        self._pynvml_h = None
        self._pynvml_ok = None  # None=untested, False=unavailable here
        self._nv_unix = None  # cached (lib, dev) for Linux/macOS NVML
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(2.0)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc = psutil.net_io_counters()
        now = time.time()
        dt = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net = nc
        self._last_net_t = now

        # Process enumeration can briefly block on Windows. Keep it off the UI
        # thread and refresh it every 10 seconds; the dashboard does not need a
        # fresh count on every telemetry frame.
        self._process_tick = (self._process_tick + 1) % 5
        process_count = self.process_count
        if self._process_tick == 1:
            try:
                process_count = len(psutil.pids())
            except Exception:
                pass

        # GPU and temperature change slowly and are the most expensive probes
        # (NVML / WMI) — refresh them every 3rd cycle (~6 s) instead of every
        # cycle, reusing the previous reading in between.
        self._slow_tick = (self._slow_tick + 1) % 3
        if self._slow_tick == 1:
            gpu = self._get_gpu()
            tmp = self._get_temp()
        else:
            gpu = self.gpu
            tmp = self.tmp

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp
            self.process_count = process_count

    def _get_gpu(self) -> float:
        if _OS == "Windows":
            # NVML and subprocess probes can crash some Windows/Python builds
            # from this background polling thread; keep startup and monitoring
            # stable rather than risk taking down the entire process.
            return -1.0

        # pynvml — subprocess-free; initialise once and reuse the handle.
        # Re-initialising NVML on every poll is slow, so cache it and stop
        # retrying pynvml entirely once it proves unavailable here.
        if self._pynvml_ok is not False:
            try:
                if self._pynvml_h is None:
                    import pynvml  # type: ignore

                    pynvml.nvmlInit()
                    self._pynvml = pynvml
                    self._pynvml_h = pynvml.nvmlDeviceGetHandleByIndex(0)
                    self._pynvml_ok = True
                return float(
                    self._pynvml.nvmlDeviceGetUtilizationRates(self._pynvml_h).gpu
                )
            except Exception:
                self._pynvml_ok = False

        # Linux / macOS: libnvidia-ml shared lib via ctypes — init once, reuse
        try:
            import ctypes

            class _Util(ctypes.Structure):
                _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

            if self._nv_unix is None:
                _lib = "libnvidia-ml.so.1" if _OS == "Linux" else "libnvidia-ml.dylib"
                nv = ctypes.CDLL(_lib)
                nv.nvmlInit_v2()
                dev = ctypes.c_void_p()
                nv.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
                self._nv_unix = (nv, dev)

            nv, dev = self._nv_unix
            u = _Util()
            nv.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
            return float(u.gpu)
        except Exception:
            pass

        return -1.0  # N/A — zero subprocess on all platforms

    def _get_temp(self) -> float:
        # psutil — works on Linux; occasionally Windows with driver support
        try:
            temps = psutil.sensors_temperatures()
            for name in [
                "coretemp",
                "k10temp",
                "cpu_thermal",
                "acpitz",
                "cpu-thermal",
                "zenpower",
                "it8688",
            ]:
                if name in temps and temps[name]:
                    return temps[name][0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass

        # Windows WMI requires COM apartment management that is not safe from
        # this lightweight polling thread on all supported Python builds.
        return -1.0  # N/A — zero subprocess on all platforms

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
                "process_count": self.process_count,
            }
