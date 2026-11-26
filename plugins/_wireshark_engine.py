"""Wireshark/TShark integration using bounded streaming subprocess I/O.

TShark performs dissection. JARVIS consumes one tab-separated metadata row at a
time; it never stores packet payloads or a complete TShark output buffer.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from plugins._network_intelligence_core import PacketRecord, analyzer

MAX_CAPTURE_FILE_BYTES = 2 * 1024 * 1024 * 1024
MAX_FILE_PACKETS = 50_000
FINDING_CHECK_INTERVAL = 15.0
_FIELDS = (
    "frame.time_epoch", "ip.src", "ipv6.src", "ip.dst", "ipv6.dst",
    "tcp.srcport", "udp.srcport", "tcp.dstport", "udp.dstport",
    "_ws.col.Protocol", "frame.len", "tcp.flags.syn", "tcp.flags.ack",
    "dns.qry.name",
)


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


def _int_or_none(value: str) -> int | None:
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def _record_from_fields(line: str) -> PacketRecord | None:
    """Convert one TShark -T fields row into a small metadata record."""
    values = line.rstrip("\r\n").split("\t")
    values.extend([""] * (len(_FIELDS) - len(values)))
    if len(values) < len(_FIELDS):
        return None
    (timestamp, src4, src6, dst4, dst6, tcp_src, udp_src, tcp_dst,
     udp_dst, protocol, length, syn, ack, dns) = values[:len(_FIELDS)]
    src_ip, dst_ip = src4 or src6, dst4 or dst6
    if not src_ip or not dst_ip:
        return None
    try:
        packet_time = float(timestamp)
    except (TypeError, ValueError):
        packet_time = time.time()
    protocol = (protocol or "OTHER").upper()[:32]
    flags = ("S" if syn == "1" else "") + ("A" if ack == "1" else "")
    return PacketRecord(
        timestamp=packet_time,
        src_ip=src_ip,
        dst_ip=dst_ip,
        src_port=_int_or_none(tcp_src or udp_src),
        dst_port=_int_or_none(tcp_dst or udp_dst),
        protocol=protocol,
        length=_int_or_none(length) or 0,
        tcp_flags=flags,
        dns_query=(dns or "").rstrip(".").lower()[:253],
    )


def _command_base(tshark: str) -> list[str]:
    return [
        tshark, "-n", "-T", "fields",
        "-E", "separator=\\t", "-E", "occurrence=f", "-E", "quote=n",
        *[arg for field in _FIELDS for arg in ("-e", field)],
    ]


class WiresharkEngine:
    def __init__(self):
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._stderr_file = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._interface = ""
        self._filter = ""
        self._last_error = ""
        self._last_packet_at = 0.0

    def status(self) -> dict:
        with self._lock:
            process = self._process
            running = bool(
                self._thread and self._thread.is_alive()
                and process is not None and process.poll() is None
                and not self._stop.is_set()
            )
            return {
                "running": running,
                "interface": self._interface,
                "bpf_filter": self._filter,
                "last_error": self._last_error,
                "last_packet_at": self._last_packet_at,
                "pid": process.pid if process and process.poll() is None else None,
                "tshark": find_tshark(),
                "wireshark": find_wireshark(),
            }

    def _require_tshark(self) -> str:
        tshark = find_tshark()
        if not tshark:
            raise RuntimeError(
                "TShark not found. Install Wireshark including TShark, or set "
                "the TShark executable path in Network Intelligence settings."
            )
        return tshark

    @staticmethod
    def _spawn(command: list[str], stderr_file):
        return subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            close_fds=(os.name != "nt"),
        )

    @staticmethod
    def _terminate(process: subprocess.Popen | None) -> None:
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    def analyze_file(self, path: str, max_packets: int = MAX_FILE_PACKETS) -> int:
        tshark = self._require_tshark()
        capture_path = Path(path).expanduser().resolve()
        if not capture_path.is_file():
            raise FileNotFoundError(f"Capture file does not exist: {capture_path}")
        if capture_path.stat().st_size > MAX_CAPTURE_FILE_BYTES:
            raise ValueError("Capture exceeds the 2 GiB safety limit.")
        limit = max(1, min(int(max_packets), MAX_FILE_PACKETS))
        analyzer().clear(source=str(capture_path))
        command = _command_base(tshark) + ["-r", str(capture_path), "-c", str(limit)]
        count = 0
        processed = 0
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stderr_file:
            process = self._spawn(command, stderr_file)
            try:
                assert process.stdout is not None
                for line in process.stdout:
                    processed += 1
                    record = _record_from_fields(line)
                    if record is not None:
                        analyzer().add_record(record)
                        count += 1
                return_code = process.wait(timeout=30)
            except BaseException:
                self._terminate(process)
                raise
            finally:
                if process.stdout is not None:
                    process.stdout.close()
            stderr_file.seek(0)
            error_text = stderr_file.read()[-4000:].strip()
        if return_code != 0:
            detail = error_text or f"TShark exited with code {return_code}."
            raise RuntimeError(f"TShark failed while reading the capture: {detail}")
        if not count:
            raise ValueError(
                "TShark completed but found no IPv4/IPv6 packets. "
                + (error_text or "Check that the selected file is a valid PCAP/PCAPNG.")
            )
        return count

    def start_background(self, interface: str = "", bpf_filter: str = "",
                         on_finding: Callable[[dict], None] | None = None) -> str:
        tshark = self._require_tshark()
        with self._lock:
            if self.status()["running"]:
                return f"Background monitoring is already running on {self._interface or 'default interface'}."
            if self._thread and self._thread.is_alive():
                raise RuntimeError("Previous TShark capture is still shutting down.")
            self._stop = threading.Event()
            stop = self._stop
            self._interface = interface.strip()
            self._filter = bpf_filter.strip()[:512]
            self._last_error = ""
            self._last_packet_at = 0.0
            analyzer().clear(source=f"wireshark-live:{self._interface or 'default'}")
            ready = threading.Event()
            startup_error: list[BaseException] = []
            self._thread = threading.Thread(
                target=self._capture_loop,
                args=(tshark, self._interface, self._filter, stop,
                      on_finding, ready, startup_error),
                name="jarvis-tshark-monitor",
                daemon=True,
            )
            thread = self._thread
            thread.start()
        if not ready.wait(timeout=10):
            stop.set()
            raise RuntimeError("Timed out while starting the TShark process.")
        if startup_error:
            raise RuntimeError(f"Could not start TShark capture: {startup_error[0]}") from startup_error[0]
        state = self.status()
        if not state["running"]:
            raise RuntimeError(state["last_error"] or "TShark exited before capture became active.")
        return f"TShark monitoring started on {self._interface or 'default interface'} (PID {state['pid']})."

    def _capture_loop(self, tshark: str, interface: str, bpf_filter: str,
                      stop: threading.Event, on_finding, ready: threading.Event,
                      startup_error: list[BaseException]) -> None:
        command = _command_base(tshark)
        command.extend(["-i", interface or "any"])
        if bpf_filter:
            command.extend(["-f", bpf_filter])
        stderr_file = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
        process = None
        seen_findings: set[tuple] = set()
        last_check = time.monotonic()
        try:
            process = self._spawn(command, stderr_file)
            with self._lock:
                self._process = process
                self._stderr_file = stderr_file
            ready.set()
            assert process.stdout is not None
            for line in process.stdout:
                if stop.is_set():
                    break
                record = _record_from_fields(line)
                if record is not None:
                    analyzer().add_record(record)
                    with self._lock:
                        self._last_packet_at = time.time()
                if on_finding and time.monotonic() - last_check >= FINDING_CHECK_INTERVAL:
                    last_check = time.monotonic()
                    for finding in analyzer().snapshot().suspicious:
                        key = (finding.get("type"), finding.get("source"), finding.get("details"))
                        if key not in seen_findings:
                            if len(seen_findings) >= 512:
                                seen_findings.clear()
                            seen_findings.add(key)
                            on_finding(finding)
            if stop.is_set():
                self._terminate(process)
            else:
                return_code = process.wait()
                if return_code:
                    stderr_file.seek(0)
                    detail = stderr_file.read()[-4000:].strip()
                    raise RuntimeError(detail or f"TShark exited with code {return_code}.")
        except Exception as exc:
            with self._lock:
                self._last_error = f"{type(exc).__name__}: {exc}"
            if not ready.is_set():
                startup_error.append(exc)
        finally:
            ready.set()
            if process is not None:
                self._terminate(process)
                if process.stdout is not None:
                    process.stdout.close()
            stderr_file.close()
            with self._lock:
                if self._process is process:
                    self._process = None
                self._stderr_file = None

    def stop_background(self) -> str:
        with self._lock:
            thread, process = self._thread, self._process
            if not thread or not thread.is_alive():
                self._thread = None
                self._process = None
                return "Background monitoring is not running."
            self._stop.set()
        self._terminate(process)
        if thread is not threading.current_thread():
            thread.join(timeout=10)
        if thread.is_alive():
            return "Stop requested, but TShark has not exited yet; its worker is retained."
        with self._lock:
            self._thread = None
            self._process = None
        return f"Background monitoring stopped. {analyzer().snapshot().captured_packets:,} packets observed."

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
        subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=(os.name != "nt"),
        )
        return "Wireshark GUI launched."


_ENGINE = WiresharkEngine()


def engine() -> WiresharkEngine:
    return _ENGINE
