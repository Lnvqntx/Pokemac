"""Display-independent movement, bounds, and encounter timing."""

import random
from dataclasses import dataclass
from time import monotonic


@dataclass(frozen=True)
class Limits:
    """Bounds for the window's top-left point in desktop coordinates."""

    left: int
    top: int
    right: int
    floor: int

    def clamp(self, x, y):
        return min(self.right, max(self.left, x)), min(self.floor, max(self.top, y))

    def opposite_edge(self, x):
        """Place an encounter at the edge farther from the companion."""
        edge = self.right if x - self.left < self.right - x else self.left
        return edge, self.floor


class Motion:
    """Walk at the floor, rest between walks, and fall after a drag."""

    speed = 46.0
    gravity = 1100.0

    def __init__(self, limits: Limits, *, rng=None, start_fraction=0.15):
        self.limits = limits
        self.rng = random if rng is None else rng
        self.x = limits.left + (limits.right - limits.left) * start_fraction
        self.y = limits.floor
        self.direction = 1
        self.dragging = False
        self._velocity = 0.0
        self._walk_left = self.rng.uniform(3.0, 7.0)
        self._rest_left = 0.0

    def set_limits(self, limits: Limits):
        grounded = self.y == self.limits.floor and not self.dragging
        self.limits = limits
        self.x, self.y = limits.clamp(self.x, limits.floor if grounded else self.y)

    def begin_drag(self):
        self.dragging = True
        self._velocity = 0.0

    def drag_to(self, x, y):
        if self.dragging:
            self.x, self.y = self.limits.clamp(x, y)

    def end_drag(self):
        self.dragging = False

    def advance(self, seconds):
        if self.dragging or seconds <= 0:
            return
        if self.y < self.limits.floor:
            self.y += self._velocity * seconds + 0.5 * self.gravity * seconds**2
            self._velocity += self.gravity * seconds
            if self.y >= self.limits.floor:
                self.y = self.limits.floor
                self._velocity = 0.0
            return
        if self.limits.left == self.limits.right:
            return

        remaining = seconds
        while remaining > 0:
            if self._rest_left > 0:
                step = min(remaining, self._rest_left)
                self._rest_left -= step
                remaining -= step
                continue

            edge = self.limits.right if self.direction == 1 else self.limits.left
            until_edge = abs(edge - self.x) / self.speed
            step = min(remaining, self._walk_left, until_edge)
            if step == until_edge:
                self.x = edge
                self.direction *= -1
            else:
                self.x += self.direction * self.speed * step
            self._walk_left -= step
            remaining -= step
            if self._walk_left <= 0:
                self._rest_left = self.rng.uniform(0.6, 1.8)
                self._walk_left = self.rng.uniform(3.0, 7.0)


class SpawnSchedule:
    """Emit one encounter per interval, using an injectable monotonic clock."""

    def __init__(self, interval=20 * 60, *, clock=monotonic):
        self._clock = clock
        self._interval = interval
        self._deadline = clock() + interval

    def due(self):
        now = self._clock()
        if now < self._deadline:
            return False
        self._deadline = now + self._interval
        return True
