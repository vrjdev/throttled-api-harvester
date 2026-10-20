import time


class RateLimiter:
    """Evenly spaced slots (no bursts): at most `qps` calls per second."""

    def __init__(self, qps: float, clock=time.monotonic, sleep=time.sleep):
        self._interval = 1.0 / qps
        self._clock = clock
        self._sleep = sleep
        self._next = clock()

    def wait(self) -> None:
        now = self._clock()
        if now < self._next:
            self._sleep(self._next - now)
            now = self._next
        self._next = now + self._interval
