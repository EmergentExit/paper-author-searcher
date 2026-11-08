"""Batched arXiv lookups: fewer requests, and every answer the one-at-a-time requests would give."""

import random
import re
import unittest
import xml.etree.ElementTree as ET
from dataclasses import replace

from paper_author_searcher.collection.arxiv_api import ATOM, ArxivApi, entry_identifier, entry_title
from paper_author_searcher.collection.listed_works import (
    ArxivWorkLookup,
    AuthorMatcher,
    BatchWorkLookup,
    ListedWorkRetriever,
)
from paper_author_searcher.config import DEFAULT_PDF, DEFAULT_SOURCES, ListedWorksConfig
from paper_author_searcher.fulltext.pdf_providers import ArxivPDFProvider
from paper_author_searcher.matching import normalize
from paper_author_searcher.models import ProfessorProfile
from paper_author_searcher.transport.http import SourceError

URL = 'https://export.arxiv.org/api/query'
PHRASE = re.compile(r'ti:"([^"]*)"')


class Paper:
    def __init__(self, identifier, title, authors=('Ada Lovelace',)):
        self.identifier, self.title, self.authors = identifier, title, authors


class FakeArxiv:
    """id_list answers each known identifier (latest version unless one is asked); a title query answers every
    paper whose title contains any of its phrases, in a fixed relevance order, cut at max_results (default 10)."""

    def __init__(self, papers, fail_on=()):
        self.papers = list(papers)
        self.fail_on = set(fail_on)
        self.requests = []

    def get(self, url, params=None, headers=None, xml=False):
        self.requests.append(dict(params))
        limit = int(params.get('max_results', 10))
        if 'id_list' in params:
            ids = params['id_list'].split(',')
            if self.fail_on & set(ids):
                raise SourceError('HTTP 400 from export.arxiv.org', status_code=400)
            found = [self._served(paper, wanted) for wanted in ids for paper in self.papers if self._is(paper, wanted)]
            return feed(found[:limit])
        phrases = [' '.join(phrase.lower().split()) for phrase in PHRASE.findall(params['search_query'])]
        found = [
            (paper, paper.identifier + 'v2')
            for paper in self.papers
            if any(phrase in ' '.join(paper.title.lower().split()) for phrase in phrases)
        ]
        return feed(found[:limit])

    @staticmethod
    def _is(paper, wanted):
        return paper.identifier == re.sub(r'v\d+$', '', wanted)

    @staticmethod
    def _served(paper, wanted):
        return paper, wanted if re.search(r'v\d+$', wanted) else paper.identifier + 'v2'


def feed(found):
    root = ET.Element(ATOM + 'feed')
    for paper, served in found:
        entry = ET.SubElement(root, ATOM + 'entry')
        ET.SubElement(entry, ATOM + 'id').text = f'http://arxiv.org/abs/{served}'
        ET.SubElement(entry, ATOM + 'title').text = paper.title
        ET.SubElement(entry, ATOM + 'published').text = '2024-01-01T00:00:00Z'
        for name in paper.authors:
            ET.SubElement(ET.SubElement(entry, ATOM + 'author'), ATOM + 'name').text = name
        ET.SubElement(entry, ATOM + 'link', {'type': 'application/pdf', 'href': f'https://arxiv.org/pdf/{served}'})
    return root


def corpus(count, seed=7):
    words = ['graph', 'neural', 'learning', 'robust', 'proof', 'language', 'model', 'sparse', 'vision', 'causal']
    rng = random.Random(seed)
    papers = [
        Paper(f'24{index // 100:02d}.{index % 100:05d}', ' '.join(rng.choice(words) for _ in range(rng.randint(2, 6))))
        for index in range(count)
    ]
    return papers


def same(found, wanted):
    return normalize(found) == normalize(wanted)


def one_by_one(papers, titles, per_title):
    """What asking each title alone (the old way) finds: its exact-title entries among the first per_title."""
    alone = ArxivApi(FakeArxiv(papers), URL)
    return {
        title: [entry_identifier(entry) for entry in alone.by_titles([title], per_title, same)[title].unwrap()]
        for title in titles
    }


class IdentifierBatchTests(unittest.TestCase):
    def test_ids_are_asked_in_batches_and_each_gets_its_own_entries(self):
        papers = corpus(250)
        fake = FakeArxiv(papers)
        found = ArxivApi(fake, URL).by_ids([paper.identifier for paper in papers])
        self.assertEqual(len(fake.requests), 3)  # 100 per request
        for paper in papers:
            self.assertEqual([entry_identifier(e) for e in found[paper.identifier].unwrap()], [paper.identifier + 'v2'])

    def test_a_versioned_request_gets_only_that_version(self):
        papers = corpus(3)
        found = ArxivApi(FakeArxiv(papers), URL).by_ids([papers[0].identifier + 'v1', papers[1].identifier])
        self.assertEqual(
            [entry_identifier(e) for e in found[papers[0].identifier + 'v1'].unwrap()], [papers[0].identifier + 'v1']
        )

    def test_a_failing_batch_is_asked_one_by_one_so_only_the_bad_id_fails(self):
        papers = corpus(5)
        fake = FakeArxiv(papers, fail_on={'bad'})
        found = ArxivApi(fake, URL).by_ids([papers[0].identifier, 'bad', papers[1].identifier])
        self.assertIsNotNone(found['bad'].error)
        self.assertEqual(len(found[papers[0].identifier].unwrap()), 1)
        self.assertEqual(len(fake.requests), 4)  # the batch, then each identifier alone

    def test_an_id_no_entry_answers_is_asked_alone(self):
        papers = corpus(3)
        fake = FakeArxiv(papers)
        found = ArxivApi(fake, URL).by_ids([papers[0].identifier, '9999.99999'])
        self.assertEqual(found['9999.99999'].unwrap(), [])
        self.assertEqual(fake.requests[-1], {'id_list': '9999.99999'})

    def test_a_single_lookup_is_the_request_it_always_was(self):
        fake = FakeArxiv(corpus(1))
        ArxivApi(fake, URL).by_ids(['2400.00000'])
        self.assertEqual(fake.requests, [{'id_list': '2400.00000'}])


