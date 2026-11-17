"""
Connection diagrams for build_guide — drawn here, never by an image model.

WHY THIS FILE DRAWS AND A MODEL DOES NOT
    An image model asked for "a wiring diagram for an Arduino and a servo"
    returns something that looks exactly like one: boxes, coloured wires,
    labels. The labels are invented. Pins that do not exist, a 5V wire landing
    on a pin printed "A0", a ground that goes nowhere. It is convincing in
    precisely the way that makes somebody plug it in — and a wrong wire on a
    board is not a typo, it is a burnt regulator.

    So the model only DESCRIBES the build: parts, the pins as printed on them,
    and which pin goes to which. This file turns that description into a
    picture with no judgement of its own. Every label on the diagram is a label
    the model committed to in text, which the user can read, question and
    correct — and nothing on it can be more wrong than the words were.

WHAT THE LAYOUT DOES
    The part with the most connections sits in the middle (a microcontroller,
    a relay board, a frame — whatever the build revolves around). Everything
    else stacks in a column either side, balanced by how many wires it has.
    Pins face the wire they carry:

        side part  ↔ centre part    straight across the gap when their rows
                                    line up, otherwise down a lane in the gap
        side part  ↔ same side      out along the outer edge
        left side  ↔ right side     down a channel under everything
        a part     ↔ itself         a short loop on its free edge

    The centre part's pins are placed at the height of the pins they connect
    to, so most wires come out straight. Every wire carries a numbered badge
    next to the part it leaves, and the steps in the content panel use the same
    numbers — that pairing is what makes the picture followable.

WHY NO WORD ON THE PICTURE COMES FROM THIS FILE
    The title, the part names and the legend are all supplied by the caller,
    in the user's language, from the model's answer. A legend word missing from
    that answer is left off the picture rather than filled in with English.
    Text is laid out by Qt, which shapes and falls back across scripts on every
    operating system — Arabic, Devanagari and CJK names render without this
    file knowing any of them exist.

THREADS
    Drawing text shares the application's font caches, so the picture is drawn
    on the Qt thread: a plugin's run() is handed over through a queued signal
    and waits for it. It takes a fraction of a second, and it is the one choice
    that behaves the same on every platform's font engine.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional

# ── Geometry (logical pixels; the image is rendered at 2× for sharpness) ─────

MARGIN = 36
TITLE_H = 56
HEADER = 48          # part name + detail line
ROW = 30             # one pin row
BOX_PAD = 12
VGAP = 34            # between parts stacked in one column
LANE = 13            # spacing between parallel routed wires
GAP_MIN = 110        # between columns
BOX_MIN_W = 150
BOX_MAX_W = 320
BADGE_R = 10
LEGEND_H = 44
MAX_PHYSICAL = 4096  # longest side of the saved image, in pixels

# ── Colours (dark, to sit on the HUD) ────────────────────────────────────────

BG = "#0a0f14"
GRID = "#101922"
BOX_FILL = "#111b24"
BOX_EDGE_DIM = "#2a3a48"
TEXT = "#eaf2f7"
TEXT_DIM = "#8ba3b5"
PIN_TEXT = "#d4e1ea"
POWER = "#ff4d4d"
GROUND = "#a7b1b8"
MECHANICAL = "#c9b38f"
SIGNALS = ("#ffd23f", "#3fd0ff", "#7dff6a", "#ff9f40", "#c58bff",
           "#ff6ad5", "#40ffc8", "#f5f57a", "#8fb4ff")
OK = "#3ddc84"
WRONG = "#ff4d4d"
UNCLEAR = "#ffb020"

KINDS = ("power", "ground", "signal", "data", "analog", "mechanical", "other")
_SIGNAL_KINDS = ("signal", "data", "analog", "other")


def kind_color(kind: str, index: int) -> str:
    if kind == "power":
        return POWER
    if kind == "ground":
        return GROUND
    if kind == "mechanical":
        return MECHANICAL
    return SIGNALS[index % len(SIGNALS)]


# ─────────────────────────────────────────────────────────────────────────────
# Layout — pure Python, no Qt, so it can be tested and reasoned about alone
# ─────────────────────────────────────────────────────────────────────────────

def layout(plan: dict, measure: Callable[[str, str], float]) -> dict:
    """Place every part, pin and wire.

    `plan` is build_guide's normalised plan: components with pins, connections
    whose endpoints name a component id and a pin id. `measure(text, role)`
    returns a text width for a role ('name', 'detail', 'pin').

    Returns boxes, pin positions, wire polylines and the canvas size. Nothing
    here draws.
    """
    comps = [c for c in plan.get("components", []) if c.get("id")]
    by_id = {c["id"]: c for c in comps}
    conns = [w for w in plan.get("connections", [])
             if w["a"][0] in by_id and w["b"][0] in by_id]

    # Only parts that carry a wire are drawn: a part named in the answer but
    # not wired (a battery "you will need later") is in the text, not the map.
    degree: dict[str, int] = {}
    for w in conns:
        degree[w["a"][0]] = degree.get(w["a"][0], 0) + 1
        if w["b"][0] != w["a"][0]:
            degree[w["b"][0]] = degree.get(w["b"][0], 0) + 1
    drawn = [c for c in comps if c["id"] in degree]
    if not drawn:
        return {"empty": True}

    order = {c["id"]: i for i, c in enumerate(comps)}
    hub = max(drawn, key=lambda c: (degree[c["id"]], -order[c["id"]]))["id"]

    # Balance the others by wire count; ties go to the side with fewer parts.
    col: dict[str, str] = {hub: "C"}
    load = {"L": 0, "R": 0}
    count = {"L": 0, "R": 0}
    for c in sorted((c for c in drawn if c["id"] != hub),
                    key=lambda c: (-degree[c["id"]], order[c["id"]])):
        side = min(("L", "R"), key=lambda s: (load[s], count[s], s != "L"))
        col[c["id"]] = side
        load[side] += degree[c["id"]]
        count[side] += 1

    def inner(cid: str) -> str:
        return "R" if col[cid] == "L" else "L"

    def outer(cid: str) -> str:
        return "L" if col[cid] == "L" else "R"

    # ── which edge each endpoint sits on, and how the wire travels ───────────
    routes = []
    for w in conns:
        (ua, pa), (ub, pb) = w["a"], w["b"]
        if ua == ub:
            s = "R" if col[ua] == "C" else outer(ua)
            routes.append((w, (ua, pa, s), (ub, pb, s), "loop"))
        elif col[ua] == "C" or col[ub] == "C":
            if col[ua] == "C":           # keep the side part first
                (ua, pa), (ub, pb) = (ub, pb), (ua, pa)
            routes.append((w, (ua, pa, inner(ua)), (ub, pb, col[ua]), "direct"))
        elif col[ua] == col[ub]:
            routes.append((w, (ua, pa, outer(ua)), (ub, pb, outer(ub)), "outer"))
        else:
            if col[ua] == "R":           # keep the left part first
                (ua, pa), (ub, pb) = (ub, pb), (ua, pa)
            routes.append((w, (ua, pa, inner(ua)), (ub, pb, inner(ub)), "bottom"))

    def pin_index(cid: str, pid: str) -> int:
        for i, p in enumerate(by_id[cid].get("pins", [])):
            if p["id"] == pid:
                return i
        return 999

    # ── pin rows per edge, ordered to keep wires from crossing ───────────────
    edge_keys: dict[tuple[str, str], dict[str, float]] = {}

    def want(cid: str, pid: str, side: str, key: float) -> None:
        keys = edge_keys.setdefault((cid, side), {})
        keys[pid] = min(keys.get(pid, key), key)

    for w, a, b, kind in routes:
        if kind == "direct":
            want(a[0], a[1], a[2], pin_index(b[0], b[1]))
            want(b[0], b[1], b[2], pin_index(b[0], b[1]))
        elif kind == "bottom":
            want(a[0], a[1], a[2], 10_000 + pin_index(a[0], a[1]))
            want(b[0], b[1], b[2], 10_000 + pin_index(b[0], b[1]))
        else:
            want(a[0], a[1], a[2], pin_index(a[0], a[1]))
            want(b[0], b[1], b[2], pin_index(b[0], b[1]))

    def edge_pins(cid: str, side: str) -> list[str]:
        keys = edge_keys.get((cid, side), {})
        return sorted(keys, key=lambda pid: (keys[pid], pin_index(cid, pid)))

    # ── box widths ───────────────────────────────────────────────────────────
    def label(cid: str, pid: str) -> str:
        for p in by_id[cid].get("pins", []):
            if p["id"] == pid:
                return p.get("label") or pid
        return pid

    width: dict[str, float] = {}
    for c in drawn:
        cid = c["id"]
        left = max((measure(label(cid, p), "pin") for p in edge_pins(cid, "L")), default=0)
        right = max((measure(label(cid, p), "pin") for p in edge_pins(cid, "R")), default=0)
        pins_w = left + right + (56 if left and right else 34)
        head_w = max(measure(c.get("name", ""), "name"),
                     measure(c.get("detail", ""), "detail")) + 30
        width[cid] = max(BOX_MIN_W, min(BOX_MAX_W, max(pins_w, head_w)))

    # ── vertical: side columns stack, the centre follows its wires ───────────
    top0 = MARGIN + TITLE_H
    box: dict[str, list[float]] = {}          # cid -> [x, y, w, h]
    pin_y: dict[tuple[str, str, str], float] = {}

    for side in ("L", "R"):
        y = top0
        for c in (c for c in drawn if col[c["id"]] == side):
            cid = c["id"]
            rows = max(len(edge_pins(cid, "L")), len(edge_pins(cid, "R")), 1)
            h = HEADER + rows * ROW + BOX_PAD
            box[cid] = [0.0, y, width[cid], h]
            for s in ("L", "R"):
                for k, pid in enumerate(edge_pins(cid, s)):
                    pin_y[(cid, pid, s)] = y + HEADER + ROW * (k + 0.5)
            y += h + VGAP

    for s in ("L", "R"):
        desired: dict[str, list[float]] = {}
        for w, a, b, kind in routes:
            if kind == "direct" and b[2] == s:
                desired.setdefault(b[1], []).append(pin_y[(a[0], a[1], a[2])])
        pins = edge_pins(hub, s)
        placed = []
        fallback = top0 + HEADER + ROW * 0.5
        for pid in pins:
            ys = desired.get(pid)
            placed.append((sum(ys) / len(ys) if ys else float("inf"), pin_index(hub, pid), pid))
        placed.sort()
        last = None
        for d, _i, pid in placed:
            if d == float("inf"):
                d = (last + ROW) if last is not None else fallback
            y = max(d, fallback, (last + ROW) if last is not None else fallback)
            pin_y[(hub, pid, s)] = y
            last = y
    hub_pin_ys = [y for (cid, _p, _s), y in pin_y.items() if cid == hub]
    hub_top = top0
    hub_bottom = (max(hub_pin_ys) + ROW * 0.5 + BOX_PAD) if hub_pin_ys \
        else top0 + HEADER + ROW + BOX_PAD
    box[hub] = [0.0, hub_top, width[hub], hub_bottom - hub_top]

    content_bottom = max(b[1] + b[3] for b in box.values())

    # ── lanes ────────────────────────────────────────────────────────────────
    lanes = {"outer_L": [], "gap_L": [], "gap_R": [], "outer_R": [], "bottom": []}
    for i, (w, a, b, kind) in enumerate(routes):
        if kind == "direct":
            if abs(pin_y[a] - pin_y[b]) > 0.5:
                lanes["gap_" + col[a[0]]].append(i)
        elif kind == "outer":
            lanes["outer_" + col[a[0]]].append(i)
        elif kind == "bottom":
            lanes["gap_L"].append(i)
            lanes["gap_R"].append(i)
            lanes["bottom"].append(i)
        elif kind == "loop":
            key = "gap_R" if col[a[0]] == "C" else "outer_" + col[a[0]]
            lanes[key].append(i)

    has_l = any(v == "L" for v in col.values())
    has_r = any(v == "R" for v in col.values())
    wl = max((width[c] for c, v in col.items() if v == "L"), default=0)
    wr = max((width[c] for c, v in col.items() if v == "R"), default=0)

    def lane_span(n: int) -> float:
        return (n + 1) * LANE if n else 0

    x = MARGIN + lane_span(len(lanes["outer_L"]))
    xl_inner = x + wl
    gap_l = max(GAP_MIN, lane_span(len(lanes["gap_L"])) + 60) if has_l else 0
    hub_x = xl_inner + gap_l if has_l else x
    box[hub][0] = hub_x
    hub_right = hub_x + width[hub]
    gap_r = max(GAP_MIN, lane_span(len(lanes["gap_R"])) + 60) \
        if (has_r or lanes["gap_R"]) else 0
    xr = hub_right + gap_r
    for cid, v in col.items():
        if v == "L":
            box[cid][0] = xl_inner - width[cid]      # inner edges line up
        elif v == "R":
            box[cid][0] = xr
    right_edge = (xr + wr) if has_r else hub_right + gap_r
    canvas_w = right_edge + lane_span(len(lanes["outer_R"])) + MARGIN

    def lane_x(key: str, i: int) -> float:
        members = lanes[key]
        k = members.index(i)
        n = len(members)
        if key == "outer_L":
            return x - LANE * (k + 1)
        if key == "outer_R":
            return right_edge + LANE * (k + 1)
        mid = (xl_inner + gap_l / 2) if key == "gap_L" else (hub_right + gap_r / 2)
        return mid + (k - (n - 1) / 2) * LANE

    def pin_point(end: tuple[str, str, str]) -> tuple[float, float]:
        bx, _by, bw, _bh = box[end[0]]
        return (bx if end[2] == "L" else bx + bw), pin_y[end]

    # ── wire polylines ───────────────────────────────────────────────────────
    wires = []
    signal_i = 0
    for i, (w, a, b, kind) in enumerate(routes):
        pa, pb = pin_point(a), pin_point(b)
        if kind == "direct":
            if i in lanes["gap_" + col[a[0]]]:
                lx = lane_x("gap_" + col[a[0]], i)
                pts = [pa, (lx, pa[1]), (lx, pb[1]), pb]
            else:
                pts = [pa, pb]
        elif kind == "outer":
            lx = lane_x("outer_" + col[a[0]], i)
            pts = [pa, (lx, pa[1]), (lx, pb[1]), pb]
        elif kind == "loop":
            key = "gap_R" if col[a[0]] == "C" else "outer_" + col[a[0]]
            lx = lane_x(key, i)
            pts = [pa, (lx, pa[1]), (lx, pb[1]), pb]
        else:  # bottom
            k = lanes["bottom"].index(i)
            by = content_bottom + 22 + k * LANE
            l1, l2 = lane_x("gap_L", i), lane_x("gap_R", i)
            pts = [pa, (l1, pa[1]), (l1, by), (l2, by), (l2, pb[1]), pb]
        color = kind_color(w.get("kind", "signal"), signal_i)
        if w.get("kind", "signal") in _SIGNAL_KINDS:
            signal_i += 1
        wires.append({"id": w["id"], "n": w["n"], "kind": w.get("kind", "signal"),
                      "color": color, "points": pts, "start": a, "end": b})

    # Badges beside the part each wire leaves, pushed outward when several
    # wires leave the same pin, so no two ever sit on top of each other.
    used: dict[tuple, int] = {}
    for wire in wires:
        a = wire["start"]
        k = used.get(a, 0)
        used[a] = k + 1
        px, py = wire["points"][0]
        nx, _ny = wire["points"][1]
        direction = 1 if nx >= px else -1
        wire["badge"] = (px + direction * (BADGE_R + 8 + k * (BADGE_R * 2 + 4)), py)

    bottom = content_bottom + (22 + len(lanes["bottom"]) * LANE if lanes["bottom"] else 0)
    canvas_h = bottom + 26 + LEGEND_H + MARGIN
    pins_out = {}
    for (cid, pid, s), y in pin_y.items():
        pins_out[(cid, pid, s)] = (pin_point((cid, pid, s)), label(cid, pid))

    return {
        "empty": False, "width": canvas_w, "height": canvas_h,
        "boxes": {cid: tuple(v) for cid, v in box.items()},
        "hub": hub, "columns": col, "pins": pins_out, "wires": wires,
        "legend_y": bottom + 26,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Drawing
# ─────────────────────────────────────────────────────────────────────────────

def render(plan: dict, out_path: str | Path, *, accent: str = "#38d6ff",
           highlight: Optional[set] = None, status: Optional[dict] = None,
           legend: Optional[dict] = None) -> Optional[dict]:
    """Draw the plan's connections to a PNG.

    `highlight` — wire ids to emphasise; the rest are dimmed.
    `status`    — wire id -> 'ok' | 'wrong' | 'missing' | 'unclear', from a check.
    `legend`    — kind -> word in the user's language; kinds without a word are
                  left out of the legend rather than named in English.

    Returns {"path", "width", "height", "wires"} or None when there is nothing
    to draw or no Qt to draw with. Never raises.
    """
    try:
        from PyQt6.QtCore import QThread
        from PyQt6.QtGui import QGuiApplication
    except Exception:
        return None
    if QGuiApplication.instance() is None:
        return None

    job = lambda: _render_now(plan, Path(out_path), accent, highlight or set(),
                              status or {}, legend or {})
    if QGuiApplication.instance().thread() is QThread.currentThread():
        return _safe(job)
    return _on_gui_thread(lambda: _safe(job))


def _safe(job):
    try:
        return job()
    except Exception as exc:
        try:
            print(f"[build_guide] diagram could not be drawn: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return None


def _on_gui_thread(job, timeout: float = 20.0):
    """Run `job` on the Qt thread and wait for its result."""
    from PyQt6.QtCore import QObject, pyqtSignal
    from PyQt6.QtGui import QGuiApplication

    done = threading.Event()
    box: dict = {}

    class _Bridge(QObject):
        fire = pyqtSignal()

    bridge = _Bridge()

    def _slot():
        try:
            box["result"] = job()
        finally:
            done.set()

    bridge.moveToThread(QGuiApplication.instance().thread())
    bridge.fire.connect(_slot)
    bridge.fire.emit()
    done.wait(timeout)
    return box.get("result")


def _render_now(plan, out_path: Path, accent, highlight, status, legend):
    from PyQt6.QtCore import QPointF, QRectF, Qt
    from PyQt6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QGuiApplication,
                             QImage, QPainter, QPainterPath, QPen, QPolygonF)

    base = QGuiApplication.font()
    fonts = {}
    for role, px, bold in (("title", 22, True), ("name", 15, True), ("detail", 12, False),
                           ("pin", 13, False), ("badge", 11, True), ("legend", 13, False)):
        f = QFont(base)
        f.setPixelSize(px)
        f.setBold(bold)
        fonts[role] = f
    metrics = {role: QFontMetricsF(f) for role, f in fonts.items()}

    def measure(text: str, role: str) -> float:
        return metrics[role].horizontalAdvance(text or "")

    geo = layout(plan, measure)
    if geo.get("empty"):
        return None

    title = str(plan.get("title") or "")
    width = max(geo["width"], measure(title, "title") + 2 * MARGIN, 420)
    height = geo["height"]
    scale = min(2.0, MAX_PHYSICAL / max(width, height))
    img = QImage(int(width * scale), int(height * scale),
                 QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(BG))

    p = QPainter(img)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.scale(scale, scale)
        accent_c = QColor(accent) if QColor(accent).isValid() else QColor("#38d6ff")

        # faint grid, like a cutting mat
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(GRID))
        step = 24
        for gx in range(0, int(width), step):
            for gy in range(0, int(height), step):
                p.drawEllipse(QPointF(gx, gy), 1.1, 1.1)

        # title
        p.setFont(fonts["title"])
        p.setPen(QColor(TEXT))
        p.drawText(QRectF(MARGIN, MARGIN - 6, width - 2 * MARGIN, TITLE_H - 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   metrics["title"].elidedText(title, Qt.TextElideMode.ElideRight,
                                               width - 2 * MARGIN))
        p.setPen(QPen(accent_c, 2))
        p.drawLine(QPointF(MARGIN, MARGIN + TITLE_H - 18),
                   QPointF(MARGIN + 64, MARGIN + TITLE_H - 18))

        # wires, under the boxes — what the user must notice drawn last, on top
        def _weight(wire):
            if status.get(wire["id"]) == "wrong":
                return 3
            if highlight and wire["id"] in highlight:
                return 2
            return 0 if (highlight and wire["id"] not in highlight) else 1
        for wire in sorted(geo["wires"], key=_weight):
            st = status.get(wire["id"], "")
            dim = bool(highlight) and wire["id"] not in highlight
            color = QColor(WRONG if st == "wrong" else wire["color"])
            if dim or st == "missing":
                color.setAlpha(70)
            path = QPainterPath()
            pts = wire["points"]
            path.moveTo(QPointF(*pts[0]))
            for pt in pts[1:]:
                path.lineTo(QPointF(*pt))
            thick = 5.0 if (highlight and not dim) else 3.2
            under = QPen(QColor(BG), thick + 3.5)
            under.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            under.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(under)
            p.drawPath(path)
            pen = QPen(color, thick)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            if st in ("missing", "wrong") or wire["kind"] == "mechanical":
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawPath(path)

        # boxes
        for cid, (bx, by, bw, bh) in geo["boxes"].items():
            comp = next(c for c in plan["components"] if c["id"] == cid)
            unsure = float(comp.get("confidence", 1.0)) < 0.6
            edge = QColor(accent_c if cid == geo["hub"] else BOX_EDGE_DIM)
            pen = QPen(edge, 1.8 if cid == geo["hub"] else 1.4)
            if unsure:
                pen.setStyle(Qt.PenStyle.DashLine)
                pen.setColor(QColor(UNCLEAR))
            p.setPen(pen)
            p.setBrush(QColor(BOX_FILL))
            p.drawRoundedRect(QRectF(bx, by, bw, bh), 8, 8)
            p.setPen(Qt.PenStyle.NoPen)
            head = QColor(accent_c)
            head.setAlpha(34)
            p.setBrush(head)
            hp = QPainterPath()
            hp.addRoundedRect(QRectF(bx + 1, by + 1, bw - 2, HEADER - 6), 7, 7)
            p.drawPath(hp)

            name = str(comp.get("name") or "")
            if unsure:
                name += " ?"
            p.setFont(fonts["name"])
            p.setPen(QColor(TEXT))
            p.drawText(QRectF(bx + 12, by + 5, bw - 24, 22),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       metrics["name"].elidedText(name, Qt.TextElideMode.ElideRight, bw - 24))
            detail = str(comp.get("detail") or "")
            if detail:
                p.setFont(fonts["detail"])
                p.setPen(QColor(TEXT_DIM))
                p.drawText(QRectF(bx + 12, by + 25, bw - 24, 16),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           metrics["detail"].elidedText(detail, Qt.TextElideMode.ElideRight,
                                                        bw - 24))

        # pins
        p.setFont(fonts["pin"])
        sides_used = {}
        for (cid, _pid, side) in geo["pins"]:
            sides_used.setdefault(cid, set()).add(side)
        for (cid, _pid, side), ((px, py), text) in geo["pins"].items():
            bx, _by, bw, _bh = geo["boxes"][cid]
            # A box with pins on one edge only lends that edge its whole width.
            half = (bw / 2 - 16) if len(sides_used[cid]) > 1 else bw - 28
            p.setPen(QColor(PIN_TEXT))
            text_e = metrics["pin"].elidedText(text, Qt.TextElideMode.ElideRight, half)
            if side == "L":
                p.drawText(QRectF(bx + 12, py - 10, half, 20),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text_e)
            else:
                p.drawText(QRectF(bx + bw - 12 - half, py - 10, half, 20),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, text_e)
            p.setPen(QPen(QColor(BG), 1.5))
            p.setBrush(QColor(TEXT))
            p.drawEllipse(QPointF(px, py), 4.6, 4.6)

        # numbered badges, with the check result drawn as a shape, not a glyph
        p.setFont(fonts["badge"])
        for wire in geo["wires"]:
            st = status.get(wire["id"], "")
            dim = bool(highlight) and wire["id"] not in highlight
            cx, cy = wire["badge"]
            fill = QColor({"ok": OK, "wrong": WRONG, "unclear": UNCLEAR}.get(st, wire["color"]))
            if dim:
                fill.setAlpha(90)
            p.setPen(QPen(QColor(BG), 2))
            p.setBrush(QBrush(fill))
            p.drawEllipse(QPointF(cx, cy), BADGE_R, BADGE_R)
            p.setPen(QColor("#061018"))
            p.drawText(QRectF(cx - BADGE_R, cy - BADGE_R, BADGE_R * 2, BADGE_R * 2),
                       Qt.AlignmentFlag.AlignCenter, str(wire["n"]))
            if st in ("ok", "wrong", "unclear"):
                mx, my = cx + BADGE_R + 1, cy - BADGE_R + 1
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(BG))
                p.drawEllipse(QPointF(mx, my), 6.5, 6.5)
                mark = QPen(fill, 2.2)
                mark.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(mark)
                if st == "ok":
                    p.drawPolyline(QPolygonF([QPointF(mx - 3.4, my), QPointF(mx - 1, my + 2.6),
                                              QPointF(mx + 3.6, my - 2.8)]))
                elif st == "wrong":
                    p.drawLine(QPointF(mx - 3, my - 3), QPointF(mx + 3, my + 3))
                    p.drawLine(QPointF(mx - 3, my + 3), QPointF(mx + 3, my - 3))
                else:
                    p.drawLine(QPointF(mx, my - 3.2), QPointF(mx, my + 0.8))
                    p.drawPoint(QPointF(mx, my + 3.2))

        # legend — only the kinds on the picture, only in words we were given
        present = []
        for wire in geo["wires"]:
            kind = wire["kind"] if wire["kind"] in ("power", "ground", "mechanical") else "signal"
            if kind not in present:
                present.append(kind)
        lx, ly = MARGIN, geo["legend_y"] + LEGEND_H / 2
        p.setFont(fonts["legend"])
        for kind in present:
            word = str(legend.get(kind) or "").strip()
            if not word:
                continue
            if kind == "signal":
                for j in range(3):
                    sp = QPen(QColor(SIGNALS[j]), 4)
                    sp.setCapStyle(Qt.PenCapStyle.RoundCap)
                    p.setPen(sp)
                    p.drawLine(QPointF(lx + j * 10, ly + 6), QPointF(lx + j * 10 + 7, ly - 6))
                sw = 30
            else:
                pen = QPen(QColor(kind_color(kind, 0)), 4)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                if kind == "mechanical":
                    pen.setStyle(Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.drawLine(QPointF(lx, ly), QPointF(lx + 24, ly))
                sw = 30
            p.setPen(QColor(TEXT_DIM))
            tw = measure(word, "legend")
            p.drawText(QRectF(lx + sw + 4, ly - 10, tw + 4, 20),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, word)
            lx += sw + tw + 28
    finally:
        p.end()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not img.save(str(out_path), "PNG"):
        return None
    return {"path": str(out_path), "width": img.width(), "height": img.height(),
            "wires": len(geo["wires"])}


# ─────────────────────────────────────────────────────────────────────────────
# The user's own photo, marked up
# ─────────────────────────────────────────────────────────────────────────────
#
# A wiring diagram answers "what goes where" in the abstract. A great many
# questions are about THIS photo instead: which of these is the resistor, where
# the screw goes, which leg is burnt, which cable is loose. For those the most
# useful picture is the one the user is already looking at, with the answer drawn
# on it — a box around each part, a numbered point at each spot that matters.
#
# The positions come from the model as 0-1000 coordinates of the picture it was
# given (Gemini's own box convention), so nothing here depends on resolution.
# Measured on a test picture: part boxes landed on the parts within a few
# pixels. Small points (a single pin) are less exact, which is why a point is
# drawn as a ring with its label beside it rather than as an arrow tip.

PROBLEM = "#ff4d4d"
FINE = "#3ddc84"


def annotate(jpeg: bytes, out_path: str | Path, *, parts: list, marks: list,
             accent: str = "#38d6ff", emphasis: Optional[set] = None) -> Optional[dict]:
    """Draw part boxes and marked points onto the photo.

    parts — [{"id", "name", "box": [ymin, xmin, ymax, xmax] (0-1000)}]
    marks — [{"label", "point": [y, x] (0-1000), "kind": "info"|"problem"|"ok", "n"}]
    emphasis — part ids to bring forward; the others are drawn faint.

    Returns {"path", "width", "height"} or None. Never raises.
    """
    try:
        from PyQt6.QtCore import QThread
        from PyQt6.QtGui import QGuiApplication
    except Exception:
        return None
    if QGuiApplication.instance() is None or not jpeg:
        return None
    job = lambda: _annotate_now(jpeg, Path(out_path), parts or [], marks or [],
                                accent, emphasis or set())
    if QGuiApplication.instance().thread() is QThread.currentThread():
        return _safe(job)
    return _on_gui_thread(lambda: _safe(job))


def _annotate_now(jpeg: bytes, out_path: Path, parts, marks, accent, emphasis):
    from PyQt6.QtCore import QPointF, QRectF, Qt
    from PyQt6.QtGui import (QColor, QFont, QFontMetricsF, QGuiApplication, QImage,
                             QPainter, QPainterPath, QPen)

    photo = QImage.fromData(jpeg)
    if photo.isNull():
        return None
    longest = 1600
    if max(photo.width(), photo.height()) > longest:
        photo = photo.scaled(longest, longest, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    w, h = photo.width(), photo.height()
    img = photo.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

    def box_rect(box):
        y0, x0, y1, x1 = box
        return QRectF(x0 / 1000 * w, y0 / 1000 * h, (x1 - x0) / 1000 * w, (y1 - y0) / 1000 * h)

    px = max(13, min(30, int(w / 52)))
    font = QFont(QGuiApplication.font())
    font.setPixelSize(px)
    font.setBold(True)
    fm = QFontMetricsF(font)
    accent_c = QColor(accent) if QColor(accent).isValid() else QColor("#38d6ff")
    placed: list = []

    def free_rect(rect: QRectF) -> QRectF:
        """Nudge a label down until it overlaps no label already placed."""
        for _ in range(24):
            if not any(rect.intersects(r) for r in placed):
                break
            rect.translate(0, rect.height() + 3)
        if rect.bottom() > h:
            rect.moveBottom(h - 2)
        if rect.right() > w:
            rect.moveRight(w - 2)
        rect.moveLeft(max(2.0, rect.left()))
        rect.moveTop(max(2.0, rect.top()))
        placed.append(QRectF(rect))
        return rect

    def chip(p, anchor: QPointF, number, text: str, color: QColor, above: bool) -> None:
        pad = px * 0.45
        badge = px * 1.25
        text = fm.elidedText(text, Qt.TextElideMode.ElideRight, w * 0.45)
        cw = badge + pad * 2 + fm.horizontalAdvance(text) + (pad if text else 0)
        ch = px * 1.7
        top = anchor.y() - ch - 4 if above else anchor.y() + 4
        rect = free_rect(QRectF(anchor.x(), top, cw, ch))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(8, 14, 20, 225))
        p.drawRoundedRect(rect, ch / 2.6, ch / 2.6)
        p.setBrush(color)
        centre = QPointF(rect.left() + pad + badge / 2, rect.center().y())
        if number in (None, ""):
            # A part is named, not numbered: numbers belong to the marked
            # points, and two "1"s on one photo would say two different things.
            p.drawRoundedRect(QRectF(centre.x() - badge * 0.3, centre.y() - badge * 0.3,
                                     badge * 0.6, badge * 0.6), 3, 3)
        else:
            p.drawEllipse(centre, badge / 2, badge / 2)
            p.setPen(QColor("#061018"))
            p.drawText(QRectF(centre.x() - badge / 2, centre.y() - badge / 2, badge, badge),
                       Qt.AlignmentFlag.AlignCenter, str(number))
        if text:
            p.setPen(QColor(TEXT))
            p.drawText(QRectF(rect.left() + pad * 2 + badge, rect.top(), cw, ch),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

    boxed = [pt for pt in parts if isinstance(pt.get("box"), (list, tuple)) and len(pt["box"]) == 4]
    p = QPainter(img)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.setFont(font)

        # Dim everything outside the parts, so the eye lands on them.
        if boxed:
            veil = QPainterPath()
            veil.setFillRule(Qt.FillRule.OddEvenFill)
            veil.addRect(QRectF(0, 0, w, h))
            for pt in boxed:
                veil.addRoundedRect(box_rect(pt["box"]), 10, 10)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 95))
            p.drawPath(veil)

        for pt in boxed:
            faint = bool(emphasis) and pt["id"] not in emphasis
            color = QColor(accent_c)
            if faint:
                color.setAlpha(110)
            pen = QPen(color, max(2.0, w / 420) * (1.6 if (emphasis and not faint) else 1.0))
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            rect = box_rect(pt["box"])
            p.drawRoundedRect(rect, 10, 10)
            chip(p, QPointF(rect.left(), rect.top()), None, pt.get("name", ""),
                 color, above=rect.top() > px * 2)

        ring = max(9.0, w / 75)
        for mk in marks:
            point = mk.get("point")
            if not (isinstance(point, (list, tuple)) and len(point) == 2):
                continue
            color = QColor({"problem": PROBLEM, "ok": FINE}.get(mk.get("kind"), UNCLEAR))
            c = QPointF(point[1] / 1000 * w, point[0] / 1000 * h)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(0, 0, 0, 170), ring * 0.55))
            p.drawEllipse(c, ring, ring)
            p.setPen(QPen(color, ring * 0.3))
            p.drawEllipse(c, ring, ring)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(c, ring * 0.22, ring * 0.22)
            right = c.x() < w * 0.62
            anchor = QPointF(c.x() + ring * 1.5 if right else c.x() - ring * 1.5, c.y() + ring * 0.2)
            label = str(mk.get("label") or "")
            if not right:
                est = px * 1.25 + px * 1.35 + fm.horizontalAdvance(label)
                anchor.setX(max(2.0, anchor.x() - est))
            chip(p, anchor, mk.get("n", ""), label, color, above=False)
    finally:
        p.end()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not img.save(str(out_path), "PNG"):
        return None
    return {"path": str(out_path), "width": w, "height": h}
