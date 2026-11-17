"""
_hands.py — hands seen through the camera: 21 points per hand, what each hand
is doing, and one shared camera for every plugin that wants hands
(gesture_control.py, holo_desk.py).

SEEING
    Google's MediaPipe hand models, as ONNX in models/hands (NOTICE there), run
    by OpenCV's DNN module on this computer: a palm detector (192x192, ~6 ms)
    finds hands, a landmark model (224x224, ~5 ms) reads 21 points on each.
    The palm detector runs only when a hand is missing: a hand already found is
    FOLLOWED - the next frame's crop is worked out from its own points, as
    MediaPipe does - so tracking two hands costs ~10 ms a frame.

SMOOTH
    Three things, each needed:
      - the camera is read on its own thread, always keeping only the newest
        frame: a frame waiting in a queue is lag you can feel;
      - every point goes through a One Euro filter (Casiez et al., CHI 2012):
        steady when the hand is still, no delay when it moves fast;
      - consumers draw at their own rate and get each hand with its velocity,
        so a 60 Hz screen moves between 30 Hz camera frames.

WHAT A HAND IS DOING (Hand.pose, Hand.pinching)
    From the points alone - relative to the hand's own size, so near or far,
    big hand or small, it reads the same:
      open, fist, point, thumbs_up, thumbs_down, two (index + middle), pinch, other
      pinching: thumb tip and index tip together, with hysteresis (it takes
      more to let go than to grab, so a held pinch does not flicker).

The picture is mirrored (as a mirror is): a hand moved to the user's right
moves right on screen. Coordinates are 0..1 of the picture.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path

import numpy as np

from core import cameras
from core.lazyimport import LazyModule

cv2 = LazyModule("cv2")

PALM_FILE = "palm_detection_mediapipe_2023feb.onnx"
HAND_FILE = "handpose_estimation_mediapipe_2023feb.onnx"

WRIST, THUMB_CMC, THUMB_MCP, THUMB_IP, THUMB_TIP = 0, 1, 2, 3, 4
INDEX_MCP, INDEX_PIP, INDEX_TIP = 5, 6, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_TIP = 9, 10, 12
RING_MCP, RING_PIP, RING_TIP = 13, 14, 16
PINKY_MCP, PINKY_PIP, PINKY_TIP = 17, 18, 20
PALM_IDS = [0, 5, 9, 13, 17, 1, 2]          # the palm detector's 7 points, in its order
BONES = ((0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10),
         (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17),
         (17, 18), (18, 19), (19, 20))

WORK_WIDTH = 640             # hands are found at this width
MAX_HANDS = 2
HAND_CONF = 0.75             # the landmark model's own "this is a hand", for a new hand
HAND_KEEP = 0.5              # ...for one already followed (a fast hand blurs; MediaPipe's own bar)
PALM_SCORE = 0.55
PINCH_ON, PINCH_OFF = 0.28, 0.42   # thumb-index gap / hand size
PINCH_LET_GO = 0.07          # s the fingers must stay apart before a pinch ends
LOST_KEEP = 0.4              # s a lost hand is remembered: found again near its way, it is the same hand
SAME_HAND = 0.5              # palm centres closer than this (hand sizes): the same hand
FOUND_AGAIN = 1.5            # ...within this of where a lost hand was heading: that hand, back


def _model(name: str) -> Path:
    try:
        from core.paths import resource
        p = Path(resource("models", "hands", name))
        if p.is_file():
            return p
    except Exception:
        pass
    return Path(__file__).resolve().parents[1] / "models" / "hands" / name


def models_present() -> bool:
    return _model(PALM_FILE).is_file() and _model(HAND_FILE).is_file()


# ── smoothing ────────────────────────────────────────────────────────────────

class OneEuro:
    """The One Euro filter over an array of any shape."""

    # Tuned on picture-fraction units (tools/test_hands.py): a still hand's
    # jitter to ~40%, a fast hand followed within ~0.6% of the picture.
    def __init__(self, min_cutoff: float = 1.0, beta: float = 20.0, d_cutoff: float = 1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self._x = self._dx = None
        self._t = 0.0

    @staticmethod
    def _alpha(cutoff, dt):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x: np.ndarray, t: float) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        if self._x is None:
            self._x, self._dx, self._t = x.copy(), np.zeros_like(x), t
            return x.copy()
        dt = max(1e-3, t - self._t)
        self._t = t
        dx = (x - self._x) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        self._dx = a_d * dx + (1 - a_d) * self._dx
        cutoff = self.min_cutoff + self.beta * np.abs(self._dx)
        a = 1.0 / (1.0 + (1.0 / (2 * math.pi * cutoff)) / dt)
        self._x = a * x + (1 - a) * self._x
        return self._x.copy()


# ── the models ───────────────────────────────────────────────────────────────

def _anchors() -> np.ndarray:
    """The palm detector's 2016 anchors (SSD, strides 8 and 16 on 192x192):
    the Model Zoo lists them by hand; generated here, the same numbers."""
    out = []
    for stride, n in ((8, 2), (16, 6)):
        g = 192 // stride
        for y in range(g):
            for x in range(g):
                out.extend([[(x + 0.5) / g, (y + 0.5) / g]] * n)
    return np.array(out, np.float32)


class _PalmDetector:
    SIZE = np.array([192, 192])

    def __init__(self):
        self.net = cv2.dnn.readNet(str(_model(PALM_FILE)))
        self.anchors = _anchors()

    def __call__(self, image: np.ndarray) -> np.ndarray:
        """Rows: [x1, y1, x2, y2, 7 x (x, y), score] in image pixels."""
        h, w = image.shape[:2]
        ratio = min(self.SIZE / np.array([h, w]))
        nh, nw = int(h * ratio), int(w * ratio)
        img = cv2.resize(image, (nw, nh))
        top, left = (192 - nh) // 2, (192 - nw) // 2
        img = cv2.copyMakeBorder(img, top, 192 - nh - top, left, 192 - nw - left,
                                 cv2.BORDER_CONSTANT, None, (0, 0, 0))
        blob = (cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0)[None]
        pad = (np.array([left, top]) / ratio).astype(np.int32)
        self.net.setInput(blob)
        out = self.net.forward(self.net.getUnconnectedOutLayersNames())
        score = 1 / (1 + np.exp(-out[1][0, :, 0].astype(np.float64)))
        delta = out[0][0]
        scale = max(w, h)
        cxy = delta[:, :2] / self.SIZE
        wh = delta[:, 2:4] / self.SIZE
        boxes = np.concatenate([(cxy - wh / 2 + self.anchors) * scale,
                                (cxy + wh / 2 + self.anchors) * scale], axis=1)
        boxes -= [pad[0], pad[1], pad[0], pad[1]]
        keep = cv2.dnn.NMSBoxes(boxes.tolist(), score.tolist(), PALM_SCORE, 0.3)
        if len(keep) == 0:
            return np.empty((0, 19))
        keep = np.array(keep).reshape(-1)
        lm = delta[keep, 4:].reshape(-1, 7, 2) / self.SIZE + self.anchors[keep][:, None, :]
        lm = lm * scale - pad
        return np.c_[boxes[keep], lm.reshape(-1, 14), score[keep]]


class _HandModel:
    SIZE = np.array([224, 224])

    def __init__(self):
        self.net = cv2.dnn.readNet(str(_model(HAND_FILE)))

    @staticmethod
    def _crop(image, box, rotation: bool):
        wh = box[1] - box[0]
        box = box + (np.array([0, 0]) if rotation else np.array([0, -0.4])) * wh
        c = box.sum(axis=0) / 2
        half = (box[1] - box[0]) * (4 if rotation else 3) / 2
        box = np.array([c - half, c + half]).astype(np.int32)
        box[:, 0] = np.clip(box[:, 0], 0, image.shape[1])
        box[:, 1] = np.clip(box[:, 1], 0, image.shape[0])
        crop = image[box[0][1]:box[1][1], box[0][0]:box[1][0]]
        if crop.size == 0:
            return None, box, np.array([0, 0])
        side = int(np.linalg.norm(crop.shape[:2]) if rotation else max(crop.shape[:2]))
        ph, pw = side - crop.shape[0], side - crop.shape[1]
        left, top = pw // 2, ph // 2
        crop = cv2.copyMakeBorder(crop, top, ph - top, left, pw - left, cv2.BORDER_CONSTANT,
                                  None, (0, 0, 0))
        return crop, box, box[0] - [left, top]

    def __call__(self, image: np.ndarray, palm: np.ndarray, min_conf: float = HAND_CONF):
        """(21 x 3 points in image pixels, handedness 0..1, confidence) or None."""
        img, _box, bias = self._crop(image, palm[0:4].reshape(2, 2), True)
        if img is None:
            return None
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        pbox = palm[0:4].reshape(2, 2) - bias
        kps = palm[4:18].reshape(7, 2) - bias
        p1, p2 = kps[0], kps[2]
        rad = np.pi / 2 - np.arctan2(-(p2[1] - p1[1]), p2[0] - p1[0])
        rad = rad - 2 * np.pi * np.floor((rad + np.pi) / (2 * np.pi))
        angle = np.rad2deg(rad)
        center = pbox.sum(axis=0) / 2
        rot = cv2.getRotationMatrix2D(tuple(float(v) for v in center), angle, 1.0)
        rimg = cv2.warpAffine(img, rot, (img.shape[1], img.shape[0]))
        rk = np.c_[kps, np.ones(len(kps))] @ rot.T
        rbox = np.array([rk.min(axis=0), rk.max(axis=0)])
        crop, rbox, _ = self._crop(rimg, rbox, False)
        if crop is None:
            return None
        blob = cv2.resize(crop, tuple(int(v) for v in self.SIZE),
                          interpolation=cv2.INTER_AREA).astype(np.float32)[None] / 255.0
        self.net.setInput(blob)
        lms, conf, handed, _world = self.net.forward(self.net.getUnconnectedOutLayersNames())
        conf = float(conf[0][0])
        if conf < min_conf:
            return None
        lms = lms[0].reshape(-1, 3).astype(np.float64)
        s = max((rbox[1] - rbox[0]) / self.SIZE)
        lms[:, :2] = (lms[:, :2] - self.SIZE / 2) * s
        lms[:, 2] *= s
        back = cv2.getRotationMatrix2D((0, 0), angle, 1.0)
        lms[:, :2] = lms[:, :2] @ back[:, :2]
        rc, tc = rot[:, :2].T, rot[:, 2]
        inv = np.c_[rc, [-(rc[0] @ tc), -(rc[1] @ tc)]]
        c = np.append(rbox.sum(axis=0) / 2, 1)
        lms[:, :2] += np.array([c @ inv[0], c @ inv[1]]) + bias
        return lms, float(handed[0][0]), conf


# ── a hand ───────────────────────────────────────────────────────────────────

def _dist(a, b) -> float:
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


class Hand:
    """One hand, as consumers read it. Points: 21 x 3, x and y 0..1 of the
    (mirrored) picture, z relative depth (negative: towards the camera)."""

    def __init__(self, track_id: int, side: str, pts: np.ndarray, velocity: np.ndarray,
                 pinching: bool, pinch: float, confidence: float):
        self.id = track_id
        self.side = side                      # "left" / "right": the user's own hand
        self.pts = pts
        self.velocity = velocity              # of the pinch point, picture widths per second
        self.pinching = pinching
        self.pinch = pinch                    # thumb-index gap / hand size (0 = touching)
        self.confidence = confidence
        self.let_go_at = None                 # a pinch that just ended: when the fingers opened
        self.gap = 0.0                        # s it was not seen before this frame (found again)
        self.size = _hand_size(pts)
        self.pose = _pose(pts, self.size)
        self.extended = sum(_fingers_out(pts, self.size))   # index..pinky out: 0..4

    @property
    def point(self) -> tuple[float, float]:
        """Where the hand acts: between thumb tip and index tip - the spot a
        pinch grabs, steady while the fingers close."""
        p = (self.pts[THUMB_TIP, :2] + self.pts[INDEX_TIP, :2]) / 2
        return float(p[0]), float(p[1])

    @property
    def aim(self) -> tuple[float, float]:
        """Where a pinch of this hand lands - worked out from the bones that do
        not move when the fingers do (wrist, knuckles), so it is the same point
        with the hand open, closing, pinched or opening again: a cursor that
        does not jump when you grab, nor push when you let go."""
        return aim_point(self.pts, self.side)

    @property
    def knuckles(self) -> tuple[float, float]:
        """Between the index and middle knuckles: it does not move when the
        fingers close or open - what a pinch is moved by (no jump on grab, no
        push on release)."""
        p = (self.pts[INDEX_MCP, :2] + self.pts[MIDDLE_MCP, :2]) / 2
        return float(p[0]), float(p[1])

    @property
    def index_tip(self) -> tuple[float, float]:
        return float(self.pts[INDEX_TIP, 0]), float(self.pts[INDEX_TIP, 1])

    @property
    def palm(self) -> tuple[float, float]:
        p = self.pts[[WRIST, INDEX_MCP, MIDDLE_MCP, PINKY_MCP], :2].mean(axis=0)
        return float(p[0]), float(p[1])

    def roll(self) -> float:
        """The hand's turn in the picture plane, radians (wrist -> middle finger)."""
        d = self.pts[MIDDLE_MCP, :2] - self.pts[WRIST, :2]
        return float(math.atan2(d[0], -d[1]))


