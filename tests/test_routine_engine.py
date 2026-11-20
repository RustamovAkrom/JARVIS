from datetime import datetime
from core.routines.engine import RoutineEngine

class Host:
    def __init__(self): self.calls=[]; self.available=True; self.idle=0
    def ready(self): return self.available
    def idle_seconds(self): return self.idle
    def say(self, text): self.calls.append(("say", text))
    def prompt(self, text): self.calls.append(("prompt", text))
    def start_task(self, *args): self.calls.append(("task", args))
    def builtin(self, name, options): self.calls.append(("builtin", name, options))

def test_add_update_remove_persist(tmp_path):
    host=Host(); path=tmp_path/"routines.json"; eng=RoutineEngine(path, host)
    row=eng.add("Test", "say", "Hello", {"type":"startup"})
    eng.update(row["id"], enabled=False, name="Updated")
    assert RoutineEngine(path, host).get(row["id"])["name"] == "Updated"
    assert eng.remove(row["id"]) and not eng.remove(row["id"])

def test_run_now_executes_host_callback(tmp_path):
    host=Host(); eng=RoutineEngine(tmp_path/"routines.json", host)
    row=eng.add("Reminder", "say", "Take a break", {"type":"startup"})
    assert eng.run_now(row["id"]) == "ran"
    assert host.calls == [("say", "Take a break")]

def test_startup_waits_until_host_ready(tmp_path):
    host=Host(); host.available=False; eng=RoutineEngine(tmp_path/"routines.json", host)
    row=eng.add("Startup", "say", "Ready", {"type":"startup"})
    eng.session_started(awake=True); eng.tick(); assert not host.calls
    host.available=True; eng.tick(); assert host.calls == [("say", "Ready")]
    eng.tick(); assert len(host.calls) == 1

def test_quiet_hours_cross_midnight(tmp_path):
    eng=RoutineEngine(tmp_path/"routines.json", Host())
    eng.set_quiet_hours("22:00-07:00")
    assert eng.in_quiet_hours(datetime(2026,11,19,23,0))
    assert eng.in_quiet_hours(datetime(2026,11,19,6,59))
    assert not eng.in_quiet_hours(datetime(2026,11,19,12,0))

def test_invalid_trigger_and_empty_text_rejected(tmp_path):
    eng=RoutineEngine(tmp_path/"routines.json", Host())
    for args in [("bad","say","",{"type":"startup"}), ("bad","say","ok",{"type":"unknown"})]:
        try: eng.add(*args); assert False, "expected ValueError"
        except ValueError: pass

def test_close_prevents_future_scheduler_ticks(tmp_path):
    host = Host()
    engine = RoutineEngine(tmp_path / "routines.json", host)
    row = engine.add("Startup", "say", "Hello", {"type": "startup"})
    engine.session_started(awake=True)

    assert engine.close(timeout=0.1)
    engine.tick()

    assert host.calls == []
    assert engine.get(row["id"])["runs"] == 0


def test_close_waits_for_in_progress_tick(tmp_path):
    import threading

    entered = threading.Event()
    release = threading.Event()

    class BlockingHost(Host):
        def ready(self):
            entered.set()
            release.wait(timeout=2)
            return True

    host = BlockingHost()
    engine = RoutineEngine(tmp_path / "routines.json", host)
    engine.add("Startup", "say", "Hello", {"type": "startup"})
    engine.session_started(awake=True)

    worker = threading.Thread(target=engine.tick)
    worker.start()
    assert entered.wait(timeout=1)

    assert not engine.close(timeout=0.01)
    release.set()
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert engine.close(timeout=0.1)

    engine.tick()
    assert host.calls == [("say", "Hello")]

