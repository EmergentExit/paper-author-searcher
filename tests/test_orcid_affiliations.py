import unittest

from paper_author_searcher.models import CareerPosition, CuratedPosition
from paper_author_searcher.orcid_affiliations import OrcidAffiliations


class Client:
    def __init__(self, sections):
        self.sections = sections
        self.urls = []

    def get(self, url, params=None, headers=None, xml=False):
        self.urls.append(url)
        return self.sections.get(url.rsplit('/', 1)[-1], {})


def summary(key, organization, department=None, end=None):
    value = {'organization': {'name': organization}, 'department-name': department}
    if end:
        value['end-date'] = {'year': {'value': str(end)}}
    return {'summaries': [{key: value}]}


class OrcidAffiliationsTests(unittest.TestCase):
    def test_career_keeps_each_positions_years(self):
        dated = summary('employment-summary', 'Stanford University', end=2020)
        dated['summaries'][0]['employment-summary']['start-date'] = {'year': {'value': '2016'}}
        unreadable = summary('education-summary', 'Fudan University')
        unreadable['summaries'][0]['education-summary']['start-date'] = {'year': {'value': 'n/a'}}
        client = Client(
            {'employments': {'affiliation-group': [dated]}, 'educations': {'affiliation-group': [unreadable]}}
        )
        self.assertEqual(
            OrcidAffiliations(client).career('0000-0003-2217-6982'),
            [
                CareerPosition('Stanford University', 'orcid', 2016, 2020),
                CareerPosition('Fudan University', 'orcid', None, None),
            ],
        )

    def test_positions_carry_unit_and_recency(self):
        client = Client(
            {
                'employments': {
                    'affiliation-group': [
                        summary(
                            'employment-summary', 'Harvard University', 'School of Engineering and Applied Sciences'
                        ),
                        summary('employment-summary', 'Harvard Medical School', 'Aneasthesia', end=2016),
                    ]
                },
                'educations': {'affiliation-group': [summary('education-summary', 'Fudan University', end=2009)]},
            }
        )
        positions = OrcidAffiliations(client, current_year=2026).positions('0000-0003-2217-6982')
        self.assertEqual(
            positions,
            [
                CuratedPosition(
                    'orcid', ('Harvard University',), ('School of Engineering and Applied Sciences',), True
                ),
                CuratedPosition('orcid', ('Harvard Medical School',), ('Aneasthesia',), False),
                CuratedPosition('orcid', ('Fudan University',), (), False),
            ],
        )
        self.assertTrue(client.urls[0].endswith('/0000-0003-2217-6982/employments'))

    def test_an_end_year_not_yet_past_is_current(self):
        self.assertTrue(
            OrcidAffiliations(Client({}), current_year=2026).current({'end-date': {'year': {'value': '2026'}}})
        )

    def test_entries_without_an_organization_are_skipped(self):
        client = Client({'employments': {'affiliation-group': [{'summaries': [{'employment-summary': {}}]}]}})
        self.assertEqual(OrcidAffiliations(client, sections=('employments',)).positions('x'), [])


if __name__ == '__main__':
    unittest.main()