# Where the pinch lands, in the hand's own frame: from the middle of the index
# and middle knuckles, `thumbward` and `up` (towards the fingers), in hand
# sizes. Started from typical hands; each real pinch moves it a little towards
# that user's own (learn_aim), per hand.
AIM_DEFAULT = (0.42, 0.62)
_aim = {"left": list(AIM_DEFAULT), "right": list(AIM_DEFAULT)}


def _hand_frame(pts):
    """(origin, thumbward unit, up unit, size) of a hand: from its bones only."""
    origin = (pts[INDEX_MCP, :2] + pts[MIDDLE_MCP, :2]) / 2
    up = pts[MIDDLE_MCP, :2] - pts[WRIST, :2]
    size = max(1e-6, float(np.hypot(*up)))
    up = up / size
    side = pts[INDEX_MCP, :2] - pts[PINKY_MCP, :2]
    side = side - up * float(side @ up)                     # square to "up"
    n = float(np.hypot(*side))
    side = side / n if n > 1e-9 else np.array([-up[1], up[0]])
    return origin, side, up, size


def aim_point(pts, side_name: str = "right") -> tuple[float, float]:
    origin, side, up, size = _hand_frame(pts)
    a, b = _aim.get(side_name, AIM_DEFAULT)
    p = origin + size * (a * side + b * up)
    return float(p[0]), float(p[1])


