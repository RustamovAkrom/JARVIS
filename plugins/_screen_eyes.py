"""
plugins/_screen_eyes.py — what the screen agent sees: the screen, a magnified
part of it, and whether it has stopped moving.

A shared driver, not a skill: the leading underscore tells plugin_loader to
skip this file. plugins/_screen_loop.py is its only user.

ONE CLEAN PICTURE, NOT A WALL OF NUMBERS
    The first version drew up to fifty numbered outlines on every screenshot
    and asked the model which NUMBER it meant. The outlines came from edge
    detection, so most of them were unlabelled fragments, the numbers covered
    the very text that says what a control is, and a model that read the
    number nearest the thing it wanted clicked whatever that number belonged
    to — watched more than once: "type into the search bar" opened a video
    thumbnail. The model had to get two things right (find the thing, then
    read the right label off it) and only the first one is its strength.

    So the model gets the screen exactly as the user sees it and answers
    WHERE: a point, x then y, each 0-1000 of the picture, under named keys so
    no model can swap them. The controls the accessibility tree names are
    listed beside the picture as text, because a real control's name and
    rectangle are facts worth having; nothing is drawn on the picture.

LOOK CLOSER BEFORE YOU CLICK
    A point read off a downscaled screenshot is a few pixels off at best and
    on the neighbour at worst, and the smaller the control and the larger the
    monitor, the worse it gets. zoom() cuts the area around a point out of
    the FULL-resolution capture and magnifies it, so the second look — "where
    exactly is the thing you described" — is made at two to three times the
    detail of the first. The same cut-out lets the model read small print
    (a date, a price, a status) before it decides or declares the task done.

    snap() then moves a refined point to the middle of the small control it
    falls in, when the picture shows one: the shapes found by edge detection
    are no longer choices for the model, only a ruler for the last few pixels.

WAITING FOR THE SCREEN, NOT FOR THE CLOCK
    settle() watches a coarse grid of the screen after an action and returns
    as soon as it stops changing, leaving out the cells that were moving on
    their own before the action (a playing video, an animation). A dialog that
    appears at once is looked at at once; a page that takes six seconds to
    load is not judged after two.

THE ASSISTANT'S OWN WINDOWS ARE PAINTED OVER
    They show the goal and a log of what the assistant believes it did. A
    model looking for a button labelled with the goal's words, or asked
    whether the goal was reached, would find them there. Every picture here
    has those windows filled with flat grey.

COORDINATES
    Everything a View holds, and everything this file returns, is in the space
    the mouse is driven in. The picture is in real pixels; the ratio between
    the two (`px`) is measured from each capture by core.screenview.capture,
    which keeps aim right on a Retina panel, a scaled Windows display and a
    plain monitor alike. Normalised coordinates (0-1000) are always relative
    to the picture the model was shown, so they do not depend on its size.

NO LANGUAGE HERE
    Edges, colours and grey levels are the same in every script and on every
    desktop. Text from the screen is passed through, never matched.
"""
from __future__ import annotations

import io
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from core import screenview
from core.screenview import Element, View

# The picture the model reads. Long side in pixels: big enough that ordinary
# interface text stays legible on a 1440p screen, small enough to be quick.
# Precision does not depend on it: zoom() looks again at full resolution.
MAX_IMAGE_PX = 1600
JPEG_QUALITY = 85

# The magnified cut-out: its long side after scaling, and how much of the
# monitor's width it covers (clamped to a range in mouse units, so a 4K and a
# 1080p screen both get a cut-out about the size of a dialog).
ZOOM_IMAGE_PX = 1280
ZOOM_SHARE = 0.3
ZOOM_MIN_UNITS = 420
ZOOM_MAX_UNITS = 1100

# Shapes found in the picture, used only to snap a point (see snap()).
MAX_MARKS = 60
SELF_FILL = (128, 128, 128)


# A box bigger than this fraction of the monitor is not a candidate for "which
# control do you mean". Measured on a running browser, the tree offered four
# containers each covering the whole window: a number on those means nothing.
MAX_MARK_FRACTION = 0.5


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def _inside(rect: tuple, x: float, y: float) -> bool:
    left, top, width, height = rect
    return left <= x < left + width and top <= y < top + height


