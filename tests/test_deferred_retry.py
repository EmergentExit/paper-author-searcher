"""Throttled sources are asked again at the end of collection: once and without waiting by default, or within a
search's waiting budget when one is configured."""

import unittest
from dataclasses import replace

from paper_author_searcher.collection.deferred_retry import DeferredRetry, RetryWindow
from paper_author_searcher.collection.listed_works import ListedWorkRetriever
from paper_author_searcher.collection.paper_collector import PaperCollector
from paper_author_searcher.config import DEFAULT_COLLECTION, ListedWorksConfig
from paper_author_searcher.models import PaperRecord, PaperSourceResult
from paper_author_searcher.transport.http import SourceError
from test_cs_sources import profile

# A run that puts recall first: three tries and up to five minutes of waiting per search.
CONFIG = replace(
    DEFAULT_COLLECTION,
    deferred_retry_seconds=300.0,
    deferred_retry_default_wait_seconds=60.0,
    deferred_retry_attempts=3,
)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def deferred(wait=None):
    return SourceError('held', status_code=429, error_type='source_deferred', retry_after=wait)


class RetryWindowTests(unittest.TestCase):
    def window(self, config=CONFIG):
        self.clock = Clock()
        return RetryWindow(config, self.clock, self.clock.sleep)

    def test_by_default_it_asks_once_more_and_never_waits(self):
        window = self.window(DEFAULT_COLLECTION)
        asked = []
        result = window.again(lambda: asked.append(1) or 'failed', lambda _: ('source_deferred', 5.0))
        self.assertEqual((result, len(asked), self.clock.slept), ('failed', 1, []))

    def test_asks_at_once_then_waits_what_the_source_says_while_it_fits(self):
        answers = iter([('source_deferred', 40.0), ('source_deferred', 100.0), None])
        asked = []
        result = self.window().again(lambda: asked.append(1) or len(asked), lambda _: next(answers))
        self.assertEqual(result, 3)
        self.assertEqual(self.clock.slept, [40.0, 100.0])

    def test_a_wait_past_the_budget_is_not_taken(self):
        window = self.window()
        self.assertEqual(window.again(lambda: 'failed', lambda _: ('source_deferred', 900.0)), 'failed')
        self.assertEqual(self.clock.slept, [])

    def test_the_budget_is_shared_by_every_retry_in_the_search(self):
        window = self.window()
        first = iter([('rate_limited', None), None])
        second = iter([('rate_limited', 250.0), None])
        window.again(lambda: 'x', lambda _: next(first))  # waits the 60 s default
        window.again(lambda: 'x', lambda _: next(second))  # 60 + 250 > 300: not waited
        self.assertEqual(self.clock.slept, [60.0])

    def test_other_errors_and_successes_are_not_retried_and_attempts_are_bounded(self):
        window = self.window(replace(CONFIG, deferred_retry_attempts=2))
        calls = []
        window.again(lambda: calls.append(1), lambda _: ('not_found', None))
        window.again(lambda: calls.append(1), lambda _: ('source_deferred', 1.0))
        self.assertEqual(len(calls), 3)  # one ask, then one ask plus one retry


class ThrottledSearch:
    name = 'arxiv'

    def __init__(self, failures):
        self.failures = list(failures)
        self.profiles = []

    def author_key(self, profile):
        return 'name:mei lin'

    def collect(self, profile):
        self.profiles.append(profile)
        result = PaperSourceResult(source=self.name, author_id=self.author_key(profile))
        if self.failures:
            error = self.failures.pop(0)
            result.error, result.error_type, result.retry_after = str(error), error.error_type, error.retry_after
            return result
        result.papers.append(record('Mine', 'arxiv'))
        result.complete = result.pagination_complete = True
        return result


class NoProviders:
    name = 'openalex'

    def collect(self, author_id):
        return PaperSourceResult(source='openalex', author_id=author_id, pagination_complete=True, complete=True)


