"""
_face_lock_core.py — what Face Lock sees and draws (plugins/face_lock.py).

SEEING
    OpenCV's own face models, shipped in models/face (NOTICE there): YuNet finds
    faces and five points on each (eyes, nose tip, mouth corners), SFace turns a
    face into 128 numbers. Two faces are the same person when those numbers
    point the same way (cosine similarity); OpenCV's measured line for SFace is
    0.363, and the strictness setting sits at or above it. All on this
    computer, on the CPU, a few milliseconds a frame.

A PHOTO IS NOT A FACE
    Asked to turn their head, a person's nose moves against their eyes - a face
    has depth. A photo, a printed one or one on a phone, turns as a flat card:
    every point moves together and the nose stays halfway between the eyes.
    So the check asks for a turn to a side chosen at random and measures how far
    the nose moved between the eyes (yaw). A video of the owner turning the
    right way at the right moment would pass; it is a lock against people at
    the keyboard, not against a prepared attack - the plugin says so.

WHAT IS KEPT
    The owner's face as numbers only (never a picture), in
    <data>/face_lock/profile.json, protected by core/secretstore: DPAPI on
    Windows, the OS-kept key elsewhere. It opens on this account on this
    computer only. Numbers cannot be turned back into a face.

DRAWING
    The camera view is mirrored (as a mirror is, so "left" is the user's left)
    and drawn on with lines only: no text in the picture - the words are on the
    lock screen, in the user's language.
"""
from __future__ import annotations

import base64
import json
import math
import random
import time
from pathlib import Path

import numpy as np

from core import cameras
from core.lazyimport import LazyModule

cv2 = LazyModule("cv2")

DETECTOR_FILE = "face_detection_yunet_2023mar.onnx"
RECOGNIZER_FILE = "face_recognition_sface_2021dec_int8.onnx"
PROFILE_NAME = "face_lock.profile"
MODEL_ID = "sface_2021dec_int8"

# Cosine similarity against the closest enrolled view.
STRICTNESS = {"Relaxed": 0.36, "Normal": 0.42, "Strict": 0.50}
STRANGER_BELOW = 0.28        # clearly someone else
TURN_KEEP = 0.30             # still the same person while turned away
TURN_NEEDED = 0.17           # yaw reached when asked to turn
FRONTAL = 0.09               # yaw counted as looking straight
MIN_FACE = 0.11              # face width / frame width to be judged at all
WORK_WIDTH = 640             # frames are judged at this width

# BGR, the HUD's colours (gui/theme.C)
PRI = (255, 212, 0)
GREEN = (136, 255, 0)
RED = (85, 51, 255)
AMBER = (0, 204, 255)
TONE_COLOR = {"idle": PRI, "busy": AMBER, "ok": GREEN, "bad": RED}


# ── files ────────────────────────────────────────────────────────────────────

def _model(name: str) -> Path:
    try:
        from core.paths import resource
        p = Path(resource("models", "face", name))
        if p.is_file():
            return p
    except Exception:
        pass
    return Path(__file__).resolve().parents[1] / "models" / "face" / name


def models_present() -> bool:
    return _model(DETECTOR_FILE).is_file() and _model(RECOGNIZER_FILE).is_file()


def _profile_path() -> Path:
    try:
        from core.paths import data_dir
        base = Path(data_dir())
    except Exception:
        base = Path(__file__).resolve().parents[1]
    return base / "face_lock" / "profile.json"


def save_profile(features: np.ndarray) -> None:
    from core import secretstore
    feats = np.asarray(features, dtype=np.float32).reshape(-1, 128)
    blob = base64.b64encode(feats.tobytes()).decode("ascii")
    doc = {"v": 1, "model": MODEL_ID, "views": int(feats.shape[0]),
           "features": secretstore.protect(blob, PROFILE_NAME),
           "made": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path = _profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc), encoding="utf-8")
    tmp.replace(path)


def load_profile() -> np.ndarray | None:
    """The enrolled views (n x 128), or None when there are none usable here."""
    try:
        doc = json.loads(_profile_path().read_text(encoding="utf-8"))
        if doc.get("model") != MODEL_ID:
            return None
        from core import secretstore
        blob = secretstore.reveal(str(doc.get("features") or ""), PROFILE_NAME)
        if not blob:
            return None
        feats = np.frombuffer(base64.b64decode(blob), dtype=np.float32).reshape(-1, 128)
        return feats.copy() if len(feats) else None
    except Exception:
        return None


def has_profile() -> bool:
    return _profile_path().is_file()


def forget_profile() -> bool:
    try:
        _profile_path().unlink()
        return True
    except FileNotFoundError:
        return False


# ── camera ───────────────────────────────────────────────────────────────────

class Camera(cameras.Camera):
    """core/cameras' watched camera, its frames mirrored (as a mirror is, so
    "left" is the user's left) and at the width the face models work at."""

    def read(self) -> np.ndarray | None:
        frame = super().read()
        return None if frame is None else _mirrored(frame)


