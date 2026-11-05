"""Past a search's time limit every request is refused, and nothing about that is cached for later searches."""

import tempfile
import unittest
from dataclasses import replace
from unittest.mock import Mock

from paper_author_searcher.composition import RunOptions, ServiceContainer
from paper_author_searcher.config import DEFAULT_PDF
from paper_author_searcher.http import SourceError
from paper_author_searcher.http_cache import CachingHttpClient
from paper_author_searcher.http_deadline import DeadlineHttpClient, SearchDeadline, TimeLimitReached
from paper_author_searcher.models import PDFProviderResult
from paper_author_searcher.pdf_resolver import PDFResolver
from paper_author_searcher.settings import Settings
from paper_author_searcher.status import OwnershipStatus, ProviderStatus

URL = 'https://api.example.org/works'


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Network:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, headers=None, xml=False):
        self.calls.append('get')
        return {'results': [1]}

    def post_json(self, url, payload, params=None, headers=None, expected_type=dict):
        self.calls.append('post_json')
        return {}

    def probe(self, url, maximum_bytes=1024, html_bytes=0, maximum_redirects=5, redirect_guard=None):
        self.calls.append('probe')
        return {}

    def stream_pdf(self, url, destination, config):
        self.calls.append('stream_pdf')
        return {}


class CountingProvider:
    name = 'counting'

    def __init__(self, on_find=None):
        self.asked = 0
        self.on_find = on_find

    def find(self, paper):
        self.asked += 1
        if self.on_find:
            self.on_find()
        return PDFProviderResult(self.name, status=ProviderStatus.ERROR, errors=['refused'])


class ResolverDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.deadline = SearchDeadline(10, self.clock)
        self.sleeps = []
        self.papers = [{'title': f'Paper {index}', 'ownership_status': OwnershipStatus.VERIFIED} for index in range(5)]

    def resolve(self, provider):
        config = replace(DEFAULT_PDF, lookup_workers=1)
        resolver = PDFResolver([provider], Mock(), config=config, sleep=self.sleeps.append, deadline=self.deadline)
        return resolver.resolve({'papers': self.papers})

    def test_past_the_limit_no_paper_is_looked_up_and_no_retry_waits(self):
        self.clock.now = 10
        provider = CountingProvider()
        resolved = self.resolve(provider)
        self.assertEqual(provider.asked, 0)
        self.assertEqual(self.sleeps, [])
        self.assertEqual(len(resolved.papers), 5)
        self.assertTrue(self.deadline.reached)

    def test_the_limit_passing_midway_leaves_the_rest_unasked(self):
        provider = CountingProvider(lambda: setattr(self.clock, 'now', self.clock.now + 4))
        self.resolve(provider)
        self.assertEqual(provider.asked, 3)
        self.assertEqual(self.sleeps, [])

    def test_within_the_limit_every_paper_is_looked_up(self):
        provider = CountingProvider()
        self.resolve(provider)
        self.assertEqual(provider.asked, 5)
        self.assertFalse(self.deadline.reached)


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.deadline = SearchDeadline(10, self.clock)
        self.network = Network()
        self.client = DeadlineHttpClient(self.network, self.deadline)

    def operations(self):
        return (
            lambda: self.client.get(URL),
            lambda: self.client.post_json(URL, {}),
            lambda: self.client.probe(URL),
            lambda: self.client.stream_pdf(URL, 'file', None),
        )

    def test_before_the_limit_every_operation_goes_through(self):
        for operation in self.operations():
            operation()
        self.assertEqual(self.network.calls, ['get', 'post_json', 'probe', 'stream_pdf'])
        self.assertFalse(self.deadline.reached)

    def test_past_the_limit_every_operation_is_refused_unsent(self):
        self.clock.now = 10
        for operation in self.operations():
            with self.assertRaises(TimeLimitReached):
                operation()
        self.assertEqual(self.network.calls, [])
        self.assertTrue(self.deadline.reached)

    def test_refusal_is_a_source_error_that_is_not_retried_as_transient(self):
        self.clock.now = 11
        with self.assertRaises(SourceError) as caught:
            self.client.get(URL)
        self.assertEqual(caught.exception.error_type, 'time_limit')

    def test_a_limit_that_refused_nothing_is_not_reported(self):
        self.clock.now = 50
        self.assertFalse(self.deadline.reached)

    def test_asking_past_the_limit_counts_as_reaching_it(self):
        self.assertFalse(self.deadline.expired())
        self.clock.now = 10
        self.assertTrue(self.deadline.expired())
        self.assertTrue(self.deadline.reached)

    def test_limit_must_be_positive(self):
        with self.assertRaises(ValueError):
            SearchDeadline(0)

    def test_a_refusal_never_reaches_the_cache_later_searches_share(self):
        with tempfile.TemporaryDirectory() as directory:
            cached = CachingHttpClient(self.network, directory, 'record')
            expired = DeadlineHttpClient(cached, self.deadline)
            self.clock.now = 10
            with self.assertRaises(TimeLimitReached):
                expired.get(URL)
            self.assertEqual(cached.get(URL), {'results': [1]})
            self.assertEqual(self.network.calls, ['get'])

    def test_the_container_puts_the_limit_on_the_whole_chain(self):
        # Under the timing layer, which only measures: the limit covers everything below it.
        client = ServiceContainer(Settings(), RunOptions(deadline=self.deadline)).http_client.client
        self.assertIsInstance(client, DeadlineHttpClient)
        self.assertNotIsInstance(ServiceContainer(Settings()).http_client.client, DeadlineHttpClient)


if __name__ == '__main__':
    unittest.main()