class CollectorRetryTests(unittest.TestCase):
    def collector(self, search, retry=True, config=CONFIG):
        self.clock = Clock()
        deferred_retry = DeferredRetry(config, self.clock, self.clock.sleep) if retry else None
        return PaperCollector([NoProviders()], name_searches=[search], deferred_retry=deferred_retry)

    def test_by_default_a_block_that_has_ended_is_recovered_without_any_wait(self):
        search = ThrottledSearch([deferred(30.0)])
        collected = self.collector(search, config=DEFAULT_COLLECTION).collect(profile())
        self.assertEqual([paper.title for paper in collected.papers], ['Mine'])
        self.assertEqual((len(search.profiles), self.clock.slept), (2, []))

    def test_a_throttled_name_search_is_asked_again_with_the_profile_it_searched_with(self):
        search = ThrottledSearch([deferred(30.0)])
        collected = self.collector(search).collect(profile())
        self.assertEqual([paper.title for paper in collected.papers], ['Mine'])
        arxiv = next(source for source in collected.sources if source.source == 'arxiv')
        self.assertIsNone(arxiv.error)
        self.assertEqual(search.profiles[0], search.profiles[1])

    def test_without_a_retry_or_for_other_errors_the_failure_stands(self):
        search = ThrottledSearch([deferred(30.0)])
        self.assertEqual(self.collector(search, retry=False).collect(profile()).papers, [])
        broken = ThrottledSearch([SourceError('HTTP 400', status_code=400, error_type='invalid_request')])
        self.assertEqual(self.collector(broken).collect(profile()).papers, [])
        self.assertEqual(len(broken.profiles), 1)


class ListedWorksRetryTests(unittest.TestCase):
    def test_only_the_source_that_throttled_a_work_is_asked_again_for_it(self):
        asked: dict[str, list[str]] = {'first': [], 'throttling': []}

        class First:
            name = 'first'

            def find(self, key, target):
                asked['first'].append(key)
                return

        class Throttling:
            name = 'throttling'

            def __init__(self):
                self.refused = True

            def find_many(self, keys, target):
                asked['throttling'].append(list(keys))
                if self.refused:
                    self.refused = False
                    raise deferred(20.0)
                return [replace_record(key) for key in keys]

            def find(self, key, target):
                return self.find_many([key], target)[0].unwrap()

        clock = Clock()
        retry = RetryWindow(CONFIG, clock, clock.sleep)
        works = profile(listed_works=['title:a', 'title:b'])
        result = ListedWorkRetriever([First(), Throttling()], ListedWorksConfig()).retrieve(works, [], retry)
        self.assertEqual(sorted(record.title for record in result.papers), ['a', 'b'])
        self.assertEqual(asked['first'], ['title:a', 'title:b'])  # not asked again
        self.assertEqual(asked['throttling'], [['title:a', 'title:b'], ['title:a', 'title:b']])
        self.assertEqual(result.metadata_errors, [])


def record(title, source):
    return PaperRecord(
        title=title,
        authors=['Mei Lin'],
        year=2024,
        doi=None,
        venue=None,
        abstract=None,
        source=source,
        source_paper_id=title,
        source_author_id=None,
        paper_url=None,
    )


def replace_record(key):
    from paper_author_searcher.concurrency import Outcome

    title = key.split(':', 1)[1]
    return Outcome(value=record(title, 'throttling'))


class CompositionTests(unittest.TestCase):
    def test_live_and_recording_runs_retry_and_replays_or_a_zero_budget_do_not(self):
        from paper_author_searcher.composition import RunOptions, ServiceContainer
        from paper_author_searcher.settings import Settings

        def built(attempts=1, **options):
            settings = replace(Settings(), collection=replace(DEFAULT_COLLECTION, deferred_retry_attempts=attempts))
            return ServiceContainer(settings, RunOptions(**options)).deferred_retry

        self.assertIsNotNone(built())
        self.assertIsNotNone(built(http_cache_dir='/tmp/cache', http_cache_mode='record'))
        self.assertIsNone(built(http_cache_dir='/tmp/cache', http_cache_mode='replay'))
        self.assertIsNone(built(attempts=0))


if __name__ == '__main__':
    unittest.main()
