# Network Intelligence

Network Intelligence integrates JARVIS with **Wireshark and TShark**. PyShark is the Python adapter; TShark performs packet dissection and Wireshark provides the desktop GUI. The plugin can inspect saved captures and monitor an explicitly selected interface in a background worker.

## Architecture and capabilities

- PCAP/PCAPNG analysis through PyShark and TShark.
- Continuous live capture through TShark, independent of the Wireshark GUI window. The GUI is opened without starting a second capture, avoiding duplicate packet processing.
- Wireshark GUI launch is independent from capture permissions and never starts monitoring implicitly. Live monitoring is a separate, explicit action.
- Streaming aggregate counters only: JARVIS does not retain packet rows, payloads, or a packet-history buffer. Per-category cardinality is bounded (IPs, flows, DNS names, ports, scan sources); low-frequency keys may be evicted when a limit is reached. Snapshots are generated from counters and cached for five seconds.
- Packet/byte totals, tracked IPs/ports/flows, protocol categories, top talkers, rare tracked sources/protocols, DNS query frequency, a short traffic timeline and bounded heuristic detections.
- Heuristic indicators for broad TCP SYN/port sweeps and unusually long DNS names, evaluated on a 15-second interval during live capture.
- Natural-language questions routed to deterministic metrics; the configured JARVIS model may explain measured results but cannot invent metrics.
- HUD dashboard and notifications for newly observed heuristic findings.

## Install

Install the Python adapter:

```bash
uv sync
```

**Wireshark must also be installed separately**, including the TShark command-line executable. PyShark does not bundle Wireshark/TShark. If automatic discovery fails, set the full executable paths in SETUP → PLUGINS → NETWORK INTELLIGENCE.

Live capture additionally requires the platform capture driver and permissions:
- Windows: install Npcap; use an account/process with capture permissions.
- macOS: ensure the process can access the required BPF device.
- Linux: install libpcap and grant suitable capture capabilities/permissions.

`uv sync` installs PyShark but cannot install Wireshark, TShark, Npcap or libpcap.

## Background monitoring

1. Install Wireshark with TShark and restart JARVIS.
2. Enable **Allow live capture** in Network Intelligence settings.
3. Select the intended capture interface (blank uses the system default interface).
4. Optionally enable **Start authorised background monitoring when JARVIS launches**.
5. Start monitoring explicitly with the `start_monitor` action. Automatic startup monitoring is available only when both opt-in settings are enabled. The HUD can be closed without stopping capture.
6. Say “stop network monitoring” or invoke `stop_monitor` to stop it.

Both settings are off by default. Monitoring is opt-in because packet metadata can reveal sensitive browsing and network activity. Only monitor devices and networks you own or are authorised to inspect.

## Actions and example requests

Actions: `analyze_file`, `ask`, `dashboard`, `start_monitor`, `stop_monitor`, `launch_wireshark`, `open_in_wireshark`, and `status`.

Examples:
- “Start background network monitoring.”
- “Open Wireshark.” (GUI only; does not start capture.)
- “Start authorised network monitoring.” (starts the separate background monitor.)
- “Analyse this capture file: C:\\captures\\lab.pcapng”
- “Open this capture in Wireshark.”
- “How many packets, IPs, ports and flows have you seen?”
- “Which source IPs are most active?”
- “Which protocols are rare?”
- “Do you see signs of port scanning?”
- “Stop network monitoring.”

## Detection limitations

Findings are triage indicators, not proof of compromise. A port sweep or unusual DNS query can have benign explanations. The plugin does not decrypt TLS, execute payloads, identify every malware family, or replace IDS/IPS tooling or professional incident response. Encrypted application content remains encrypted unless separately decrypted with appropriate session secrets. JARVIS processes packet metadata from the selected interface to update fixed-size counters, then discards each packet object. It does not build a packet log, retain full payloads, or upload captures to an external service. Exact totals require observing packets; this is not a zero-inspection mode. TShark still performs packet dissection, so high-throughput links can consume CPU and may drop packets at the OS capture layer.

## Troubleshooting

- **TShark not found:** install Wireshark with TShark or configure its executable path.
- **Wireshark GUI not found:** configure the GUI executable path separately.
- **Capture permission denied:** verify Npcap/libpcap/BPF installation, selected interface and OS permissions.
- **No packets:** check the selected interface, capture filter and whether traffic is present.
- **Missing Python dependency:** run `uv sync` from the project root and restart JARVIS.
