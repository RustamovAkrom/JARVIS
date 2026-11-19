"""Host-agnostic routine state engine; application owns scheduling thread and delivery."""
from __future__ import annotations
import json, os, tempfile, threading, time, uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from core.routines import rules

KINDS = {"say", "prompt", "task", "builtin"}
DEFAULT_GRACE = {"say": 7200, "prompt": 2700, "task": 21600, "builtin": 2700}

class RoutineEngine:
    def __init__(self, path: str | Path, host: Any, *, clock=time.time, monotonic=time.monotonic,
                 grace: dict[str, float] | None = None, logger=print):
        self.path, self.host = Path(path), host
        self.clock, self.monotonic = clock, monotonic
        self.grace, self.log = {**DEFAULT_GRACE, **(grace or {})}, logger
        self._lock = threading.RLock()
        self._doc = self._load()
        self._session_started = None
        self._startup_awake = False
        self._startup_done = set()
        self._idle_last = {}

    def _load(self):
        if not self.path.exists():
            return {"version": 1, "quiet_hours": "", "routines": []}
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not read routines: {exc}") from exc
        if not isinstance(doc, dict) or not isinstance(doc.get("routines", []), list):
            raise RuntimeError("Routine store has an invalid format")
        doc.setdefault("version", 1); doc.setdefault("quiet_hours", ""); doc.setdefault("routines", [])
        return doc

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=self.path.parent,
                    prefix=self.path.name + ".", suffix=".tmp", delete=False) as stream:
                temp = Path(stream.name)
                json.dump(self._doc, stream, ensure_ascii=False, indent=2)
                stream.flush(); os.fsync(stream.fileno())
            temp.replace(self.path)
        except OSError as exc:
            if temp: temp.unlink(missing_ok=True)
            raise RuntimeError(f"Could not save routines: {exc}") from exc

    def list(self):
        with self._lock: return [dict(r) for r in self._doc["routines"]]
    def get(self, rid):
        with self._lock:
            row = self._find(rid)
            return dict(row) if row else None
    def _find(self, rid):
        return next((r for r in self._doc["routines"] if r.get("id") == rid), None)

    def add(self, name, kind, text="", trigger=None, *, options=None, enabled=True, quiet=None,
             deliver="voice", agent="general", minutes=10, builtin="", system=False):
        if kind not in KINDS: raise ValueError(f"kind must be one of: {', '.join(sorted(KINDS))}")
        if kind != "builtin" and not str(text or "").strip(): raise ValueError("routine text cannot be empty")
        trigger = self._validate_trigger(trigger or {})
        now = self.clock()
        row = {"id": uuid.uuid4().hex[:10], "name": " ".join(str(name or text or builtin).split())[:80],
            "kind": kind, "builtin": builtin, "text": str(text or "").strip(), "trigger": trigger,
            "options": dict(options or {}), "enabled": bool(enabled),
            "quiet": trigger["type"] == "idle" if quiet is None else bool(quiet),
            "deliver": deliver if deliver in {"voice", "panel", "silent"} else "voice",
            "agent": str(agent or "general"), "minutes": max(1, min(720, int(minutes))),
            "system": bool(system), "created": now, "next_ts": 0.0, "last_run": 0.0,
            "runs": 0, "last_result": ""}
        if trigger["type"] == "time":
            row["next_ts"] = rules.next_fire(trigger["rule"], datetime.fromtimestamp(now).astimezone()).timestamp()
        with self._lock:
            self._doc["routines"].append(row); self._save()
        return dict(row)

    def update(self, rid, **fields):
        allowed = {"name", "text", "trigger", "options", "enabled", "quiet", "deliver", "agent", "minutes"}
        unknown = set(fields) - allowed
        if unknown: raise ValueError(f"unsupported fields: {', '.join(sorted(unknown))}")
        with self._lock:
            row = self._find(rid)
            if row is None: raise ValueError("routine not found")
            if "trigger" in fields: fields["trigger"] = self._validate_trigger(fields["trigger"])
            if "name" in fields: fields["name"] = " ".join(str(fields["name"]).split())[:80]
            if "text" in fields:
                fields["text"] = str(fields["text"] or "").strip()
                if row["kind"] != "builtin" and not fields["text"]: raise ValueError("routine text cannot be empty")
            if "minutes" in fields: fields["minutes"] = max(1, min(720, int(fields["minutes"])))
            if fields.get("deliver") not in {None, "voice", "panel", "silent"}: fields["deliver"] = "voice"
            row.update(fields)
            if row["trigger"]["type"] == "time" and ("trigger" in fields or fields.get("enabled")):
                row["next_ts"] = rules.next_fire(row["trigger"]["rule"], datetime.fromtimestamp(self.clock()).astimezone()).timestamp()
            self._save(); return dict(row)

    def remove(self, rid):
        with self._lock:
            row = self._find(rid)
            if row is None: return False
            if row.get("system"): raise ValueError("built-in routines can be disabled but not deleted")
            self._doc["routines"].remove(row); self._save(); return True

    def session_started(self, awake=True):
        if self._session_started is None:
            self._session_started, self._startup_awake = self.monotonic(), bool(awake)

    def tick(self):
        now = self.clock(); dt = datetime.fromtimestamp(now).astimezone()
        ready = bool(self._host("ready", default=False))
        idle = float(self._host("idle_seconds", default=0) or 0)
        for row in self.list():
            if not row.get("enabled"): continue
            trigger = row["trigger"]; typ = trigger["type"]
            if typ == "startup":
                if row["id"] in self._startup_done or self._session_started is None: continue
                if not self._startup_awake: self._startup_done.add(row["id"])
                elif ready: self._startup_done.add(row["id"]); self._fire(row)
                elif self.monotonic() - self._session_started > 180:
                    self._startup_done.add(row["id"]); self.log(f"[Routines] {row['name']} skipped: assistant not ready")
            elif typ == "idle":
                if not ready or (row.get("quiet") and self.in_quiet_hours(dt)): continue
                if idle < trigger["minutes"] * 60: continue
                last = self._idle_last.get(row["id"])
                if last is not None and self.monotonic() - last < trigger["cooldown"] * 60: continue
                self._idle_last[row["id"]] = self.monotonic(); self._fire(row)
            elif typ == "time": self._tick_time(row, now, dt, ready and idle >= 10)

    def _tick_time(self, row, now, dt, ready):
        due = float(row.get("next_ts") or 0)
        if not due: self._advance(row["id"], dt); return
        if now < due: return
        late = now - due
        if row.get("quiet") and self.in_quiet_hours(dt): self._advance(row["id"], dt, "quiet hours"); return
        if late > float(self.grace.get(row["kind"], 2700)): self._advance(row["id"], dt, f"missed by {late / 60:.0f} min"); return
        if row["kind"] != "task" and not ready: return
        self._fire(row); self._advance(row["id"], dt)

    def _advance(self, rid, dt, note=""):
        with self._lock:
            row = self._find(rid)
            if row is None: return
            if note: row["last_result"] = f"skipped: {note}"; self.log(f"[Routines] {row['name']} skipped — {note}")
            nxt = rules.next_fire(row["trigger"]["rule"], dt)
            if nxt is None:
                if row.get("system"): row["enabled"] = False
                else: self._doc["routines"].remove(row)
            else: row["next_ts"] = nxt.timestamp()
            self._save()

    def run_now(self, rid):
        row = self.get(rid)
        if row is None: raise ValueError("routine not found")
        return self._fire(row)

    def _fire(self, row):
        try:
            kind = row["kind"]
            if kind == "say": self.host.say(row["text"])
            elif kind == "prompt": self.host.prompt(self.prompt_text(row))
            elif kind == "task": self.host.start_task(row["text"], row.get("agent", "general"), int(row.get("minutes", 10)), row.get("deliver", "voice"))
            else: self.host.builtin(row.get("builtin", ""), dict(row.get("options") or {}))
            result = "ran"
        except Exception as exc:
            result = f"failed: {type(exc).__name__}: {exc}"; self.log(f"[Routines] {row['name']} {result}")
        with self._lock:
            live = self._find(row["id"])
            if live: live.update(last_run=self.clock(), runs=int(live.get("runs") or 0)+1, last_result=result); self._save()
        return result

    def prompt_text(self, row):
        now = datetime.fromtimestamp(self.clock()).astimezone()
        return f"[ROUTINE '{row['name']}' — user configured earlier]\nCurrent local time: {now:%A %d %B %Y, %H:%M}\n{row['text']}\nPerform this instruction now using available tools. Respond in the user's language."

    def quiet_hours(self): return str(self._doc.get("quiet_hours") or "")
    def set_quiet_hours(self, value):
        text = str(value or "").replace(" ", "")
        if text:
            first, sep, last = text.partition("-")
            if not sep: raise ValueError("quiet hours must be HH:MM-HH:MM")
            text = f"{rules.hhmm(first)}-{rules.hhmm(last)}"
        with self._lock: self._doc["quiet_hours"] = text; self._save()
        return text
    def in_quiet_hours(self, dt):
        quiet = self.quiet_hours()
        if not quiet: return False
        first, _, last = quiet.partition("-"); start, end = rules.parse_hhmm(first), rules.parse_hhmm(last)
        current = (dt.hour, dt.minute)
        return start <= current < end if start < end else current >= start or current < end

    @staticmethod
    def _validate_trigger(trigger):
        typ = trigger.get("type")
        if typ == "startup": return {"type": "startup"}
        if typ == "idle": return {"type": "idle", "minutes": max(1, int(trigger.get("minutes") or 15)), "cooldown": max(1, int(trigger.get("cooldown") or 20))}
        if typ == "time" and isinstance(trigger.get("rule"), dict):
            rule = dict(trigger["rule"]); rules.next_fire(rule, datetime.now().astimezone()); return {"type": "time", "rule": rule}
        raise ValueError("trigger must be startup, idle, or time with a rule")

    def _host(self, method, *args, default=None):
        fn = getattr(self.host, method, None)
        if not callable(fn): return default
        try: return fn(*args)
        except Exception as exc: self.log(f"[Routines] host.{method} failed: {exc}"); return default
