"""
plugins/_printer_core.py — shared 3D-printer connectivity layer for the JARVIS
printer plugin suite.

The leading underscore matters: core/plugin_loader.py SKIPS files starting with
"_", so this is treated as a shared helper, never loaded as a plugin. It will
not appear in the Plugin Manager, and that is correct — but it MUST be
downloaded alongside printer_control.py, in the same plugins/ folder, or that
plugin cannot load. The real plugins import from it:

    from plugins._printer_core import get_active_backend, PrinterStatus

Design goal — support ALL Bambu + ALL network-capable Creality by PROTOCOL
FAMILY, not per-model code:

  • Bambu Lab       -> local MQTT (mqtts://<ip>:8883, user 'bblp',
                       password = 8-digit access code, topics keyed by serial).
                       One backend covers EVERY Bambu model (A1 … P2S … H2D).
  • Creality/Klipper-> Moonraker HTTP (http://<ip>:7125). One backend covers
                       K1/K2/Ender-3 V3 KE and any Klipper host. (Stubbed for a
                       later phase — the interface is here so plugins can be
                       written against it now.)

Credentials live ONLY in config/api_keys.json (never in code, never hard-coded).
Nothing here is proprietary — these are the printers' documented local endpoints.

Third-party dependency: `paho-mqtt` (for Bambu). It is imported lazily inside
connect() so a missing install can never stop this module from importing or the
plugin from loading — the user just gets a friendly "please install" message.

    pip install paho-mqtt
"""
from __future__ import annotations

import json
import socket
import ssl
import threading
import time
import uuid
from dataclasses import dataclass, field

from memory.config_manager import get_plugin_config, save_plugin_config

# Shared config namespace, so that if more printer plugins are ever added a user
# still enters their connection details only once. As of today the suite is one
# plugin — printer_control — and this file is its only companion.
# Values are entered through the generic plugin-settings UI — the
# plugins declare PLUGIN_SETTINGS with this same "namespace", so one form drives
# all of them. Stored at config/api_keys.json > plugin_config > "printer".
PRINTER_NS = "printer"

# When the stored IP doesn't answer (the printer often boots late and DHCP hands
# it a slightly different address), scan these last-octet values on the same /24
# before giving up. Inclusive range — default 100..106.
_IP_SCAN_LO = 100
_IP_SCAN_HI = 106


def get_active_printer() -> dict | None:
    """The configured printer, or None if the user hasn't filled it in yet.

    Returns a normalised dict:
        {"brand": "bambu", "model": "P2S", "ip": "192.168.1.50",
         "access_code": "12345678", "serial": "01P00A..."}
    Credentials come straight from config (entered via the settings UI) — never
    logged, never embedded in code.
    """
    cfg = get_plugin_config(PRINTER_NS)
    ip = (cfg.get("ip") or "").strip()
    if not ip:
        return None
    return {
        "brand":       (cfg.get("brand") or "bambu").strip().lower(),
        "model":       (cfg.get("model") or "").strip(),
        "ip":          ip,
        "access_code": (cfg.get("access_code") or "").strip(),
        "serial":      (cfg.get("serial") or "").strip(),
    }