def learn_aim(pts, side_name: str) -> None:
    """A real pinch: where thumb and index actually met, in the hand's frame,
    nudges this hand's aim point (slowly - one odd frame changes nothing)."""
    origin, side, up, size = _hand_frame(pts)
    pinch = (pts[THUMB_TIP, :2] + pts[INDEX_TIP, :2]) / 2 - origin
    a, b = float(pinch @ side) / size, float(pinch @ up) / size
    if not (-0.5 < a < 1.5 and -0.5 < b < 2.0):           # not a believable pinch
        return
    cur = _aim.setdefault(side_name, list(AIM_DEFAULT))
    cur[0] += 0.03 * (a - cur[0])
    cur[1] += 0.03 * (b - cur[1])


def _hand_size(pts) -> float:
    """Wrist to middle-finger knuckle: steady whatever the fingers do."""
    return max(1e-4, _dist(pts[WRIST], pts[MIDDLE_MCP]))


def _extended(pts, tip, pip, mcp, size) -> bool:
    """A finger is out when its tip is farther from the wrist than its middle
    joint by a clear margin."""
    return _dist(pts[tip], pts[WRIST]) > _dist(pts[pip], pts[WRIST]) + 0.18 * size


def _index_out(pts) -> bool:
    """The index tip farther from the wrist than its knuckle: in a pinch it
    is, in a fist (where the thumb tip also meets the index) it is not."""
    return _dist(pts[INDEX_TIP], pts[WRIST]) > _dist(pts[INDEX_MCP], pts[WRIST])


