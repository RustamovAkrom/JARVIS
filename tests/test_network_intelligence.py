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


def test_pyshark_packet_adapter_normalizes_ipv4_tcp_and_dns_fields():
    from types import SimpleNamespace
    from plugins._wireshark_engine import _packet_record

    packet = SimpleNamespace(
        ip=SimpleNamespace(src="192.0.2.10", dst="198.51.100.20"),
        tcp=SimpleNamespace(srcport="51515", dstport="443", flags_syn="1", flags_ack="0"),
        udp=None,
        dns=SimpleNamespace(qry_name="example.test"),
        transport_layer="TCP",
        highest_layer="TLS",
        sniff_timestamp="1700000000.5",
        length="74",
    )
    record = _packet_record(packet)
    assert record is not None
    assert record.src_ip == "192.0.2.10"
    assert record.dst_ip == "198.51.100.20"
    assert (record.src_port, record.dst_port) == (51515, 443)
    assert record.tcp_flags == "S"
    assert record.dns_query == "example.test"
    assert record.length == 74


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


def test_live_capture_does_not_pass_file_only_keep_packets_argument(monkeypatch):
    import sys
    from types import SimpleNamespace
    from plugins import _wireshark_engine

    captured = {}

    class FakeLiveCapture:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def sniff_continuously(self):
            return iter(())

        def close(self):
            pass

    monkeypatch.setattr(_wireshark_engine, "find_tshark", lambda: "tshark")
    monkeypatch.setitem(sys.modules, "pyshark", SimpleNamespace(LiveCapture=FakeLiveCapture))
    engine = _wireshark_engine.WiresharkEngine()
    engine.start_background()
    engine._thread.join(timeout=2)

    assert "keep_packets" not in captured
    assert captured["include_raw"] is False
    assert captured["use_json"] is True
    assert captured["custom_parameters"]["-s"] == "256"



def test_pyshark_event_loop_is_created_in_worker_thread():
    import asyncio
    import threading
    from plugins._wireshark_engine import _ensure_event_loop

    result = {}

    def worker():
        with _ensure_event_loop() as loop:
            result["loop"] = loop
            result["running"] = asyncio.get_event_loop() is loop
            result["closed_inside"] = loop.is_closed()
        result["closed_after"] = loop.is_closed()
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            result["cleared_after"] = True
        else:
            result["cleared_after"] = False

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert result["running"] is True
    assert result["closed_inside"] is False
    assert result["closed_after"] is True
    assert result["cleared_after"] is True



def test_file_capture_has_event_loop_when_called_from_worker_thread(tmp_path, monkeypatch):
    import asyncio
    import threading
    from types import SimpleNamespace
    from plugins import _wireshark_engine

    capture_path = tmp_path / "empty.pcapng"
    capture_path.write_bytes(b"test")
    observed = {}

    class FakeFileCapture:
        def __init__(self, *args, **kwargs):
            observed["constructor_loop"] = asyncio.get_event_loop().is_closed() is False

        def __iter__(self):
            observed["iteration_loop"] = asyncio.get_event_loop().is_closed() is False
            return iter(())

        def close(self):
            observed["close_loop"] = asyncio.get_event_loop().is_closed() is False

    fake_pyshark = SimpleNamespace(FileCapture=FakeFileCapture)
    engine = _wireshark_engine.WiresharkEngine()
    monkeypatch.setattr(engine, "_pyshark", lambda: (fake_pyshark, "tshark"))

    def worker():
        try:
            engine.analyze_file(str(capture_path))
        except ValueError as exc:
            observed["empty_capture_error"] = "no IPv4/IPv6 packets" in str(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=3)

    assert not thread.is_alive()
    assert observed == {
        "constructor_loop": True,
        "iteration_loop": True,
        "close_loop": True,
        "empty_capture_error": True,
    }
