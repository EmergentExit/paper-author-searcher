"""A time limit for one search: past it every request is refused, so the run ends with what it has found.

Sources fail in isolation, so refused requests let the remaining stages finish and the search still returns its
verified papers. Sources that pace themselves wait before they reach this client, so the full-text stage also checks
the deadline itself (``PDFResolver``). Place it outside the response cache (deadline -> cache -> ...), so a refusal is
never recorded as the answer for later searches.
"""

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from .http import SourceError
from .http_decorators import HttpClientDecorator

logger = logging.getLogger(__name__)


class TimeLimitReached(SourceError):
    def __init__(self, message: str) -> None:
        super().__init__(message, error_type='time_limit')


class SearchDeadline:
    """Shared by every client in one run; ``reached`` says whether the limit refused anything."""

    def __init__(
        self,
        seconds: float,
        clock: Callable[[], float] = time.monotonic,
        stop: threading.Event | None = None,
    ) -> None:
        if seconds <= 0:
            raise ValueError('seconds must be positive')
        self.seconds = seconds
        self.clock = clock
        self.expires_at = clock() + seconds
        self.reached = False
        # Set when the search's owner stops it: from then on the run is past its limit at once.
        self.stop = stop
        self._lock = threading.Lock()

    @property
    def stopped(self) -> bool:
        return self.stop is not None and self.stop.is_set()

    def expired(self) -> bool:
        """Whether the limit has passed; asking when it has counts as the limit having cut the run short."""
        if self.stopped:
            return True
        if self.clock() < self.expires_at:
            return False
        with self._lock:
            if not self.reached:
                logger.warning('Search time limit of %.0f s reached; finishing with what was found', self.seconds)
            self.reached = True
        return True

    def check(self) -> None:
        if self.stopped:
            raise TimeLimitReached('The search was stopped; request not sent')
        if self.expired():
            raise TimeLimitReached(f'Search time limit of {self.seconds:.0f} s reached; request not sent')


class DeadlineHttpClient(HttpClientDecorator):
    """Refuses every request once the run's deadline has passed; a request already sent keeps its own timeouts."""

    def __init__(self, client: Any, deadline: SearchDeadline) -> None:
        super().__init__(client)
        self.deadline = deadline

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        xml: bool = False,
    ) -> Any:
        self.deadline.check()
        return super().get(url, params, headers, xml)

    def post_json(
        self,
        url: str,
        payload: Any,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected_type: type = dict,
    ) -> Any:
        self.deadline.check()
        return super().post_json(url, payload, params, headers, expected_type)

    def probe(
        self,
        url: str,
        maximum_bytes: int = 1024,
        html_bytes: int = 0,
        maximum_redirects: int = 5,
        redirect_guard: Callable[[str], None] | None = None,
    ) -> Any:
        self.deadline.check()
        return super().probe(url, maximum_bytes, html_bytes, maximum_redirects, redirect_guard)

    def stream_pdf(self, url: str, destination: Any, config: Any) -> Any:
        self.deadline.check()
        return super().stream_pdf(url, destination, config)