def _fingers_out(pts, size) -> list[bool]:
    return [_extended(pts, t, p, m, size) for t, p, m in
            ((INDEX_TIP, INDEX_PIP, INDEX_MCP), (MIDDLE_TIP, MIDDLE_PIP, MIDDLE_MCP),
             (RING_TIP, RING_PIP, RING_MCP), (PINKY_TIP, PINKY_PIP, PINKY_MCP))]


def _pose(pts, size) -> str:
    fingers = _fingers_out(pts, size)
    thumb_out = _dist(pts[THUMB_TIP], pts[INDEX_MCP]) > 0.75 * size \
        and _dist(pts[THUMB_TIP], pts[PINKY_MCP]) > 1.0 * size
    n = sum(fingers)
    if _dist(pts[THUMB_TIP], pts[INDEX_TIP]) < PINCH_ON * size and _index_out(pts):
        return "pinch"                       # fingers may be out: still not an open palm
    if n == 4 and thumb_out:
        return "open"
    if n == 0:
        # thumb up: out, and above the rest of the hand (picture y grows downwards)
        if thumb_out and pts[THUMB_TIP, 1] < pts[INDEX_MCP, 1] - 0.5 * size:
            return "thumbs_up"
        if thumb_out and pts[THUMB_TIP, 1] > pts[PINKY_MCP, 1] + 0.5 * size:
            return "thumbs_down"
        return "fist"
    if fingers == [True, False, False, False]:
        return "point"
    if fingers == [True, True, False, False]:
        return "two"
    return "other"


