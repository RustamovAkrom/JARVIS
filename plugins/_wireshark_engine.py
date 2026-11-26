"""Wireshark/TShark integration for JARVIS Network Intelligence.

PyShark is the Python adapter; TShark performs packet dissection. The GUI is
launched separately so background capture never depends on a visible window.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from plugins._network_intelligence_core import PacketRecord, analyzer

MAX_CAPTURE_FILE_BYTES = 2 * 1024 * 1024 * 1024
MAX_FILE_PACKETS = 50_000
FINDING_CHECK_INTERVAL = 15.0


def _configured_path(key: str) -> str | None:
    try:
        from memory.config_manager import get_plugin_setting
        value = get_plugin_setting("network_intelligence", key, "")
        return str(value).strip() or None
    except Exception:
        return None


def find_tshark() -> str | None:
    configured = _configured_path("tshark_path")
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which("tshark") or shutil.which("tshark.exe")
    if found:
        return found
    if os.name == "nt":
        candidates = [
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Wireshark" / "tshark.exe",
            Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Wireshark" / "tshark.exe",
        ]
        return next((str(p) for p in candidates if p.is_file()), None)
    return None


def find_wireshark() -> str | None:
    configured = _configured_path("wireshark_path")
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which("wireshark") or shutil.which("Wireshark.exe")
    if found:
        return found
    if os.name == "nt":
        candidates = [
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Wireshark" / "Wireshark.exe",
            Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Wireshark" / "Wireshark.exe",
        ]
        return next((str(p) for p in candidates if p.is_file()), None)
    return None


def _packet_record(packet) -> PacketRecord | None:
    ip = getattr(packet, "ip", None)
    ipv6 = getattr(packet, "ipv6", None)
    layer = ip or ipv6
    if layer is None:
        return None
    tcp = getattr(packet, "tcp", None)
    udp = getattr(packet, "udp", None)
    transport = tcp or udp
    src_port = _int_or_none(getattr(transport, "srcport", None)) if transport else None
    dst_port = _int_or_none(getattr(transport, "dstport", None)) if transport else None
    flags = ""
    if tcp:
        if str(getattr(tcp, "flags_syn", "0")) == "1":
            flags += "S"
        if str(getattr(tcp, "flags_ack", "0")) == "1":
            flags += "A"
    dns = getattr(packet, "dns", None)
    query = str(getattr(dns, "qry_name", "") or "").rstrip(".").lower()[:253] if dns else ""
    protocol = str(getattr(packet, "highest_layer", None) or getattr(packet, "transport_layer", None) or "OTHER").upper()
    try:
        timestamp = float(packet.sniff_timestamp)
    except (AttributeError, TypeError, ValueError):
        timestamp = time.time()
    return PacketRecord(
        timestamp=timestamp,
        src_ip=str(getattr(layer, "src", "")),
        dst_ip=str(getattr(layer, "dst", "")),
        src_port=src_port,
        dst_port=dst_port,
        protocol=protocol[:32],
        length=_int_or_none(getattr(packet, "length", None)) or 0,
        tcp_flags=flags,
        dns_query=query,
    )


def _int_or_none(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class WiresharkEngine:
    def __init__(self):
        self._lock = threading.RLock()
        self._capture = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._interface = ""
        self._filter = ""
        self._last_error = ""

    def status(self) -> dict:
        with self._lock:
            running = bool(self._thread and self._thread.is_alive() and not self._stop.is_set())
            return {
                "running": running,
                "interface": self._interface,
                "bpf_filter": self._filter,
                "last_error": self._last_error,
                "tshark": find_tshark(),
                "wireshark": find_wireshark(),
            }

    def _pyshark(self):
        tshark = find_tshark()
        if not tshark:
            raise RuntimeError("TShark not found. Install Wireshark (including TShark) or set its executable path in Network Intelligence settings.")
        try:
            import pyshark
        except ImportError as exc:
            raise RuntimeError("PyShark is missing. Run 'uv sync' and restart JARVIS.") from exc
        return pyshark, tshark

    def analyze_file(self, path: str, max_packets: int = 250_000) -> int:
        pyshark, tshark = self._pyshark()
        capture_path = Path(path).expanduser().resolve()
        if not capture_path.is_file():
            raise FileNotFoundError(f"Capture file does not exist: {capture_path}")
        if capture_path.stat().st_size > MAX_CAPTURE_FILE_BYTES:
            raise ValueError("Capture exceeds the 2 GiB safety limit.")
        analyzer().clear(source=str(capture_path))
        count = 0
        processed = 0
        limit = max(1, min(int(max_packets), MAX_FILE_PACKETS))
        cap = pyshark.FileCapture(
            str(capture_path), tshark_path=tshark, keep_packets=False,
            use_json=True, include_raw=False, custom_parameters={"-n": ""},
        )
        try:
            for packet in cap:
                processed += 1
                if processed > limit:
                    break
                record = _packet_record(packet)
                if record is not None and record.src_ip and record.dst_ip:
                    analyzer().add_record(record)
                    count += 1
        finally:
            cap.close()
        if not count:
            raise ValueError("TShark found no IPv4/IPv6 packets in this capture.")
        return count

    def start_background(self, interface: str = "", bpf_filter: str = "",
                         on_finding: Callable[[dict], None] | None = None) -> str:
        pyshark, tshark = self._pyshark()
        with self._lock:
            if self._thread and self._thread.is_alive() and not self._stop.is_set():
                return f"Background monitoring is already running on {self._interface or 'default interface'}."
            self._stop = threading.Event()
            self._interface = interface.strip()
            self._filter = bpf_filter.strip()[:512]
            self._last_error = ""
            analyzer().clear(source=f"wireshark-live:{self._interface or 'default'}")
            capture = pyshark.LiveCapture(
                interface=self._interface or None,
                bpf_filter=self._filter or None,
                tshark_path=tshark,
                keep_packets=False,
                use_json=True,
                custom_parameters={"-n": "", "-o": "tcp.desegment_tcp_streams:false"},
            )
            self._capture = capture
            self._thread = threading.Thread(
                target=self._capture_loop, args=(capture, self._stop, on_finding),
                name="jarvis-tshark-monitor", daemon=True,
            )
            self._thread.start()
        return f"Wireshark/TShark background monitoring started on {self._interface or 'default interface'}."

    def _capture_loop(self, capture, stop: threading.Event, on_finding) -> None:
        seen_findings: set[tuple] = set()
        last_check = time.monotonic()
        try:
            for packet in capture.sniff_continuously():
                if stop.is_set():
                    break
                record = _packet_record(packet)
                if record is not None and record.src_ip and record.dst_ip:
                    analyzer().add_record(record)
                if on_finding and time.monotonic() - last_check >= FINDING_CHECK_INTERVAL:
                    last_check = time.monotonic()
                    for finding in analyzer().snapshot().suspicious:
                        key = (finding.get("type"), finding.get("source"), finding.get("details"))
                        if key not in seen_findings:
                            seen_findings.add(key)
                            on_finding(finding)
        except Exception as exc:
            with self._lock:
                self._last_error = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                capture.close()
            except Exception:
                pass

    def stop_background(self) -> str:
        with self._lock:
            thread, capture = self._thread, self._capture
            if not thread or not thread.is_alive():
                self._thread = None
                self._capture = None
                return "Background monitoring is not running."
            self._stop.set()
        try:
            capture.close()
        except Exception:
            pass
        if thread is not threading.current_thread():
            thread.join(timeout=10)
        if thread.is_alive():
            return "Stop requested, but TShark has not exited yet. The capture handle is retained so it can be stopped safely."
        with self._lock:
            self._thread = None
            self._capture = None
        return f"Background monitoring stopped. {analyzer().snapshot().captured_packets:,} packets retained."

    def launch_gui(self, path: str | None = None, interface: str = "", start_capture: bool = False) -> str:
        executable = find_wireshark()
        if not executable:
            raise RuntimeError("Wireshark GUI not found. Install Wireshark or set its executable path in plugin settings.")
        args = [executable]
        if path:
            capture_path = Path(path).expanduser().resolve()
            if not capture_path.is_file():
                raise FileNotFoundError(f"Capture file does not exist: {capture_path}")
            args.extend(["-r", str(capture_path)])
        elif start_capture:
            args.append("-k")
            if interface:
                args.extend(["-i", interface])
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=(os.name != "nt"))
        return "Wireshark GUI launched."


_ENGINE = WiresharkEngine()


def engine() -> WiresharkEngine:
    return _ENGINE
