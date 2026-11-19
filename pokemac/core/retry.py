"""Bounded retry and shared rate-limit backoff for PokéAPI sync requests."""

from email.utils import parsedate_to_datetime
from threading import Lock
from time import monotonic, sleep, time

import requests


class RequestRetry:
    def __init__(self, *, attempts=5, clock=monotonic, wait=sleep, wall_clock=time):
        self.attempts = attempts
        self._clock = clock
        self._wait = wait
        self._wall_clock = wall_clock
        self._resume_at = 0.0
        self._lock = Lock()

    def _retry_after(self, header):
        if not header:
            return 0.0
        try:
            if header.isdecimal():
                return float(header)
            return max(0.0, parsedate_to_datetime(header).timestamp() - self._wall_clock())
        except (ValueError, OverflowError):
            return 0.0

    def _wait_for_rate_limit(self):
        while True:
            with self._lock:
                remaining = self._resume_at - self._clock()
            if remaining <= 0:
                return
            self._wait(remaining)

    def __call__(self, request):
        for attempt in range(self.attempts):
            self._wait_for_rate_limit()
            try:
                return request()
            except requests.RequestException as error:
                response = error.response
                status = response.status_code if response is not None else 0
                transient = (
                    status in (408, 429) or 500 <= status < 600
                    or isinstance(error, (requests.ConnectionError, requests.Timeout))
                )
                if not transient:
                    raise
                after = self._retry_after(response.headers.get("Retry-After")) if status else 0
                delay = max(2**attempt, after)
                shared = status == 429 or after > 0
                if shared:
                    with self._lock:
                        self._resume_at = max(self._resume_at, self._clock() + delay)
                if attempt + 1 == self.attempts:
                    raise
                if not shared:
                    self._wait(delay)