class _Track:
    def __init__(self, track_id: int):
        self.id = track_id
        self.filter = OneEuro()
        self.speed = OneEuro(min_cutoff=3.0, beta=0.0)
        self.raw = None                       # last points, image pixels (for the next crop)
        self.t = 0.0                          # when they were seen
        self.vel_px = np.zeros(2)             # the palm's speed, pixels per second
        self.side_votes = 0.0
        self.pinching = False
        self.apart_since = None               # a pinch's fingers apart since (PINCH_LET_GO)
        self.let_go_at = None                 # the pinch just ended: its fingers opened then
        self.gap = 0.0                        # s unseen before this frame
        self.lost_at = None
        self.last_point = None
        self.last_t = 0.0

    def center(self) -> np.ndarray:
        return self.raw[PALM_IDS, :2].mean(axis=0)

    def size_px(self) -> float:
        return _hand_size(self.raw)

    def ahead(self, t: float) -> np.ndarray:
        """How far the hand has gone since it was seen, at its own speed (a
        tenth of a second at most: a guess, not a flight)."""
        return self.vel_px * min(0.1, max(0.0, t - self.t))

    def seen(self, raw: np.ndarray, t: float) -> None:
        self.gap = (t - self.lost_at) if self.lost_at is not None else 0.0
        if self.raw is not None and t > self.t:
            v = (raw[PALM_IDS, :2].mean(axis=0) - self.center()) / (t - self.t)
            self.vel_px = v if self.lost_at is not None else 0.5 * self.vel_px + 0.5 * v
        self.raw, self.t, self.lost_at = raw, t, None

    def roi(self, t: float | None = None) -> np.ndarray:
        """A palm row for the landmark model, made from this hand's own points
        - moved on to where the hand is heading, so a fast hand stays in its
        crop instead of leaving it between two frames."""
        k = self.raw[PALM_IDS, :2] + (self.ahead(t) if t is not None else 0.0)
        return np.r_[k.min(axis=0), k.max(axis=0), k.reshape(-1), 1.0]


class Tracker:
    """Frames in, hands out (process). Up to MAX_HANDS, each followed.

    A hand keeps its id for as long as it is in view - consumers hold things
    by it (a panel in the Holo Desk). So: the crop follows the hand's speed,
    a followed hand is kept down to HAND_KEEP, a hand lost for a moment (a
    blur) is searched for at once and, found near where it was heading within
    LOST_KEEP, is the same hand again; two hands close together stay two (only
    palms on the same spot are one), and a pinch ends only when the fingers
    have stayed apart for PINCH_LET_GO - one blurred frame drops nothing."""

    def __init__(self, max_hands: int = MAX_HANDS):
        self.max_hands = max_hands
        self._palms = _PalmDetector()
        self._hand = _HandModel()
        self._tracks: list[_Track] = []
        self._lost: list[_Track] = []
        self._next_id = 1
        self._since_search = 0
        self.stats = {"frames": 0, "lost": 0, "found_again": 0, "seconds": 0.0}

    @staticmethod
    def _near(c: np.ndarray, tr: _Track, within: float) -> bool:
        return float(np.hypot(*(c - tr.center()))) < within * tr.size_px()

    def process(self, frame: np.ndarray, t: float | None = None) -> list[Hand]:
        t = time.monotonic() if t is None else t
        t0 = time.perf_counter()
        h, w = frame.shape[:2]
        if w > WORK_WIDTH:
            frame = cv2.resize(frame, (WORK_WIDTH, int(h * WORK_WIDTH / w)), interpolation=cv2.INTER_AREA)
            h, w = frame.shape[:2]
        kept, conf, lost_now = [], {}, False
        for tr in sorted(self._tracks, key=lambda tr: tr.id):
            got = self._hand(frame, tr.roi(t), HAND_KEEP)
            if got is None:
                tr.lost_at = t
                self._lost.append(tr)
                self.stats["lost"] += 1
                lost_now = True
                continue
            tr.seen(got[0], t)
            tr.side_votes = 0.8 * tr.side_votes + 0.2 * (got[1] - 0.5)
            # two crops that ended on the same hand: the older one stays
            if any(self._near(tr.center(), o, SAME_HAND * 0.7) for o in kept):
                continue
            kept.append(tr)
            conf[tr.id] = got[2]
        self._tracks = kept
        self._lost = [tr for tr in self._lost if t - tr.lost_at <= LOST_KEEP]
        self._since_search += 1
        if len(self._tracks) < self.max_hands and (not self._tracks or lost_now or self._lost
                                                   or self._since_search >= 6):
            self._since_search = 0
            for palm in self._palms(frame):
                if len(self._tracks) >= self.max_hands:
                    break
                c = (palm[0:2] + palm[2:4]) / 2
                if any(self._near(c, tr, SAME_HAND) for tr in self._tracks):
                    continue
                got = self._hand(frame, palm, HAND_CONF)
                if got is None:
                    continue
                c = got[0][PALM_IDS, :2].mean(axis=0)
                if any(self._near(c, tr, SAME_HAND) for tr in self._tracks):
                    continue
                back = [tr for tr in self._lost
                        if float(np.hypot(*(c - tr.center() - tr.ahead(t)))) < FOUND_AGAIN * tr.size_px()]
                if back:
                    tr = min(back, key=lambda tr: float(np.hypot(*(c - tr.center() - tr.ahead(t)))))
                    self._lost.remove(tr)
                    self.stats["found_again"] += 1
                    tr.side_votes = 0.8 * tr.side_votes + 0.2 * (got[1] - 0.5)
                else:
                    tr = _Track(self._next_id)
                    self._next_id += 1
                    tr.side_votes = got[1] - 0.5
                tr.seen(got[0], t)
                self._tracks.append(tr)
                conf[tr.id] = got[2]
        hands = []
        scale = np.array([w, h, w], dtype=np.float64)
        for tr in self._tracks:
            pts = tr.filter(tr.raw / scale, t)
            size = _hand_size(pts)
            gap = _dist(pts[THUMB_TIP], pts[INDEX_TIP]) / size
            if not tr.pinching:
                tr.pinching = gap < PINCH_ON and _index_out(pts)
                tr.apart_since = None
            elif gap <= PINCH_OFF:
                tr.apart_since = None
            elif tr.apart_since is None:
                tr.apart_since = t
            elif t - tr.apart_since >= PINCH_LET_GO:
                tr.pinching, tr.let_go_at, tr.apart_since = False, tr.apart_since, None
            p = (pts[THUMB_TIP, :2] + pts[INDEX_TIP, :2]) / 2
            if tr.last_point is None:
                vel = np.zeros(2)
            else:
                vel = tr.speed((p - tr.last_point) / max(1e-3, t - tr.last_t), t)
            tr.last_point, tr.last_t = p, t
            side_name = "right" if tr.side_votes > 0 else "left"
            if gap < PINCH_ON * 0.8 and _index_out(pts):
                learn_aim(pts, side_name)
            hd = Hand(tr.id, side_name, pts, vel, tr.pinching, gap, conf.get(tr.id, 1.0))
            hd.let_go_at, tr.let_go_at = tr.let_go_at, None
            hd.gap, tr.gap = tr.gap, 0.0
            hd.frame_t = t
            hands.append(hd)
        self.stats["frames"] += 1
        self.stats["seconds"] += time.perf_counter() - t0
        return hands


