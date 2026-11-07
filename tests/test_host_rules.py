"""Per-host request rules: one connection at a time, and an escalating hold after a host refuses us."""

import threading
import time
import unittest
from itertools import pairwise
from unittest.mock import patch

from paper_author_searcher.config import HttpConfig
from paper_author_searcher.transport.http import HttpClient, SourceError
from paper_author_searcher.transport.http_decorators import PacedHttpClient
from paper_author_searcher.transport.refusals import RefusalBackoff

ARXIV = 'https://export.arxiv.org/api/query'
OTHER = 'https://api.example.org/works'


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def refusal(retry_after=None):
    return SourceError('HTTP 429', status_code=429, retry_after=retry_after)


class Network:
    """Answers each request with the next scripted outcome (an exception is raised), recording overlap."""

    def __init__(self, outcomes=(), duration=0.0):
        self.outcomes = list(outcomes)
        self.duration = duration
        self.calls = 0
        self.in_flight = 0
        self.most_in_flight = 0
        self.spans: list[tuple[float, float]] = []
        self._lock = threading.Lock()

    def get(self, url, params=None, headers=None, xml=False):
        with self._lock:
            self.calls += 1
            self.in_flight += 1
            self.most_in_flight = max(self.most_in_flight, self.in_flight)
            outcome = self.outcomes.pop(0) if self.outcomes else {'ok': True}
        start = time.monotonic()
        time.sleep(self.duration)
        with self._lock:
            self.in_flight -= 1
            self.spans.append((start, time.monotonic()))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class RefusalBackoffTests(unittest.TestCase):
    def test_each_refusal_in_a_row_takes_the_next_step_and_a_success_resets(self):
        clock = Clock()
        backoff = RefusalBackoff([('export.arxiv.org', (60.0, 120.0))], clock)
        self.assertEqual([backoff.refused('export.arxiv.org') for _ in range(3)], [60.0, 120.0, 120.0])
        clock.now = 100.0
        self.assertEqual(backoff.remaining('export.arxiv.org'), 20.0)
        backoff.succeeded('export.arxiv.org')
        self.assertEqual(backoff.remaining('export.arxiv.org'), 0.0)
        self.assertEqual(backoff.refused('export.arxiv.org'), 60.0)

    def test_only_listed_hosts_are_covered_and_steps_must_be_positive(self):
        self.assertFalse(RefusalBackoff([('export.arxiv.org', (1.0,))]).covers('api.example.org'))
        with self.assertRaises(ValueError):
            RefusalBackoff([('export.arxiv.org', ())])
        with self.assertRaises(ValueError):
            HttpConfig(refusal_backoff=(('export.arxiv.org', (0.0,)),))


class SingleConnectionTests(unittest.TestCase):
    def test_a_single_connection_host_never_has_two_requests_in_flight(self):
        network = Network(duration=0.03)
        client = PacedHttpClient(network, [('export.arxiv.org', 0.02)], single_connection=['export.arxiv.org'])
        threads = [threading.Thread(target=client.get, args=(ARXIV,)) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(network.most_in_flight, 1)
        spans = sorted(network.spans)
        # The interval counts from the end of the previous response, not from its start.
        gaps = [later[0] - earlier[1] for earlier, later in pairwise(spans)]
        self.assertTrue(all(gap >= 0.015 for gap in gaps), gaps)

    def test_other_hosts_still_overlap(self):
        network = Network(duration=0.03)
        client = PacedHttpClient(network, [], single_connection=['export.arxiv.org'])
        threads = [threading.Thread(target=client.get, args=(OTHER,)) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertGreater(network.most_in_flight, 1)


class RefusalHoldTests(unittest.TestCase):
    def client(self, network, steps, maximum_hold=60.0):
        self.clock = Clock()
        self.slept: list[float] = []

        def sleep(seconds):
            self.slept.append(seconds)
            self.clock.now += seconds

        backoff = RefusalBackoff([('export.arxiv.org', steps)], self.clock)
        return PacedHttpClient(network, [], maximum_hold=maximum_hold, backoff=backoff, sleep=sleep)

    def test_a_refusal_within_the_hold_limit_is_waited_out_and_sent_once_more(self):
        network = Network([refusal(), {'ok': 'second'}])
        client = self.client(network, (30.0, 120.0))
        self.assertEqual(client.get(ARXIV), {'ok': 'second'})
        self.assertEqual(network.calls, 2)
        self.assertEqual(self.slept, [30.0])
        self.assertEqual(client.backoff.remaining('export.arxiv.org'), 0.0)  # the success reset the ladder

    def test_a_second_refusal_is_raised_and_the_host_is_left_alone_until_the_hold_ends(self):
        network = Network([refusal(), refusal()])
        client = self.client(network, (30.0, 120.0))
        with self.assertRaises(SourceError):
            client.get(ARXIV)
        self.assertEqual(network.calls, 2)
        with self.assertRaises(SourceError) as deferred:
            client.get(ARXIV)
        self.assertEqual(deferred.exception.error_type, 'source_deferred')
        self.assertEqual(network.calls, 2)  # not asked while held
        self.clock.now += 70  # 50 s of the 120 s hold left: within the limit, so it is waited out
        self.assertEqual(client.get(ARXIV), {'ok': True})
        self.assertEqual(network.calls, 3)
        self.assertAlmostEqual(self.slept[-1], 50.0)

    def test_hosts_without_a_ladder_keep_the_retry_after_hold_and_are_not_resent(self):
        network = Network([refusal(retry_after=5)])
        client = PacedHttpClient(network, [('api.example.org', 0.0)], backoff=RefusalBackoff())
        with self.assertRaises(SourceError):
            client.get(OTHER)
        self.assertEqual(network.calls, 1)

    def test_other_errors_are_not_refusals(self):
        network = Network([SourceError('HTTP 503', status_code=503)])
        client = self.client(network, (30.0,))
        with self.assertRaises(SourceError):
            client.get(ARXIV)
        self.assertEqual((network.calls, self.slept), (1, []))


class RateLimitRetriesTests(unittest.TestCase):
    def test_a_host_listed_with_no_retries_is_not_retried_on_429(self):
        client = HttpClient(config=HttpConfig())
        with patch.object(client, '_read_response', side_effect=_http_error(429)) as read, patch('time.sleep'):
            with self.assertRaises(SourceError):
                client.get(ARXIV)
        self.assertEqual(read.call_count, 1)

    def test_a_host_listed_with_more_retries_keeps_them(self):
        client = HttpClient(config=HttpConfig())
        with (
            patch.object(client, '_read_response', side_effect=_http_error(429)) as read,
            patch('paper_author_searcher.transport.http.time.sleep'),
        ):
            with self.assertRaises(SourceError):
                client.get('https://api.semanticscholar.org/graph/v1/paper/x')
        self.assertEqual(read.call_count, 9)


def _http_error(code):
    from urllib.error import HTTPError

    def raise_error(*args, **kwargs):
        raise HTTPError('https://example.org', code, 'refused', {}, None)

    return raise_error


if __name__ == '__main__':
    unittest.main()
