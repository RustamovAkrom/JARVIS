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
            "action": {"type": "STRING", "description": "'analyze_file', 'ask', 'dashboard', 'start_capture', 'stop_capture', 'start_monitor', 'stop_monitor', 'launch_wireshark', 'open_in_wireshark', 'status'"},
            "path": {"type": "STRING", "description": "Absolute or user-selected path to a PCAP/PCAPNG file."},
            "question": {"type": "STRING", "description": "Natural-language analyst question about the currently loaded traffic."},
            "interface": {"type": "STRING", "description": "Explicit network interface name for authorised live capture or Wireshark GUI."},
            "bpf_filter": {"type": "STRING", "description": "Optional BPF capture filter, e.g. 'tcp or udp'."},
            "max_packets": {"type": "INTEGER", "description": "Maximum packets to inspect from a file, capped at 50000."},
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
        {"key": "max_packets", "label": "Maximum packets per file", "type": "number", "default": 50000},
        {"key": "allow_live_capture", "label": "Allow live capture (requires OS capture permissions)", "type": "boolean", "default": False},
        {"key": "background_monitoring", "label": "Start authorised background monitoring when JARVIS launches", "type": "boolean", "default": False},
        {"key": "interface", "label": "Capture interface (blank = system default)", "type": "text", "default": ""},
        {"key": "tshark_path", "label": "Custom TShark executable path", "type": "text", "default": ""},
        {"key": "wireshark_path", "label": "Custom Wireshark executable path", "type": "text", "default": ""},
    ],
}


def _core():
    try:
        from plugins import _network_intelligence_core as core
    except ImportError:
        import _network_intelligence_core as core
    return core




def _engine():
    from plugins._wireshark_engine import engine
    return engine()


def _setting(key: str, default=""):
    try:
        from memory.config_manager import get_plugin_setting
        return get_plugin_setting(_NS, key, default)
    except Exception:
        return default


def _notify(player, finding: dict) -> None:
    message = (
        f"Possible network anomaly ({finding.get('severity', 'info')}): "
        f"{finding.get('type')} from {finding.get('source')}. "
        f"{finding.get('details')}. This is a heuristic indicator, not confirmation of an attack."
    )
    _show(player, message)


def on_app_start(player=None) -> None:
    """Resume monitoring only after the user explicitly enabled both consent settings."""
    if not bool(_setting("allow_live_capture", False)):
        return
    if not bool(_setting("background_monitoring", False)):
        return
    interface = str(_setting("interface", "") or "")
    try:
        message = _engine().start_background(
            interface,
            on_finding=lambda finding: _notify(player, finding),
        )
        _show(player, message)
    except Exception as exc:
        _show(player, f"Background monitoring could not start: {type(exc).__name__}: {exc}")

def run(parameters: dict, player=None, session_memory=None) -> str:
    p = dict(parameters or {})
    action = str(p.get("action") or "status").strip().lower()
    try:
        core = _core()
        if action in {"analyze_file", "load", "open_pcap"}:
            path = str(p.get("path") or "").strip()
            if not path:
                return "Please provide the PCAP/PCAPNG file path."
            max_packets = max(1, min(int(p.get("max_packets") or _setting("max_packets", 50000)), 50000))
            if _engine().status()["running"]:
                return "Stop background monitoring before importing a capture file. This prevents live and file-analysis data from being mixed."
            count = core.analyzer().load_pcap(path, max_packets=max_packets)
            result = core.analyzer().answer("give me a traffic summary")
            _show(player, f"Loaded {count:,} packets from {Path(path).name}.")
            core.show_dashboard(player, "give me a traffic summary")
            return f"Loaded {count:,} packets. {result['answer']} Open the Network Intelligence dashboard to inspect findings."
        if action in {"start_capture", "capture_start"}:
            try:
                from memory.config_manager import get_plugin_setting
                allowed = bool(get_plugin_setting(_NS, "allow_live_capture", False))
            except Exception:
                allowed = False
            if not allowed:
                return "Live capture is disabled. Enable 'Allow live capture' in JARVIS plugin settings first. Only monitor interfaces and networks you are authorised to inspect."
            message = _engine().start_background(str(p.get("interface") or _setting("interface", "")), str(p.get("bpf_filter") or ""), on_finding=lambda finding: _notify(player, finding))
            _show(player, message)
            core.show_dashboard(player)
            return message
        if action in {"stop_capture", "capture_stop", "stop_monitor"}:
            message = core.stop_capture()
            _show(player, message)
            return message
        if action in {"start_monitor", "monitor_start"}:
            if not bool(_setting("allow_live_capture", False)):
                return "Live monitoring is disabled. Enable 'Allow live capture' in plugin settings first."
            message = _engine().start_background(
                str(p.get("interface") or _setting("interface", "")),
                str(p.get("bpf_filter") or ""),
                on_finding=lambda finding: _notify(player, finding),
            )
            _show(player, message)
            core.show_dashboard(player)
            return message
        if action in {"launch_wireshark", "wireshark"}:
            # Opening the desktop application is independent from packet capture.
            # It must work even when live monitoring is disabled.
            gui_message = _engine().launch_gui()
            _show(player, gui_message)
            return gui_message + " Live monitoring remains stopped; start it separately when explicitly requested."
        if action in {"open_in_wireshark", "open_capture"}:
            path = str(p.get("path") or "").strip()
            if not path:
                return "Please provide the capture file path."
            return _engine().launch_gui(path=path)
        if action in {"dashboard", "show_dashboard", "open"}:
            question = str(p.get("question") or "")
            shown = core.show_dashboard(player, question)
            return "Network Intelligence dashboard opened." if shown else "Could not open the HUD dashboard. Ensure the JARVIS HUD and Live Panels plugin are available."
        if action == "status":
            state = _engine().status()
            return (
                f"Wireshark integration: TShark={'available' if state['tshark'] else 'missing'}, "
                f"Wireshark GUI={'available' if state['wireshark'] else 'missing'}, "
                f"background monitoring={'running' if state['running'] else 'stopped'}, "
                f"interface={state['interface'] or 'default'}. "
                f"Packets retained: {core.analyzer().snapshot().captured_packets}. "
                f"Last capture error: {state['last_error'] or 'none'}."
            )
        if action in {"ask", "question", "analyze"}:
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
        return "Unknown action. Use analyze_file, ask, dashboard, start_monitor, stop_monitor, launch_wireshark, open_in_wireshark or status."
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
