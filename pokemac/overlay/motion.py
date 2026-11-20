"""Display-independent movement, bounds, and encounter timing."""

import random
from dataclasses import dataclass
from math import cos, sin, tau


@dataclass(frozen=True)
class Limits:
    """Bounds for the window's top-left point in desktop coordinates."""

    left: int
    top: int
    right: int
    bottom: int

    def clamp(self, x, y):
        return min(self.right, max(self.left, x)), min(self.bottom, max(self.top, y))

    def distance_squared(self, x, y):
        nearest_x, nearest_y = self.clamp(x, y)
        return (x - nearest_x) ** 2 + (y - nearest_y) ** 2

    def opposite_edge(self, x):
        """Place an encounter halfway up the edge farther from the party."""
        edge = self.right if x - self.left < self.right - x else self.left
        return edge, (self.top + self.bottom) // 2


def _bounce(position, velocity, seconds, low, high):
    span = high - low
    if span == 0 or velocity == 0:
        return position, velocity
    offset = (position - low + velocity * seconds) % (2 * span)
    if offset == 0:
        return low, abs(velocity)
    if offset == span:
        return high, -abs(velocity)
    if offset < span:
        return low + offset, velocity
    return high - (offset - span), -velocity


class Motion:
    """Roam in a work area with either a walking or floating gait."""

    def __init__(self, limits: Limits, *, rng=None, start=(0.15, 0.5), floating=False):
        self.limits = limits
        self.rng = random if rng is None else rng
        self.x = limits.left + (limits.right - limits.left) * start[0]
        self.y = limits.top + (limits.bottom - limits.top) * start[1]
        self.dragging = False
        self.floating = floating
        self.napping = False
        self._rest_left = 0.0
        self._base_y = self.y
        self._phase = 0.0
        self._choose_heading()

    @property
    def direction(self):
        return 1 if self._vx >= 0 else -1

    def _choose_heading(self):
        angle = self.rng.uniform(0, tau)
        speed = 32 if self.floating else 48
        self._vx = speed * cos(angle)
        self._vy = speed * sin(angle)
        self._walk_left = self.rng.uniform(1.5, 5.0) if not self.floating else 0.0

    def set_floating(self, floating):
        if floating != self.floating:
            self.floating = floating
            self.napping = False
            self._rest_left = 0.0
            self._base_y = self.y
            self._phase = 0.0
            self._choose_heading()

    def set_limits(self, limits: Limits):
        if limits != self.limits:
            self.limits = limits
            self.x, self.y = limits.clamp(self.x, self.y)
            self._base_y = self.y
            self._phase = 0.0

    def begin_drag(self):
        self.dragging = True
        self.napping = False
        self._rest_left = 0.0

    def drag_to(self, x, y):
        if self.dragging:
            self.x, self.y = self.limits.clamp(x, y)
            self._base_y = self.y
            self._phase = 0.0

    def end_drag(self):
        if self.dragging:
            self.dragging = False
            self._choose_heading()

    def _move(self, seconds):
        bounds = self.limits
        self.x, self._vx = _bounce(self.x, self._vx, seconds, bounds.left, bounds.right)
        self._base_y, self._vy = _bounce(self._base_y, self._vy, seconds, bounds.top, bounds.bottom)
        bob = 0.0
        if self.floating:
            self._phase = (self._phase + seconds * 2.8) % tau
            bob = 6 * sin(self._phase)
        _, self.y = bounds.clamp(self.x, self._base_y + bob)

    def advance(self, seconds):
        if self.dragging or seconds <= 0:
            return
        if self.floating:
            self._move(seconds)
            return

        remaining = seconds
        while remaining > 0:
            if self._rest_left > 0:
                step = min(remaining, self._rest_left)
                self._rest_left -= step
                remaining -= step
                if self._rest_left == 0:
                    self.napping = False
                    self._choose_heading()
                continue

            step = min(remaining, self._walk_left)
            self._move(step)
            self._walk_left -= step
            remaining -= step
            if self._walk_left == 0:
                self.napping = self.rng.random() < 0.12
                self._rest_left = self.rng.uniform(6, 12) if self.napping else self.rng.uniform(0.5, 1.8)