# ── a recording of the hands, for tuning ─────────────────────────────────────

class Recorder:
    """ASSISTANT_HANDS_RECORD=<file> (set before starting, from source): every
    frame's hands as one JSON line - time, id, side, pose, pinch and the 21
    points, rounded. Points only, never a picture. What a gesture that "does
    not work" really looked like, to replay in tools/test_hands.py."""

    def __init__(self, path: str):
        self.f = open(path, "a", encoding="utf-8")

    @classmethod
    def from_env(cls):
        path = os.environ.get("ASSISTANT_HANDS_RECORD", "").strip()
        if not path:
            return None
        try:
            rec = cls(path)
            print(f"[Hands] recording the hands' points to {path}", flush=True)
            return rec
        except OSError as e:
            print(f"[Hands] cannot record: {e}", flush=True)
            return None

    def write(self, t: float, hands: list) -> None:
        self.f.write(json.dumps({"t": round(t, 4), "hands": [
            {"id": h.id, "side": h.side, "pose": h.pose, "pinching": h.pinching, "gap": round(h.gap, 3),
             "pts": np.round(h.pts, 4).tolist()} for h in hands]}) + "\n")

    def close(self) -> None:
        try:
            self.f.close()
        except OSError:
            pass


# ── one camera for everyone who wants hands ──────────────────────────────────

