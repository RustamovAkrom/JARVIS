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
