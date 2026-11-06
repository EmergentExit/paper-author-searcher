"""Run independent calls side by side and get their answers back in the order they were given.

A caller that then works through the answers in that order decides exactly as if it had made the calls one after
another; only the waiting overlaps.
"""

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar('T')


@dataclass(frozen=True)
class Outcome(Generic[T]):
    """A call's answer, or the expected error it raised instead."""

    value: T | None = None
    error: Exception | None = None

    def unwrap(self) -> T:
        if self.error is not None:
            raise self.error
        return self.value  # type: ignore[return-value]


def attempt(call: Callable[[], T], expected: tuple[type[Exception], ...]) -> Outcome[T]:
    """The call's outcome; an error of an ``expected`` type is kept for later, any other propagates."""
    try:
        return Outcome(value=call())
    except expected as exc:
        return Outcome(error=exc)


def in_order(calls: Sequence[Callable[[], T]], workers: int, name: str = 'worker') -> list[T]:
    """Every call's answer, in the calls' order, on up to ``workers`` threads. Each thread runs in a copy of the
    caller's context, so phase timings still record. An unexpected error propagates once all calls have ended."""
    workers = min(workers, len(calls))
    if workers <= 1:
        return [call() for call in calls]
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=name) as pool:
        futures = [pool.submit(copy_context().run, call) for call in calls]
        return [future.result() for future in futures]
