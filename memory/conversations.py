"""Local, opt-in conversation recall. Raw transcripts never leave this machine."""
from __future__ import annotations
import json, queue, re, threading, time, unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from core.runtime_paths import PATHS

DIR = PATHS.memory_dir / "conversations"
_LINE_MAX = 1200
_OUTPUT_MAX = 2600
_queue: queue.Queue[tuple[str, dict]] = queue.Queue()
_writer: threading.Thread | None = None
_lock = threading.RLock()
_cache = {"at": 0.0, "value": {"enabled": False, "days": 90}}

def settings() -> dict:
    now = time.monotonic()
    if now - _cache["at"] > 5:
        try:
            from memory.config_manager import get_conversation_memory
            _cache["value"] = get_conversation_memory()
        except Exception:
            pass
        _cache["at"] = now
    return dict(_cache["value"])

def refresh_settings() -> None:
    _cache["at"] = 0.0

def enabled() -> bool:
    return bool(settings().get("enabled", False))

def _writer_loop() -> None:
    while True:
        filename, row = _queue.get()
        try:
            DIR.mkdir(parents=True, exist_ok=True)
            with _lock, (DIR / filename).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as exc:
            print(f"[ConversationRecall] write failed: {type(exc).__name__}: {exc}")
        finally:
            _queue.task_done()

def _put(filename: str, row: dict) -> None:
    global _writer
    if _writer is None or not _writer.is_alive():
        _writer = threading.Thread(target=_writer_loop, name="conversation-recall-writer", daemon=True)
        _writer.start()
    _queue.put((filename, row))

def flush(timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while _queue.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.02)
    return _queue.unfinished_tasks == 0

def record(user: str = "", assistant: str = "", when: datetime | None = None) -> None:
    user, assistant = str(user or "").strip(), str(assistant or "").strip()
    if not enabled() or not (user or assistant):
        return
    when = when or datetime.now()
    row = {"time": when.strftime("%H:%M")}
    if user: row["user"] = user[:_LINE_MAX]
    if assistant: row["assistant"] = assistant[:_LINE_MAX]
    _put(f"{when:%Y-%m-%d}.jsonl", row)

def _read(path: Path) -> list[dict]:
    rows=[]
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    row=json.loads(line)
                    if isinstance(row,dict): rows.append(row)
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return rows

def _fold(text: str) -> str:
    text=str(text or "").casefold().replace("ı","i").replace("ş","s").replace("ğ","g").replace("ç","c").replace("ö","o").replace("ü","u")
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))

_STOP=set(_fold("""the a an and or of to in on at for is are was were be it this that with what
which who about from my your me you i we our did do does have had can could would should will
not no yes as by how when where why they them he she its bir ve ile de da mi mu ne bu şu o
ben sen biz siz için gibi daha çok var yok ama fakat geçen hafta dün bugün önce sonra""").split())

def _terms(query: str) -> list[str]:
    terms=[]
    for word in re.findall(r"[^\W_]{2,}", _fold(query)):
        if word in _STOP or len(word)<3: continue
        term=word if len(word)<=4 else word[:max(4,len(word)-2)]
        if term not in terms: terms.append(term)
    return terms

def _score(terms: list[str], text: str) -> int:
    words=re.findall(r"[^\W_]{2,}", _fold(text))
    return sum(any(w==t or (len(t)>=4 and w.startswith(t)) for w in words) for t in terms)

def _parse_day(value) -> date | None:
    try: return datetime.strptime(str(value or "")[:10], "%Y-%m-%d").date()
    except ValueError: return None

def _days() -> list[date]:
    if not DIR.exists(): return []
    out=[]
    for p in DIR.glob("*.jsonl"):
        d=_parse_day(p.stem)
        if d: out.append(d)
    return sorted(out)

def _clip(text: str, n: int=300) -> str:
    text=" ".join(str(text or "").split())
    return text if len(text)<=n else text[:n-1]+"…"

def search(query: str="", date_from=None, date_to=None, limit: int=6) -> str:
    if not enabled() and not _days():
        return "Conversation memory is disabled and no conversations have been stored."
    terms=_terms(query)
    start,end=_parse_day(date_from),_parse_day(date_to)
    if start and end and start>end: start,end=end,start
    dates=[d for d in _days() if (start is None or d>=start) and (end is None or d<=end)]
    if not terms:
        lines=[]
        for d in dates[-3:]:
            for row in _read(DIR/f"{d}.jsonl")[:4]:
                lines.append(_format(d,row))
        return _cut("Recent conversations:\n"+ "\n".join(lines)) if lines else "No stored conversations match that date range."
    scored=[]
    today=date.today()
    for d in dates:
        for row in _read(DIR/f"{d}.jsonl"):
            user,assistant=row.get("user",""),row.get("assistant","")
            score=_score(terms,user+" "+assistant)
            if score:
                recency=max(0,1-(today-d).days/60)
                score+=recency+(0.5 if _score(terms,user) else 0)
                scored.append((score,d,row))
    if not scored:
        return f"No stored conversations mention {query!r}."
    scored.sort(key=lambda item:item[0], reverse=True)
    selected=sorted(scored[:max(1,min(12,int(limit)))],key=lambda item:(item[1],item[2].get("time","")))
    return _cut(f"Conversation matches for {query!r}:\n"+"\n".join(_format(d,r) for _,d,r in selected))

def _format(day: date, row: dict) -> str:
    parts=[]
    if row.get("user"): parts.append("User: "+_clip(row["user"]))
    if row.get("assistant"): parts.append("JARVIS: "+_clip(row["assistant"]))
    return f"{day} {row.get('time','')} — "+" / ".join(parts)

def _cut(text: str) -> str:
    return text if len(text)<=_OUTPUT_MAX else text[:_OUTPUT_MAX-1]+"…"

def stats() -> dict:
    dates=_days()
    total=sum(len(_read(DIR/f"{d}.jsonl")) for d in dates)
    cfg=settings()
    return {"enabled":cfg["enabled"],"days_kept":cfg["days"],"days_with_conversations":len(dates),
            "first":dates[0].isoformat() if dates else "", "last":dates[-1].isoformat() if dates else "",
            "exchanges":total}

def prune(keep_days: int | None=None) -> int:
    keep=max(1,int(keep_days or settings().get("days",90)))
    cutoff=date.today()-timedelta(days=keep)
    deleted=0
    with _lock:
        for day in _days():
            if day<cutoff:
                try: (DIR/f"{day}.jsonl").unlink(); deleted+=1
                except OSError: pass
    return deleted

def forget(date_from=None,date_to=None,*,everything=False) -> int:
    if everything: start=end=None
    else:
        start,end=_parse_day(date_from),_parse_day(date_to)
        if start is None and end is None: return 0
        if start and end and start>end: start,end=end,start
    deleted=0
    with _lock:
        for day in _days():
            if everything or ((start is None or day>=start) and (end is None or day<=end)):
                try: (DIR/f"{day}.jsonl").unlink(); deleted+=1
                except OSError: pass
    return deleted
