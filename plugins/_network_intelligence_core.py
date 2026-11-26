"""Network traffic analysis engine and optional JARVIS HUD dashboard.

The analysis layer is deterministic and can be tested without Qt or packet capture
privileges. Live capture is opt-in and only observes interfaces selected by the user.
"""
from __future__ import annotations

import collections
import ipaddress
import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

MAX_PACKETS = 50_000
SNAPSHOT_CACHE_SECONDS = 5.0
MAX_FLOWS = 50_000
MAX_DNS_NAMES = 20_000


@dataclass
class PacketRecord:
    timestamp: float
    src_ip: str
    dst_ip: str
    src_port: int | None
    dst_port: int | None
    protocol: str
    length: int
    tcp_flags: str = ""
    dns_query: str = ""


@dataclass
class AnalysisSnapshot:
    source: str
    captured_packets: int
    total_bytes: int
    unique_ips: int
    unique_ports: int
    unique_flows: int
    protocols: dict[str, int]
    top_sources: list[dict[str, Any]]
    top_destinations: list[dict[str, Any]]
    top_protocols: list[dict[str, Any]]
    rare_protocols: list[dict[str, Any]]
    rare_ips: list[dict[str, Any]]
    suspicious: list[dict[str, Any]]
    dns_top: list[dict[str, Any]]
    timeline: list[dict[str, Any]]
    started_at: float
    ended_at: float
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TrafficAnalyzer:
    """Bounded, deterministic packet analyzer; no packet payloads are retained."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: collections.deque[PacketRecord] = collections.deque(maxlen=MAX_PACKETS)
        self._source = "empty"
        self._started_at = 0.0
        self._ended_at = 0.0
        self._dropped = 0
        self._generation = 0
        self._snapshot_cache = None
        self._snapshot_cache_at = 0.0

    def clear(self, source: str = "empty") -> None:
        with self._lock:
            self._records.clear()
            self._source = source
            self._started_at = time.time()
            self._ended_at = self._started_at
            self._dropped = 0
            self._generation += 1
            self._snapshot_cache = None
            self._snapshot_cache_at = 0.0

    def add_record(self, record: PacketRecord) -> None:
        with self._lock:
            if not self._started_at:
                self._started_at = record.timestamp
            if len(self._records) == self._records.maxlen:
                self._dropped += 1
            self._records.append(record)
            self._ended_at = max(self._ended_at, record.timestamp)

    def load_pcap(self, path: str | os.PathLike[str], max_packets: int = MAX_PACKETS) -> int:
        """Dissect PCAP/PCAPNG through Wireshark's TShark engine."""
        from plugins._wireshark_engine import engine
        return engine().analyze_file(str(path), max_packets=max_packets)

    @staticmethod
    def _top(counter: collections.Counter, limit: int = 10) -> list[dict[str, Any]]:
        return [{"name": str(k), "count": int(v)} for k, v in counter.most_common(limit)]

    def snapshot(self) -> AnalysisSnapshot:
        now = time.monotonic()
        with self._lock:
            cached = self._snapshot_cache
            if cached is not None and now - self._snapshot_cache_at < SNAPSHOT_CACHE_SECONDS:
                return cached
            rows = list(self._records)
            source, started, ended, dropped = self._source, self._started_at, self._ended_at, self._dropped
            generation = self._generation
        protocols: collections.Counter = collections.Counter()
        src: collections.Counter = collections.Counter()
        dst: collections.Counter = collections.Counter()
        ports: set[int] = set()
        flows: collections.Counter = collections.Counter()
        dns: collections.Counter = collections.Counter()
        dst_ports_by_src: dict[str, set[int]] = collections.defaultdict(set)
        syn_only: collections.Counter = collections.Counter()
        buckets: collections.Counter = collections.Counter()
        total_bytes = 0
        for row in rows:
            protocols[row.protocol] += 1
            src[row.src_ip] += 1
            dst[row.dst_ip] += 1
            if row.src_port is not None: ports.add(row.src_port)
            if row.dst_port is not None: ports.add(row.dst_port)
            flows[(row.src_ip, row.src_port, row.dst_ip, row.dst_port, row.protocol)] += 1
            if row.dst_port is not None: dst_ports_by_src[row.src_ip].add(row.dst_port)
            if row.protocol == "TCP" and "S" in row.tcp_flags and "A" not in row.tcp_flags:
                syn_only[row.src_ip] += 1
            if row.dns_query: dns[row.dns_query] += 1
            buckets[int(row.timestamp // 10) * 10] += row.length
            total_bytes += row.length

        suspicious: list[dict[str, Any]] = []
        for ip, seen_ports in dst_ports_by_src.items():
            syn_count = syn_only[ip]
            if len(seen_ports) >= 20 or syn_count >= 30:
                suspicious.append({
                    "severity": "high" if len(seen_ports) >= 80 or syn_count >= 100 else "medium",
                    "type": "possible_port_scan", "source": ip,
                    "details": f"{len(seen_ports)} destination ports; {syn_count} SYN-without-ACK packets",
                    "evidence": {"unique_destination_ports": len(seen_ports), "syn_packets": syn_count},
                })
        for name, count in dns.most_common():
            if len(name) >= 55 and name.count(".") >= 4:
                suspicious.append({
                    "severity": "medium", "type": "unusually_long_dns_name",
                    "source": name, "details": f"DNS query length {len(name)}; inspect manually",
                    "evidence": {"query_length": len(name), "count": count},
                })
                if len(suspicious) >= 100: break

        rare_ips = [{"name": ip, "count": count} for ip, count in sorted(src.items(), key=lambda x: (x[1], x[0]))[:10]]
        rare_protocols = [{"name": p, "count": c} for p, c in sorted(protocols.items(), key=lambda x: (x[1], x[0]))[:10]]
        timeline = [{"timestamp": ts, "bytes": size} for ts, size in sorted(buckets.items())[-60:]]
        warnings = []
        if dropped: warnings.append(f"Bounded buffer evicted {dropped} oldest packets.")
        if len(rows) >= MAX_PACKETS: warnings.append("Analysis reached the in-memory packet limit; results may be partial.")
        result = AnalysisSnapshot(
            source=source, captured_packets=len(rows), total_bytes=total_bytes,
            unique_ips=len(set(src) | set(dst)), unique_ports=len(ports), unique_flows=len(flows),
            protocols=dict(protocols), top_sources=self._top(src), top_destinations=self._top(dst),
            top_protocols=self._top(protocols), rare_protocols=rare_protocols, rare_ips=rare_ips,
            suspicious=suspicious[:100], dns_top=self._top(dns), timeline=timeline,
            started_at=started, ended_at=ended, warnings=warnings,
        )
        with self._lock:
            if generation == self._generation:
                self._snapshot_cache = result
                self._snapshot_cache_at = time.monotonic()
        return result

    def answer(self, question: str) -> dict[str, Any]:
        """Map common analyst questions to deterministic metrics, never guess."""
        q = re.sub(r"\s+", " ", str(question or "").strip().lower())
        s = self.snapshot()
        if not s.captured_packets:
            return {"answer": "No traffic data is loaded. Import a PCAP/PCAPNG or start an authorised live capture.", "snapshot": s.to_dict()}
        if any(x in q for x in ("rare protocol", "rarest protocol", "самый редкий протокол", "самый редкий", "реже всего")) and ("protocol" in q or "протокол" in q or "rare" in q or "редк" in q):
            rows = s.rare_protocols
            answer = f"Rarest observed protocol: {rows[0]['name']} ({rows[0]['count']} packets)." if rows else "No protocol data."
        elif any(x in q for x in ("rare ip", "rarest ip", "least common ip", "самый редкий ip", "реже всего ip")):
            rows = s.rare_ips
            answer = f"Least frequent source IP: {rows[0]['name']} ({rows[0]['count']} packets)." if rows else "No IP data."
        elif any(x in q for x in ("strange", "suspicious", "anomal", "подозр", "стран", "необыч", "аномал")):
            answer = f"Found {len(s.suspicious)} heuristic finding(s). " + ("; ".join(f"{x['type']} from {x['source']}: {x['details']}" for x in s.suspicious[:5]) if s.suspicious else "No configured heuristic triggered; this does not prove the traffic is benign.")
        elif any(x in q for x in ("protocol", "протокол")):
            answer = "Protocol distribution: " + ", ".join(f"{x['name']} {x['count']}" for x in s.top_protocols[:10])
        elif any(x in q for x in ("port", "порт")):
            answer = f"Observed {s.unique_ports} unique transport ports across {s.unique_flows} flows."
        elif any(x in q for x in ("how many", "count", "сколько", "количество", "число")):
            answer = f"{s.captured_packets} packets, {s.total_bytes:,} bytes, {s.unique_ips} unique IPs, {s.unique_ports} unique ports and {s.unique_flows} unique flows."
        elif any(x in q for x in ("top", "most", "active", "част", "актив", "популяр", "чаще всего", "самый частый", "наиболее частый")):
            answer = "Most active source IPs: " + ", ".join(f"{x['name']} ({x['count']})" for x in s.top_sources[:5])
        else:
            answer = f"Capture summary: {s.captured_packets} packets, {s.total_bytes:,} bytes, {s.unique_ips} IPs, {s.unique_flows} flows. Top protocols: " + ", ".join(f"{x['name']} ({x['count']})" for x in s.top_protocols[:5])
        return {"answer": answer, "snapshot": s.to_dict()}


_ANALYZER = TrafficAnalyzer()
_SNIFFER = None
_SNIFFER_LOCK = threading.RLock()


def analyzer() -> TrafficAnalyzer:
    return _ANALYZER


def start_capture(interface: str = "", bpf_filter: str = "") -> str:
    """Start continuous background capture through Wireshark/TShark."""
    from plugins._wireshark_engine import engine
    return engine().start_background(interface, bpf_filter)


def stop_capture() -> str:
    """Stop the TShark background capture."""
    from plugins._wireshark_engine import engine
    return engine().stop_background()


def show_dashboard(player, question: str = "") -> bool:
    """Display a compact HUD dashboard through the shared Live Panels lifecycle."""
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import (QDialog, QGridLayout, QLabel, QPushButton,
                                     QTableWidget, QTableWidgetItem, QVBoxLayout)
        from plugins import _live_panels_core as panels
    except Exception:
        return False

    snap = _ANALYZER.snapshot()
    answer = _ANALYZER.answer(question)["answer"] if question else "Import a capture or start live monitoring to populate the dashboard."

    def open_dialog() -> bool:
        host = panels._host(player)
        if host is None:
            return False
        old = getattr(show_dashboard, "_dialog", None)
        if old is not None:
            try: old.close()
            except Exception: pass
        dlg = QDialog(host)
        dlg.setWindowTitle("JARVIS · Network Intelligence")
        dlg.setWindowFlags(Qt.WindowType.Widget)
        dlg.setStyleSheet("QDialog{background:#08121d;color:#e6f3ff;border:1px solid #24445b;} QLabel{color:#e6f3ff;} QTableWidget{background:#0d1b29;color:#e6f3ff;gridline-color:#24445b;border:1px solid #24445b;} QPushButton{background:#153047;color:#e6f3ff;border:1px solid #2a5873;border-radius:6px;padding:7px;}")
        layout = QVBoxLayout(dlg)
        title = QLabel("NETWORK INTELLIGENCE  ·  TRAFFIC OVERVIEW")
        title.setStyleSheet("font-size:18px;font-weight:700;color:#39d6ff;padding:8px;")
        layout.addWidget(title)
        grid = QGridLayout()
        metrics = [("PACKETS", f"{snap.captured_packets:,}"), ("BYTES", f"{snap.total_bytes:,}"),
                   ("UNIQUE IPs", str(snap.unique_ips)), ("PORTS", str(snap.unique_ports)),
                   ("FLOWS", str(snap.unique_flows)), ("FINDINGS", str(len(snap.suspicious)))]
        for i, (name, value) in enumerate(metrics):
            label = QLabel(f"{name}\n{value}")
            label.setStyleSheet("background:#10263a;border:1px solid #24445b;padding:10px;font-size:14px;")
            grid.addWidget(label, i // 3, i % 3)
        layout.addLayout(grid)
        summary = QLabel(answer)
        summary.setWordWrap(True)
        summary.setStyleSheet("padding:8px;color:#a9c5d8;")
        layout.addWidget(summary)
        traffic_table = QTableWidget(0, 3)
        traffic_table.setHorizontalHeaderLabels(["RANKING", "IP / PROTOCOL", "PACKETS"])
        for category, items in (("TOP SOURCE", snap.top_sources[:5]), ("PROTOCOL", snap.top_protocols[:5])):
            for item in items:
                row = traffic_table.rowCount(); traffic_table.insertRow(row)
                for col, value in enumerate((category, item["name"], item["count"])):
                    traffic_table.setItem(row, col, QTableWidgetItem(str(value)))
        traffic_table.resizeColumnsToContents()
        traffic_table.setMaximumHeight(190)
        layout.addWidget(traffic_table)
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["SEVERITY", "DETECTION", "SOURCE", "EVIDENCE"])
        for item in snap.suspicious[:30]:
            row = table.rowCount(); table.insertRow(row)
            for col, value in enumerate((item["severity"], item["type"], item["source"], item["details"])):
                table.setItem(row, col, QTableWidgetItem(str(value)))
        table.resizeColumnsToContents()
        layout.addWidget(table, 1)
        close = QPushButton("CLOSE")
        close.clicked.connect(dlg.close)
        layout.addWidget(close)
        dlg.setGeometry(host.rect())
        dlg.show(); dlg.raise_()
        show_dashboard._dialog = dlg
        return True

    return bool(panels._gui(open_dialog))
