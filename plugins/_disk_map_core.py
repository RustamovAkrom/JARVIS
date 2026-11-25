"""
Disk Map's core — what is taking the space on a drive or in a folder, drawn
as a live, clickable treemap where the avatar sits.

A shared driver for disk_map.py (the leading underscore keeps the plugin loader
from treating it as a skill). It builds on Live Panels' frame
(_live_panels_core.py: the backdrop, header, ×, fade, scaling and placement on
the HUD), so it opens, closes and gives way exactly like the other live panels.

    Scanning    a helper process walks the folder tree and reports as it goes:
                every top-level folder drops onto the map the moment it is
                counted, and the space still to be read is shown as one block
                that shrinks while the progress ring fills.
    The map     every block is a folder or file, its area its size, its colour
                what kind of files fill it. Each folder shows what is inside it.
                Click a folder to zoom in, right-click / Backspace to come back,
                the path at the top to jump up, a file to select it.
                EXPLORER shows the selection in Windows Explorer. Nothing here
                ever deletes or moves a file.

WHY THE SCAN RUNS IN ANOTHER PROCESS
    Walking a drive is hundreds of thousands of calls into Windows, and Python
    holds its interpreter lock through each one. the assistant's voice is written to
    the speaker from Python too, so a scan inside the assistant would be heard as the
    voice stuttering. A helper process has its own interpreter lock. It runs at
    below-normal priority and ends by itself when the assistant does (its pipe breaks).
    If it cannot start, the same code runs in a thread here, pausing often.

WHAT IS COUNTED
    File sizes as Windows reports them. Junctions and symbolic links are not
    followed (their target is counted where it really lives, once). OneDrive
    files that are only in the cloud take no space and are not counted. On a
    whole drive, what the scan could not read (system files, other users,
    restore points) is shown as one hatched SYSTEM & HIDDEN block, so the map
    adds up to the space actually used.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from PyQt6 import sip
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QFontMetricsF, QLinearGradient, QPainter, QPen, QPixmap

try:
    from plugins import _live_panels_core as lb
except ImportError:  # loaded outside the package
    import _live_panels_core as lb  # type: ignore

_q, _mix, _once = lb._q, lb._mix, lb._once
BG, TEXT, DIM, WARN, HOT = lb.BG, lb.TEXT, lb.DIM, lb.WARN, lb.HOT

# Same order as the scanner's extension table below.
CATS = (
    ("VIDEO",      (255, 86, 120)),
    ("IMAGES",     (255, 166, 72)),
    ("AUDIO",      (186, 120, 255)),
    ("ARCHIVES",   (250, 214, 90)),
    ("PROGRAMS",   (64, 200, 255)),
    ("DOCUMENTS",  (96, 224, 150)),
    ("CODE & DEV", (110, 140, 255)),
    ("OTHER",      (132, 150, 170)),
)
REST_COL = (70, 84, 100)
GAP_COL = (104, 112, 124)

# Node = [name, size, kind, files, category, children | None]
#   kind 0 folder · 1 file · 2 the smaller items of a folder, as one block
#        3 SYSTEM & HIDDEN (used space the scan could not read)
#   children None on a folder = not sent by the scanner; opening it scans it.
NAME, SIZE, KIND, COUNT, CAT, KIDS = range(6)

AREA = QRectF(70, 236, 1060, 682)          # the treemap, on the 1600 × 1000 canvas
SX, SR = 1172.0, 1530.0                    # the side column


def fmt_size(b: float) -> str:
    b = float(max(0, b))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(b)} B"
            return f"{b:.1f} {unit}" if b < 100 else f"{b:.0f} {unit}"
        b /= 1024
    return f"{b:.0f} TB"


def _ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _lerp_rect(a: QRectF, b: QRectF, t: float) -> QRectF:
    return QRectF(a.left() + (b.left() - a.left()) * t, a.top() + (b.top() - a.top()) * t,
                  a.width() + (b.width() - a.width()) * t, a.height() + (b.height() - a.height()) * t)


def _expand(a: QRectF, r: QRectF) -> QRectF:
    """Where `a` lands when the map is zoomed so that `r` fills `a`."""
    if r.width() <= 0 or r.height() <= 0:
        return QRectF(a)
    kx, ky = a.width() / r.width(), a.height() / r.height()
    return QRectF(a.left() + (a.left() - r.left()) * kx, a.top() + (a.top() - r.top()) * ky,
                  a.width() * kx, a.height() * ky)


# ─────────────────────────────────────────────────────────────────────────────
# Treemap layout (squarified: Bruls, Huizing & van Wijk)
# ─────────────────────────────────────────────────────────────────────────────

def _worst(row: list, side: float) -> float:
    s = sum(row)
    if s <= 0 or side <= 0:
        return float("inf")
    return max(side * side * max(row) / (s * s), (s * s) / (side * side * min(row)))


def _place(row: list, x: float, y: float, w: float, h: float, out: list):
    s = sum(row)
    if w >= h:                                  # a column down the left
        cw = s / h if h > 0 else 0.0
        yy = y
        for a in row:
            ah = a / cw if cw > 0 else 0.0
            out.append(QRectF(x, yy, cw, ah))
            yy += ah
        return x + cw, y, w - cw, h
    rh = s / w if w > 0 else 0.0                # a row along the top
    xx = x
    for a in row:
        aw = a / rh if rh > 0 else 0.0
        out.append(QRectF(xx, y, aw, rh))
        xx += aw
    return x, y + rh, w, h - rh


def squarify(values: list, r: QRectF) -> list:
    """Rectangles for `values` (positive, largest first) filling `r`."""
    total = sum(values)
    if total <= 0 or r.width() <= 1 or r.height() <= 1:
        return [QRectF() for _ in values]
    k = r.width() * r.height() / total
    areas = [v * k for v in values]
    out: list = []
    x, y, w, h = r.left(), r.top(), r.width(), r.height()
    row: list = []
    i = 0
    while i < len(areas):
        a = areas[i]
        side = min(w, h)
        if not row or _worst(row + [a], side) <= _worst(row, side):
            row.append(a)
            i += 1
        else:
            x, y, w, h = _place(row, x, y, w, h, out)
            row = []
    if row:
        _place(row, x, y, w, h, out)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# The scanner (runs as its own process; see the module docstring)
# ─────────────────────────────────────────────────────────────────────────────

_SCAN_SCRIPT = r'''
import heapq, json, os, shutil, sys, time
from array import array

NC = 8
KEEP = 30          # children shown per folder; the rest become one block
BUDGET = 30000     # nodes sent back; anything deeper is scanned when it is opened
OTHER = 7
REPARSE = 0x400
CLOUD = 0x400000 | 0x1000 | 0x40000        # cloud-only / offline: no space on this disk
LINKS = (0xA0000003, 0xA000000C)           # junctions and symbolic links
_EXT = {}
for _c, _names in enumerate((
    "mp4 mkv avi mov wmv flv webm m4v mpg mpeg ts m2ts mts 3gp vob ogv",
    "jpg jpeg png gif bmp tif tiff webp heic heif raw cr2 cr3 nef arw dng psd svg ico tga exr hdr avif",
    "mp3 wav flac aac ogg m4a wma opus aiff aif mid midi",
    "zip rar 7z tar gz tgz bz2 xz zst iso img cab dmg wim esd vhd vhdx vmdk",
    "exe dll sys msi msp msix appx ocx drv so dylib lib jar apk cpl mui efi",
    "pdf doc docx xls xlsx ppt pptx txt rtf odt ods odp csv md epub one",
    "py pyc pyd js mjs cjs ts tsx jsx json xml html htm css c cc cpp h hpp cs java class go rs rb "
    "php lua sh ps1 bat ipynb whl pdb obj o a map",
)):
    for _e in _names.split():
        _EXT[_e] = _c


class Stop(Exception):
    pass


def scan(root, min_file, emit, pause=None, threads=8):
    """Walk `root`. Worker threads read folders (Windows answers several at once
    and the interpreter lock is free while it does); this thread does all the
    counting. threads=0 reads them here, one at a time, calling pause()."""
    import queue, threading
    t0 = time.time()
    win = os.name == "nt"
    top_path = os.path.abspath(root)
    io_root = top_path
    if win and not top_path.startswith("\\\\"):
        io_root = "\\\\?\\" + top_path            # paths longer than 260 characters too
    strip = len(io_root) - len(top_path)
    sep = os.sep
    mount = os.path.ismount(top_path)
    try:
        du = shutil.disk_usage(top_path)
        used, total, free = du.used, du.total, du.free
    except OSError:
        used = total = free = 0
    dev = None
    if not win:
        try:
            dev = os.stat(top_path).st_dev
        except OSError:
            pass
    skip = set() if win else {"/proc", "/sys", "/dev", "/run"}

    def read(path):
        """One folder: (own bytes, files, bytes per type, kept files, subfolders, cloud, errors)."""
        own = n = cloud = errors = steps = 0
        cv = [0] * NC
        kept = []
        dirs = []
        try:
            it = os.scandir(path)
        except OSError:
            return 0, 0, cv, None, dirs, 0, 1
        with it:
            while True:
                try:
                    e = next(it)
                except StopIteration:
                    break
                except OSError:
                    errors += 1
                    break
                if pause is not None:
                    steps += 1
                    if steps % 200 == 0:
                        pause()
                try:
                    if e.is_dir(follow_symlinks=False):
                        if win:
                            st = e.stat(follow_symlinks=False)
                            if st.st_file_attributes & REPARSE:
                                tag = getattr(st, "st_reparse_tag", 0)
                                if not tag:
                                    try:
                                        tag = os.lstat(e.path).st_reparse_tag
                                    except (OSError, AttributeError):
                                        tag = LINKS[0]
                                if tag in LINKS:
                                    continue
                        else:
                            if e.path in skip:
                                continue
                            if dev is not None and e.stat(follow_symlinks=False).st_dev != dev:
                                continue
                        dirs.append(e.name)
                    elif not e.is_symlink():
                        st = e.stat(follow_symlinks=False)
                        n += 1
                        if win and st.st_file_attributes & CLOUD:
                            cloud += st.st_size
                            continue
                        s = st.st_size
                        nm = e.name
                        c = _EXT.get(nm.rpartition(".")[2].lower(), OTHER) if "." in nm else OTHER
                        own += s
                        cv[c] += s
                        if s >= min_file:
                            kept.append((s, nm, c))
                except OSError:
                    errors += 1
        if len(kept) > KEEP:
            kept = heapq.nlargest(KEEP, kept)
        return own, n, cv, kept or None, dirs, cloud, errors

    zero = [0] * NC
    names = [os.path.basename(top_path.rstrip("\\/")) or top_path]
    parent = array("q", [-1]); size = array("q", [0]); count = array("q", [0])
    cats = array("q", zero); topof = array("q", [-1])
    subs = [[]]; files = [None]
    tbytes = {}; tcats = {}; tleft = {}
    nfiles = nbytes = errors = cloud = 0
    big = []
    last = 0.0

    work = queue.LifoQueue()                      # depth first: memory stays small
    results = queue.Queue()
    pending = 0

    def worker():
        while True:
            job = work.get()
            if job is None:
                return
            path, i = job
            try:
                res = read(path)
            except BaseException:
                res = (0, 0, [0] * NC, None, [], 0, 1)
            results.put((path, i, res))

    pool = [threading.Thread(target=worker, daemon=True) for _ in range(threads)]
    for th in pool:
        th.start()

    def submit(path, i):
        nonlocal pending
        pending += 1
        if pool:
            work.put((path, i))
        else:
            results.put((path, i, read(path)))

    submit(io_root, 0)
    try:
        while pending:
            path, i, (own, n, cv, kept, dirs, cl, er) = results.get()
            pending -= 1
            t = topof[i]
            size[i] = own
            count[i] = n
            b = i * NC
            for k in range(NC):
                if cv[k]:
                    cats[b + k] = cv[k]
            files[i] = kept
            nfiles += n
            nbytes += own
            cloud += cl
            errors += er
            if t > 0:
                tbytes[t] = tbytes.get(t, 0) + own
                tc = tcats.get(t)
                if tc is None:
                    tc = tcats[t] = [0] * NC
                for k in range(NC):
                    tc[k] += cv[k]
            base = path if path.endswith(sep) else path + sep
            if kept:
                for s, nm, c in kept:
                    if s > 0 and (len(big) < 25 or s > big[0][0]):
                        item = (s, (base + nm)[strip:])
                        if len(big) < 25:
                            heapq.heappush(big, item)
                        else:
                            heapq.heapreplace(big, item)
            for nm in dirs:
                j = len(names)
                tj = j if i == 0 else t
                names.append(nm); parent.append(i); size.append(0); count.append(0)
                cats.extend(zero); topof.append(tj)
                subs.append([]); files.append(None)
                subs[i].append(j)
                tleft[tj] = tleft.get(tj, 0) + 1
                submit(base + nm, j)
            if t > 0:
                tleft[t] -= 1                     # 0: every folder under this top one is counted
            now = time.time()
            if now - last > 0.2:
                last = now
                live = sorted(tbytes.items(), key=lambda x: -x[1])[:40]
                emit({"t": "p", "files": nfiles, "bytes": nbytes, "dirs": len(names) - 1,
                      "cur": path[strip:],
                      "tops": [[names[t], b, max(range(NC), key=tcats[t].__getitem__), tleft[t] == 0]
                               for t, b in live if b > 0]})
    finally:
        for _ in pool:
            work.put(None)

    for j in range(len(names) - 1, 0, -1):        # children always come after their parent
        p = parent[j]
        size[p] += size[j]
        count[p] += count[j]
        a, b = p * NC, j * NC
        for k in range(NC):
            cats[a + k] += cats[b + k]

    def dom(j):
        b = j * NC
        return max(range(NC), key=lambda k: cats[b + k])

    tree = [names[0], size[0], 0, count[0], dom(0), None]
    heap = [(-size[0], 0, tree)]
    sent = 0
    while heap and sent < BUDGET:                 # the biggest folders get detail first
        _, j, ref = heapq.heappop(heap)
        items = [(size[k], 0, k) for k in subs[j]]
        items += [(s, 1, (nm, c)) for s, nm, c in (files[j] or ())]
        items.sort(key=lambda x: x[0], reverse=True)
        kids = []
        shown = shown_n = 0
        for s, kind, v in items[:KEEP]:
            if kind == 0:
                ch = [names[v], s, 0, count[v], dom(v), None]
                kids.append(ch)
                shown += s
                shown_n += count[v]
                if subs[v] or files[v]:
                    heapq.heappush(heap, (-s, v, ch))
                elif count[v] == 0:
                    ch[5] = []
            elif s > 0:
                kids.append([v[0], s, 1, 1, v[1], None])
                shown += s
                shown_n += 1
        rest = size[j] - shown
        if rest > 0:
            kids.append(["", rest, 2, max(0, count[j] - shown_n), OTHER, None])
        ref[5] = kids
        sent += len(kids)

    gap = 0
    if mount and used and used - size[0] > used * 0.01:
        gap = used - size[0]
        if tree[5] is None:
            tree[5] = []
        tree[5].append(["", gap, 3, 0, OTHER, None])
        tree[1] += gap
    emit({"t": "done", "tree": tree, "sum": {
        "root": top_path, "scanned": size[0], "files": count[0], "dirs": len(names) - 1,
        "errors": errors, "cloud": cloud, "used": used, "total": total, "free": free,
        "gap": gap, "mount": mount, "cats": [cats[k] for k in range(NC)],
        "big": sorted(big, reverse=True), "secs": round(time.time() - t0, 1)}})


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.setrecursionlimit(20000)

    def emit(o):
        sys.stdout.write(json.dumps(o, separators=(",", ":")) + "\n")
        sys.stdout.flush()

    try:
        scan(sys.argv[1], int(sys.argv[2]), emit)
    except (BrokenPipeError, OSError):
        return                                    # the assistant is gone, or stopped listening
    except Exception as e:
        try:
            emit({"t": "fail", "error": f"{type(e).__name__}: {e}"})
        except Exception:
            pass


if __name__ == "__main__":
    main()
'''


class _Scan:
    """One scan of one folder: progress, the top folders as they finish, and
    finally the tree. Read from the GUI under `lock`; written by its thread."""

    def __init__(self, root: str):
        self.root = root
        self.lock = threading.Lock()
        self.state = "running"                  # running | done | failed
        self.prog: dict = {"files": 0, "bytes": 0, "dirs": 0, "cur": ""}
        self.tree: Optional[list] = None
        self.sum: dict = {}
        self.error = ""
        self.t0 = time.time()
        self.t_done = 0.0
        self.done = threading.Event()
        self._waiters: list = []
        self._stop = threading.Event()
        self._child: Optional[subprocess.Popen] = None
        self.mount = os.path.ismount(root)
        self.used = 0
        if self.mount:
            try:
                self.used = shutil.disk_usage(root).used
            except OSError:
                pass

    def start(self) -> "_Scan":
        threading.Thread(target=self._run, name="disk-map-scan", daemon=True).start()
        return self

    def cancel(self) -> None:
        self._stop.set()
        child = self._child
        if child is not None and child.poll() is None:
            try:
                child.kill()
            except Exception:
                pass

    def when_done(self, cb: Callable[["_Scan"], None]) -> None:
        with self.lock:
            if not self.done.is_set():
                self._waiters.append(cb)
                return
        try:
            cb(self)
        except Exception as exc:
            _once("disk map callback", exc)

    def _handle(self, msg: dict) -> None:
        kind = msg.get("t")
        with self.lock:
            if kind == "p":
                self.prog = msg
            elif kind == "done":
                self.tree, self.sum = msg["tree"], msg["sum"]
                self.state, self.t_done = "done", time.time()
            elif kind == "fail":
                self.error, self.state = str(msg.get("error") or "scan failed"), "failed"

    def _run(self) -> None:
        min_file = 128 * 1024 if self.mount else 0       # a drive: tiny files only as a sum
        try:
            try:
                if lb.compiled():
                    raise RuntimeError("packaged program: no Python to start a helper with")
                exe = sys.executable
                if exe.lower().endswith("pythonw.exe"):
                    alt = exe[:-len("pythonw.exe")] + "python.exe"
                    exe = alt if Path(alt).exists() else exe
                # CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS
                flags = (0x08000000 | 0x00004000) if os.name == "nt" else 0
                self._child = subprocess.Popen(
                    [exe, "-c", _SCAN_SCRIPT, self.root, str(min_file)],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                    creationflags=flags, text=True, encoding="utf-8", errors="surrogateescape")
            except Exception as exc:
                _once("disk map helper", exc)
                self._run_here(min_file)
                return
            for line in self._child.stdout:
                if self._stop.is_set():
                    break
                try:
                    self._handle(json.loads(line))
                except Exception:
                    continue
            try:
                self._child.stdout.close()
                self._child.wait(timeout=5)
            except Exception:
                pass
            with self.lock:
                if self.state == "running":
                    self.state = "failed"
                    self.error = "stopped" if self._stop.is_set() else "the scanner stopped unexpectedly"
        except Exception as exc:
            with self.lock:
                self.state, self.error = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            self._finish()

    def _run_here(self, min_file: int) -> None:
        """The fallback: the same scan in this process, pausing every 200 entries."""
        ns: dict = {"__name__": "disk_map_scan"}
        exec(compile(_SCAN_SCRIPT, "<disk-map-scan>", "exec"), ns)

        def pause():
            if self._stop.is_set():
                raise ns["Stop"]()
            time.sleep(0.002)
        try:
            ns["scan"](self.root, min_file, self._handle, pause, 0)
        except ns["Stop"]:
            with self.lock:
                self.state, self.error = "failed", "stopped"

    def _finish(self) -> None:
        with self.lock:
            self.done.set()
            waiters, self._waiters = self._waiters, []
        for cb in waiters:
            try:
                cb(self)
            except Exception as exc:
                _once("disk map callback", exc)


_scans: dict = {}            # normcase(root) -> _Scan; a finished scan is reused for a while
_REUSE_S = 15 * 60


def get_scan(root: str, fresh: bool = False) -> _Scan:
    key = os.path.normcase(os.path.abspath(root))
    sc = _scans.get(key)
    if sc is not None and not fresh:
        if sc.state == "running" or (sc.state == "done" and time.time() - sc.t_done < _REUSE_S):
            return sc
    if sc is not None and sc.state == "running":
        sc.cancel()
    sc = _scans[key] = _Scan(root).start()
    return sc


# ─────────────────────────────────────────────────────────────────────────────
# Where a spoken name points
# ─────────────────────────────────────────────────────────────────────────────

_KNOWN = {
    "desktop":   "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "pictures":  "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "videos":    "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
    "music":     "{4BD8D571-6D19-48D3-BE97-422220080E43}",
}
_ALIASES = {
    "downloads": ("download", "downloads", "indirilenler", "indirmeler", "indirilenlerim"),
    "desktop":   ("desktop", "masaüstü", "masaustu"),
    "documents": ("documents", "document", "docs", "my documents", "belgeler", "belgelerim"),
    "pictures":  ("pictures", "photos", "images", "resimler", "fotoğraflar", "fotograflar"),
    "videos":    ("videos", "video", "videolar"),
    "music":     ("music", "müzik", "muzik"),
    "home":      ("home", "user", "profile", "user folder", "kullanıcı", "kullanici"),
    "appdata":   ("appdata", "app data"),
    "temp":      ("temp", "tmp", "temporary files", "geçici", "gecici"),
    "programs":  ("program files", "programs", "programlar"),
}


def _known_folder(name: str) -> Optional[str]:
    """This system's own idea of the folder (core/user_dirs): Windows' known
    folder, so a Desktop moved into OneDrive is found; Linux's user-dirs, in
    the system's language (a Turkish Ubuntu's desktop is ~/Masaüstü)."""
    try:
        from core.user_dirs import find
        found = find(name)
    except Exception as exc:
        _once("known folder", exc)
        found = None
    return str(found) if found else None


def system_drive() -> str:
    if os.name == "nt":
        return (os.environ.get("SystemDrive") or "C:") + "\\"
    return "/"


def resolve(target: str) -> Optional[str]:
    """A drive, a well-known folder (English or Turkish) or a path → a folder."""
    t = str(target or "").strip().strip("\"'").strip()
    if not t:
        return system_drive()
    low = t.casefold()
    if low in ("c", "system", "system drive", "main drive", "sistem", "sistem diski"):
        return system_drive()
    m = re.fullmatch(r"([a-z])\s*:?\s*[\\/]?\s*(?:drive|disk|sürücü\w*|surucu\w*|diski?)?", low)
    if m and os.name == "nt":
        root = m.group(1).upper() + ":\\"
        return root if os.path.isdir(root) else None
    for key, words in _ALIASES.items():
        if low in words:
            if key in _KNOWN:
                return _known_folder(key)
            if key == "home":
                return str(Path.home())
            if key == "appdata":
                base = os.environ.get("LOCALAPPDATA")
                return str(Path(base).parent) if base else None
            if key == "temp":
                import tempfile
                return tempfile.gettempdir()
            if key == "programs":
                return os.environ.get("ProgramFiles") or None
    p = os.path.expandvars(os.path.expanduser(t))
    if os.path.isdir(p):
        return os.path.abspath(p)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Facts for the assistant to speak
# ─────────────────────────────────────────────────────────────────────────────

def _find(tree: list, parts: list) -> Optional[list]:
    node = tree
    for part in parts:
        kids = node[KIDS] or []
        node = next((k for k in kids if k[KIND] in (0, 1) and k[NAME].casefold() == part.casefold()),
                    None)
        if node is None:
            return None
    return node


def _spots(sc: _Scan) -> list:
    """Places where space is usually easy to win back, with their size here."""
    home = Path.home()
    if sys.platform.startswith("linux"):
        import tempfile
        data = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
        cache = Path(os.environ.get("XDG_CACHE_HOME") or home / ".cache")
        places = (
            ("Trash", data / "Trash"),
            ("Cache", cache),
            ("Temp files", Path(tempfile.gettempdir())),
            ("System journal (logs)", Path("/var/log/journal")),
            ("Downloads", Path(_known_folder("downloads") or home / "Downloads")),
        )
    else:
        win = Path(os.environ.get("SystemRoot") or r"C:\Windows")
        drive = Path(system_drive())
        local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        places = (
            ("Recycle Bin", drive / "$Recycle.Bin"),
            ("Temp files", local / "Temp"),
            ("Windows temp", win / "Temp"),
            ("Windows Update download cache", win / "SoftwareDistribution" / "Download"),
            ("Previous Windows installation (Windows.old)", drive / "Windows.old"),
            ("Downloads", Path(_known_folder("downloads") or home / "Downloads")),
            ("Hibernation file", drive / "hiberfil.sys"),
        )
    root = os.path.normcase(os.path.abspath(sc.root)).rstrip("\\/")
    out = []
    for label, path in places:
        full = os.path.normcase(str(path))
        if not full.startswith(root + os.sep) and full != root:
            continue
        rel = os.path.relpath(str(path), sc.root)
        node = _find(sc.tree, [x for x in Path(rel).parts if x not in (".", "")])
        if node is not None and node[SIZE] > 200 * 1024 ** 2:
            out.append(f"{label} {fmt_size(node[SIZE])}")
    return out


def describe(sc: _Scan, node: Optional[list] = None, where: str = "") -> str:
    s = sc.sum
    tree = sc.tree or ["", 0, 0, 0, 7, []]
    node = node or tree
    parts = [f"Scanned {s.get('root')}: {fmt_size(s.get('scanned', 0))} in "
             f"{s.get('files', 0):,} files and {s.get('dirs', 0):,} folders ({s.get('secs')} s)."]
    if s.get("mount") and s.get("total"):
        pct = s["used"] / s["total"] * 100
        parts.append(f"Drive: {fmt_size(s['used'])} used of {fmt_size(s['total'])}, "
                     f"{fmt_size(s['free'])} free ({pct:.0f}% full).")
    if s.get("gap"):
        parts.append(f"{fmt_size(s['gap'])} is system/hidden space the scan could not read.")
    kids = sorted((k for k in (node[KIDS] or []) if k[KIND] in (0, 1)), key=lambda k: -k[SIZE])[:8]
    if kids:
        label = where or str(s.get("root"))
        parts.append(f"Biggest in {label}: " + "; ".join(
            f"{k[NAME]} {fmt_size(k[SIZE])}{'' if k[KIND] == 0 else ' (file)'}" for k in kids) + ".")
    if node is tree:
        big = s.get("big") or []
        if big:
            parts.append("Largest files: " + "; ".join(f"{p} {fmt_size(b)}" for b, p in big[:6]) + ".")
        cats = s.get("cats") or []
        order = sorted(range(len(cats)), key=lambda i: -cats[i])[:5]
        if cats:
            parts.append("By type: " + ", ".join(f"{CATS[i][0].lower()} {fmt_size(cats[i])}"
                                                for i in order if cats[i]) + ".")
        spots = _spots(sc)
        if spots:
            parts.append("Space that is often safe to reclaim: " + "; ".join(spots) + ".")
        if s.get("cloud"):
            parts.append(f"{fmt_size(s['cloud'])} of OneDrive files are online-only and use no space.")
        if s.get("errors"):
            parts.append(f"{s['errors']:,} folders could not be read (normal for system folders).")
    return " ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# The board
# ─────────────────────────────────────────────────────────────────────────────

def _colour(k: list):
    if k[KIND] == 2:
        return REST_COL
    if k[KIND] == 3:
        return GAP_COL
    return CATS[k[CAT] if 0 <= k[CAT] < len(CATS) else 7][1]


def _label(k: list) -> str:
    if k[KIND] == 2:
        return f"{k[COUNT]:,} smaller items" if k[COUNT] else "Smaller items"
    if k[KIND] == 3:
        return "System & hidden"
    return k[NAME] or "?"


def _under(items: list, pt) -> list:
    """[(rect, node)] from the outermost block under `pt` to the innermost."""
    out = []
    while items:
        for r, k, nested in items:
            if r.contains(pt):
                out.append((r, k))
                items = nested
                break
        else:
            break
    return out


def _locate(items: list, node: list) -> Optional[QRectF]:
    for r, k, nested in items:
        if k is node:
            return r
        found = _locate(nested, node)
        if found is not None:
            return found
    return None


class DiskBoard(lb.Board):
    def __init__(self, acc, parent=None):
        super().__init__(acc, parent)
        self.idle_ms = 250
        self.scan: Optional[_Scan] = None
        self.stack: list = []                   # nodes from the root to the folder on show
        self.sel: Optional[list] = None
        self.sub: Optional[dict] = None         # a folder being scanned deeper
        self.trans: Optional[dict] = None       # the zoom between two folders
        self.reveal_t0 = 0.0
        self.cur_pix: Optional[QPixmap] = None
        self.flash = ("", 0.0)
        self._lay: dict = {}
        self._pix: dict = {}
        self._fm: dict = {}
        self._seen: dict = {}                   # top folder -> when it first showed while scanning

    # ── state ────────────────────────────────────────────────────────────────
    def set_scan(self, sc: _Scan) -> None:
        if sc is self.scan:
            return
        if self.sub:
            self.sub["scan"].cancel()
        self.scan, self.stack, self.sel, self.sub, self.trans = sc, [], None, None, None
        self.reveal_t0 = 0.0
        self._seen.clear()
        self._lay.clear()
        self._pix.clear()

    def path_of(self, chain: list) -> str:
        root = self.scan.root if self.scan else ""
        names = [n[NAME] for n in chain[1:]]
        return os.path.join(root, *names) if names else root

    def view(self) -> Optional[list]:
        return self.stack[-1] if self.stack else None

    def animating(self) -> bool:
        if self.trans is not None or self.reveal_t0:
            return True
        sc = self.scan
        if sc is not None and sc.state == "running":
            now = time.time()
            return any(now - t < 0.5 for t in self._seen.values())
        return False

    def on_close(self) -> None:
        self.trans = None

    # ── helpers ──────────────────────────────────────────────────────────────
    def fm(self, font) -> QFontMetricsF:
        m = self._fm.get(id(font))
        if m is None:
            m = self._fm[id(font)] = QFontMetricsF(font)
        return m

    def elide(self, s: str, font, width: float) -> str:
        if width <= 8:
            return ""
        return self.fm(font).elidedText(s, Qt.TextElideMode.ElideMiddle, width)

    # Nesting: a folder big enough on the map shows what is inside it, down to
    # three levels, each with a strip for its name. HEAD = that strip's height.
    HEAD = (28.0, 22.0, 19.0)
    DEPTH = 3

    def _nest(self, r: QRectF, k: list, level: int) -> list:
        """[(rect, child, nested)] for what is inside `k`, drawn in `r`."""
        if level >= self.DEPTH or k[KIND] != 0 or not k[KIDS]:
            return []
        head = self.HEAD[level]
        if r.width() < (70 if level == 0 else 96) or r.height() < head + (36 if level == 0 else 46):
            return []
        ir = QRectF(r.left() + 3, r.top() + head, r.width() - 6, r.height() - head - 3)
        kids = sorted((c for c in k[KIDS] if c[SIZE] > 0), key=lambda c: -c[SIZE])
        out = []
        for rr, c in zip(squarify([c[SIZE] for c in kids], ir), kids):
            rr = rr.adjusted(1, 1, -1, -1)
            if rr.width() >= 2 and rr.height() >= 2:
                out.append((rr, c, self._nest(rr, c, level + 1)))
        return out

    def layout(self, view: list) -> list:
        """[(rect, node, nested)] for a folder, largest first; nested is the same
        shape again, for what is inside each block."""
        lay = self._lay.get(id(view))
        if lay is not None:
            return lay
        kids = sorted((k for k in (view[KIDS] or []) if k[SIZE] > 0), key=lambda k: -k[SIZE])
        lay = []
        for r, k in zip(squarify([k[SIZE] for k in kids], AREA), kids):
            r = r.adjusted(1.5, 1.5, -1.5, -1.5)
            if r.width() >= 1 and r.height() >= 1:
                lay.append((r, k, self._nest(r, k, 0)))
        if len(self._lay) > 60:
            self._lay.clear()
        self._lay[id(view)] = lay
        return lay

    def block(self, p: QPainter, r: QRectF, k: list, level: int) -> None:
        col = _colour(k)
        base = _mix(BG, col, min(0.66, 0.30 + 0.17 * level) if k[KIND] != 1 else 0.74)
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0.0, _q(_mix(base, (255, 255, 255), 0.16)))
        g.setColorAt(1.0, _q(_mix(base, BG, 0.38)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawRect(r)
        if k[KIND] in (2, 3):
            p.setBrush(QBrush(_q(col, 90), Qt.BrushStyle.BDiagPattern))
            p.drawRect(r)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_q(col, 170 if level == 0 else 90), 1))
        p.drawRect(r)

    def label(self, p: QPainter, r: QRectF, k: list, level: int, has_inner: bool) -> None:
        name, size = _label(k), fmt_size(k[SIZE])
        if has_inner:                           # the name in the strip above the contents
            if r.width() < 60:
                return
            fn = self.font("display" if level == 0 else "body", (19, 15, 13)[min(level, 2)])
            fs = self.font("mono", (14, 12, 11)[min(level, 2)])
            sw = self.fm(fs).horizontalAdvance(size)
            show_size = r.width() > sw + 90
            room = r.width() - 16 - (sw + 12 if show_size else 0)
            self.text(p, r.left() + 7, r.top() + (4 if level == 0 else 2), self.elide(name, fn, room), fn,
                      TEXT if level == 0 else _mix(TEXT, _colour(k), 0.15))
            if show_size:
                self.text(p, r.right() - 7, r.top() + (8 if level == 0 else 4), size, fs,
                          _mix(_colour(k), TEXT, 0.25 * level), align="r")
            return
        if r.width() < 58 or r.height() < 26:
            return
        if level == 0 and r.width() > 160 and r.height() > 110:
            big = max(18.0, min(40.0, r.height() / 5, r.width() / 9))
            fn = self.font("display", big)
            self.text(p, r.center().x(), r.center().y() - big * 0.35,
                      self.elide(name, fn, r.width() - 24), fn, TEXT, align="c", valign="mid")
            self.text(p, r.center().x(), r.center().y() + big * 0.65, size,
                      self.font("mono", big * 0.55), _mix(_colour(k), TEXT, 0.4), align="c", valign="mid")
            return
        fn = self.font("body", 14 if level else 16)
        self.text(p, r.left() + 6, r.top() + 4, self.elide(name, fn, r.width() - 12), fn, TEXT)
        if r.height() > 44:
            self.text(p, r.left() + 6, r.top() + 24, size, self.font("mono", 12),
                      _mix(_colour(k), TEXT, 0.3))

    def _paint_tree(self, p: QPainter, items: list, level: int) -> None:
        for r, k, nested in items:
            self.block(p, r, k, level)
            self._paint_tree(p, nested, level + 1)
            self.label(p, r, k, level, bool(nested))

    def map_pix(self, view: list, p: QPainter) -> QPixmap:
        """The folder's map, painted once into a picture: the frames in between
        only lay the hover on top of it (see Board on why frames must be cheap)."""
        sc = p.transform().m11()
        dpr = max(1.0, self.devicePixelRatioF())
        key = (id(view), round(sc, 4), dpr)
        pix = self._pix.get(key)
        if pix is not None:
            return pix
        pix = QPixmap(max(1, math.ceil(AREA.width() * sc * dpr)),
                      max(1, math.ceil(AREA.height() * sc * dpr)))
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.GlobalColor.transparent)
        qp = QPainter(pix)
        try:
            qp.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            qp.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            qp.scale(sc, sc)
            qp.translate(-AREA.left(), -AREA.top())
            self._paint_tree(qp, self.layout(view), 0)
        finally:
            qp.end()
        if len(self._pix) > 4:
            self._pix.clear()
        self._pix[key] = pix
        return pix

    # ── navigation ───────────────────────────────────────────────────────────
    def _rect_in_view(self, node: list) -> Optional[QRectF]:
        v = self.view()
        if v is None:
            return None
        for r, k, inner in self.layout(v):
            if k is node:
                return r
        return None

    def enter(self, node: list, rect: Optional[QRectF], select: Optional[list] = None) -> None:
        if node[KIND] != 0:
            self.sel = node
            return
        if node[KIDS] is None:
            if node[SIZE] > 0 or node[COUNT] > 0:
                self.scan_deeper(self.stack + [node], rect)
            return
        old = self.cur_pix
        self.stack.append(node)
        self.sel = select
        r = rect or AREA
        self.trans = {"old": old, "of": QRectF(AREA), "ot": _expand(AREA, r) if rect else QRectF(AREA),
                      "nf": QRectF(r), "nt": QRectF(AREA), "t0": time.time()}
        self.update()

    def up(self) -> None:
        if len(self.stack) < 2:
            return
        child = self.stack.pop()
        r = self._rect_in_view(child)
        old = self.cur_pix
        self.sel = child
        self.trans = {"old": old, "of": QRectF(AREA), "ot": QRectF(r) if r else QRectF(AREA),
                      "nf": _expand(AREA, r) if r else QRectF(AREA), "nt": QRectF(AREA),
                      "t0": time.time()}
        self.update()

    def jump(self, depth: int) -> None:
        while len(self.stack) > depth + 2:
            self.stack.pop()
        self.up()

    def scan_deeper(self, chain: list, rect: Optional[QRectF]) -> None:
        if self.sub is not None:
            return
        path = self.path_of(chain)
        self.sub = {"scan": _Scan(path).start(), "chain": chain, "rect": rect}

    def _sub_step(self) -> None:
        sub = self.sub
        if sub is None:
            return
        sc = sub["scan"]
        with sc.lock:
            state, tree, err = sc.state, sc.tree, sc.error
        if state == "running":
            return
        self.sub = None
        chain = sub["chain"]
        if state == "failed" or tree is None:
            self.flash = (f"Could not read {chain[-1][NAME]}: {err}", time.time())
            return
        node = chain[-1]
        node[KIDS] = tree[KIDS] or []
        self._lay.clear()
        self._pix.clear()
        if self.stack and self.stack == chain[:len(self.stack)] and len(chain) == len(self.stack) + 1:
            self.enter(node, sub["rect"])
        else:
            self.go_to(chain, None)

    def go_to(self, chain: list, select: Optional[list]) -> None:
        old = self.cur_pix
        self.stack, self.sel = list(chain), select
        self.trans = {"old": old, "of": QRectF(AREA), "ot": QRectF(AREA),
                      "nf": QRectF(AREA), "nt": QRectF(AREA), "t0": time.time()}
        self.update()

    def focus(self, query: str) -> Optional[tuple]:
        """Show the folder or file the user named: (node, path) or None."""
        if not self.stack:
            return None
        q = str(query or "").strip().strip("\"'")
        if not q:
            return None
        if os.path.isabs(q) and self.scan:
            rel = os.path.relpath(q, self.scan.root)
            if not rel.startswith(".."):
                chain = [self.stack[0]]
                for part in Path(rel).parts:
                    if part in (".", ""):
                        continue
                    nxt = next((k for k in (chain[-1][KIDS] or [])
                                if k[KIND] in (0, 1) and k[NAME].casefold() == part.casefold()), None)
                    if nxt is None:
                        break
                    chain.append(nxt)
                return self._show_chain(chain)
        qf = q.casefold()
        best = None                                # (rank, size, chain)
        for start in (self.stack, [self.stack[0]]):
            queue = [list(start)]
            seen = 0
            while queue and seen < 60000:
                chain = queue.pop(0)
                for k in chain[-1][KIDS] or []:
                    if k[KIND] not in (0, 1):
                        continue
                    seen += 1
                    name = k[NAME].casefold()
                    rank = 2 if name == qf else (1 if qf in name else 0)
                    if rank and (best is None or (rank, k[SIZE]) > best[:2]):
                        best = (rank, k[SIZE], chain + [k])
                    if k[KIND] == 0 and k[KIDS]:
                        queue.append(chain + [k])
            if best is not None:
                break
        return self._show_chain(best[2]) if best else None

    def _show_chain(self, chain: list) -> tuple:
        node = chain[-1]
        if node[KIND] == 0 and node[KIDS] is None and (node[SIZE] or node[COUNT]):
            self.go_to(chain[:-1], node)
            self.scan_deeper(chain, None)
        elif node[KIND] == 0 and len(chain) > 1:
            self.go_to(chain, None)
        else:
            self.go_to(chain[:-1] or chain, node)
        return node, self.path_of(chain)

    def reveal(self) -> None:
        node = self.sel if self.sel is not None and self.sel[KIND] in (0, 1) else None
        if node is not None and node is not self.view():
            path, is_file = self.path_of(self.stack + [node]), node[KIND] == 1
        else:
            path, is_file = self.path_of(self.stack), False
        try:
            if os.name == "nt":
                if is_file:
                    subprocess.Popen(f'explorer /select,"{path}"')
                else:
                    os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path] if is_file else ["open", path])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path) if is_file else path])
            self.flash = (f"Opened {path}", time.time())
        except Exception as exc:
            self.flash = (f"Could not open {path}: {exc}", time.time())

    def rescan(self) -> None:
        if self.scan is not None and self.scan.state != "running":
            self.set_scan(get_scan(self.scan.root, fresh=True))

    # ── input ────────────────────────────────────────────────────────────────
    def clicked(self, key: str) -> None:
        busy = self.trans is not None or self.reveal_t0
        if key == "up":
            self.up()
        elif key == "explorer":
            self.reveal()
        elif key == "rescan":
            self.rescan()
        elif key.startswith("crumb:"):
            self.jump(int(key[6:]))
        elif busy or self.sub is not None:
            return
        elif key.startswith("t:"):
            r, k, _inner = self.layout(self.view())[int(key[2:])]
            if k[KIND] == 0:
                self.enter(k, r)
            else:
                self.sel = None if self.sel is k else k
        elif key.startswith("s:"):
            i, j = (int(x) for x in key[2:].split(":"))
            r, k, inner = self.layout(self.view())[i]
            self.enter(k, r, select=inner[j][1])

    def mousePressEvent(self, e) -> None:
        try:
            if e.button() == Qt.MouseButton.RightButton:
                self.last_input = time.time()
                self.up()
                return
        except Exception as exc:
            _once("disk map right click", exc)
        super().mousePressEvent(e)

    def keyPressEvent(self, e) -> None:
        try:
            if e.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Left):
                self.up()
            elif e.key() == Qt.Key.Key_Escape:
                if len(self.stack) > 1:
                    self.up()
                else:
                    self.close_view()
            self.last_input = time.time()
            self.update()
        except Exception as exc:
            _once("disk map key", exc)

    # ── painting ─────────────────────────────────────────────────────────────
    def title(self):
        sc = self.scan
        if sc is None:
            return "Disk map", ""
        if sc.state == "running":
            return "Disk map", f"SCANNING  {sc.root}"
        if sc.state == "failed":
            return "Disk map", f"{sc.root}  ·  SCAN FAILED"
        s = sc.sum
        return "Disk map", (f"{s.get('root')}  ·  {fmt_size(s.get('scanned', 0))}  ·  "
                            f"{s.get('files', 0):,} FILES  ·  {s.get('dirs', 0):,} FOLDERS")

    def draw(self, p: QPainter, dt: float, now: float) -> None:
        sc = self.scan
        if sc is None:
            return
        with sc.lock:
            state, prog, tree = sc.state, dict(sc.prog), sc.tree
        if state == "done" and not self.stack and tree is not None:
            self.stack = [tree]
            self.reveal_t0 = now
        self._sub_step()
        self.idle_ms = 90 if (state == "running" or self.sub is not None) else 250
        self._breadcrumb(p, state)
        if state == "running":
            self._scanning(p, now, prog, sc)
        elif state == "failed":
            self.text(p, AREA.center().x(), AREA.center().y(), "SCAN FAILED", self.font("display", 44),
                      HOT, align="c", valign="mid", glow=True)
            self.text(p, AREA.center().x(), AREA.center().y() + 50, self.elide(sc.error, self.font("mono", 18), 900),
                      self.font("mono", 18), DIM, align="c", valign="mid")
        else:
            self._map(p, now)
        self._side(p, now, state, prog, sc)
        self._status(p, now, state, prog)
        if state == "done" and self.trans is None and not self.reveal_t0:
            self._tooltip(p)

    def _breadcrumb(self, p: QPainter, state: str) -> None:
        f = self.font("mono", 18)
        y = 200
        chain = self.stack if self.stack else []
        root = self.scan.root if self.scan else ""
        segs = [(root, 0)] + [(n[NAME], i) for i, n in enumerate(chain[1:], 1)]
        sep = "  ›  "
        sepw = self.fm(f).horizontalAdvance(sep)
        widths = [self.fm(f).horizontalAdvance(s) for s, _ in segs]
        limit = AREA.width()
        shown = list(range(len(segs)))
        while len(shown) > 2 and sum(widths[i] for i in shown) + sepw * len(shown) + 40 > limit:
            shown.pop(1)
        x = AREA.left()
        for n, i in enumerate(shown):
            if n:
                if shown[n - 1] != i - 1:
                    x += self.text(p, x, y, "  ›  …", f, DIM, valign="mid")
                x += self.text(p, x, y, sep, f, DIM, valign="mid")
            s, depth = segs[i]
            last = i == len(segs) - 1
            r = QRectF(x - 4, y - 16, widths[i] + 8, 32)
            over = (not last) and state == "done" and self.hit(r, f"crumb:{depth}")
            col = self.acc if last else (TEXT if over else DIM)
            x += self.text(p, x, y, s, f, col, valign="mid", glow=last)

    def _map(self, p: QPainter, now: float) -> None:
        view = self.view()
        if view is None:
            return
        lay = self.layout(view)
        pix = self.map_pix(view, p)
        src = QRectF(0, 0, pix.width(), pix.height())
        base = p.opacity()
        tr = self.trans
        if tr is not None:
            t = (now - tr["t0"]) / 0.38
            if t >= 1.0:
                self.trans = None
            else:
                e = _ease(t)
                p.save()
                p.setClipRect(AREA)
                if tr["old"] is not None and not tr["old"].isNull():
                    old = tr["old"]
                    p.setOpacity(base * (1 - e))
                    p.drawPixmap(_lerp_rect(tr["of"], tr["ot"], e), old, QRectF(0, 0, old.width(), old.height()))
                p.setOpacity(base * min(1.0, e * 1.5))
                p.drawPixmap(_lerp_rect(tr["nf"], tr["nt"], e), pix, src)
                p.restore()
                self.cur_pix = pix
                return
        self.cur_pix = pix
        if self.reveal_t0:
            t = (now - self.reveal_t0) / 0.9
            if t >= 1.0:
                self.reveal_t0 = 0.0
            else:
                e = _ease(t)
                x = AREA.left() + AREA.width() * e
                p.save()
                p.setClipRect(QRectF(AREA.left(), AREA.top(), x - AREA.left(), AREA.height()))
                p.drawPixmap(AREA, pix, src)
                p.restore()
                self.stroke(p, lambda: p.drawLine(QPointF(x, AREA.top()), QPointF(x, AREA.bottom())),
                            self.acc, 3)
                return
        if not lay:
            self.text(p, AREA.center().x(), AREA.center().y(), "EMPTY FOLDER", self.font("display", 40),
                      DIM, align="c", valign="mid")
            return
        p.drawPixmap(AREA, pix, src)
        busy = self.sub is not None
        hov_r = hov_s = None
        for i, (r, k, inner) in enumerate(lay):
            self.hit(r, f"t:{i}")
            on = r.contains(self.mouse)
            if on:
                hov_r = r
            for j, (rr, c, _n) in enumerate(inner):
                self.hit(rr, f"s:{i}:{j}")
            if on:
                trail = _under(inner, self.mouse)
                if trail:
                    hov_s = trail[-1][0]
        if self.sel is not None:
            found = _locate(lay, self.sel)
            if found is not None:
                self.stroke(p, lambda: p.drawRect(found), self.acc, 3)
        if not busy:
            if hov_r is not None:
                self.stroke(p, lambda: p.drawRect(hov_r), self.acc, 2)
            if hov_s is not None:
                self.stroke(p, lambda: p.drawRect(hov_s), TEXT, 1.5, glow=False)
        if busy:
            sc = self.sub["scan"]
            with sc.lock:
                n = sc.prog.get("files", 0)
            p.fillRect(AREA, _q(BG, 170))
            pulse = 0.5 + 0.5 * math.sin(now * 4)
            self.text(p, AREA.center().x(), AREA.center().y() - 20,
                      self.elide(f"SCANNING  {self.sub['chain'][-1][NAME]}", self.font("display", 34), 900),
                      self.font("display", 34), _mix(DIM, self.acc, pulse), align="c", valign="mid", glow=True)
            self.text(p, AREA.center().x(), AREA.center().y() + 26, f"{n:,} FILES",
                      self.font("mono", 18), DIM, align="c", valign="mid")

    def _scanning(self, p: QPainter, now: float, prog: dict, sc: _Scan) -> None:
        """The top folders as they are being counted: each grows while it is
        read (dashed, pulsing) and turns solid when it is complete."""
        done_b = int(prog.get("bytes") or 0)
        items = [(t[1], t) for t in (prog.get("tops") or []) if t[1] > 0]
        rem = max(0, sc.used - done_b) if sc.used else 0
        if rem:
            items.append((rem, None))
        items.sort(key=lambda x: -x[0])
        if not items:
            pulse = 0.5 + 0.5 * math.sin(now * 3)
            self.text(p, AREA.center().x(), AREA.center().y(), "SCANNING", self.font("display", 52),
                      _mix(DIM, self.acc, pulse), align="c", valign="mid", glow=True)
            return
        pulse = 0.5 + 0.5 * math.sin(now * 3)
        for r, (size, t) in zip(squarify([s for s, _ in items], AREA), items):
            r = r.adjusted(1.5, 1.5, -1.5, -1.5)
            if r.width() < 1 or r.height() < 1:
                continue
            if t is None:
                p.setPen(QPen(_q(self.acc, int(60 + 120 * pulse)), 1.5, Qt.PenStyle.DashLine))
                p.setBrush(_q(_mix(BG, self.acc, 0.05)))
                p.drawRect(r)
                if r.width() > 120 and r.height() > 60:
                    self.text(p, r.center().x(), r.center().y() - 14, "READING…", self.font("display", 26),
                              _mix(DIM, self.acc, pulse), align="c", valign="mid")
                    self.text(p, r.center().x(), r.center().y() + 18, fmt_size(size) + " TO GO",
                              self.font("mono", 16), DIM, align="c", valign="mid")
                continue
            born = self._seen.setdefault(t[0], now)
            e = _ease((now - born) / 0.45)
            node = [t[0], size, 0, 0, t[2], None]
            if e < 1.0:
                c = r.center()
                k = 0.6 + 0.4 * e
                r = QRectF(c.x() - r.width() * k / 2, c.y() - r.height() * k / 2, r.width() * k, r.height() * k)
                p.save()
                p.setOpacity(p.opacity() * e)
            self.block(p, r, node, 0)
            if not t[3]:                         # still being read
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(_q(TEXT, int(40 + 110 * pulse)), 1.5, Qt.PenStyle.DashLine))
                p.drawRect(r.adjusted(2, 2, -2, -2))
            self.label(p, r, node, 0, False)
            if e < 1.0:
                p.restore()

    def _side(self, p: QPainter, now: float, state: str, prog: dict, sc: _Scan) -> None:
        s = sc.sum
        cx, cy, rad = SX + 78, 292, 70
        if state == "running":
            frac = min(0.99, prog.get("bytes", 0) / sc.used) if sc.used else None
            box = QRectF(cx - rad, cy - rad, 2 * rad, 2 * rad)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_q(_mix(BG, self.acc, 0.18)), 12))
            p.drawEllipse(box)
            if frac is None:
                start = int((now * 220) % 360 * 16)
                self.stroke(p, lambda: p.drawArc(box, -start, 90 * 16), self.acc, 12, cap=Qt.PenCapStyle.FlatCap)
                value = "…"
            else:
                span = -int(frac * 360 * 16)
                if frac > 0.003:
                    self.stroke(p, lambda: p.drawArc(box, 90 * 16, span), self.acc, 12, cap=Qt.PenCapStyle.FlatCap)
                value = f"{frac * 100:.0f}%"
            self.text(p, cx, cy - 6, value, self.font("display", 38), TEXT, align="c", valign="mid", glow=True)
            self.text(p, cx, cy + 30, "SCANNING", self.font("mono", 14), self.acc, align="c", valign="mid")
            x = SX + 176
            self.text(p, x, 236, fmt_size(prog.get("bytes", 0)), self.font("display", 32), TEXT)
            self.text(p, x, 280, f"{prog.get('files', 0):,} files", self.font("mono", 17), DIM)
            self.text(p, x, 306, f"{prog.get('dirs', 0):,} folders", self.font("mono", 17), DIM)
            self.text(p, x, 332, f"{now - sc.t0:.0f} s", self.font("mono", 17), DIM)
            return
        if state != "done":
            return
        if s.get("mount") and s.get("total"):
            pct, label = s["used"] / s["total"] * 100, "USED"
            line1, line2 = fmt_size(s["used"]), f"of {fmt_size(s['total'])}"
            line3 = f"FREE {fmt_size(s['free'])}"
        else:
            try:
                du = shutil.disk_usage(sc.root)
                pct = s.get("scanned", 0) / max(1, du.total) * 100
            except OSError:
                pct = 0.0
            label, line1, line2 = "OF DRIVE", fmt_size(s.get("scanned", 0)), "in this folder"
            line3 = f"{s.get('files', 0):,} FILES"
        self.ring(p, cx, cy, rad, pct, label, f"{pct:.0f}%")
        x = SX + 176
        self.text(p, x, 240, line1, self.font("display", 32), TEXT, glow=True)
        self.text(p, x, 284, line2, self.font("mono", 17), DIM)
        self.text(p, x, 318, line3, self.font("mono", 18), lb._level(pct, self.acc) if s.get("mount") else self.acc)

        # file types, over the whole scan
        y = 404
        self.text(p, SX, y, "FILE TYPES", self.font("mono", 15), DIM)
        cats = s.get("cats") or [0] * len(CATS)
        tot = max(1, sum(cats))
        x0, bw = SX, SR - SX
        p.setPen(Qt.PenStyle.NoPen)
        for i, v in enumerate(cats):
            w = bw * v / tot
            if w > 0.5:
                p.setBrush(_q(CATS[i][1]))
                p.drawRect(QRectF(x0, y + 28, w, 14))
                x0 += w
        order = sorted(range(len(CATS)), key=lambda i: -cats[i])
        for n, i in enumerate(order):
            cx2 = SX + (n % 2) * (bw / 2 + 6)
            cy2 = y + 60 + (n // 2) * 26
            p.setBrush(_q(CATS[i][1]))
            p.drawRect(QRectF(cx2, cy2 + 2, 11, 11))
            self.text(p, cx2 + 18, cy2, CATS[i][0], self.font("mono", 13), TEXT if cats[i] else DIM)
            self.text(p, cx2 + bw / 2 - 12, cy2, fmt_size(cats[i]), self.font("mono", 13), DIM, align="r")

        # the biggest things in the folder on show
        view = self.view()
        if view is None:
            return
        y = 590
        name = view[NAME] if len(self.stack) > 1 else (s.get("root") or "")
        f = self.font("mono", 15)
        self.text(p, SX, y, self.elide(f"LARGEST IN {name}".upper(), f, SR - SX), f, DIM)
        lay = self.layout(view)
        top = max((k[SIZE] for _r, k, _i in lay[:7]), default=1) or 1
        for i, (r, k, _inner) in enumerate(lay[:7]):
            ry = y + 30 + i * 37
            row = QRectF(SX - 6, ry - 4, SR - SX + 12, 35)
            over = self.trans is None and self.hit(row, f"t:{i}")
            if over or k is self.sel:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_q(_mix(BG, self.acc, 0.22 if over else 0.14)))
                p.drawRect(row)
            col = _colour(k)
            fs = self.font("mono", 14)
            size = fmt_size(k[SIZE])
            sw = self.fm(fs).horizontalAdvance(size)
            fb = self.font("body", 16)
            self.text(p, SX, ry, self.elide(_label(k), fb, SR - SX - sw - 16), fb, TEXT)
            self.text(p, SR, ry + 2, size, fs, col, align="r")
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_q(_mix(BG, col, 0.25)))
            p.drawRect(QRectF(SX, ry + 25, SR - SX, 4))
            p.setBrush(_q(col))
            p.drawRect(QRectF(SX, ry + 25, (SR - SX) * k[SIZE] / top, 4))

        # buttons
        bw3 = (SR - SX - 24) / 3
        for n, (key, text, enabled) in enumerate((("up", "◂ BACK", len(self.stack) > 1),
                                                  ("explorer", "EXPLORER", True),
                                                  ("rescan", "RESCAN", True))):
            r = QRectF(SX + n * (bw3 + 12), 878, bw3, 40)
            over = enabled and self.hit(r, key)
            self.panel(p, r, 0.28 if over else 0.10, active=over)
            self.text(p, r.center().x(), r.center().y(), text, self.font("mono", 15),
                      (TEXT if over else _mix(DIM, TEXT, 0.4)) if enabled else _mix(BG, DIM, 0.5),
                      align="c", valign="mid")

    def _status(self, p: QPainter, now: float, state: str, prog: dict) -> None:
        f = self.font("mono", 16)
        y = 948
        msg, t0 = self.flash
        if msg and now - t0 < 4:
            self.text(p, AREA.left(), y, self.elide(msg, f, AREA.width()), f, self.acc, valign="mid")
            return
        if state == "running":
            self.text(p, AREA.left(), y, self.elide("›  " + str(prog.get("cur") or ""), f, AREA.width()),
                      f, DIM, valign="mid")
            return
        if state != "done" or self.scan is None:
            return
        s = self.scan.sum
        bits = [f"SCANNED IN {s.get('secs')} S"]
        if s.get("errors"):
            bits.append(f"{s['errors']:,} FOLDERS UNREADABLE")
        if s.get("cloud"):
            bits.append(f"{fmt_size(s['cloud'])} IN THE CLOUD")
        bits.append("CLICK: OPEN  ·  RIGHT-CLICK: BACK")
        self.text(p, AREA.left(), y, self.elide("   ·   ".join(bits), f, AREA.width()), f, DIM, valign="mid")

    def _tooltip(self, p: QPainter) -> None:
        view = self.view()
        if view is None or not AREA.contains(self.mouse) or self.sub is not None:
            return
        trail = _under(self.layout(view), self.mouse)
        node = trail[-1][1] if trail else None
        if node is None:
            return
        whole = max(1, view[SIZE])
        lines = [(_label(node), self.font("display", 22), TEXT)]
        where = view[NAME] if len(self.stack) > 1 else (self.scan.root if self.scan else "")
        lines.append((f"{fmt_size(node[SIZE])}  ·  {node[SIZE] / whole * 100:.1f}% of {where}",
                      self.font("mono", 15), _colour(node)))
        if node[KIND] == 0:
            hint = (f"{node[COUNT]:,} files  ·  "
                    + ("click to scan inside" if node[KIDS] is None else "click to open"))
        elif node[KIND] == 1:
            hint = CATS[node[CAT]][0].lower() + " file  ·  click to select"
        elif node[KIND] == 2:
            hint = "too small to draw one by one"
        else:
            hint = "space the scan could not read: system files, other users, restore points"
        lines.append((hint, self.font("mono", 14), DIM))
        w = min(560.0, max(self.fm(f).horizontalAdvance(s) for s, f, _c in lines) + 28)
        h = 18 + 30 + 24 + 22
        x = self.mouse.x() + 20
        y = self.mouse.y() + 22
        if x + w > W_CANVAS - 40:
            x = self.mouse.x() - w - 20
        if y + h > 985:
            y = self.mouse.y() - h - 16
        box = QRectF(x, y, w, h)
        p.setPen(QPen(_q(_colour(node)), 1.5))
        p.setBrush(_q(_mix(BG, _colour(node), 0.14), 240))
        p.drawRoundedRect(box, 6, 6)
        ty = y + 10
        for s, f, c in lines:
            self.text(p, x + 14, ty, self.elide(s, f, w - 28), f, c)
            ty += 30 if f is lines[0][1] else 24


W_CANVAS = lb.W


# ─────────────────────────────────────────────────────────────────────────────
# Entry points (safe from any thread)
# ─────────────────────────────────────────────────────────────────────────────

def board() -> Optional[DiskBoard]:
    b = lb._boards.get("disk")
    if b is None or sip.isdeleted(b):
        return None
    return b


def visible() -> bool:
    return bool(lb._gui(lambda: bool(board() and board().isVisible() and board().fade_to > 0), timeout=3.0))


def show(player, sc: _Scan) -> bool:
    return bool(lb._gui(lambda: lb._open(player, "disk", DiskBoard, lambda b: b.set_scan(sc))))


def focus(query: str) -> Any:
    return lb._gui(lambda: board().focus(query) if board() else None)


def up() -> None:
    lb._gui(lambda: board().up() if board() else None)