def _overlap(a: tuple, b: tuple) -> float:
    """Intersection over the smaller box: 1.0 when one sits inside the other."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    smaller = max(1, min(aw * ah, bw * bh))
    return (ix * iy) / smaller


def _on_monitor(el: Element, monitor: dict) -> bool:
    cx, cy = el.center
    return _inside((monitor["left"], monitor["top"], monitor["width"],
                    monitor["height"]), cx, cy)


def _tree_candidates(view: View, monitor: dict, own: list[tuple],
                     disabled: bool = False) -> list[Element]:
    """Elements from the accessibility tree worth drawing a box around.

    Interactable ones first, smallest first: when the cap bites it should drop
    scenery, not buttons. Tiny slivers are skipped because a number will not fit
    on them legibly, whole-window containers because a number on them would
    mean nothing, and anything on the assistant's own windows because it is
    never the target. `disabled` keeps greyed-out controls too (the list the
    model reads marks them), behind the usable ones.
    """
    screen_area = max(1, monitor["width"] * monitor["height"])
    usable = [el for el in view.elements
              if el.width >= 12 and el.height >= 10 and (el.enabled or disabled)
              and el.area <= MAX_MARK_FRACTION * screen_area
              and _on_monitor(el, monitor)
              and not any(_inside(r, *el.center) for r in own)]
    usable.sort(key=lambda el: (0 if el.interactable else 1 if el.role in screenview.INTERACTABLE
                                else 2, el.area))
    return usable


def _visual_candidates(image: Any, monitor: dict, px: float,
                       own: Optional[list[tuple]] = None) -> list[Element]:
    """Candidate controls found in the PICTURE, for screens that expose none.

    THE CASE THIS EXISTS FOR, IN THE USER'S OWN WORDS
        "it clicked the profile, but there was no text on the profile - just a
        photo, and it should have understood that".

        A photo with no label is still a rectangle on a screen. Edges are
        visible whether or not anything is written, so the boxes can be found
        in the image itself and numbered exactly the same way. The model then
        picks a NUMBER, and the geometry behind that number is measured rather
        than imagined.

    WHY THIS IS NOT A GUESS EVEN THOUGH IT USES NO SEMANTICS
        It does not try to work out what anything IS. It finds closed shapes of
        a plausible size for something clickable and hands them over as
        choices. Deciding which one is the profile is the model's job.

    Language-free and platform-free by construction: edges are edges in every
    script, on every desktop, in every application that ever drew a button.
    Sizes are judged in mouse units rather than pixels, so a Retina capture and
    a plain one find the same shapes.
    """
    try:
        import cv2
        import numpy as np
    except Exception:
        return []

    own = own or []
    try:
        frame = np.array(image)
        height, width = frame.shape[:2]
        grey = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        # Two passes, because a screen holds two kinds of edge: a crisp border
        # around a button, and the soft boundary of a photo against a page.
        edges = cv2.Canny(cv2.GaussianBlur(grey, (3, 3), 0), 60, 180)
        blocks = cv2.adaptiveThreshold(grey, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                       cv2.THRESH_BINARY_INV, 25, 9)
        mask = cv2.bitwise_or(edges, blocks)
        # TWO SCALES, BECAUSE CONTROLS COME IN TWO SIZES OF GAP
        #     A control is an icon, a label and a border, and closing the mask
        #     welds those into one shape. How wide to close decides what comes
        #     out: wide enough to weld "Sign up" into one box also welds it to
        #     "Log in" beside it, and a row of tabs becomes one bar; narrow enough
        #     to keep neighbours apart leaves a long label in pieces. Measured on
        #     a real browser with one wide kernel: six tabs came back as one box.
        #
        #     So both are taken. The container rule below then removes any wide
        #     shape that holds two or more narrow ones, which is exactly the case
        #     where the wide kernel joined separate controls.
        contours = []
        for kx, ky in ((6, 4), (15, 7)):
            kernel = (max(3, int(round(kx * px))), max(3, int(round(ky * px))))
            closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                                      cv2.getStructuringElement(cv2.MORPH_RECT, kernel))
            found, _ = cv2.findContours(closed, cv2.RETR_LIST,
                                        cv2.CHAIN_APPROX_SIMPLE)
            contours.extend(found)
    except Exception as e:
        print(f"[ScreenEyes] could not read shapes from the picture: {e}")
        return []

    # -- where the PICTURES are --------------------------------------------
    #
    #     Measured on a video page: every one of fifty boxes went to things
    #     INSIDE the video frame and inside thumbnails - a shelf, a cable, a
    #     face - and the search field, the subscribe button and the like button
    #     had none. The model, wanting the search field, borrowed the nearest
    #     number and clicked a thumbnail.
    #
    #     A photograph is recognisable without knowing what it shows: it has
    #     many distinct colours in every small patch, where interface controls
    #     have two or three. So the picture regions are found by that, the
    #     picture itself keeps ONE box (a thumbnail is a real thing to click),
    #     and nothing inside it gets a box of its own.
    busy = _busy_cells(frame, px)
    cell = _cell_px(px)

    def busy_share(x: int, y: int, w: int, h: int) -> float:
        if busy is None:
            return 0.0
        r0, r1 = int(y // cell), int(math.ceil((y + h) / cell))
        c0, c1 = int(x // cell), int(math.ceil((x + w) / cell))
        region = busy[max(0, r0):max(0, r1), max(0, c0):max(0, c1)]
        return float(region.mean()) if region.size else 0.0

    max_w, max_h = width * 0.6, height * 0.5
    pictures: list[tuple[int, int, int, int]] = []
    raw: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        uw, uh = w / px, h / px
        if (uw >= PICTURE_MIN[0] and uh >= PICTURE_MIN[1]
                and w * h <= PICTURE_MAX_SHARE * width * height
                and busy_share(x, y, w, h) >= PICTURE_REGION):
            pictures.append((x, y, w, h))
        if w > max_w or h > max_h:
            continue
        # Sized like something a person could aim at, in mouse units. Small
        # enough to keep a toolbar or sidebar icon (about 20 units square);
        # anything smaller is a glyph or an artefact.
        if uw < 18 or uh < 14 or uw * uh < 360:
            continue
        if uw > uh * 22 or uh > uw * 7:
            continue
        raw.append((x, y, w, h))

    def inside_picture(b: tuple[int, int, int, int]) -> bool:
        x, y, w, h = b
        cx, cy = x + w / 2, y + h / 2
        return any(px0 <= cx <= px0 + pw and py0 <= cy <= py0 + ph
                   and w * h < 0.6 * pw * ph
                   for px0, py0, pw, ph in pictures)

    kinds: dict[tuple[float, float, float, float], str] = {}
    boxes: list[tuple[float, float, float, float]] = []
    for b in raw:
        if inside_picture(b):
            continue
        x, y, w, h = b
        share = busy_share(x, y, w, h)
        # Into mouse coordinates now, so every later test speaks one language.
        unit = (monitor["left"] + x / px, monitor["top"] + y / px, w / px, h / px)
        kinds[unit] = ("image" if share >= PICTURE_BUSY
                       else "control" if share < 0.2 else "mixed")
        boxes.append(unit)

    boxes = [b for b in boxes
             if not any(_inside(r, b[0] + b[2] / 2, b[1] + b[3] / 2) for r in own)]

    # PANELS OUT, THEN DUPLICATES OUT — IN THAT ORDER
    #
    #     The first version kept the LARGER of any two boxes whose centres were
    #     close, on the theory that the pair was an outline and its fill. On a
    #     real editor that rule let every panel swallow every control inside
    #     it: the picture came back with twenty boxes around sidebars, previews
    #     and property sheets, and none around Import, Export, a thumbnail or a
    #     toolbar icon — the things a person actually clicks.
    #
    #     A box that holds two or more other candidates is a container, whatever
    #     it looks like, so it goes. What remains is deduplicated only against
    #     boxes that really are the same thing: nearly the same rectangle.
    def contains(outer, inner) -> bool:
        return (outer is not inner
                and outer[0] <= inner[0] + 1 and outer[1] <= inner[1] + 1
                and inner[0] + inner[2] <= outer[0] + outer[2] + 1
                and inner[1] + inner[3] <= outer[1] + outer[3] + 1
                and inner[2] * inner[3] < 0.8 * outer[2] * outer[3])

    # ...but a CONTROL often holds parts too: a search field holds its clear and
    # search icons, a pill button holds an icon and a word. Dropping every box
    # with two things inside removed the search field and the like and share
    # buttons from a video page. A panel differs from a control in shape: it is
    # much TALLER than what it holds, or it holds a whole row or grid of things.
    def is_panel(outer) -> bool:
        inner = [other for other in boxes if contains(outer, other)]
        if len(inner) < 2:
            return False
        tallest = max(other[3] for other in inner)
        return len(inner) > PANEL_PARTS or outer[3] > PANEL_TALLER * tallest

    boxes = [b for b in boxes if not is_panel(b)]

    boxes.sort(key=lambda b: b[2] * b[3], reverse=True)
    kept: list[tuple[float, float, float, float]] = []
    for box in boxes:
        if any(_overlap(box, k) > 0.7 and
               min(box[2] * box[3], k[2] * k[3]) > 0.5 * max(box[2] * box[3],
                                                             k[2] * k[3])
               for k in kept):
            continue
        kept.append(box)

    # When the cap bites it should drop scenery, not controls: flat interface
    # controls first, then pictures, then whatever is neither; smallest first
    # within each.
    #
    # Pictures come after controls on purpose. A thumbnail or photo is a big
    # target, the easiest thing there is to aim at with a point; a small icon
    # is the hardest. The numbers go where aiming by eye is hardest.
    rank = {"control": 0, "image": 1, "mixed": 2}
    kept.sort(key=lambda b: (rank.get(kinds.get(b, "mixed"), 2), b[2] * b[3]))
    kept = kept[:MAX_MARKS * 2]
    return [Element(eid=10_000 + i,
                    role=(screenview.ROLE_IMAGE if kinds.get(b) == "image"
                          else screenview.ROLE_OTHER),
                    name="", value="",
                    left=int(round(b[0])), top=int(round(b[1])),
                    width=int(round(b[2])), height=int(round(b[3])),
                    enabled=True, focused=False, scrollable=False, depth=0)
            for i, b in enumerate(kept)]


# A patch of screen is "busy" - part of a photograph or video - when it holds
# several distinct COLOURS. Greys are not counted: text blending into its
# background makes a run of greys, which made headlines and captions look like
# photographs; a photograph has real hue variety in almost every patch.
# Measured on a video page: patches inside the video and thumbnails scored far
# above interface controls, which scored near zero.
BUSY_COLOURS = 5
BUSY_TONES = 6                # ...or at least this many tones in all, with
BUSY_DOMINANT = 0.4           # no single tone covering this share of the patch
# A box holding more parts than this, or taller than its tallest part by this
# factor, is a panel rather than a control.
PANEL_PARTS = 4
PANEL_TALLER = 2.5
PICTURE_BUSY = 0.5            # a box this busy is itself a picture (thumbnail, photo)
PICTURE_REGION = 0.35         # a large rectangle this busy is a picture region;
                              # lower, because dark scenes in a video are calm
PICTURE_MAX_SHARE = 0.6       # a "region" bigger than this is the page, not a picture
PICTURE_MIN = (60, 34)        # mouse units; smaller busy things are icons/avatars


def _cell_px(px: float) -> int:
    """Patch size in picture pixels: 16 mouse units, even."""
    return max(8, int(round(16 * px)) // 2 * 2)


def _busy_cells(frame: Any, px: float) -> Any:
    """A (rows, cols) boolean grid: which patches of the screen are photographic.

    Colours are quantised to 3 bits a channel and every other pixel is sampled,
    then distinct colours are counted per patch in one vectorised pass. None
    when numpy cannot do it, which simply turns picture detection off.
    """
    try:
        import numpy as np
        cell = _cell_px(px)
        half = cell // 2
        height, width = frame.shape[:2]
        rows, cols = height // cell, width // cell
        if rows == 0 or cols == 0:
            return None
        sub = frame[:rows * cell:2, :cols * cell:2, :3] >> 5
        r = sub[..., 0].astype(np.int16)
        g = sub[..., 1].astype(np.int16)
        b = sub[..., 2].astype(np.int16)
        full = (r << 6) | (g << 3) | b

        def per_patch(values):
            values = values.reshape(rows, half, cols, half).transpose(0, 2, 1, 3)
            return np.sort(values.reshape(rows, cols, half * half), axis=2)

        # Signal 1 - colour variety. Every grey collapses to one code, so text
        # blending into its background adds at most one "colour".
        coloured = per_patch(np.where((r == g) & (g == b), -1, full))
        colour_count = 1 + (np.diff(coloured, axis=2) != 0).sum(axis=2)

        # Signal 2 - no dominant tone. Text and controls sit on a background
        # that fills most of any patch; a photograph, even a black-and-white
        # one, spreads over many tones with none in charge. Measured: grey and
        # desaturated thumbnails passed signal 1 as "interface" and their
        # fragments crowded real buttons out of the numbered boxes.
        tones = per_patch(full)
        size = tones.shape[2]
        index = np.arange(size)
        starts = np.concatenate(
            [np.ones(tones.shape[:2] + (1,), dtype=bool),
             np.diff(tones, axis=2) != 0], axis=2)
        run_start = np.maximum.accumulate(np.where(starts, index, 0), axis=2)
        dominant = (index - run_start + 1).max(axis=2) / float(size)
        tone_count = starts.sum(axis=2)

        return (colour_count >= BUSY_COLOURS) | (
            (tone_count >= BUSY_TONES) & (dominant < BUSY_DOMINANT))
    except Exception:
        return None


# A coarse grid of the screen's brightness, for "did that click open something".
GRID_SIZE = (48, 27)
GRID_CELL_CHANGED = 18        # grey levels a cell must move to count as changed


def grid(monitor: dict) -> bytes:
    """The monitor as a 48x27 grid of grey levels, own windows painted over.

    Cheap enough to take between two inputs (one grab, one tiny resize), and
    coarse on purpose: a caret blinking or a focus ring appearing changes no
    cell, a new page changes most of them.
    """
    image, monitor, px = screenview.capture(monitor)
    _paint_over_self(image, monitor, px, screenview.own_visible_regions())
    return image.convert("L").resize(GRID_SIZE).tobytes()


def grid_change(before: bytes, after: bytes, ignore: bytes = b"") -> float:
    """Fraction of cells that changed from `before` to `after`, 0.0 - 1.0.

    Cells that were ALREADY changing on their own — marked in `ignore` by a
    second grid taken a moment before — are left out of both the count and the
    total, so a playing video or an animation does not look like the click
    opened a new page.
    """
    if not before or len(before) != len(after):
        return 0.0
    moving = set()
    if ignore and len(ignore) == len(before):
        moving = {i for i, (a, b) in enumerate(zip(ignore, before))
                  if abs(a - b) > GRID_CELL_CHANGED}
    total = len(before) - len(moving)
    if total <= 0:
        return 0.0
    changed = sum(1 for i, (a, b) in enumerate(zip(before, after))
                  if i not in moving and abs(a - b) > GRID_CELL_CHANGED)
    return changed / total


def _paint_over_self(image: Any, monitor: dict, px: float,
                     own: list[tuple]) -> None:
    import PIL.ImageDraw
    draw = PIL.ImageDraw.Draw(image)
    for left, top, width, height in own:
        x0 = (left - monitor["left"]) * px
        y0 = (top - monitor["top"]) * px
        x1, y1 = x0 + width * px, y0 + height * px
        if x1 <= 0 or y1 <= 0 or x0 >= image.width or y0 >= image.height:
            continue
        draw.rectangle([max(0, x0), max(0, y0), min(image.width, x1),
                        min(image.height, y1)], fill=SELF_FILL)


# ---------------------------------------------------------------------------
# One look
# ---------------------------------------------------------------------------
@dataclass
class Frame:
    """One capture of one monitor, and everything derived from it.

    `image` is the full-resolution capture with the assistant's windows
    painted over; `jpeg` is what the model is shown; `grid` is the coarse
    brightness grid settle() compares against.
    """

    image: Any
    monitor: dict
    px: float
    own: list = field(default_factory=list)
    jpeg: bytes = b""
    grid: bytes = b""
    _shapes: Optional[list] = None

    # -- normalised (0-1000 of the picture) <-> mouse units ------------------
    def to_screen(self, x_n: float, y_n: float) -> tuple[int, int]:
        x_n = max(0.0, min(1000.0, float(x_n))) / 1000.0
        y_n = max(0.0, min(1000.0, float(y_n))) / 1000.0
        m = self.monitor
        return (min(m["left"] + m["width"] - 1, int(round(m["left"] + x_n * m["width"]))),
                min(m["top"] + m["height"] - 1, int(round(m["top"] + y_n * m["height"]))))

    def to_norm(self, x: float, y: float) -> tuple[int, int]:
        m = self.monitor
        return (int(round((x - m["left"]) * 1000 / max(1, m["width"]))),
                int(round((y - m["top"]) * 1000 / max(1, m["height"]))))

    def shapes(self) -> list[Element]:
        """Closed shapes in the picture, computed once and only when asked."""
        if self._shapes is None:
            try:
                self._shapes = _visual_candidates(self.image, self.monitor,
                                                  self.px, self.own)
            except Exception:
                self._shapes = []
        return self._shapes


RING_COLOUR = (255, 0, 200)


def _encode(image: Any, long_side: int, ring: bool = False) -> bytes:
    import PIL.Image
    image = _scaled(image, long_side)
    if ring:
        _draw_ring(image)
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue()


def _draw_ring(image: Any) -> None:
    """A hollow ring at the middle, with a white edge so it shows on any
    background. Hollow on purpose: what is under it stays visible."""
    import PIL.ImageDraw
    draw = PIL.ImageDraw.Draw(image)
    cx, cy = image.width / 2, image.height / 2
    radius = max(10, image.width // 55)
    for r, colour, width in ((radius + 2, (255, 255, 255), 5), (radius, RING_COLOUR, 3)):
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=colour, width=width)


def _scaled(image: Any, long_side: int) -> Any:
    import PIL.Image
    image = image.copy()
    biggest = max(image.width, image.height)
    if biggest > long_side:
        image.thumbnail((long_side, long_side), PIL.Image.LANCZOS)
    elif biggest < long_side:
        # Magnifying a cut-out: smooth enough to read, sharp enough to aim.
        scale = long_side / float(biggest)
        image = image.resize((max(1, int(image.width * scale)),
                              max(1, int(image.height * scale))),
                             PIL.Image.BICUBIC)
    return image


def _grid_of(image: Any) -> bytes:
    return image.convert("L").resize(GRID_SIZE).tobytes()


def capture(view: Optional[View] = None, *, max_px: int = MAX_IMAGE_PX) -> Frame:
    """The monitor the front window is on, as one Frame."""
    image, monitor, px = screenview.capture(screenview.monitor_rect(view))
    # Only the parts of the assistant's windows that are really on top; a
    # window behind the browser must not grey out the browser.
    own = screenview.own_visible_regions()
    _paint_over_self(image, monitor, px, own)
    return Frame(image=image, monitor=monitor, px=px, own=own,
                 jpeg=_encode(image, min(max_px, max(image.width, image.height))),
                 grid=_grid_of(image))


# ---------------------------------------------------------------------------
# Looking closer
# ---------------------------------------------------------------------------
@dataclass
class Zoom:
    """A magnified cut-out: the picture, and where it is on the screen."""

    jpeg: bytes
    rect: tuple[float, float, float, float]       # mouse units: left, top, w, h

    def to_screen(self, x_n: float, y_n: float) -> tuple[int, int]:
        left, top, width, height = self.rect
        x_n = max(0.0, min(1000.0, float(x_n))) / 1000.0
        y_n = max(0.0, min(1000.0, float(y_n))) / 1000.0
        return int(round(left + x_n * width)), int(round(top + y_n * height))

    def to_norm(self, x: float, y: float) -> tuple[int, int]:
        left, top, width, height = self.rect
        return (int(round((x - left) * 1000 / max(1, width))),
                int(round((y - top) * 1000 / max(1, height))))


def zoom(frame: Frame, x: float, y: float, *, share: float = ZOOM_SHARE,
         ring: bool = False) -> Zoom:
    """The area around (x, y) in mouse units, cut from the full-resolution
    capture and scaled to ZOOM_IMAGE_PX.

    ALWAYS CENTRED ON (x, y)
        Near an edge the part beyond the screen is painted flat grey rather
        than the cut-out being shifted. So "the middle of the picture" always
        means "where it was seen", and the second look can be told that in
        words instead of in coordinates — a model given coordinates as a hint
        was measured handing them straight back (2026-10-11 bench: the
        refined point equal to the first one, to the pixel, on most targets
        near the edge of a 1366x768 screen).

    `ring` draws a hollow ring at the middle: "is the thing under the ring?"
    is a question about what is THERE, which a model answers by looking,
    where "where is it?" let it pick any matching text in the cut-out.
    """
    import PIL.Image
    m = frame.monitor
    width = min(m["width"], max(ZOOM_MIN_UNITS,
                                min(ZOOM_MAX_UNITS, m["width"] * share)))
    height = width * m["height"] / max(1, m["width"])
    left, top = x - width / 2, y - height / 2
    px = frame.px
    box = (int(round((left - m["left"]) * px)), int(round((top - m["top"]) * px)),
           int(round((left - m["left"] + width) * px)),
           int(round((top - m["top"] + height) * px)))
    canvas = PIL.Image.new("RGB", (max(1, box[2] - box[0]), max(1, box[3] - box[1])),
                           SELF_FILL)
    inner = (max(0, box[0]), max(0, box[1]),
             min(frame.image.width, box[2]), min(frame.image.height, box[3]))
    if inner[2] > inner[0] and inner[3] > inner[1]:
        canvas.paste(frame.image.crop(inner), (inner[0] - box[0], inner[1] - box[1]))
    return Zoom(jpeg=_encode(canvas, ZOOM_IMAGE_PX, ring=ring),
                rect=(left, top, width, height))


# A control a point may be snapped into: small enough that its middle is a
# better place to click than wherever inside it the point fell.
SNAP_MAX_UNITS = (160, 64)


def snap(frame: Frame, x: int, y: int) -> tuple[int, int]:
    """The middle of the smallest small shape holding (x, y), or (x, y)."""
    best = None
    for shape in frame.shapes():
        if (shape.width <= SNAP_MAX_UNITS[0] and shape.height <= SNAP_MAX_UNITS[1]
                and shape.left <= x <= shape.left + shape.width
                and shape.top <= y <= shape.top + shape.height):
            if best is None or shape.area < best.area:
                best = shape
    return best.center if best is not None else (x, y)


# ---------------------------------------------------------------------------
# Controls the accessibility tree names
# ---------------------------------------------------------------------------
MAX_LISTED = 120
ITEM_ROLES = ("listitem", "treeitem")


def named_controls(view: View, frame: Frame) -> list[Element]:
    """Controls worth listing beside the picture: real, on this monitor, not
    on the assistant's windows, and either named or a place to type. In
    reading order, so the list reads like the screen.

    Disabled ones are listed too, marked as such: left out, a greyed-out
    checkbox looked like any other in the picture and was clicked again and
    again (live, 2026-10-13) - nothing said it could not be used.

    THE PLACES ARE SHARED BETWEEN KINDS OF CONTROL
        Taken smallest first overall, a ribbon's eighty little buttons filled
        every place and not one of a file manager's 41 files was listed - so a
        deleted folder could never be seen to go (2026-10-13). Each kind
        (buttons, tabs, list items, checkboxes...) is taken smallest first, in
        turns, until the places are used."""
    groups: dict[str, list[Element]] = {}
    candidates = _tree_candidates(view, frame.monitor, frame.own, disabled=True)
    # The cells of a row (a file's name, date, type, size) are parts of the
    # item, which is what is clicked: they took as many places as the files
    # themselves. A box with its own state inside an item stays, and so does
    # the field being typed in (a name being edited).
    items = [el.rect for el in candidates if el.role in ITEM_ROLES]
    for el in candidates:
        if el.role not in screenview.INTERACTABLE:
            continue
        if el.role not in ITEM_ROLES and el.checked is None and not el.focused and any(
                _inside(rect, *el.center) and rect != el.rect for rect in items):
            continue
        if not (el.label or el.role in (screenview.ROLE_EDIT,
                                        screenview.ROLE_COMBOBOX)):
            continue
        groups.setdefault(el.role, []).append(el)       # already smallest first
    queues = list(groups.values())
    picked: list[Element] = []
    while queues and len(picked) < MAX_LISTED:
        for queue in list(queues):
            if len(picked) >= MAX_LISTED:
                break
            picked.append(queue.pop(0))
            if not queue:
                queues.remove(queue)
    return sorted(picked, key=lambda el: (el.top // 12, el.left))


# ---------------------------------------------------------------------------
# Waiting for the screen to settle
# ---------------------------------------------------------------------------
SETTLE_POLL_S = 0.15
SETTLE_QUIET_S = 0.45          # unchanged this long counts as settled
SETTLE_MIN_S = 0.35            # an action's effect is rarely instant
SETTLE_STILL = 0.004           # share of cells changing between two polls


def moving_cells(earlier: bytes, later: bytes) -> bytes:
    """A grid marking the cells that changed between two grids taken while
    nothing was being done: those are moving on their own."""
    if not earlier or len(earlier) != len(later):
        return b""
    return bytes(255 if abs(a - b) > GRID_CELL_CHANGED else 0
                 for a, b in zip(earlier, later))


def _changed_share(a: bytes, b: bytes, moving: bytes = b"",
                   cells: Optional[set] = None) -> float:
    if not a or len(a) != len(b):
        return 0.0
    total = changed = 0
    for i, (x, y) in enumerate(zip(a, b)):
        if cells is not None and i not in cells:
            continue
        if moving and len(moving) == len(a) and moving[i]:
            continue
        total += 1
        changed += abs(x - y) > GRID_CELL_CHANGED
    return changed / total if total else 0.0


def _cells_near(monitor: dict, x: float, y: float, radius: int = 2) -> set:
    cols, rows = GRID_SIZE
    c = int((x - monitor["left"]) * cols / max(1, monitor["width"]))
    r = int((y - monitor["top"]) * rows / max(1, monitor["height"]))
    return {rr * cols + cc
            for rr in range(max(0, r - radius), min(rows, r + radius + 1))
            for cc in range(max(0, c - radius), min(cols, c + radius + 1))}


def snapshot(monitor: dict) -> tuple[Any, float]:
    """(full-resolution image with the assistant's windows painted over, px)."""
    image, monitor, px = screenview.capture(monitor)
    _paint_over_self(image, monitor, px, screenview.own_visible_regions())
    return image, px


# The area around an aimed point that spot_change() compares, in mouse units:
# about one control and its label.
SPOT_UNITS = (70, 36)
SPOT_PIXEL_CHANGED = 28        # grey levels a pixel must move to count


def spot_change(before: tuple[Any, float], after: tuple[Any, float],
                monitor: dict, x: float, y: float) -> Optional[float]:
    """Share of the pixels around (x, y) that changed, at full resolution.

    WHY NOT THE COARSE GRID
        The grid is 48 x 27 cells for the whole screen: a radio button being
        selected, a checkbox ticked or a switch flipped changes a dozen pixels
        and no cell. Measured: a pizza size was "clicked" three times and the
        screen "did not change" each time, so neither the model nor anybody
        could tell whether the right circle had been filled. Looking only at
        the spot that was aimed at, pixel by pixel, sees it.
    """
    try:
        import PIL.ImageChops
        (img_a, px_a), (img_b, px_b) = before, after
        if img_a.size != img_b.size:
            return None
        w, h = SPOT_UNITS[0] * px_a, SPOT_UNITS[1] * px_a
        cx, cy = (x - monitor["left"]) * px_a, (y - monitor["top"]) * px_a
        box = (int(max(0, cx - w / 2)), int(max(0, cy - h / 2)),
               int(min(img_a.width, cx + w / 2)), int(min(img_a.height, cy + h / 2)))
        if box[2] <= box[0] or box[3] <= box[1]:
            return None
        diff = PIL.ImageChops.difference(img_a.crop(box).convert("L"),
                                         img_b.crop(box).convert("L"))
        histogram = diff.histogram()
        changed = sum(histogram[SPOT_PIXEL_CHANGED:])
        return changed / float(max(1, sum(histogram)))
    except Exception:
        return None


def change_area(before: tuple[Any, float], after: tuple[Any, float], monitor: dict,
                moving: bytes = b"") -> Optional[tuple[float, tuple[int, int, int, int]]]:
    """Where the screen changed, at full resolution: (share of the screen's
    pixels that changed, (left, top, right, bottom) of the changed area in
    mouse units), or None when nothing changed or it cannot be measured.
    Cells that were moving on their own before the action are left out.

    WHY
        After a key press nobody knows where to look: space on a focused
        checkbox ticks a box a dozen pixels wide, and the coarse grid says
        "nothing changed". Measured: a form's checkbox was ticked with the
        keyboard, judged a failure from the picture, and the run gave up on
        a box that was probably already ticked. Where the pixels moved is a
        fact any program, any language and any action produces.
    """
    try:
        import PIL.ImageChops
        import PIL.ImageDraw
        (img_a, px), (img_b, _px_b) = before, after
        if img_a.size != img_b.size:
            return None
        diff = PIL.ImageChops.difference(img_a.convert("L"), img_b.convert("L"))
        mask = diff.point(lambda v: 255 if v >= SPOT_PIXEL_CHANGED else 0)
        if moving and len(moving) == GRID_SIZE[0] * GRID_SIZE[1]:
            draw = PIL.ImageDraw.Draw(mask)
            cw, ch = mask.width / GRID_SIZE[0], mask.height / GRID_SIZE[1]
            for i, flag in enumerate(moving):
                if flag:
                    c, r = i % GRID_SIZE[0], i // GRID_SIZE[0]
                    draw.rectangle([c * cw, r * ch, (c + 1) * cw, (r + 1) * ch], fill=0)
        if mask.getbbox() is None:
            return None
        share = mask.histogram()[255] / float(mask.width * mask.height)
        boxes = []
        for left, top, right, bottom in _regions(mask):
            boxes.append((int(monitor["left"] + left / px), int(monitor["top"] + top / px),
                          int(monitor["left"] + right / px), int(monitor["top"] + bottom / px)))
        return (share, boxes[0], boxes) if boxes else None
    except Exception:
        return None


REGION_CELLS = (96, 54)        # the grid separate changes are told apart on
MAX_REGIONS = 3


def _regions(mask: Any) -> list[tuple[int, int, int, int]]:
    """The separate changed areas of a mask, biggest first, in its pixels.

    Two changes far apart — a focus outline leaving one field and appearing
    on another — are two areas, not one box spanning the screen between them.
    Neighbouring cells of a coarse grid are joined; that is all.
    """
    cols, rows = REGION_CELLS
    small = mask.resize((cols, rows))           # any changed pixel lights its cell
    lit = [v > 0 for v in small.getdata()]
    seen = [False] * len(lit)
    found = []
    for start, on in enumerate(lit):
        if not on or seen[start]:
            continue
        stack, cells = [start], []
        seen[start] = True
        while stack:
            i = stack.pop()
            cells.append(i)
            c, r = i % cols, i // cols
            for dc in (-1, 0, 1):
                for dr in (-1, 0, 1):
                    cc, rr = c + dc, r + dr
                    j = rr * cols + cc
                    if 0 <= cc < cols and 0 <= rr < rows and lit[j] and not seen[j]:
                        seen[j] = True
                        stack.append(j)
        cw, ch = mask.width / cols, mask.height / rows
        cs = [i % cols for i in cells]
        rs = [i // cols for i in cells]
        # Back to pixels, then tightened to the changed pixels inside.
        box = (int(min(cs) * cw), int(min(rs) * ch),
               int((max(cs) + 1) * cw), int((max(rs) + 1) * ch))
        tight = mask.crop(box).getbbox()
        if tight:
            box = (box[0] + tight[0], box[1] + tight[1], box[0] + tight[2], box[1] + tight[3])
        found.append((len(cells), box))
    found.sort(key=lambda item: -item[0])
    return [box for _n, box in found[:MAX_REGIONS]]


@dataclass
class Settled:
    changed: float          # share of the screen that changed, 0-1
    near: Optional[float]   # share of the cells around the aimed point, or None
    seconds: float
    still: bool             # False when it was still moving at the deadline


def settle(monitor: dict, before: bytes, *, moving: bytes = b"",
           point: Optional[tuple[float, float]] = None, max_s: float = 4.0,
           stop: Optional[threading.Event] = None, sample=None) -> Settled:
    """Wait until the screen stops changing after an action, then say how
    much changed compared with `before` (the grid taken just before it).
    `sample` replaces the screen grab in tests."""
    sample = sample or (lambda: grid(monitor))
    started = time.monotonic()
    previous = b""
    quiet_since: Optional[float] = None
    current = before
    while True:
        if stop is not None and stop.is_set():
            break
        time.sleep(SETTLE_POLL_S)
        try:
            current = sample()
        except Exception:
            # A grab that fails mid-change (a page being replaced) is no
            # answer: keep watching until the deadline.
            if time.monotonic() - started >= max_s:
                break
            continue
        now = time.monotonic()
        if previous and _changed_share(previous, current, moving) <= SETTLE_STILL:
            quiet_since = quiet_since or now
        else:
            quiet_since = None
        previous = current
        elapsed = now - started
        if quiet_since is not None and elapsed >= SETTLE_MIN_S \
                and now - quiet_since >= SETTLE_QUIET_S:
            break
        if elapsed >= max_s:
            break
    near = None
    if point is not None:
        near = _changed_share(before, current, moving,
                              _cells_near(monitor, point[0], point[1]))
    return Settled(changed=_changed_share(before, current, moving), near=near,
                   seconds=time.monotonic() - started,
                   still=quiet_since is not None)
