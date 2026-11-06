import threading
import unittest
from functools import partial

from paper_author_searcher.concurrency import Outcome, attempt, in_order
from paper_author_searcher.http import SourceError
from paper_author_searcher.timings import PhaseTimings, phase, recording


class InOrderTests(unittest.TestCase):
    def test_answers_come_back_in_the_calls_order_while_the_calls_overlap(self):
        started = threading.Barrier(3, timeout=5)

        def call(value):
            started.wait()  # all three must be running at once, or this times out
            return value

        self.assertEqual(in_order([partial(call, value) for value in 'abc'], workers=3), ['a', 'b', 'c'])

    def test_one_worker_runs_the_calls_one_by_one_on_this_thread(self):
        threads = in_order([threading.get_ident, threading.get_ident], workers=1)
        self.assertEqual(threads, [threading.get_ident()] * 2)
        self.assertEqual(in_order([], workers=4), [])

    def test_workers_record_phases_in_the_callers_run(self):
        timings = PhaseTimings()

        def timed():
            with phase('step'):
                return 1

        with recording(timings):
            in_order([timed, timed], workers=2)
        self.assertEqual(timings.to_dict()['step']['count'], 2)

    def test_an_unexpected_error_propagates(self):
        def boom():
            raise KeyError('x')

        with self.assertRaises(KeyError):
            in_order([lambda: 1, boom], workers=2)


class AttemptTests(unittest.TestCase):
    def test_an_expected_error_is_kept_and_raised_on_unwrap(self):
        error = SourceError('HTTP 404', status_code=404)

        def fail():
            raise error

        outcome = attempt(fail, (SourceError,))
        self.assertIs(outcome.error, error)
        with self.assertRaises(SourceError):
            outcome.unwrap()
        self.assertEqual(attempt(lambda: 5, (SourceError,)), Outcome(value=5))

    def test_any_other_error_propagates(self):
        def fail():
            raise ValueError('bad')

        with self.assertRaises(ValueError):
            attempt(fail, (SourceError,))


if __name__ == '__main__':
    unittest.main()
