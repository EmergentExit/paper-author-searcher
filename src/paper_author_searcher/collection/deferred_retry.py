"""Asking a throttled source again at the end of collection, once its hold may have ended.

A source that throttles us (arXiv's load-based 429s, a host the transport is holding back) fails a request with
``rate_limited`` or ``source_deferred``. Its blocks often last only minutes (arXiv took requests again two minutes
after refusing a name search on 2026-11-07), while a person's collection runs for several, so the requests that
failed that way are asked again once the rest of collection is done.

``RetryWindow`` decides only when to ask: its callers decide what to ask again. One window is one search's budget
of seconds to wait, shared by every retry in it, so a long block cannot hold a search past the budget.
"""

import time
from collections.abc import Callable
from typing import TypeVar

from paper_author_searcher.config import CollectionConfig

T = TypeVar('T')

# (error type, seconds the source asked us to wait) of a failed answer; None when the answer did not fail.
Failure = tuple[str | None, float | None] | None


class RetryWindow:
    def __init__(
        self,
        config: CollectionConfig,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._clock = clock
        self._sleep = sleep
        self._ends = clock() + config.deferred_retry_seconds

    def retryable(self, error_type: str | None) -> bool:
        return error_type in self.config.deferred_retry_error_types

    def again(self, ask: Callable[[], T], failure: Callable[[T], Failure]) -> T:
        """Ask once more; while the answer failed for throttling and its wait fits the window, wait and ask again."""
        answer = ask()
        for _ in range(self.config.deferred_retry_attempts - 1):
            wait = self._wait(failure(answer))
            if wait is None:
                break
            self._sleep(wait)
            answer = ask()
        return answer

    def _wait(self, failed: Failure) -> float | None:
        """Seconds to wait before the next try, or None: not a throttling failure, or a wait past the window."""
        if failed is None or not self.retryable(failed[0]):
            return None
        wait = failed[1] if failed[1] is not None else self.config.deferred_retry_default_wait_seconds
        return wait if self._clock() + wait <= self._ends else None


class DeferredRetry:
    """Opens one ``RetryWindow`` per search."""

    def __init__(
        self,
        config: CollectionConfig,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._clock = clock
        self._sleep = sleep

    def window(self) -> RetryWindow:
        return RetryWindow(self.config, self._clock, self._sleep)
