"""Background system telemetry sampler used by the monitor and HUD."""

import ctypes
import platform
import subprocess
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
        self.cpu_freq_mhz = -1.0
        self.cpu_cores_physical = psutil.cpu_count(logical=False) or 0
        self.cpu_cores_logical = psutil.cpu_count(logical=True) or 0
        memory = psutil.virtual_memory()
        self.mem_total_bytes = int(memory.total)
        self.mem_available_bytes = int(memory.available)
        self.net_upload_bps = 0.0
        self.net_download_bps = 0.0
        self.gpu_memory_used_mb = -1.0
        self.gpu_memory_total_mb = -1.0
        self.gpu_temp = -1.0
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
        memory = psutil.virtual_memory()
        mem = memory.percent
        try:
            cpu_freq = psutil.cpu_freq()
            cpu_freq_mhz = float(cpu_freq.current) if cpu_freq else -1.0
        except Exception:
            # Frequency probing is optional and not supported on every host.
            # Do not let a platform-specific psutil error suppress other data.
            cpu_freq_mhz = -1.0

        nc = psutil.net_io_counters()
        now = time.time()
        dt = now - self._last_net_t
        if dt > 0:
            upload_bps = max(0.0, (nc.bytes_sent - self._last_net.bytes_sent) / dt)
            download_bps = max(0.0, (nc.bytes_recv - self._last_net.bytes_recv) / dt)
            net = (upload_bps + download_bps) / (1024 * 1024)
        else:
            upload_bps = download_bps = net = 0.0
        self._last_net = nc
        self._last_net_t = now

        # Process enumeration can briefly block on Windows. Keep it off the UI
        # thread and refresh it every 10 seconds.
        self._process_tick = (self._process_tick + 1) % 5
        process_count = self.process_count
        if self._process_tick == 1:
            try:
                process_count = len(psutil.pids())
            except Exception:
                pass

        # GPU and temperature probes are relatively expensive; refresh them
        # every third cycle and reuse the last readings between probes.
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
            self.cpu_freq_mhz = cpu_freq_mhz
            self.mem_total_bytes = int(memory.total)
            self.mem_available_bytes = int(memory.available)
            self.net_upload_bps = upload_bps
            self.net_download_bps = download_bps

    def _read_nvidia_smi(self) -> tuple[float, float, float, float] | None:
        """Return GPU utilization, used/total VRAM and temperature in MB/°C."""
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
                creationflags=(
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    if _OS == "Windows"
                    else 0
                ),
            )
            if result.returncode != 0 or not result.stdout.strip():
                return None
            fields = [value.strip() for value in result.stdout.splitlines()[0].split(",")]
            if len(fields) < 4:
                return None
            values = [float(value) for value in fields[:4]]
            return values[0], values[1], values[2], values[3]
        except (OSError, ValueError, subprocess.SubprocessError):
            return None

    def _set_gpu_details(
        self, used_mb: float, total_mb: float, temperature_c: float
    ) -> None:
        """Publish the related GPU readings as one snapshot under the sampler lock."""
        with self._lock:
            self.gpu_memory_used_mb = float(used_mb)
            self.gpu_memory_total_mb = float(total_mb)
            self.gpu_temp = float(temperature_c)

    def _get_gpu(self) -> float:
        # Windows GPU providers can enter COM/NVML code that is not safe to
        # call from the sampler thread. Keep this probe disabled on Windows;
        # CPU, memory, network and process metrics remain available.
        if _OS == "Windows":
            return -1.0

        # nvidia-smi works on Linux/macOS desktop drivers and headless systems.
        smi = self._read_nvidia_smi()
        if smi is not None:
            gpu, used, total, temperature = smi
            self._set_gpu_details(used, total, temperature)
            return gpu

        # pynvml — initialise once and reuse its device handle.
        if self._pynvml_ok is not False:
            try:
                if self._pynvml_h is None:
                    import pynvml  # type: ignore

                    pynvml.nvmlInit()
                    self._pynvml = pynvml
                    self._pynvml_h = pynvml.nvmlDeviceGetHandleByIndex(0)
                    self._pynvml_ok = True
                usage = float(
                    self._pynvml.nvmlDeviceGetUtilizationRates(self._pynvml_h).gpu
                )
                try:
                    memory = self._pynvml.nvmlDeviceGetMemoryInfo(self._pynvml_h)
                    used_mb = memory.used / (1024 * 1024)
                    total_mb = memory.total / (1024 * 1024)
                except Exception:
                    used_mb = total_mb = -1.0
                try:
                    temperature = float(
                        self._pynvml.nvmlDeviceGetTemperature(
                            self._pynvml_h,
                            self._pynvml.NVML_TEMPERATURE_GPU,
                        )
                    )
                except Exception:
                    temperature = -1.0
                self._set_gpu_details(used_mb, total_mb, temperature)
                return usage
            except Exception:
                self._pynvml_ok = False

        # Linux / macOS fallback: libnvidia-ml shared library via ctypes.
        try:
            class _Util(ctypes.Structure):
                _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

            if self._nv_unix is None:
                library = "libnvidia-ml.so.1" if _OS == "Linux" else "libnvidia-ml.dylib"
                nv = ctypes.CDLL(library)
                nv.nvmlInit_v2()
                device = ctypes.c_void_p()
                nv.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(device))
                self._nv_unix = (nv, device)

            nv, device = self._nv_unix
            usage = _Util()
            nv.nvmlDeviceGetUtilizationRates(device, ctypes.byref(usage))
            self._set_gpu_details(-1.0, -1.0, -1.0)
            return float(usage.gpu)
        except Exception:
            self._set_gpu_details(-1.0, -1.0, -1.0)
            return -1.0

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
                "cpu_freq_mhz": self.cpu_freq_mhz,
                "cpu_cores_physical": self.cpu_cores_physical,
                "cpu_cores_logical": self.cpu_cores_logical,
                "mem_total_bytes": self.mem_total_bytes,
                "mem_available_bytes": self.mem_available_bytes,
                "net_upload_bps": self.net_upload_bps,
                "net_download_bps": self.net_download_bps,
                "gpu_memory_used_mb": self.gpu_memory_used_mb,
                "gpu_memory_total_mb": self.gpu_memory_total_mb,
                "gpu_temp": self.gpu_temp,
            }
