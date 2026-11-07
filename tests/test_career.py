"""The person's career: positions from the caller and the person's ORCID record, and how ownership uses them."""

import unittest
from dataclasses import replace

from paper_author_searcher.models import AnchorEvidence, CareerPosition, ProfessorProfile
from paper_author_searcher.resolution.career import CareerTimeline, OrcidCareer, StatedCareer
from paper_author_searcher.status import AnchorKind

ORCID = '0000-0002-1825-0097'


def profile(**changes):
    base = ProfessorProfile(
        name='Mei Lin',
        affiliations=[],
        author_ids={},
        identity_confidence=1.0,
        status='resolved',
        evidence=[],
        requested_university='Harvard University',
    )
    return replace(base, **changes)


class Failing:
    name = 'broken'

    def positions(self, profile):
        raise RuntimeError('down')


class CareerPositionTests(unittest.TestCase):
    def test_years_are_inclusive_and_open_ends_stay_open(self):
        position = CareerPosition('Stanford University', 'orcid', 2016, 2020)
        self.assertTrue(position.during(2016))
        self.assertTrue(position.during(2021, tolerance=1))
        self.assertFalse(position.during(2022, tolerance=1))
        self.assertFalse(position.during(None))
        self.assertTrue(CareerPosition('Stanford University', 'orcid', 2016).during(2030))
        self.assertTrue(CareerPosition('Stanford University', 'stated').during(None))

    def test_a_checkpoint_dict_comes_back_as_a_position(self):
        position = CareerPosition('Stanford University', 'orcid', 2016, 2020)
        stored = {'institution': 'Stanford University', 'source': 'orcid', 'start_year': 2016, 'end_year': 2020}
        self.assertEqual(CareerPosition.coerce(stored), position)
        self.assertIs(CareerPosition.coerce(position), position)


class SourceTests(unittest.TestCase):
    def test_stated_schools_have_no_years(self):
        stated = StatedCareer().positions(profile(previous_universities=['Stanford University', ' ', '']))
        self.assertEqual(stated, [CareerPosition('Stanford University', 'stated')])

    def orcid(self, record, **changes):
        read = lambda orcid: record  # noqa: E731
        return OrcidCareer(read, 0.8).positions(profile(orcid=ORCID, **changes))

    def test_an_orcid_record_naming_the_school_is_read(self):
        record = [
            CareerPosition('Harvard University', 'orcid', 2021),
            CareerPosition('Stanford University', 'orcid', 2016, 2020),
        ]
        self.assertEqual(self.orcid(record), record)

    def test_an_orcid_record_that_never_names_the_school_is_someone_else_unless_anchored(self):
        record = [CareerPosition('Stanford University', 'orcid', 2016, 2020)]
        self.assertEqual(self.orcid(record), [])
        anchor = AnchorEvidence('openalex', 'A1', 'supplied ORCID', AnchorKind.ORCID)
        self.assertEqual(self.orcid(record, identity_anchors=[anchor]), record)

    def test_no_orcid_or_a_conflicting_one_reads_nothing(self):
        record = [CareerPosition('Harvard University', 'orcid', 2021)]
        self.assertEqual(OrcidCareer(lambda _: record, 0.8).positions(profile()), [])
        self.assertEqual(self.orcid(record, orcid_conflict=True), [])

    def test_the_timeline_joins_sources_once_each_and_survives_a_failure(self):
        target = profile(previous_universities=['Stanford University'])
        stated = CareerPosition('Stanford University', 'stated')
        joined = CareerTimeline([StatedCareer(), Failing(), StatedCareer()]).attach(target)
        self.assertEqual(joined.career, [stated])
        self.assertEqual(target.career, [])


class SavedResultTests(unittest.TestCase):
    def test_a_saved_collection_keeps_the_career_its_checks_used(self):
        from paper_author_searcher.collection.paper_reanalysis import saved_collection_profile

        stored = {'institution': 'Stanford University', 'source': 'stated', 'start_year': None, 'end_year': None}
        saved = {
            'profile': {'name': 'Mei Lin', 'previous_universities': ['Stanford University']},
            'collection': {'selected_author_ids': {'openalex': ['A1']}, 'career': [stored]},
        }
        restored = saved_collection_profile(saved)
        self.assertEqual(restored.career, [CareerPosition('Stanford University', 'stated')])
        self.assertEqual(restored.previous_universities, ['Stanford University'])
