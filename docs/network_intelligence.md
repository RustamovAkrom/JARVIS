# Network Intelligence

Network Intelligence is an advanced JARVIS plugin for authorised network monitoring and packet-forensics triage.

## Capabilities

- Import PCAP and PCAPNG files through Scapy; packet payloads are not retained.
- Bounded in-memory capture (250,000 packet records) and live capture using Scapy AsyncSniffer.
- Deterministic statistics: packet/byte counts, unique IPs/ports/flows, protocol distribution, top talkers, rare source IPs, rare protocols, DNS query frequency and a 10-second traffic timeline.
- Heuristic indicators for broad TCP SYN/port sweeps and unusually long DNS names.
- Natural-language questions routed to deterministic analysis functions; the configured JARVIS model may explain measured results but cannot create metrics.
- HUD dashboard with summary metrics and a findings table.

## Install

The plugin dependencies are declared in both `pyproject.toml` and `requirements.txt`. Run:

```bash
uv sync
```

Live capture also requires an operating-system packet capture driver:
- Windows: install Npcap and run JARVIS with the required capture permissions.
- macOS: ensure the process has permission to access the selected BPF device.
- Linux: install libpcap and grant the process suitable capture capabilities/permissions.

These native components cannot be installed by `uv sync`. PCAP/PCAPNG file analysis does not require live-capture privileges.

## Use

Enable **Network Intelligence** in SETUP → PLUGINS.

Example requests:
- “Analyse this capture file” (provide the local file path)
- “How many packets, IPs, ports and flows are there?”
- “Which protocol is most common / rarest?”
- “Which source IP is least frequent?”
- “Show the top talkers.”
- “Do you see anything strange?”
- “Open the Network Intelligence dashboard.”

The plugin tool actions are `analyze_file`, `ask`, `dashboard`, `start_capture`, `stop_capture` and `status`. Live capture is disabled by default and must be explicitly enabled in plugin settings. The user must also explicitly request capture and supply an interface if needed.

## Detection limitations

Findings are triage indicators, not proof of compromise. A large number of destination ports or long DNS labels can have benign explanations. The plugin does not decrypt TLS, execute payloads, identify every malware family, or replace Zeek, Suricata, Wireshark or a professional incident-response workflow. It intentionally does not store packet payloads or transmit captures to an external service. Only capture networks and interfaces you are authorised to monitor.

## Troubleshooting

- “No packets could be read”: verify that the file is a supported PCAP/PCAPNG and contains IPv4/IPv6 packets.
- Live capture fails: verify Npcap/libpcap installation, interface name and OS permissions.
- Dashboard unavailable: ensure JARVIS HUD is running and the built-in Live Panels helper is present.
- Missing Python dependency: run `uv sync` from the project root and restart JARVIS.
