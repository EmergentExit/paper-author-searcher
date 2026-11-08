"""Decorator base for layers around the HTTP client (cache, budget, public-resource scheduling).

Each layer overrides only the operations it changes and forwards the rest explicitly, so the chain is visible to
type checking and to readers; nothing is reached through ``__getattr__`` magic. ``unwrap`` finds the network
client at the bottom of a chain.
"""

import threading
import time
from collections.abc import Callable, Iterable
from contextlib import nullcontext
from http import HTTPStatus
from typing import Any
from urllib.parse import urlsplit

from .http import SourceError
from .pacing import Pacer, RequestPacer
from .refusals import RefusalBackoff, waits_for_holds


class HttpClientDecorator:
    """Forwards every client operation and attribute to ``self.client``; subclasses override what they change."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        xml: bool = False,
    ) -> Any:
        return self.client.get(url, params, headers, xml)

    def post_json(
        self,
        url: str,
        payload: Any,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected_type: type = dict,
    ) -> Any:
        return self.client.post_json(url, payload, params=params, headers=headers, expected_type=expected_type)

    def probe(
        self,
        url: str,
        maximum_bytes: int = 1024,
        html_bytes: int = 0,
        maximum_redirects: int = 5,
        redirect_guard: Callable[[str], None] | None = None,
    ) -> Any:
        if redirect_guard is None:
            return self.client.probe(url, maximum_bytes, html_bytes, maximum_redirects)
        return self.client.probe(url, maximum_bytes, html_bytes, maximum_redirects, redirect_guard=redirect_guard)

    def stream_pdf(self, url: str, destination: Any, config: Any) -> Any:
        return self.client.stream_pdf(url, destination, config)

    def serves_probe_from_cache(self, url: str, maximum_bytes: int = 1024, html_bytes: int = 0) -> bool:
        return serves_probe_from_cache(self.client, url, maximum_bytes, html_bytes)

    def close(self) -> None:
        close = getattr(self.client, 'close', None)
        if callable(close):
            close()

    @property
    def timeout(self) -> Any:
        return getattr(self.client, 'timeout', None)

    @property
    def retries(self) -> Any:
        return getattr(self.client, 'retries', None)

    @property
    def transport_statistics(self) -> Any:
        return getattr(self.client, 'transport_statistics', None)


def serves_probe_from_cache(client: Any, url: str, maximum_bytes: int = 1024, html_bytes: int = 0) -> bool:
    """Whether a probe through this client would be answered from a cache below it (False for plain clients)."""
    # Looked up on the type: a client that merely answers every attribute (a test double) declares no cache.
    if not callable(getattr(type(client), 'serves_probe_from_cache', None)):
        return False
    return client.serves_probe_from_cache(url, maximum_bytes, html_bytes) is True


def unwrap(client: Any) -> Any:
    """The innermost client of a decorator chain (the network client itself when it is undecorated)."""
    seen = set()
    while isinstance(client, HttpClientDecorator) and id(client) not in seen:
        seen.add(id(client))
        client = client.client
    return client


class PacedHttpClient(HttpClientDecorator):
    """Keeps each configured host's request rules. It belongs directly above the network client, below the cache, so
    only requests that really go out wait. A pacer shared between processes keeps the interval across all of them.

    - Interval: at least the host's interval between request starts.
    - One connection (``single_connection``): no request to the host starts while another is in flight, and the
      interval counts from the end of the previous response, as arXiv's terms require.
    - Refusals: a 429 with Retry-After holds the host back for that long, up to ``maximum_hold`` seconds (a daily
      quota's hours-long answer must not put every search to sleep). For a host with a ``backoff`` ladder a 429 holds
      it for the ladder's next step: the refused request waits out a hold within ``maximum_hold`` and is sent once
      more; past that, it and every request until the hold ends fail at once as ``source_deferred``, so a blocked
      host is not asked again while it is blocked.
    """

    def __init__(
        self,
        client: Any,
        intervals: Iterable[tuple[str, float]],
        pacer_factory: Callable[[float], Pacer] = RequestPacer,
        maximum_hold: float = 60.0,
        single_connection: Iterable[str] = (),
        backoff: RefusalBackoff | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(client)
        self.intervals = {host.lower(): interval for host, interval in intervals if interval > 0}
        self.pacers = {host: pacer_factory(interval) for host, interval in self.intervals.items()}
        self.maximum_hold = maximum_hold
        self.connections = {host.lower(): threading.Lock() for host in single_connection}
        self.backoff = backoff or RefusalBackoff()
        self._sleep = sleep
        self._clock = clock
        # Retry-After holds this client placed: host -> when the hold ends (the pacer keeps no hold apart from slots).
        self._held_until: dict[str, float] = {}
        self._holds_lock = threading.Lock()

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        xml: bool = False,
    ) -> Any:
        return self._send(url, lambda: self.client.get(url, params, headers, xml))

    def post_json(
        self,
        url: str,
        payload: Any,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected_type: type = dict,
    ) -> Any:
        return self._send(
            url, lambda: super(PacedHttpClient, self).post_json(url, payload, params, headers, expected_type)
        )

    def probe(
        self,
        url: str,
        maximum_bytes: int = 1024,
        html_bytes: int = 0,
        maximum_redirects: int = 5,
        redirect_guard: Callable[[str], None] | None = None,
    ) -> Any:
        return self._send(
            url,
            lambda: super(PacedHttpClient, self).probe(
                url, maximum_bytes, html_bytes, maximum_redirects, redirect_guard
            ),
            note_refusals=False,
        )

    def _send(self, url: str, request: Callable[[], Any], note_refusals: bool = True) -> Any:
        host = (urlsplit(url).hostname or '').lower()
        sent_after_refusal = False
        while True:
            self._refuse_while_held(host)
            with self.connections.get(host) or nullcontext():
                self._wait(host)
                try:
                    answer = request()
                except SourceError as error:
                    hold = self._note_refusal(host, error) if note_refusals else None
                    if hold is not None and hold <= self.maximum_hold and not sent_after_refusal and waits_for_holds():
                        sent_after_refusal = True
                        continue
                    raise
                finally:
                    self._after_response(host)
            if self.backoff.covers(host):
                self.backoff.succeeded(host)
            return answer

    def _wait(self, host: str) -> None:
        pacer = self.pacers.get(host)
        if pacer is not None:
            pacer.wait(host)

    def _after_response(self, host: str) -> None:
        """A single-connection host's interval counts from the end of the response."""
        pacer = self.pacers.get(host)
        if pacer is not None and host in self.connections:
            pacer.hold(host, self.intervals[host])

    def _refuse_while_held(self, host: str) -> None:
        """Wait out a host's backoff hold within ``maximum_hold``; past it, or when requests may not wait for holds
        (refusals.without_waiting_for_holds), refuse without asking."""
        remaining = self._hold_remaining(host)
        if remaining > 0 and not waits_for_holds():
            raise self._deferred(host, remaining)
        if not self.backoff.covers(host):
            return
        if 0 < remaining <= self.maximum_hold:
            # The pacer holds a host for at most maximum_hold (other processes share it); sleep out the rest here.
            self._sleep(remaining)
        elif remaining > self.maximum_hold:
            raise self._deferred(host, remaining)

    def _hold_remaining(self, host: str) -> float:
        """Seconds left of the host's hold: its backoff ladder's, or a Retry-After this client was given."""
        with self._holds_lock:
            until = self._held_until.get(host)
        retry_after = max(0.0, until - self._clock()) if until is not None else 0.0
        return max(self.backoff.remaining(host), retry_after)

    @staticmethod
    def _deferred(host: str, remaining: float) -> SourceError:
        return SourceError(
            f'{host} refused recent requests; not asked again for {remaining:.0f} s',
            status_code=HTTPStatus.TOO_MANY_REQUESTS,
            error_type='source_deferred',
            retry_after=remaining,
            endpoint_host=host,
        )

    def _note_refusal(self, host: str, error: SourceError) -> float | None:
        """Hold the host back after a 429; the hold of a host with a backoff ladder, None for any other."""
        if error.status_code != HTTPStatus.TOO_MANY_REQUESTS:
            return None
        pacer = self.pacers.get(host)
        if self.backoff.covers(host):
            hold = self.backoff.refused(host)
            if pacer is not None:
                pacer.hold(host, min(hold, self.maximum_hold))
            return hold
        if pacer is not None and error.retry_after:
            seconds = min(float(error.retry_after), self.maximum_hold)
            pacer.hold(host, seconds)
            with self._holds_lock:
                self._held_until[host] = max(self._held_until.get(host, 0.0), self._clock() + seconds)
        return None