class HandService:
    """The camera and the tracker, shared: gesture control and the Holo Desk
    can both want hands at once, and a camera opens once.

        acquire(name) / release(name)   it runs while anyone holds it
        subscribe(fn)                   fn(hands, frame, t) after each frame
        latest()                        (hands, frame, t) - the newest
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._holders: set[str] = set()
        self._subs: list = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame = None
        self._frame_t = 0.0
        self._new = threading.Event()
        self._latest = ([], None, 0.0)
        self.fps = 0.0
        self.error = ""
        self.camera = None

    def acquire(self, name: str) -> bool:
        if not models_present():
            self.error = "the hand models are missing from this installation"
            return False
        with self._lock:
            self._holders.add(name)
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._run, daemon=True, name="hands")
                self._thread.start()
        return True

    def release(self, name: str) -> None:
        with self._lock:
            self._holders.discard(name)
            if not self._holders:
                self._stop.set()

    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def subscribe(self, fn) -> None:
        with self._lock:
            if fn not in self._subs:
                self._subs.append(fn)

    def unsubscribe(self, fn) -> None:
        with self._lock:
            if fn in self._subs:
                self._subs.remove(fn)

    def latest(self):
        return self._latest

    def _grab(self, cam) -> None:
        """Read the camera as fast as it delivers, keep only the newest frame."""
        while not self._stop.is_set():
            frame = cam.read()
            if frame is None:
                time.sleep(0.2)
                continue
            self._frame, self._frame_t = frame, time.monotonic()
            self._new.set()

    def _run(self) -> None:
        try:
            cam = cameras.Camera(cameras.chosen(), fast=True, want=(1280, 720, 30))
        except TypeError:                      # an older core (Jarvis) without the fast way
            cam = cameras.Camera(cameras.chosen())
        self.camera = cam
        grabber = tracker = None
        rec = Recorder.from_env()
        started = time.monotonic()
        try:
            if not cam.open():
                self.error = "no camera shows a live picture"
                holder = cameras.in_use_by()
                if holder:
                    self.error = f"{holder} is using the camera"
                print(f"[Hands] {self.error}", flush=True)
                return
            self.error = ""
            # A sharper picture for the Holo Desk's background where the camera
            # has one (hands are still found at WORK_WIDTH), MJPG so 720p keeps
            # its frame rate over USB, and no queue of old frames.
            # Opened the fast way (Media Foundation) the size and rate were
            # asked at the opening - set again now, the stream breaks.
            fast = getattr(cam, "how", "") == "fast"
            for prop, val in (() if fast else (
                    (cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG")),
                    (cv2.CAP_PROP_FRAME_WIDTH, 1280), (cv2.CAP_PROP_FRAME_HEIGHT, 720),
                    (cv2.CAP_PROP_FPS, 30), (cv2.CAP_PROP_BUFFERSIZE, 1))):
                try:
                    cam.cap.set(prop, val)
                except Exception:
                    pass
            self._camera_said = False
            tracker = Tracker()
            grabber = threading.Thread(target=self._grab, args=(cam,), daemon=True, name="hands-camera")
            grabber.start()
            n, t_count = 0, time.monotonic()
            while not self._stop.is_set():
                if not self._new.wait(0.5):
                    continue
                self._new.clear()
                frame, t = self._frame, self._frame_t
                if frame is None:
                    continue
                frame = cv2.flip(frame, 1)
                hands = tracker.process(frame, t)
                if rec is not None:
                    rec.write(t, hands)
                self._latest = (hands, frame, t)
                with self._lock:
                    subs = list(self._subs)
                for fn in subs:
                    try:
                        fn(hands, frame, t)
                    except Exception as e:
                        print(f"[Hands] a listener failed: {type(e).__name__}: {e}")
                n += 1
                now = time.monotonic()
                if now - t_count >= 1.0:
                    self.fps, n, t_count = n / (now - t_count), 0, now
                    if not self._camera_said and now - started > 3:
                        # what the camera really gives, once (the one number a
                        # "it does not follow my hand" report needs first)
                        self._camera_said = True
                        h_, w_ = frame.shape[:2]
                        print(f"[Hands] camera {w_}x{h_}, {self.fps:.0f} frames a second"
                              f"{' (Media Foundation)' if getattr(cam, 'how', '') == 'fast' else ''}", flush=True)
        except Exception as e:
            self.error = f"hand tracking stopped ({type(e).__name__})"
            print(f"[Hands] {self.error}: {e}", flush=True)
        finally:
            self._stop.set()
            if tracker is not None and tracker.stats["frames"]:
                # What a "it does not follow my hand" report needs: frames a
                # second, the work per frame, hands lost and found again.
                st, took = tracker.stats, max(1e-3, time.monotonic() - started)
                print(f"[Hands] {st['frames']} frames, {st['frames'] / took:.0f} a second, "
                      f"{1000 * st['seconds'] / st['frames']:.0f} ms each; hands lost {st['lost']}, "
                      f"found again {st['found_again']}", flush=True)
            if rec is not None:
                rec.close()
            if grabber is not None:
                grabber.join(timeout=1.0)
            cam.release()
            self.camera = None
            self._latest = ([], None, 0.0)


SERVICE = HandService()


# ── a view on the screen that hands can move ─────────────────────────────────
#
# Found live 2026-11-01: with the globe open, "let me control it with my hands"
# reached gesture control, whose camera picture took the HUD's centre - and the
# globe closes when anything takes its place. So a view that fills the centre
# and can be moved by hand says so here (any plugin's view, by name):
#   - gesture control's camera picture does not cover it, and
#   - switching hand control on gives the hands to it, whoever is asked.

_screen_lock = threading.Lock()
_on_screen: dict = {}


def on_screen(name: str, label: str, take_hands) -> None:
    """`take_hands()` -> (done, what to tell the user): callable from any thread."""
    with _screen_lock:
        _on_screen.pop(name, None)
        _on_screen[name] = (label, take_hands)        # the newest is last


def off_screen(name: str) -> None:
    with _screen_lock:
        _on_screen.pop(name, None)


def screen_user():
    """(name, label, take_hands) of the view now on screen, or None."""
    with _screen_lock:
        if not _on_screen:
            return None
        name = next(reversed(_on_screen))
        label, take = _on_screen[name]
        return name, label, take


# ── gestures over time ───────────────────────────────────────────────────────

class Gestures:
    """Hands over time -> events, each once (held poses must be held; a swipe
    must be fast; every event has a cool-down):

        "palm"        an open palm held still ~0.6 s
        "thumbs_up" / "thumbs_down"   held ~0.9 s (they answer a confirmation:
                      a thumb passing by on its way is not a yes)
        "two"         two fingers (index + middle) held ~0.6 s
        "swipe_left" / "swipe_right"   an open hand moved fast sideways
        "pinch_drag"  while pinching and moving vertically: (dy since the start)

    A held pose fires once per hold - again only after the hand left the pose -
    and its value is the time the pose began (the frames' clock), so a caller
    can tell a pose made for what is on screen from one held since before.
    """

    HOLD = {"palm": 0.6, "thumbs_up": 0.9, "thumbs_down": 0.9, "two": 0.6}
    COOLDOWN = 1.2
    SWIPE_SPEED = 1.6          # picture widths per second
    STILL = 0.35

    def __init__(self):
        self._since: dict[str, float] = {}
        self._held: set[str] = set()          # poses that fired in this hold
        self._last_fired: dict[str, float] = {}
        self._swipe_x: dict[int, list] = {}

    def _fire(self, name: str, t: float, out: list, value=None) -> None:
        if t - self._last_fired.get(name, -99.0) >= self.COOLDOWN:
            self._last_fired[name] = t
            out.append((name, value))

    def update(self, hands: list[Hand], t: float) -> list[tuple[str, object]]:
        out: list[tuple[str, object]] = []
        seen = set()
        for h in hands:
            speed = float(np.hypot(*h.velocity))
            for pose, event in (("open", "palm"), ("thumbs_up", "thumbs_up"),
                                ("thumbs_down", "thumbs_down"), ("two", "two")):
                key = f"{event}:{h.id}"
                if h.pose == pose and (event != "palm" or speed < self.STILL):
                    seen.add(key)
                    start = self._since.setdefault(key, t)
                    if key not in self._held and t - start >= self.HOLD[event]:
                        self._fire(event, t, out, start)
                        self._held.add(key)
            # a swipe: the open hand travels 0.25 of the picture within 0.35 s
            trail = self._swipe_x.setdefault(h.id, [])
            if h.pose in ("open", "other") and not h.pinching:
                trail.append((t, h.palm[0]))
                trail[:] = [p for p in trail if t - p[0] <= 0.35]
                if len(trail) >= 3:
                    dx = trail[-1][1] - trail[0][1]
                    if abs(dx) > 0.25 and abs(h.velocity[0]) > self.SWIPE_SPEED * 0.5:
                        self._fire("swipe_right" if dx > 0 else "swipe_left", t, out)
                        trail.clear()
            else:
                trail.clear()
        for key in list(self._since):
            if key not in seen:
                del self._since[key]
                self._held.discard(key)
        live = {h.id for h in hands}
        for hid in list(self._swipe_x):
            if hid not in live:
                del self._swipe_x[hid]
        return out


def draw_hands(img: np.ndarray, hands: list[Hand], color=(255, 212, 0)) -> np.ndarray:
    """The skeleton over a picture of the same size (BGR colours)."""
    h, w = img.shape[:2]
    for hand in hands:
        pts = [(int(p[0] * w), int(p[1] * h)) for p in hand.pts]
        c = (136, 255, 0) if hand.pinching else color
        for a, b in BONES:
            cv2.line(img, pts[a], pts[b], tuple(int(v * 0.6) for v in c), 2, cv2.LINE_AA)
        for i, p in enumerate(pts):
            cv2.circle(img, p, 5 if i in (THUMB_TIP, INDEX_TIP) else 3, c, -1, cv2.LINE_AA)
    return img
