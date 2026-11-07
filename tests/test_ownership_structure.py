"""How the ownership validator is put together: record decisions first, then paper-level passes in order."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from paper_author_searcher.collection.ownership import OwnershipContext, default_passes
from paper_author_searcher.collection.ownership.passes import (
    CrossSourceAgreement,
    CuratedProfileVenues,
    NamesakeGroups,
    RareNameFields,
    RecurringCoauthors,
    UnplacedGroups,
)
from paper_author_searcher.collection.paper_deduplication import deduplicate_papers
from paper_author_searcher.collection.paper_ownership import PaperOwnershipValidator
from paper_author_searcher.config import DEFAULT_OWNERSHIP
from paper_author_searcher.status import OwnershipStatus
from test_paper_validation import profile, record


class RecordingPass:
    def __init__(self, name, log):
        self.name, self.log = name, log

    def apply(self, papers, context):
        statuses = [paper.records[0].ownership_status for paper in papers]
        self.log.append((self.name, statuses, context.profile.name))


class OwnershipStructureTests(unittest.TestCase):
    def test_default_passes_run_in_the_documented_order(self):
        kinds = [type(rule) for rule in default_passes(DEFAULT_OWNERSHIP)]
        self.assertEqual(
            kinds,
            [
                CrossSourceAgreement,
                RecurringCoauthors,
                RareNameFields,
                CuratedProfileVenues,
                NamesakeGroups,
                UnplacedGroups,
            ],
        )

    def test_passes_see_every_record_decided_and_run_in_order(self):
        log = []
        validator = PaperOwnershipValidator(passes=[RecordingPass('first', log), RecordingPass('second', log)])
        papers = deduplicate_papers([record(orcid='profile-orcid')])
        validator.validate(papers, profile())
        self.assertEqual([name for name, _, _ in log], ['first', 'second'])
        self.assertEqual(log[0][1], [OwnershipStatus.VERIFIED])
        self.assertEqual(log[0][2], 'Alex Morgan')

    def test_one_validator_serves_several_identities_at_once(self):
        validator = PaperOwnershipValidator()
        other = replace(profile(), orcid='someone-else', name='Alex Morgan')

        def run(target):
            papers = deduplicate_papers([record(orcid='profile-orcid')])
            return validator.validate(papers, target)[0].ownership_status

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(run, [profile(), other] * 20))
        self.assertEqual(set(results[0::2]), {OwnershipStatus.VERIFIED})
        self.assertEqual(set(results[1::2]), {OwnershipStatus.REJECTED})

    def test_a_pass_can_be_applied_on_its_own(self):
        validator = PaperOwnershipValidator()
        papers = deduplicate_papers([record(), record(source='semantic_scholar')])
        facts = validator.facts(profile())
        for paper in papers:
            for item in paper.records:
                item.ownership_status, item.ownership_evidence = validator.decision.evaluate(item, facts)
        CrossSourceAgreement(DEFAULT_OWNERSHIP).apply(papers, OwnershipContext(facts))
        self.assertTrue(all(item.ownership_status == OwnershipStatus.VERIFIED for item in papers[0].records))


if __name__ == '__main__':
    unittest.main()
