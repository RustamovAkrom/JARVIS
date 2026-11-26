from plugins._network_intelligence_core import PacketRecord, TrafficAnalyzer


def _sample():
    analyzer = TrafficAnalyzer()
    analyzer.clear("unit-test")
    for i in range(35):
        analyzer.add_record(PacketRecord(
            timestamp=1000 + i, src_ip="10.0.0.5", dst_ip="10.0.0.1",
            src_port=40000 + i, dst_port=20 + i, protocol="TCP",
            length=60, tcp_flags="S",
        ))
    analyzer.add_record(PacketRecord(
        timestamp=1040, src_ip="10.0.0.9", dst_ip="10.0.0.1",
        src_port=53000, dst_port=53, protocol="UDP", length=80,
        dns_query="example.com",
    ))
    return analyzer


def test_snapshot_counts_packets_ips_ports_and_flows():
    snapshot = _sample().snapshot()
    assert snapshot.captured_packets == 36
    assert snapshot.unique_ips == 3
    assert snapshot.unique_ports == 71
    assert snapshot.unique_flows == 36
    assert snapshot.total_bytes == 2180


def test_detects_possible_port_scan_heuristically():
    snapshot = _sample().snapshot()
    assert any(item["type"] == "possible_port_scan" for item in snapshot.suspicious)


def test_answers_common_analyst_questions():
    analyzer = _sample()
    assert "Protocol distribution" in analyzer.answer("which protocols?")["answer"]
    assert "36 packets" in analyzer.answer("how many packets?")["answer"]
    assert "possible_port_scan" in analyzer.answer("any strange traffic?")["answer"]


def test_empty_capture_returns_actionable_message():
    result = TrafficAnalyzer().answer("what is happening?")
    assert "No traffic data is loaded" in result["answer"]


def test_tshark_fields_adapter_normalizes_ipv4_tcp_and_dns_fields():
    from plugins._wireshark_engine import _record_from_fields

    fields = [
        "1700000000.5", "192.0.2.10", "", "", "198.51.100.20",
        "51515", "", "443", "", "TLS", "74", "1", "0", "example.test",
    ]
    record = _record_from_fields("\t".join(fields))
    assert record is not None
    assert record.src_ip == "192.0.2.10"
    assert record.dst_ip == "198.51.100.20"
    assert (record.src_port, record.dst_port) == (51515, 443)
    assert record.tcp_flags == "S"
    assert record.dns_query == "example.test"
    assert record.length == 74


def test_tshark_fields_adapter_supports_ipv6_and_missing_transport():
    from plugins._wireshark_engine import _record_from_fields

    fields = [
        "1700000001.0", "", "2001:db8::1", "", "2001:db8::2",
        "", "", "", "", "ICMPV6", "128", "", "", "",
    ]
    record = _record_from_fields("\t".join(fields))
    assert record is not None
    assert record.src_ip == "2001:db8::1"
    assert record.dst_ip == "2001:db8::2"
    assert record.src_port is None
    assert record.dst_port is None
    assert record.protocol == "ICMPV6"


def test_snapshot_is_cached_between_refresh_windows():
    analyzer = _sample()
    first = analyzer.snapshot()
    assert analyzer.snapshot() is first


def test_clear_invalidates_cached_snapshot():
    analyzer = _sample()
    first = analyzer.snapshot()
    analyzer.clear("new-source")
    second = analyzer.snapshot()
    assert second is not first
    assert second.source == "new-source"
    assert second.captured_packets == 0



def test_streaming_aggregator_does_not_retain_packet_rows():
    analyzer = TrafficAnalyzer()
    analyzer.clear("stream-test")
    for i in range(5000):
        analyzer.add_record(PacketRecord(
            timestamp=2000 + i, src_ip=f"192.0.2.{i}",
            dst_ip="198.51.100.1", src_port=10000 + i,
            dst_port=443, protocol="TLS", length=120,
        ))

    snapshot = analyzer.snapshot()
    assert snapshot.captured_packets == 5000
    assert snapshot.total_bytes == 600000
    assert len(analyzer._src) <= analyzer.MAX_IP_KEYS
    assert len(analyzer._dst) <= analyzer.MAX_IP_KEYS
    assert len(analyzer._flows) <= analyzer.MAX_FLOW_KEYS
    assert not hasattr(analyzer, "_records")
    assert snapshot.warnings





def test_tshark_command_uses_streaming_fields_without_payload():
    from plugins._wireshark_engine import _command_base

    command = _command_base("tshark")
    assert "-T" in command
    assert command[command.index("-T") + 1] == "fields"
    assert "frame.len" in command
    assert "tcp.payload" not in command
    assert "data.data" not in command


def test_live_tshark_command_omits_invalid_windows_any_interface():
    from plugins._wireshark_engine import _command_base

    command = _command_base("tshark")
    interface = ""
    if interface:
        command.extend(["-i", interface])
    assert "-i" not in command
