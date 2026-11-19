from datetime import datetime
import memory.conversations as conv

def test_record_is_opt_in(monkeypatch, tmp_path):
    monkeypatch.setattr(conv, "DIR", tmp_path)
    monkeypatch.setattr(conv, "_cache", {"at": float("inf"), "value": {"enabled": False, "days": 90}})
    conv.record("hello", "hi", datetime(2026, 1, 2, 3, 4))
    assert not list(tmp_path.glob("*.jsonl"))

def test_record_search_and_forget(monkeypatch, tmp_path):
    monkeypatch.setattr(conv, "DIR", tmp_path)
    monkeypatch.setattr(conv, "_cache", {"at": float("inf"), "value": {"enabled": True, "days": 90}})
    conv.record("We discussed FastAPI architecture", "I suggested a service layer", datetime(2026, 1, 2, 3, 4))
    assert conv.flush()
    result = conv.search("FastAPI")
    assert "FastAPI architecture" in result
    assert conv.stats()["exchanges"] == 1
    assert conv.forget(everything=True) == 1
    assert conv.stats()["exchanges"] == 0

def test_search_date_range(monkeypatch, tmp_path):
    monkeypatch.setattr(conv, "DIR", tmp_path)
    monkeypatch.setattr(conv, "_cache", {"at": float("inf"), "value": {"enabled": True, "days": 90}})
    conv.record("Project Alpha", "Created API", datetime(2026, 1, 2, 3, 4))
    conv.record("Project Beta", "Created worker", datetime(2026, 1, 3, 3, 4))
    conv.flush()
    assert "Alpha" in conv.search("Project", date_from="2026-01-02", date_to="2026-01-02")
    assert "Beta" not in conv.search("Project", date_from="2026-01-02", date_to="2026-01-02")