def _mirrored(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    if w > WORK_WIDTH:
        frame = cv2.resize(frame, (WORK_WIDTH, int(h * WORK_WIDTH / w)),
                           interpolation=cv2.INTER_AREA)
    return cv2.flip(frame, 1)


def blank() -> np.ndarray:
    """An empty view (no camera to show)."""
    return np.zeros((480, WORK_WIDTH, 3), np.uint8)


def jpeg(frame: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
    return buf.tobytes() if ok else b""


# ── seeing ───────────────────────────────────────────────────────────────────

class Face:
    """One face found in a frame: box, five points, score."""
    __slots__ = ("row", "x", "y", "w", "h", "score", "points")

    def __init__(self, row: np.ndarray):
        self.row = row
        self.x, self.y, self.w, self.h = (float(v) for v in row[:4])
        self.points = row[4:14].reshape(5, 2).astype(np.float32)
        self.score = float(row[14])

    @property
    def centre(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2

    def yaw(self) -> float:
        """Where the nose sits between the eyes, -0.5..0.5; 0 is straight on.
        Negative: turned to the left of the (mirrored) picture.

        Measured ALONG the line through the eyes, not along the picture's
        x axis: a photo tilted sideways moves the nose sideways on screen too,
        and an x-only measure took that tilt for a head turn (a phone photo
        unlocked, 2026-10-26). Along the eye line, any tilt, move, zoom or
        flat turn of a picture leaves the nose where it was - only a face with
        depth moves it."""
        a, b = self.points[0], self.points[1]
        if a[0] > b[0]:
            a, b = b, a
        axis = b - a
        span2 = float(axis @ axis)
        if span2 < 4.0:
            return 0.0
        return float((self.points[2] - a) @ axis / span2 - 0.5)


class Eyes:
    """The two models, made once per session."""

    def __init__(self):
        self._det = cv2.FaceDetectorYN.create(str(_model(DETECTOR_FILE)), "", (320, 240),
                                              0.85, 0.3, 50)
        self._rec = cv2.FaceRecognizerSF.create(str(_model(RECOGNIZER_FILE)), "")
        self._size = (0, 0)

    def faces(self, frame: np.ndarray) -> list[Face]:
        h, w = frame.shape[:2]
        if (w, h) != self._size:
            self._det.setInputSize((w, h))
            self._size = (w, h)
        _, rows = self._det.detect(frame)
        if rows is None:
            return []
        found = [Face(r) for r in rows]
        return sorted(found, key=lambda f: f.w * f.h, reverse=True)

    def feature(self, frame: np.ndarray, face: Face) -> np.ndarray:
        crop = self._rec.alignCrop(frame, face.row)
        f = self._rec.feature(crop).reshape(-1).astype(np.float32)
        n = float(np.linalg.norm(f))
        return f / n if n > 0 else f


def similarity(feature: np.ndarray, profile: np.ndarray) -> float:
    """Cosine similarity to the closest enrolled view."""
    if profile is None or not len(profile):
        return 0.0
    p = profile / np.maximum(np.linalg.norm(profile, axis=1, keepdims=True), 1e-9)
    return float(np.max(p @ feature))


def big_enough(face: Face, frame: np.ndarray) -> bool:
    return face.w / max(1, frame.shape[1]) >= MIN_FACE


def same_person(features: list[np.ndarray], front: int = 4) -> bool:
    """Enrolled views are of one person: the straight-on ones (first) agree
    with each other, and every view - turned ones too - with them."""
    if len(features) < 2:
        return True
    m = np.stack(features)
    ref = m[:max(1, min(front, len(m)))].mean(axis=0)
    ref /= max(float(np.linalg.norm(ref)), 1e-9)
    sims = m @ ref
    return float(sims[:front].min()) >= 0.50 and float(sims.min()) >= 0.40


def pick_side() -> str:
    return random.choice(("left", "right"))


def turned(face: Face, side: str) -> bool:
    y = face.yaw()
    return y <= -TURN_NEEDED if side == "left" else y >= TURN_NEEDED


# ── drawing ──────────────────────────────────────────────────────────────────

def _glow_line(img, a, b, color, width=2):
    dim = tuple(int(c * 0.35) for c in color)
    cv2.line(img, a, b, dim, width + 4, cv2.LINE_AA)
    cv2.line(img, a, b, color, width, cv2.LINE_AA)


def _dim_outside(img, box=None, amount=0.45):
    dark = (img * amount).astype(np.uint8)
    if box is not None:
        x, y, w, h = (int(v) for v in box)
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(img.shape[1], x + w), min(img.shape[0], y + h)
        if x1 > x0 and y1 > y0:
            dark[y0:y1, x0:x1] = img[y0:y1, x0:x1]
    img[:] = dark


def _grid(img, color, t):
    h, w = img.shape[:2]
    over = img.copy()
    step = 40
    off = int(t * 12) % step
    faint = tuple(int(c * 0.18) for c in color)
    for y in range(off, h, step):
        cv2.line(over, (0, y), (w, y), faint, 1)
    for x in range(0, w, step):
        cv2.line(over, (x, 0), (x, h), faint, 1)
    cv2.addWeighted(over, 0.3, img, 0.7, 0, img)


def _frame_corners(img, color):
    h, w = img.shape[:2]
    n = int(min(w, h) * 0.08)
    for (x, y, dx, dy) in ((6, 6, 1, 1), (w - 7, 6, -1, 1), (6, h - 7, 1, -1), (w - 7, h - 7, -1, -1)):
        _glow_line(img, (x, y), (x + dx * n, y), color, 2)
        _glow_line(img, (x, y), (x, y + dy * n), color, 2)


def draw(img: np.ndarray, face: Face | None, tone: str, t: float,
         progress: float | None = None, side: str | None = None,
         ticks: tuple[int, int] | None = None) -> np.ndarray:
    """Draw the scan over a (mirrored) frame and return it.

    progress  0..1 drawn as an arc round the face (enrolment, a match building)
    side      "left" / "right": chevrons asking for a head turn that way
    ticks     (done, total): segments of the ring lit, one per enrolled view
    """
    color = TONE_COLOR.get(tone, PRI)
    h, w = img.shape[:2]
    if face is None:
        _dim_outside(img, None, 0.6)
        _grid(img, color, t)
        # where to put the face: a breathing oval
        cx, cy = w // 2, int(h * 0.48)
        a, b = int(w * 0.16 * (1 + 0.04 * math.sin(t * 3))), int(h * 0.30)
        for k in range(0, 360, 12):
            cv2.ellipse(img, (cx, cy), (a, b), 0, k, k + 6, color, 2, cv2.LINE_AA)
        _frame_corners(img, color)
        return img

    pad = 0.18
    box = (face.x - face.w * pad, face.y - face.h * pad, face.w * (1 + 2 * pad), face.h * (1 + 2 * pad))
    _dim_outside(img, box, 0.5)
    _grid(img, color, t)
    x, y, bw, bh = (int(v) for v in box)
    n = int(min(bw, bh) * 0.22)
    pulse = int(3 * math.sin(t * 6))
    for (px, py, dx, dy) in ((x, y, 1, 1), (x + bw, y, -1, 1), (x, y + bh, 1, -1), (x + bw, y + bh, -1, -1)):
        px, py = px - dx * pulse, py - dy * pulse
        _glow_line(img, (px, py), (px + dx * n, py), color, 3)
        _glow_line(img, (px, py), (px, py + dy * n), color, 3)

    # the scan line, sweeping with a fading trail
    span = max(1, bh)
    pos = (t * 0.9) % 2.0
    pos = pos if pos <= 1 else 2 - pos
    for k, alpha in enumerate((1.0, 0.45, 0.2)):
        sy = int(y + span * min(1.0, max(0.0, pos - k * 0.025)))
        c = tuple(int(v * alpha) for v in color)
        cv2.line(img, (x + 4, sy), (x + bw - 4, sy), c, 2 if k == 0 else 1, cv2.LINE_AA)

    # (The five points the models use stay unseen: on a face they were clutter.)

    # the ring: turning ticks, and what is done so far
    cx, cy = (int(v) for v in face.centre)
    r = int(max(face.w, face.h) * 0.95)
    spin = (t * 40) % 360
    for k in range(0, 360, 30):
        cv2.ellipse(img, (cx, cy), (r, r), 0, k + spin, k + spin + 10,
                    tuple(int(c * 0.5) for c in color), 1, cv2.LINE_AA)
    if ticks is not None:
        done, total = ticks
        seg = 360.0 / max(1, total)
        for i in range(total):
            c = color if i < done else tuple(int(v * 0.25) for v in color)
            cv2.ellipse(img, (cx, cy), (r + 8, r + 8), -90, i * seg + 3, (i + 1) * seg - 3,
                        c, 4, cv2.LINE_AA)
    elif progress is not None:
        cv2.ellipse(img, (cx, cy), (r + 8, r + 8), -90, 0, 360 * max(0.0, min(1.0, progress)),
                    color, 4, cv2.LINE_AA)

    if side in ("left", "right"):
        d = -1 if side == "left" else 1
        base = cx + d * (r + 30)
        for k in range(3):
            phase = (t * 2.2 - k * 0.25) % 1.0
            cxk = int(base + d * (k * 26 + phase * 10))
            c = tuple(int(v * (0.4 + 0.6 * (1 - phase))) for v in AMBER)
            tip = (cxk + d * 14, cy)
            cv2.line(img, (cxk - d * 4, cy - 22), tip, c, 4, cv2.LINE_AA)
            cv2.line(img, (cxk - d * 4, cy + 22), tip, c, 4, cv2.LINE_AA)
    _frame_corners(img, color)
    return img