def save_printer(brand: str, model: str, ip: str, access_code: str = "",
                 serial: str = "") -> None:
    """Convenience writer (the settings UI can also write the namespace directly
    via config_manager.save_plugin_config)."""
    save_plugin_config(PRINTER_NS, {
        "brand":       (brand or "").strip().lower(),
        "model":       (model or "").strip(),
        "ip":          (ip or "").strip(),
        "access_code": (access_code or "").strip(),
        "serial":      (serial or "").strip(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Status model — a printer-agnostic snapshot the plugins and Gemini read from.
# Every field is Optional so a backend fills in only what it actually knows.
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class PrinterStatus:
    state:          str          = "unknown"   # printing|paused|idle|finished|failed|offline|unknown
    progress:       float | None = None        # 0..100
    layer:          int   | None = None
    total_layers:   int   | None = None
    remaining_min:  int   | None = None
    nozzle_temp:    float | None = None
    nozzle_target:  float | None = None
    bed_temp:       float | None = None
    bed_target:     float | None = None
    chamber_temp:   float | None = None
    file_name:      str   | None = None
    error_code:     str   | None = None        # non-empty => printer is reporting an error
    raw:            dict         = field(default_factory=dict)

    def one_line(self) -> str:
        """A compact English summary; JARVIS re-speaks it in the user's language."""
        if self.state == "offline":
            return "The printer is offline or unreachable."
        bits = [f"state={self.state}"]
        if self.progress is not None:
            bits.append(f"{self.progress:.0f}% done")
        if self.layer is not None and self.total_layers:
            bits.append(f"layer {self.layer}/{self.total_layers}")
        if self.remaining_min is not None:
            bits.append(f"~{self.remaining_min} min left")
        if self.nozzle_temp is not None:
            bits.append(f"nozzle {self.nozzle_temp:.0f}°C")
        if self.bed_temp is not None:
            bits.append(f"bed {self.bed_temp:.0f}°C")
        if self.chamber_temp is not None:
            bits.append(f"chamber {self.chamber_temp:.0f}°C")
        if self.file_name:
            bits.append(f'file "{self.file_name}"')
        if self.error_code:
            bits.append(f"ERROR {self.error_code}")
        return ", ".join(bits)


# ─────────────────────────────────────────────────────────────────────────────
# Abstract backend — the single interface every protocol implements. Plugins
# only ever touch these methods; they never know if it's Bambu or Klipper.
# ─────────────────────────────────────────────────────────────────────────────
class PrinterBackend:
    brand = "generic"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def connect(self, timeout: float = 8.0) -> bool:
        raise NotImplementedError

    def disconnect(self) -> None:
        pass

    @property
    def connected(self) -> bool:
        return False

    def get_status(self) -> PrinterStatus:
        raise NotImplementedError

    def pause(self)  -> bool: raise NotImplementedError
    def resume(self) -> bool: raise NotImplementedError
    def stop(self)   -> bool: raise NotImplementedError
    def set_light(self, on: bool) -> bool: raise NotImplementedError

    # Extended control (full voice control). Each protocol implements these in
    # its own dialect; the plugins only ever call these semantic methods.
    def set_temperature(self, target: str, celsius: int) -> bool:
        """target: 'nozzle' or 'bed'. celsius 0 = heater off."""
        raise NotImplementedError

    def set_fan(self, on: bool, percent: int = 100) -> bool:
        raise NotImplementedError

    def set_speed(self, level: int) -> bool:
        """level 1=silent, 2=standard, 3=sport, 4=ludicrous."""
        raise NotImplementedError

    def home(self) -> bool:
        raise NotImplementedError

    def gcode(self, line: str) -> bool:
        """Escape hatch — send a raw G-code line."""
        raise NotImplementedError

    def camera_feed(self) -> "CameraFeed":
        """The printer's own camera as a stream of JPEG frames. Raises a
        PrinterError whose text says why when there is none to open."""
        raise PrinterError("this printer has no camera I know how to open.")


# ─────────────────────────────────────────────────────────────────────────────
# Bambu Lab — local MQTT. Covers every Bambu model.
# ─────────────────────────────────────────────────────────────────────────────
class BambuBackend(PrinterBackend):
    brand = "bambu"

    _GCODE_STATE = {
        "RUNNING":  "printing",
        "PAUSE":    "paused",
        "IDLE":     "idle",
        "FINISH":   "finished",
        "FAILED":   "failed",
        "PREPARE":  "printing",
        "SLICING":  "printing",
    }

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self._client = None
        self._connected = False
        self._report: dict = {}                 # merged partial reports from the printer
        self._lock = threading.Lock()
        self._got_data = threading.Event()

    # -- topics --
    @property
    def _serial(self) -> str:
        return (self.cfg.get("serial") or "").strip()

    @property
    def _topic_report(self)  -> str: return f"device/{self._serial}/report"
    @property
    def _topic_request(self) -> str: return f"device/{self._serial}/request"

    @staticmethod
    def _seq() -> str:
        return uuid.uuid4().hex[:8]

    def connect(self, timeout: float = 8.0) -> bool:
        ip   = (self.cfg.get("ip") or "").strip()
        code = (self.cfg.get("access_code") or "").strip()
        if not ip or not code or not self._serial:
            raise PrinterConfigError(
                "Bambu needs IP, 8-digit access code, and serial number "
                "(set them in the printer settings panel)."
            )

        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            raise PrinterDependencyError(
                "the 'paho-mqtt' package is not installed — run: pip install paho-mqtt"
            )

        # paho 2.x requires an explicit callback-API version; fall back for 1.x.
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)  # type: ignore[attr-defined]
        except (AttributeError, TypeError):
            client = mqtt.Client()

        client.username_pw_set("bblp", code)
        # Bambu presents a self-signed certificate on the local broker.
        client.tls_set(cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect

        self._client = client
        self._got_data.clear()
        try:
            client.connect(ip, 8883, keepalive=60)
        except Exception as e:
            self._client = None
            raise PrinterConnectionError(f"could not reach the printer at {ip}: {e}")
        client.loop_start()

        # Wait for the first full status snapshot (pushall reply).
        self._got_data.wait(timeout=timeout)
        return self._connected

    def disconnect(self) -> None:
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
        self._client = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    # -- paho callbacks --
    def _on_connect(self, client, userdata, flags, rc, *args):
        if rc == 0:
            self._connected = True
            client.subscribe(self._topic_report)
            self._request_full_status()
        else:
            self._connected = False

    def _on_disconnect(self, client, userdata, rc, *args):
        self._connected = False

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            return
        # Bambu sends partial updates; merge the "print" section so we keep a
        # complete picture even when a message carries only a few changed keys.
        with self._lock:
            for section, body in payload.items():
                if isinstance(body, dict):
                    self._report.setdefault(section, {}).update(body)
                else:
                    self._report[section] = body
        self._got_data.set()

    # -- commands --
    def _publish(self, obj: dict) -> bool:
        if self._client is None or not self._connected:
            return False
        try:
            self._client.publish(self._topic_request, json.dumps(obj))
            return True
        except Exception:
            return False

    def _request_full_status(self) -> bool:
        return self._publish({"pushing": {"sequence_id": self._seq(),
                                          "command": "pushall"}})

    def pause(self) -> bool:
        return self._publish({"print": {"sequence_id": self._seq(), "command": "pause"}})

    def resume(self) -> bool:
        return self._publish({"print": {"sequence_id": self._seq(), "command": "resume"}})

    def stop(self) -> bool:
        return self._publish({"print": {"sequence_id": self._seq(), "command": "stop"}})

    def set_light(self, on: bool) -> bool:
        return self._publish({"system": {
            "sequence_id": self._seq(),
            "command": "ledctrl",
            "led_node": "chamber_light",
            "led_mode": "on" if on else "off",
            "led_on_time": 500, "led_off_time": 500,
            "loop_times": 0, "interval_time": 0,
        }})

    def gcode(self, line: str) -> bool:
        return self._publish({"print": {"sequence_id": self._seq(),
                                        "command": "gcode_line",
                                        "param": f"{line}\n"}})

    def set_temperature(self, target: str, celsius: int) -> bool:
        t = max(0, int(celsius))
        if target == "nozzle":
            return self.gcode(f"M104 S{t}")
        if target == "bed":
            return self.gcode(f"M140 S{t}")
        return False

    def set_fan(self, on: bool, percent: int = 100) -> bool:
        # Part-cooling fan (index P1 on Bambu). 0-100% mapped to 0-255.
        if not on:
            return self.gcode("M106 P1 S0")
        v = max(0, min(255, round(int(percent) * 255 / 100)))
        return self.gcode(f"M106 P1 S{v}")

    def set_speed(self, level: int) -> bool:
        lvl = int(level)
        if lvl not in (1, 2, 3, 4):
            return False
        return self._publish({"print": {"sequence_id": self._seq(),
                                        "command": "print_speed",
                                        "param": str(lvl)}})

    def home(self) -> bool:
        return self.gcode("G28")

    # -- camera --
    def light_is_on(self) -> bool | None:
        """Chamber light as last reported, or None if the printer never said."""
        with self._lock:
            lights = self._report.get("print", {}).get("lights_report")
        for node in lights if isinstance(lights, list) else []:
            if isinstance(node, dict) and node.get("node") == "chamber_light":
                return str(node.get("mode", "")).lower() == "on"
        return None

    def camera_feed(self) -> "CameraFeed":
        """Bambu has two camera dialects, and the printer says which it speaks:
        models that report an `ipcam.rtsp_url` (X1, H2, P2 series) serve RTSP
        over TLS on :322; the rest (P1, A1) serve bare JPEGs over TLS on :6000."""
        with self._lock:
            cam = dict(self._report.get("print", {}).get("ipcam") or {})
        ip   = (self.cfg.get("ip") or "").strip()
        code = (self.cfg.get("access_code") or "").strip()
        if str(cam.get("ipcam_dev", "1")) == "0":
            raise PrinterError("the printer reports that it has no camera.")
        url = str(cam.get("rtsp_url") or "").strip()
        if url.lower() == "disable":
            raise PrinterError(
                "the printer's LAN live view is switched off — turn on "
                "'LAN Only Liveview' in the printer's own settings, then ask again.")
        if url.lower().startswith("rtsp"):
            # The printer reports the URL without credentials; the password is
            # the same access code as MQTT. Never log the result.
            scheme, rest = url.split("://", 1)
            rest = rest.split("@", 1)[-1]
            return RtspFeed(f"{scheme}://bblp:{code}@{rest}")
        return BambuJpegFeed(ip, code)

    # -- status --
    def get_status(self) -> PrinterStatus:
        if not self._connected:
            return PrinterStatus(state="offline")
        with self._lock:
            p = dict(self._report.get("print", {}))

        def _f(key):
            v = p.get(key)
            return float(v) if isinstance(v, (int, float)) else None

        def _i(key):
            v = p.get(key)
            return int(v) if isinstance(v, (int, float)) else None

        gcode_state = str(p.get("gcode_state", "")).upper()
        err = p.get("print_error")
        error_code = str(err) if isinstance(err, int) and err != 0 else None

        return PrinterStatus(
            state         = self._GCODE_STATE.get(gcode_state, "idle" if not gcode_state else "unknown"),
            progress      = _f("mc_percent"),
            layer         = _i("layer_num"),
            total_layers  = _i("total_layer_num"),
            remaining_min = _i("mc_remaining_time"),
            nozzle_temp   = _f("nozzle_temper"),
            nozzle_target = _f("nozzle_target_temper"),
            bed_temp      = _f("bed_temper"),
            bed_target    = _f("bed_target_temper"),
            chamber_temp  = _f("chamber_temper"),
            file_name     = p.get("subtask_name") or p.get("gcode_file") or None,
            error_code    = error_code,
            raw           = p,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Creality / Klipper — Moonraker HTTP. Interface only for now; the concrete
# implementation lands in a later phase (developed against a virtual Klipper).
# ─────────────────────────────────────────────────────────────────────────────
class KlipperBackend(PrinterBackend):
    brand = "creality"

    def connect(self, timeout: float = 8.0) -> bool:
        raise PrinterConnectionError(
            "Creality/Klipper support is coming in a later phase — "
            "only Bambu Lab is wired up for now."
        )

    def get_status(self) -> PrinterStatus:
        return PrinterStatus(state="offline")

    def camera_feed(self) -> "CameraFeed":
        """Moonraker lists the host's webcams, so ask it first; fall back to
        the address stock Creality firmware serves its camera on. Needs no
        control connection, which is why the camera works before the rest of
        this backend does."""
        ip = (self.cfg.get("ip") or "").strip()
        if not ip:
            raise PrinterConfigError("no printer address is set.")
        import requests
        base = f"http://{ip}"
        try:
            r = requests.get(f"{base}:7125/server/webcams/list", timeout=4)
            cams = (r.json().get("result") or {}).get("webcams") or []
        except Exception:
            cams = []
        for cam in cams:
            if cam.get("enabled") is False:
                continue
            service = str(cam.get("service") or "").lower()
            stream, snap = cam.get("stream_url") or "", cam.get("snapshot_url") or ""
            absolute = lambda u: u if "://" in u else base + ("" if u.startswith("/") else "/") + u
            if stream and service.startswith("mjpeg"):
                return MjpegFeed(absolute(stream))
            if snap:
                return SnapshotFeed(absolute(snap))
        return MjpegFeed(f"{base}:8080/?action=stream")


# ─────────────────────────────────────────────────────────────────────────────
# Camera feeds — each turns one camera dialect into an iterator of JPEG bytes,
# so whatever shows them never learns which printer it is looking at.
# ─────────────────────────────────────────────────────────────────────────────
_CAM_MAX_WIDTH = 1280     # a 1080p frame is downscaled; the HUD is never wider


class CameraFeed:
    def open(self) -> None:
        """Connect, or raise PrinterConnectionError / PrinterDependencyError."""
        raise NotImplementedError

    def frames(self, max_fps: float = 20.0):
        """Yield JPEG bytes until the source ends or close() is called. Sources
        that can't go faster than `max_fps` are free to ignore it."""
        raise NotImplementedError

    def close(self) -> None:
        pass


def _jpeg(frame) -> bytes | None:
    import cv2
    h, w = frame.shape[:2]
    if w > _CAM_MAX_WIDTH:
        frame = cv2.resize(frame, (_CAM_MAX_WIDTH, round(h * _CAM_MAX_WIDTH / w)),
                           interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return buf.tobytes() if ok else None


class RtspFeed(CameraFeed):
    """RTSP(S) through OpenCV's bundled FFmpeg. Every frame is read as it
    arrives — a reader that falls behind watches the past — but only as many
    as `max_fps` allows are re-encoded and handed on."""

    def __init__(self, url: str):
        self._url = url
        self._cap = None

    def open(self) -> None:
        try:
            import cv2
        except ImportError:
            raise PrinterDependencyError(
                "the 'opencv-python' package is not installed — run: pip install opencv-python")
        try:
            cap = cv2.VideoCapture(self._url, cv2.CAP_FFMPEG,
                                   [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 10000,
                                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, 8000])
        except TypeError:           # OpenCV older than 4.6 takes no params
            cap = cv2.VideoCapture(self._url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            cap.release()
            raise PrinterConnectionError("the camera stream did not open.")
        self._cap = cap

    def frames(self, max_fps: float = 20.0):
        cap, gap, due = self._cap, 1.0 / max_fps, 0.0
        while cap is not None and cap.isOpened():
            ok, frame = cap.read()
            if not ok or frame is None:
                return
            now = time.time()
            if now < due:
                continue
            due = now + gap
            img = _jpeg(frame)
            if img:
                yield img

    def close(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
        self._cap = None


class BambuJpegFeed(CameraFeed):
    """Bambu P1 / A1: TLS to :6000, an 80-byte login, then each frame is a
    16-byte header (payload length in the first three bytes, little-endian)
    followed by one complete JPEG."""

    def __init__(self, ip: str, code: str):
        self._ip, self._code = ip, code
        self._sock = None

    def open(self) -> None:
        import struct
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE          # self-signed, like the MQTT broker
        try:
            raw = socket.create_connection((self._ip, 6000), timeout=8)
            self._sock = ctx.wrap_socket(raw, server_hostname=self._ip)
            self._sock.sendall(struct.pack("<IIII", 0x40, 0x3000, 0, 0)
                               + b"bblp".ljust(32, b"\0")
                               + self._code.encode("ascii", "ignore").ljust(32, b"\0"))
        except Exception as e:
            self.close()
            raise PrinterConnectionError(f"the camera did not answer: {e}")

    def _read(self, n: int) -> bytes | None:
        buf = bytearray()
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return bytes(buf)

    def frames(self, max_fps: float = 20.0):
        try:
            while self._sock is not None:
                head = self._read(16)
                if head is None:
                    return
                size = int.from_bytes(head[0:3], "little")
                body = self._read(size) if 0 < size < 8_000_000 else None
                if body is None:
                    return
                if body[:2] == b"\xff\xd8":
                    yield body
                elif size < 64:
                    return          # a short non-image reply is a refusal
        except (OSError, ValueError):
            return

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
        self._sock = None


class MjpegFeed(CameraFeed):
    """multipart/x-mixed-replace over HTTP (mjpg-streamer, crowsnest, most
    Klipper and OctoPrint cameras): cut the byte stream at JPEG markers."""

    def __init__(self, url: str):
        self._url = url
        self._resp = None

    def open(self) -> None:
        import requests
        try:
            self._resp = requests.get(self._url, stream=True, timeout=(5, 10))
            self._resp.raise_for_status()
        except Exception as e:
            self.close()
            raise PrinterConnectionError(f"the camera did not answer: {e}")

    def frames(self, max_fps: float = 20.0):
        buf = b""
        try:
            for chunk in self._resp.iter_content(chunk_size=32768):
                buf += chunk
                while True:
                    start = buf.find(b"\xff\xd8")
                    end = buf.find(b"\xff\xd9", start + 2) if start >= 0 else -1
                    if end < 0:
                        break
                    yield buf[start:end + 2]
                    buf = buf[end + 2:]
                if len(buf) > 8_000_000:
                    buf = b""
        except Exception:
            return

    def close(self) -> None:
        if self._resp is not None:
            try:
                self._resp.close()
            except Exception:
                pass
        self._resp = None


class SnapshotFeed(CameraFeed):
    """Cameras that only offer single pictures (WebRTC-only streamers expose
    a snapshot URL too): poll it a few times a second."""

    def __init__(self, url: str, fps: float = 4.0):
        self._url, self._gap = url, 1.0 / fps
        self._open = False

    def _grab(self) -> bytes | None:
        import requests
        r = requests.get(self._url, timeout=5)
        return r.content if r.ok and r.content[:2] == b"\xff\xd8" else None

    def open(self) -> None:
        try:
            if self._grab() is None:
                raise ValueError("no picture came back")
        except Exception as e:
            raise PrinterConnectionError(f"the camera did not answer: {e}")
        self._open = True

    def frames(self, max_fps: float = 20.0):
        while self._open:
            t = time.time()
            try:
                img = self._grab()
            except Exception:
                return
            if img:
                yield img
            time.sleep(max(0.0, self._gap - (time.time() - t)))

    def close(self) -> None:
        self._open = False


def open_camera(player=None) -> tuple["CameraFeed", PrinterBackend | None]:
    """An OPENED camera feed for the configured printer, plus the connected
    control backend when there is one (for status readouts and the light).
    Raises a PrinterError subclass, worded for the user, on failure."""
    cfg = get_active_printer()
    if not cfg:
        raise PrinterConfigError(
            "no printer is set up yet — open the printer settings panel and add one.")
    if cfg["brand"] in ("creality", "klipper"):
        backend, feed = None, build_backend(cfg).camera_feed()
    else:
        backend = get_active_backend(player=player)
        feed = backend.camera_feed()
    feed.open()
    return feed, backend


# ─────────────────────────────────────────────────────────────────────────────
# Errors — plugins catch these to turn them into friendly spoken strings.
# ─────────────────────────────────────────────────────────────────────────────
class PrinterError(Exception):            pass
class PrinterConfigError(PrinterError):   pass      # missing/invalid settings
class PrinterConnectionError(PrinterError): pass    # network/handshake failure
class PrinterDependencyError(PrinterError): pass    # a python package isn't installed


# ─────────────────────────────────────────────────────────────────────────────
# Factory + connection cache. An MQTT link is worth keeping alive between voice
# commands, so we cache one connected backend per (brand, ip, serial) identity.
# ─────────────────────────────────────────────────────────────────────────────
_BACKENDS = {
    "bambu":    BambuBackend,
    "creality": KlipperBackend,
    "klipper":  KlipperBackend,
}

_active_backend: PrinterBackend | None = None
_active_key: tuple | None = None
_active_lock = threading.Lock()


def _key(cfg: dict) -> tuple:
    return (cfg.get("brand", ""), cfg.get("ip", ""), cfg.get("serial", ""))


def build_backend(cfg: dict) -> PrinterBackend:
    """Create (but do not connect) the right backend for a printer config."""
    brand = (cfg.get("brand") or "").strip().lower()
    cls = _BACKENDS.get(brand)
    if cls is None:
        raise PrinterConfigError(
            f"unknown printer brand '{brand}' — supported: bambu, creality/klipper."
        )
    return cls(cfg)


def _probe_port(cfg: dict, timeout: float = 1.5) -> bool:
    """Fast TCP reachability check for the printer's control port, so IP scanning
    doesn't hang on dead addresses. Bambu = 8883 (MQTT/TLS), Klipper = 7125."""
    ip = (cfg.get("ip") or "").strip()
    if not ip:
        return False
    brand = (cfg.get("brand") or "bambu").strip().lower()
    port = 7125 if brand in ("creality", "klipper") else 8883
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def _connect_cfg(cfg: dict, timeout: float = 8.0, probe_timeout: float = 2.0) -> PrinterBackend:
    """Build + connect a backend for exactly this cfg (port-probed first so an
    unreachable address fails fast). Returns a connected backend or raises."""
    if not _probe_port(cfg, probe_timeout):
        raise PrinterConnectionError(f"no response from {cfg.get('ip')}")
    backend = build_backend(cfg)
    try:
        ok = backend.connect(timeout=timeout)
    except PrinterError:
        backend.disconnect()
        raise
    if not ok:
        backend.disconnect()
        raise PrinterConnectionError(
            "connected to the printer but no status arrived — "
            "check the access code and that the printer is powered on."
        )
    return backend


def _ip_scan_candidates(ip: str, lo: int = _IP_SCAN_LO, hi: int = _IP_SCAN_HI) -> list[str]:
    """Sibling IPs on the same /24 with the last octet in [lo, hi], excluding the
    stored one (it just failed). Empty if `ip` isn't a plain IPv4 we can vary."""
    parts = (ip or "").split(".")
    if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return []
    prefix, stored_last = ".".join(parts[:3]), int(parts[3])
    return [f"{prefix}.{n}" for n in range(lo, hi + 1) if n != stored_last]


def _announce_retry(player) -> None:
    """One-time spoken heads-up while we scan (JARVIS phrases it in the user's
    language, e.g. 'tekrar deniyorum efendim'). Safe no-op without a player."""
    if player is None:
        return
    fn = getattr(player, "request_say", None)
    if callable(fn):
        try:
            fn("You couldn't reach the 3D printer at its usual address. Tell the "
               "user briefly that you're trying again on nearby addresses. Keep it "
               "to one short sentence.")
        except Exception:
            pass


def get_active_backend(reconnect: bool = False, player=None) -> PrinterBackend:
    """Return a CONNECTED backend for the active printer, reusing a live link
    when possible. If the stored IP doesn't answer, scan last-octets 100..106 on
    the same /24 (the printer may have booted late onto a different DHCP address),
    announcing the retry once via `player`. A found address is saved so the next
    command connects straight away. Raises a PrinterError subclass on failure.
    """
    cfg = get_active_printer()
    if not cfg:
        raise PrinterConfigError(
            "no printer is set up yet — open the printer settings panel and add one."
        )

    global _active_backend, _active_key
    with _active_lock:
        want = _key(cfg)
        if (not reconnect and _active_backend is not None
                and _active_key == want and _active_backend.connected):
            return _active_backend

        # Drop any stale/other connection before opening a fresh one.
        if _active_backend is not None:
            _active_backend.disconnect()
            _active_backend = None
            _active_key = None

        # 1) Try the stored address.
        try:
            backend = _connect_cfg(cfg)
        except PrinterConnectionError as first_err:
            # 2) It didn't answer — scan nearby addresses (100..106) and use the
            #    first that connects. Config/dependency errors are NOT scanned.
            candidates = _ip_scan_candidates(cfg.get("ip", ""))
            if not candidates:
                raise
            _announce_retry(player)
            backend = None
            for cand in candidates:
                try:
                    backend = _connect_cfg({**cfg, "ip": cand},
                                           timeout=6.0, probe_timeout=1.5)
                except PrinterError:
                    backend = None
                    continue
                cfg = {**cfg, "ip": cand}
                try:
                    save_plugin_config(PRINTER_NS, {"ip": cand})   # remember it
                except Exception:
                    pass
                break
            if backend is None:
                raise first_err

        _active_backend = backend
        _active_key = _key(cfg)
        return backend
