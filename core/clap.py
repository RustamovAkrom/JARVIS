"""
Hearing a clap — the trigger the clap launcher (clap_launcher.pyw) waits for.

WHAT A DELIBERATE DOUBLE CLAP LOOKS LIKE TO A MICROPHONE
    Not its loudness or its pitch — both depend on the microphone. A laptop's
    built-in array hears a clap near full scale; a webcam across the room hears
    it at a tenth of that, through a filter that removes most of what makes a
    clap sound "bright". Thresholds in absolute units therefore suit one
    microphone and fail on the next. What survives every microphone is the
    clap's SHAPE and the SILENCE around it:

      * it starts INSTANTLY: the level jumps several times over in one 16 ms
        block, where a voice or music swells in over tens of milliseconds;
      * it is OVER almost at once: gone within ~160 ms, where a syllable or a
        note holds;
      * it stands well above the room (_FLOOR_RATIO times its quiet level);
      * it comes out of quiet, the two claps have quiet between them, and quiet
        follows. Talk is the opposite: sharp sounds (plosives, a laugh, a hand
        on the desk) surrounded by more sound. This is what stops it starting
        by itself while someone talks near the microphone.

    Every level test is RELATIVE to the room, measured continuously (the 20th
    percentile of the last two seconds). The only absolute numbers are a gate
    low enough to pass a clap on a quiet webcam yet above a microphone's hiss.

LEARNING ONE PARTICULAR MICROPHONE (optional)
    `learn_level(levels)` turns a few claps heard in learning mode into a level;
    the detector then also requires a sound to reach a share of it. That shuts
    out the quieter sharp sounds of a room (a key, a mouse click) that pass the
    relative tests in a very quiet room. The clap launcher offers it from its
    menu; without it the relative tests stand on their own.

WHY TWO CLAPS BY DEFAULT
    One clap is a door, a book put down, a television. Two claps a short,
    steady interval apart, with nothing after them, is a person asking. More
    than two in a row is applause, and is refused. The count is a setting.

NO MODEL, NO DOWNLOAD, NOTHING KEPT
    numpy only, a few hundred operations per 16 ms block. The audio is looked
    at and dropped; nothing is recorded or sent anywhere.
"""
from __future__ import annotations

from collections import deque
from typing import Optional

import numpy as np

RATE = 16000
BLOCK = 256                       # 16 ms at 16 kHz

# The relative tests, at sensitivity 1.0 ("normal"). Sensitivity scales the
# level tests: 1.4 hears softer claps from further away, 0.7 only sharp, close
# ones. Figures for reference, from claps recorded on a webcam microphone at
# ~1.5 m (the hardest case met so far): 9 - 39x the room, rising 6 - 11x in one
# block, gone in 50 - 100 ms; the room just before a clap 3 - 5x its quiet
# level (a sound still fading); talk 10 - 30x.
_FLOOR_RATIO = 7.0                # a clap: this many times the room's level...
_RISE_RATIO = 4.0                 # ...and this many times the block before it
_DECAY_RATIO = 0.30               # it falls below this share of its peak...
_DECAY_WITHIN_S = 0.16            # ...within this long of starting
_QUIET_BEFORE_S = 0.25            # before the first clap, this long...
_QUIET_RATIO = 6.0                # ...no block above this many times the room
_TAIL_S = 0.12                    # a clap rings on this long before "quiet" is judged
_QUIET_GAP_RATIO = 4.0            # between the claps and after the last
_SIMILAR_RATIO = 4.0              # two claps of one pattern, this close in level
_GAP_MIN_S = 0.12                 # between two claps of one pattern
_GAP_MAX_S = 1.00
_QUIET_AFTER_S = 0.35             # nothing more for this long, or it is applause
_REFRACTORY_S = 3.0               # after firing, before listening again
_WARMUP_S = 1.5                   # after the microphone opens: the room's level
                                  # is not known yet, and a microphone settling
                                  # (a pop, automatic gain) looks like a clap

