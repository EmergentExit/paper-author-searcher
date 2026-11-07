"""Computer-science sources: arXiv author search and OpenReview profiles (synthetic responses only)."""

import threading
import unittest
import xml.etree.ElementTree as ET

from paper_author_searcher import ProfessorQuery as Q
from paper_author_searcher.collection.cs_sources import ArxivAuthorSearch, OpenReviewProfiles
from paper_author_searcher.collection.paper_collector import PaperCollector
from paper_author_searcher.config import CSSourcesConfig
from paper_author_searcher.models import PaperSourceResult, ProfessorProfile

FEED = """<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
<opensearch:totalResults>2</opensearch:totalResults>
<entry><id>http://arxiv.org/abs/2401.00001v1</id><title>Mine</title><published>2024-01-01T00:00:00Z</published>
<author><name>Mei Lin</name></author><author><name>Jane Smith</name></author></entry>
<entry><id>http://arxiv.org/abs/2401.00002v1</id><title>Not mine</title><published>2024-01-01T00:00:00Z</published>
<author><name>Someone Else</name></author></entry></feed>"""


class FeedClient:
    def __init__(self):
        self.params = []

    def get(self, url, params=None, headers=None, xml=False):
        self.params.append(params)
        return ET.fromstring(FEED)


def profile(**values):
    data = {
        'name': 'Mei Lin',
        'affiliations': ['Example University'],
        'author_ids': {'openalex': 'A1'},
        'identity_confidence': 0.9,
        'status': 'resolved',
        'evidence': [],
        'author_id_aliases': {'openalex': ['A1']},
        'requested_name': 'Mei Lin',
    }
    data.update(values)
    return ProfessorProfile(**data)


def search(client):
    return ArxivAuthorSearch(client, 'https://export.arxiv.org/api/query', CSSourcesConfig(), current_year=2025)


class ArxivAuthorSearchTests(unittest.TestCase):
    def test_recent_preprints_naming_the_person_are_collected_under_a_name_key(self):
        client = FeedClient()
        result = search(client).collect(profile())
        self.assertEqual([record.title for record in result.papers], ['Mine'])
        record = result.papers[0]
        self.assertEqual(record.source_author_id, 'name:mei lin')
        self.assertEqual(record.author_details[0]['author_id'], 'name:mei lin')
        self.assertIn('submittedDate:[202001010000 TO 202512312359]', client.params[0]['search_query'])
        self.assertTrue(result.pagination_complete)

    def test_the_collector_adds_the_name_key_to_the_selected_identity(self):
        class NoProviders:
            name = 'openalex'

            def collect(self, author_id):
                return PaperSourceResult(
                    source='openalex', author_id=author_id, pagination_complete=True, complete=True
                )

        collected = PaperCollector([NoProviders()], name_searches=[search(FeedClient())]).collect(profile())
        self.assertEqual(collected.selected_author_ids['arxiv'], ['name:mei lin'])
        self.assertEqual([paper.title for paper in collected.papers], ['Mine'])

    def test_name_searches_run_beside_the_author_id_sources_in_order_for_a_settled_identity(self):
        searching = threading.Event()
        overlapped = []

        class WaitingProvider:
            name = 'openalex'

            def collect(self, author_id):
                overlapped.append(searching.wait(timeout=5))
                return PaperSourceResult(
                    source='openalex', author_id=author_id, pagination_complete=True, complete=True
                )

        class KeyedSearch:
            def __init__(self, name):
                self.name, self.seen = name, None

            def author_key(self, profile):
                return 'name:' + self.name

            def collect(self, profile):
                searching.set()
                self.seen = sorted(profile.author_id_aliases)
                return PaperSourceResult(source=self.name, author_id=self.author_key(profile), complete=True)

        first, second = KeyedSearch('first'), KeyedSearch('second')
        collected = PaperCollector([WaitingProvider()], name_searches=[first, second]).collect(profile())
        self.assertEqual(overlapped, [True])
        self.assertEqual([source.source for source in collected.sources], ['openalex', 'first', 'second'])
        self.assertEqual((first.seen, second.seen), (['openalex'], ['first', 'openalex']))
        self.assertEqual(collected.selected_author_ids['second'], ['name:second'])

    def test_a_contest_does_not_search_names_alongside_the_author_id_sources(self):
        order = []

        class Provider:
            name = 'openalex'

            def collect(self, author_id):
                order.append('id ' + author_id)
                return PaperSourceResult(
                    source='openalex', author_id=author_id, pagination_complete=True, complete=True
                )

        class Search:
            name = 'arxiv'

            def author_key(self, profile):
                return 'name:mei lin'

            def collect(self, profile):
                order.append('name')
                return PaperSourceResult(source='arxiv', author_id='name:mei lin', complete=True)

        contest = profile(status='ambiguous_contenders', author_id_aliases={'openalex': ['A1', 'B1']})
        PaperCollector([Provider()], name_searches=[Search()]).collect(contest)
        self.assertNotIn('name', order)


class OpenReviewTests(unittest.TestCase):
    class Client:
        def __init__(self):
            self.headers = []

        def get(self, url, params=None, headers=None, xml=False):
            self.headers.append(headers)
            if url.endswith('/profiles/search'):
                history = lambda name: [{'institution': {'name': name}}]  # noqa: E731
                return {
                    'profiles': [
                        {
                            'id': '~Mei_Lin1',
                            'content': {'names': [{'fullname': 'Mei Lin'}], 'history': history('Example University')},
                        },
                        {
                            'id': '~Mei_Lin2',
                            'content': {'names': [{'fullname': 'Mei Lin'}], 'history': history('Other College')},
                        },
                    ]
                }
            notes = [
                {
                    'content': {
                        'title': {'value': 'Verified Study'},
                        'pdf': {'value': 'https://arxiv.org/abs/2402.00003'},
                    }
                },
                {'content': {'title': {'value': 'Second Study'}}},
            ]
            return {'notes': notes}

    def test_the_profile_at_the_requested_institution_lists_its_papers(self):
        client = self.Client()
        works = OpenReviewProfiles(client, 'token').works(Q('Mei Lin', 'Example University'))
        self.assertEqual(works, {'arxiv:2402.00003', 'title:verified study', 'title:second study'})
        self.assertEqual(client.headers[0], {'Authorization': 'Bearer token'})

    def test_without_a_token_nothing_is_requested(self):
        client = self.Client()
        self.assertEqual(OpenReviewProfiles(client, None).works(Q('Mei Lin', 'Example University')), set())
        self.assertEqual(client.headers, [])


if __name__ == '__main__':
    unittest.main()
