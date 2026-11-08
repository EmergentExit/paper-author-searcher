"""How long to leave a host alone after it refuses us (HTTP 429), for hosts whose refusals start a longer block.

arXiv throttles with 429s that carry no Retry-After, and not at a fixed count: on 2026-11-05 it took 147 requests at
one per 3-4 s and then refused everything for ten minutes or more; on 2026-11-07 it refused after about 50 in nine
minutes and took requests again two minutes later. Asking again sooner only prolongs a block, so each consecutive
refusal holds the host for the next, longer step of its ladder, and a success resets it.
"""

import threading
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar

# Whether a request may wait for a host's hold to end (False: a held host is refused at once, nothing waits).
_WAITS_FOR_HOLDS: ContextVar[bool] = ContextVar('waits_for_holds', default=True)


@contextmanager
def without_waiting_for_holds() -> Iterator[None]:
    """Requests in this block never wait for a held host: they are refused at once as ``source_deferred``.

    For a retry that must cost no time: it is worth one request when the host is free, and nothing when it is not.
    """
    token = _WAITS_FOR_HOLDS.set(False)
    try:
        yield
    finally:
        _WAITS_FOR_HOLDS.reset(token)


def waits_for_holds() -> bool:
    return _WAITS_FOR_HOLDS.get()


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