# Absolute gate: under this it is the microphone's own hiss, whatever the room.
_GATE_PEAK = 0.03
_GATE_RMS = 0.006
# With a learned clap level, a sound must reach this share of it.
_LEARNED_SHARE = 0.30

SENSITIVITY = {"low": 0.7, "normal": 1.0, "high": 1.4}


def learn_level(levels) -> Optional[float]:
    """The clap level to remember, from the levels heard in learning mode, or
    None when there were too few claps to say (fewer than three)."""
    vals = sorted(float(v) for v in levels if v and v > 0)
    if len(vals) < 3:
        return None
    return float(np.median(vals))


class ClapDetector:
    """Feed it microphone blocks; `feed()` returns True when the pattern is
    complete. Not thread-safe — one audio callback feeds one detector.

    learn=True: never fires; every sound with a clap's shape is added to
    `heard_levels`, for learn_level()."""

    def __init__(self, claps: int = 2, sensitivity: float = 1.0,
                 level: Optional[float] = None, learn: bool = False,
                 rate: int = RATE) -> None:
        self.claps = max(1, int(claps))
        self.sensitivity = max(0.5, min(2.0, float(sensitivity)))
        self.level = float(level) if level else None
        self.learn = bool(learn)
        self.heard_levels: list[float] = []
        self.rate = int(rate)
        self._t = 0.0                         # seconds of audio seen
        self._levels: deque = deque(maxlen=int(2.0 * rate / BLOCK))
        self._prev = deque([0.0, 0.0], maxlen=2)
        self._hist: deque = deque(maxlen=int(1.5 * rate / BLOCK))   # (t, rms)
        self._pending = None                  # (onset time, peak level)
        self._times: list[float] = []         # confirmed claps in this pattern
        self._levels_of: list[float] = []     # their peak levels
        self._ended = 0.0                     # when the last clap had died away
        self._noisy_since_end = False         # anything loud after it?
        self._quiet_until = 0.0
        self._carry = np.zeros(0, dtype=np.float32)
        self.last_reason = ""                 # the last decision, for the log

    # ── public ───────────────────────────────────────────────────────────────
    def feed(self, samples) -> bool:
        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        if self._carry.size:
            x = np.concatenate([self._carry, x])
        fired = False
        n = (x.size // BLOCK) * BLOCK
        for i in range(0, n, BLOCK):
            if self._block(x[i:i + BLOCK]):
                fired = True
        self._carry = x[n:].copy()
        return fired

    def reset(self) -> None:
        self._pending = None
        self._times.clear()
        self._levels_of.clear()
        self._noisy_since_end = False

    # ── one block ────────────────────────────────────────────────────────────
    def _block(self, b: np.ndarray) -> bool:
        now = self._t
        self._t += BLOCK / self.rate
        rms = float(np.sqrt(np.mean(b * b)) + 1e-9)
        peak = float(np.max(np.abs(b)))
        floor = (float(np.percentile(self._levels, 20))
                 if len(self._levels) >= 10 else max(rms, _GATE_RMS))
        floor = max(floor, 1e-5)
        before = max(self._prev)
        self._prev.append(rms)
        self._levels.append(rms)
        self._hist.append((now, rms))

        if now < self._quiet_until or now < _WARMUP_S:
            return False

        # A candidate in progress: has it died away fast enough?
        if self._pending is not None:
            t0, level = self._pending
            if rms > level:
                # Still rising (a clap peaks a block or two in): judge the decay
                # against the peak, not the first block.
                level = rms
                self._pending = (t0, level)
            if rms <= level * _DECAY_RATIO:
                self._pending = None
                return self._confirmed(t0, level, now, floor)
            if now - t0 > _DECAY_WITHIN_S:
                self._pending = None
                self.last_reason = "too long (a voice, music or a thud)"
                self.reset()
            return False

        # A pattern that has gone quiet long enough is either complete or dead.
        if self._times and not self.learn:
            since = now - self._times[-1]
            if len(self._times) >= self.claps and since >= _QUIET_AFTER_S:
                noisy = self._noisy_since_end
                self.reset()
                if noisy:
                    self.last_reason = "not quiet after the claps (talk or noise)"
                    return False
                self.last_reason = "pattern complete"
                self._quiet_until = now + _REFRACTORY_S
                return True
            if len(self._times) < self.claps and since > _GAP_MAX_S:
                self.reset()

        if self._is_onset(rms, peak, floor, before):
            self._pending = (now, rms)
            return False
        # Anything else loud after the last clap died away (the next clap's own
        # first block excepted, above): not a pattern standing on its own.
        if (self._times and now > self._ended + _TAIL_S
                and rms > floor * _QUIET_GAP_RATIO):
            self._noisy_since_end = True
        return False

    def _is_onset(self, rms: float, peak: float, floor: float, before: float) -> bool:
        s = self.sensitivity
        if self.learn:
            # Learning is for finding the claps, quiet microphone or not: the
            # gate and the shape only.
            return (peak >= _GATE_PEAK * 0.5 and rms >= floor * _FLOOR_RATIO
                    and rms >= before * _RISE_RATIO)
        if peak < _GATE_PEAK / s or rms < _GATE_RMS / s:
            return False
        if self.level and rms < self.level * _LEARNED_SHARE * 0.5 / s:
            # Far below what this microphone hears a clap at. Half the share
            # only, because a clap's first block is not yet its peak; the full
            # share is checked on the peak in _confirmed.
            return False
        return rms >= floor * _FLOOR_RATIO / s and rms >= before * _RISE_RATIO

    def _quiet(self, start: float, end: float, ratio: float, floor: float) -> bool:
        """Was every block between `start` and `end` (audio seconds) within
        `ratio` times the room's level?"""
        levels = [r for t, r in self._hist if start <= t < end]
        return not levels or max(levels) <= floor * ratio

    def _confirmed(self, t0: float, level: float, ended: float, floor: float) -> bool:
        if self.learn:
            self.heard_levels.append(level)
            self.last_reason = f"heard a clap ({len(self.heard_levels)})"
            return False
        if self.level and level < self.level * _LEARNED_SHARE / self.sensitivity:
            self.last_reason = "too quiet for a clap on this microphone"
            self.reset()
            return False
        if self._times:
            gap = t0 - self._times[-1]
            if gap < _GAP_MIN_S:
                return False                  # the same clap ringing on
            if gap > _GAP_MAX_S:
                self.reset()
        if not self._times:
            # The first clap of a pattern comes out of quiet. In talk it does not.
            if not self._quiet(t0 - _QUIET_BEFORE_S, t0 - 0.02, _QUIET_RATIO, floor):
                self.last_reason = "not quiet before it (talk or noise)"
                return False
        else:
            if self._noisy_since_end or not self._quiet(self._ended + _TAIL_S, t0 - 0.02,
                                                         _QUIET_GAP_RATIO, floor):
                self.last_reason = "not quiet between the claps (talk or noise)"
                self.reset()
                return False
            first = self._levels_of[0]
            if max(first, level) > min(first, level) * _SIMILAR_RATIO:
                self.last_reason = "the two sounds were too different to be claps"
                self.reset()
                return False
        self._times.append(t0)
        self._levels_of.append(level)
        self._ended = ended
        self._noisy_since_end = False
        self.last_reason = (f"clap {len(self._times)} of {self.claps} "
                            f"({level / floor:.0f}x the room)")
        if len(self._times) > self.claps:
            # More claps than the pattern: applause, not a request.
            self.last_reason = "too many claps (applause)"
            self.reset()
            self._quiet_until = t0 + 1.0
        return False
