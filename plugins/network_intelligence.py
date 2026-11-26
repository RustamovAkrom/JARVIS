"""Autonomous network traffic analysis for JARVIS."""
from __future__ import annotations

import json
from pathlib import Path

PLUGIN = {
    "name": "network_intelligence",
    "description": (
        "Advanced network traffic analyst for cybersecurity specialists. Import and analyse "
        "PCAP/PCAPNG files, start/stop explicitly requested live capture, show the Network "
        "Intelligence dashboard, and answer questions about packet counts, bytes, unique IPs, "
        "ports, flows, most/least frequent IPs and protocols, DNS queries, top talkers, and "
        "heuristic anomalies such as possible port scans. Use this tool for network traffic, "
        "Wireshark, PCAP, packet, protocol, suspicious connection and traffic-statistics questions. "
        "Never claim an attack is confirmed from heuristics alone."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "'analyze_file', 'ask', 'dashboard', 'start_capture', 'stop_capture', 'status'"},
            "path": {"type": "STRING", "description": "Absolute or user-selected path to a PCAP/PCAPNG file."},
            "question": {"type": "STRING", "description": "Natural-language analyst question about the currently loaded traffic."},
            "interface": {"type": "STRING", "description": "Explicit network interface name for authorised live capture."},
            "bpf_filter": {"type": "STRING", "description": "Optional BPF capture filter, e.g. 'tcp or udp'."},
            "max_packets": {"type": "INTEGER", "description": "Maximum packets to read from a file, capped at 250000."},
        },
        "required": ["action"],
    },
    "behavior": "BLOCKING",
    "tier": "extra",
    "brief": "Analyse PCAP/live traffic, answer network statistics questions, and show the network dashboard.",
}

_NS = "network_intelligence"
PLUGIN_SETTINGS = {
    "namespace": _NS,
    "title": "NETWORK INTELLIGENCE",
    "fields": [
        {"key": "max_packets", "label": "Maximum packets per file", "type": "number", "default": 250000},
        {"key": "allow_live_capture", "label": "Allow live capture (requires OS capture permissions)", "type": "boolean", "default": False},
    ],
}


def _core():
    try:
        from plugins import _network_intelligence_core as core
    except ImportError:
        import _network_intelligence_core as core
    return core


def run(parameters: dict, player=None, session_memory=None) -> str:
    p = dict(parameters or {})
    action = str(p.get("action") or "status").strip().lower()
    try:
        core = _core()
        if action in {"analyze_file", "load", "open_pcap"}:
            path = str(p.get("path") or "").strip()
            if not path:
                return "Please provide the PCAP/PCAPNG file path."
            max_packets = max(1, min(int(p.get("max_packets") or 250000), 250000))
            count = core.analyzer().load_pcap(path, max_packets=max_packets)
            result = core.analyzer().answer("give me a traffic summary")
            _show(player, f"Loaded {count:,} packets from {Path(path).name}.")
            return f"Loaded {count:,} packets. {result['answer']} Open the Network Intelligence dashboard to inspect findings."
        if action in {"start_capture", "capture_start"}:
            try:
                from memory.config_manager import get_plugin_setting
                allowed = bool(get_plugin_setting(_NS, "allow_live_capture", False))
            except Exception:
                allowed = False
            if not allowed:
                return "Live capture is disabled. Enable 'Allow live capture' in JARVIS plugin settings first. Only monitor interfaces and networks you are authorised to inspect."
            message = core.start_capture(str(p.get("interface") or ""), str(p.get("bpf_filter") or ""))
            _show(player, message)
            return message
        if action in {"stop_capture", "capture_stop"}:
            message = core.stop_capture()
            _show(player, message)
            return message
        if action in {"dashboard", "show_dashboard", "open"}:
            question = str(p.get("question") or "")
            shown = core.show_dashboard(player, question)
            return "Network Intelligence dashboard opened." if shown else "Could not open the HUD dashboard. Ensure the JARVIS HUD and Live Panels plugin are available."
        if action in {"ask", "question", "analyze", "status"}:
            question = str(p.get("question") or "Summarise the loaded network traffic.")
            result = core.analyzer().answer(question)
            answer = result["answer"]
            _show(player, answer)
            # Use the configured JARVIS model for explanation only; all metrics/findings
            # originate in the deterministic analysis engine.
            try:
                from core import gemini
                prompt = (
                    "You are a cautious network-forensics assistant. Explain the following "
                    "measured traffic analysis in the user's language. Do not invent facts, "
                    "do not claim an attack is confirmed, distinguish heuristic indicators "
                    "from evidence, and mention limitations.\n"
                    f"Question: {question}\nMeasured analysis: {json.dumps(result['snapshot'], ensure_ascii=False)[:18000]}\n"
                    f"Deterministic result: {answer}"
                )
                explanation = gemini.text(prompt, tier=gemini.SMART, timeout_ms=45000)
                if explanation and str(explanation).strip():
                    return str(explanation).strip()
            except Exception:
                pass
            return answer
        return "Unknown action. Use analyze_file, ask, dashboard, start_capture, stop_capture or status."
    except FileNotFoundError as exc:
        return f"Capture file not found: {exc}"
    except ImportError as exc:
        return f"Network Intelligence dependency is missing: {exc}. Run 'uv sync' in the JARVIS environment."
    except Exception as exc:
        return f"Network Intelligence failed safely: {type(exc).__name__}: {exc}"


def _show(player, message: str) -> None:
    if player is not None:
        try:
            player.write_log(f"JARVIS · Network Intelligence: {message}")
        except Exception:
            pass
