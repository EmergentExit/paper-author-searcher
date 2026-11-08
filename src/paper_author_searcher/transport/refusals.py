"""How long to leave a host alone after it refuses us (HTTP 429), for hosts whose refusals start a longer block.

arXiv answers too many requests in a window with 429s that carry no Retry-After and then refuses everything for
ten minutes or more (measured 2026-11-05: 147 requests at one per 3-4 s succeeded, then 62 refusals in a row).
Asking again sooner only prolongs the block, so each consecutive refusal holds the host for the next, longer step
of its ladder, and a success resets it.
"""

import threading
import time
from collections.abc import Callable, Iterable, Sequence


class RefusalBackoff:
    """Escalating per-host holds: the n-th refusal in a row holds the host for the n-th step (the last step repeats).

    Thread-safe; one instance is shared by every request of a client.
    """

    def __init__(
        self, ladders: Iterable[tuple[str, Sequence[float]]] = (), clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.ladders = {host.lower(): tuple(steps) for host, steps in ladders}
        for host, steps in self.ladders.items():
            if not steps or any(step <= 0 for step in steps):
                raise ValueError(f'refusal backoff for {host} needs positive steps')
        self._clock = clock
        self._refusals: dict[str, int] = {}
        self._until: dict[str, float] = {}
        self._lock = threading.Lock()

    def covers(self, host: str) -> bool:
        return host in self.ladders

    def refused(self, host: str) -> float:
        """Record a refusal; the seconds the host is now held for."""
        steps = self.ladders[host]
        with self._lock:
            count = self._refusals.get(host, 0)
            self._refusals[host] = count + 1
            hold = steps[min(count, len(steps) - 1)]
            self._until[host] = self._clock() + hold
        return hold

    def succeeded(self, host: str) -> None:
        with self._lock:
            self._refusals.pop(host, None)
            self._until.pop(host, None)

    def remaining(self, host: str) -> float:
        """Seconds left of the host's current hold (0 when it is not held)."""
        with self._lock:
            until = self._until.get(host)
        return max(0.0, until - self._clock()) if until is not None else 0.0