class TitleBatchTests(unittest.TestCase):
    def test_titles_are_asked_in_batches_and_find_what_one_by_one_finds(self):
        papers = corpus(300)
        titles = [paper.title for paper in papers[:45]] + ['associate professor with indefinite tenure']
        fake = FakeArxiv(papers)
        found = ArxivApi(fake, URL).by_titles(titles, 3, same)
        self.assertLess(len(fake.requests), len(titles))
        expected = one_by_one(papers, titles, 3)
        for title in titles:
            batched = [entry_identifier(entry) for entry in found[title].unwrap()]
            # Everything a title's own request found; a full answer can hold more same-title papers it cut off.
            self.assertTrue(set(expected[title]) <= set(batched), title)
            self.assertTrue(all(same(entry_title(entry), title) for entry in found[title].unwrap()))
        self.assertEqual(found['associate professor with indefinite tenure'].unwrap(), [])

    def test_a_possibly_truncated_batch_is_split_until_answered_whole(self):
        papers = corpus(300)
        titles = [paper.title for paper in papers[:20]]
        config = replace(DEFAULT_SOURCES, arxiv_title_batch_results=15)
        fake = FakeArxiv(papers)
        found = ArxivApi(fake, URL, config).by_titles(titles, 3, same)
        expected = one_by_one(papers, titles, 3)
        for title in titles:
            self.assertTrue(set(expected[title]) <= {entry_identifier(e) for e in found[title].unwrap()}, title)
        self.assertTrue(any(len(PHRASE.findall(request['search_query'])) < 20 for request in fake.requests))

    def test_a_single_title_is_the_request_it_always_was(self):
        fake = FakeArxiv(corpus(3))
        ArxivApi(fake, URL).by_titles(['A "quoted"   Title'], 3, same)
        self.assertEqual(fake.requests, [{'search_query': 'ti:"A quoted Title"', 'max_results': 3}])


class ListedWorksBatchTests(unittest.TestCase):
    def profile(self, keys):
        return ProfessorProfile(
            name='Ada Lovelace',
            affiliations=[],
            author_ids={},
            identity_confidence=1.0,
            status='resolved',
            evidence=[],
            listed_works=keys,
        )

    def test_the_arxiv_stage_asks_once_for_every_work_still_missing_and_matches_one_by_one(self):
        papers = corpus(60)
        keys = [f'title:{normalize(paper.title)}' for paper in papers[:25]] + ['title:huan zhang google scholar']
        keys += [f'arxiv:{papers[30].identifier}', 'doi:10.1/x']
        batched_fake = FakeArxiv(papers)
        lookup = ArxivWorkLookup(ArxivApi(batched_fake, URL), ListedWorksConfig(), AuthorMatcher())
        self.assertIsInstance(lookup, BatchWorkLookup)
        result = ListedWorkRetriever([lookup], ListedWorksConfig()).retrieve(self.profile(keys), [])
        single = ArxivWorkLookup(ArxivApi(FakeArxiv(papers), URL), ListedWorksConfig(), AuthorMatcher())
        expected = [single.find(key, self.profile(keys)) for key in sorted(keys)]
        self.assertEqual(
            [record.source_paper_id for record in result.papers],
            [record.source_paper_id for record in expected if record is not None],
        )
        self.assertLessEqual(len(batched_fake.requests), 3)

    def test_a_batch_that_raises_fails_each_of_its_works(self):
        class Broken:
            name = 'broken'

            def find(self, key, profile):
                raise AssertionError('asked one by one')

            def find_many(self, keys, profile):
                raise SourceError('HTTP 503', status_code=503)

        result = ListedWorkRetriever([Broken()], ListedWorksConfig()).retrieve(self.profile(['title:a', 'title:b']), [])
        self.assertEqual([error['paper_id'] for error in result.metadata_errors], ['title:a', 'title:b'])


class PdfProviderBatchTests(unittest.TestCase):
    def papers(self, papers):
        return [
            {
                'title': paper.title,
                'authors': list(paper.authors),
                'records': [{'external_ids': {'arxiv': paper.identifier}}],
            }
            for paper in papers
        ]

    def test_prepare_batches_the_lookups_and_find_answers_as_before(self):
        corpus_papers = corpus(150)
        saved = self.papers(corpus_papers)
        fake = FakeArxiv(corpus_papers)
        provider = ArxivPDFProvider(fake, DEFAULT_PDF)
        provider.prepare(saved)
        prepared = [provider.find(paper) for paper in saved]
        self.assertEqual(len(fake.requests), 2)  # 150 identifiers, 100 per request; find asks nothing more
        alone = ArxivPDFProvider(FakeArxiv(corpus_papers), DEFAULT_PDF)
        expected = [alone.find(paper) for paper in saved]
        self.assertEqual(
            [[c.url for c in result.candidates] for result in prepared],
            [[c.url for c in result.candidates] for result in expected],
        )
        self.assertEqual([r.status for r in prepared], [r.status for r in expected])


if __name__ == '__main__':
    unittest.main()
