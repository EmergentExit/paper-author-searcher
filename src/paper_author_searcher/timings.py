"""Wall-clock timing of a search: its phases, and its HTTP requests per host.

Measurement only: nothing here changes what a search finds or decides. A workflow run records its phases with
``recording(PhaseTimings())``; any component on that thread marks a block with ``phase(name)``, which costs nothing
outside a recorded run. HTTP requests are timed by ``TimedHttpClient`` into an ``HttpMeter``, whose ``since`` gives
one run's share of a client that outlives it.
"""

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .http_decorators import HttpClientDecorator

Clock = Callable[[], float]
SECONDS_PRECISION = 3


class PhaseTimings:
    """Seconds and entries per named phase of one run; nested phases overlap their parents, so they do not sum."""

    def __init__(self, clock: Clock = time.perf_counter) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        self._phases: dict[str, list[float]] = {}

    @contextmanager
    def measure(self, name: str) -> Iterator[None]:
        started = self.clock()
        try:
            yield
        finally:
            self.add(name, self.clock() - started)

    def add(self, name: str, seconds: float) -> None:
        with self._lock:
            entry = self._phases.setdefault(name, [0, 0.0])
            entry[0] += 1
            entry[1] += seconds

    def to_dict(self) -> dict[str, dict[str, float]]:
        with self._lock:
            return {
                name: {'count': int(count), 'seconds': round(seconds, SECONDS_PRECISION)}
                for name, (count, seconds) in self._phases.items()
            }


_CURRENT: ContextVar[PhaseTimings | None] = ContextVar('paper_search_phase_timings', default=None)


@contextmanager
def recording(timings: PhaseTimings) -> Iterator[PhaseTimings]:
    """Make ``timings`` the recorder for ``phase`` blocks on this thread until the block ends."""
    token = _CURRENT.set(timings)
    try:
        yield timings
    finally:
        _CURRENT.reset(token)


@contextmanager
def phase(name: str) -> Iterator[None]:
    """Time the block as ``name`` in the run being recorded on this thread; a no-op when none is."""
    timings = _CURRENT.get()
    if timings is None:
        yield
        return
    with timings.measure(name):
        yield


@dataclass
class HostTiming:
    calls: int = 0
    failures: int = 0
    seconds: float = 0.0


class HttpMeter:
    """Requests, failures and seconds per host; thread-safe. Seconds include pacing waits, retries and cache reads,
    and requests on parallel threads overlap, so per-host seconds can exceed the run's wall time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hosts: dict[str, HostTiming] = {}

    def record(self, host: str, seconds: float, failed: bool) -> None:
        with self._lock:
            timing = self._hosts.setdefault(host, HostTiming())
            timing.calls += 1
            timing.failures += failed
            timing.seconds += seconds

    def snapshot(self) -> dict[str, HostTiming]:
        with self._lock:
            return {host: HostTiming(item.calls, item.failures, item.seconds) for host, item in self._hosts.items()}

    def since(self, before: dict[str, HostTiming] | None = None) -> dict[str, dict[str, float]]:
        """What was recorded after ``before`` (a ``snapshot``), slowest host first."""
        before = before or {}
        result = {}
        for host, now in self.snapshot().items():
            earlier = before.get(host, HostTiming())
            calls = now.calls - earlier.calls
            if calls:
                result[host] = {
                    'calls': calls,
                    'failures': now.failures - earlier.failures,
                    'seconds': round(now.seconds - earlier.seconds, SECONDS_PRECISION),
                }
        return dict(sorted(result.items(), key=lambda item: (-item[1]['seconds'], item[0])))


class TimedHttpClient(HttpClientDecorator):
    """Times every request by host into ``meter``; requests and answers pass through unchanged."""

    def __init__(self, client: Any, meter: HttpMeter, clock: Clock = time.perf_counter) -> None:
        super().__init__(client)
        self.meter = meter
        self.clock = clock

    def _timed(self, url: str, call: Callable[[], Any]) -> Any:
        host = urlsplit(url).hostname or 'unknown'
        started = self.clock()
        failed = True
        try:
            result = call()
            failed = False
            return result
        finally:
            self.meter.record(host, self.clock() - started, failed)

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        xml: bool = False,
    ) -> Any:
        return self._timed(url, lambda: HttpClientDecorator.get(self, url, params, headers, xml))

    def post_json(
        self,
        url: str,
        payload: Any,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected_type: type = dict,
    ) -> Any:
        return self._timed(
            url,
            lambda: HttpClientDecorator.post_json(self, url, payload, params, headers, expected_type),
        )

    def probe(
        self,
        url: str,
        maximum_bytes: int = 1024,
        html_bytes: int = 0,
        maximum_redirects: int = 5,
        redirect_guard: Callable[[str], None] | None = None,
    ) -> Any:
        return self._timed(
            url,
            lambda: HttpClientDecorator.probe(self, url, maximum_bytes, html_bytes, maximum_redirects, redirect_guard),
        )

    def stream_pdf(self, url: str, destination: Any, config: Any) -> Any:
        return self._timed(url, lambda: HttpClientDecorator.stream_pdf(self, url, destination, config))
