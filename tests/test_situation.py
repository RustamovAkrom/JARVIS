import core.situation as situation

def test_situation_sources_are_bounded_and_fail_independently(monkeypatch, capsys):
    situation.register("ok", lambda: "  current   state ")
    situation.register("long", lambda: "x" * (situation.LINE_MAX + 20))
    situation.register("broken", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    rows=situation.lines()
    assert rows[0]=="ok: current state"
    assert len(rows[1])<=situation.LINE_MAX+len("long: ")
    assert len(rows)==2
    assert "source broken failed" in capsys.readouterr().out
    situation.unregister("ok")
    situation.unregister("long")
    situation.unregister("broken")

def test_situation_host_routes_and_handles_errors(capsys):
    situation.host("first", "Holo Desk", lambda kind, **opts: kind=="weather")
    situation.host("broken", "Broken", lambda kind, **opts: (_ for _ in ()).throw(RuntimeError("bad")))
    assert situation.show_panel("weather", location="home")=="Holo Desk"
    assert situation.show_panel("unknown")==""
    assert "host broken failed" in capsys.readouterr().out
    situation.unhost("first")
    situation.unhost("broken")
