"""An identity whose merged profiles carry different ORCIDs takes the one the evidence supports."""

import unittest

from paper_author_searcher.models import AuthorCandidate, ProfessorQuery
from paper_author_searcher.resolution.anchoring.model import IdentityAnchors
from paper_author_searcher.resolution.identity_scoring import ProfileScorer
from paper_author_searcher.resolution.orcid_choice import choose_orcid

OWN = '0000-0001-8634-7360'
STRANGER = '0009-0004-2246-548X'


def profile(author_id, orcid=None, source='openalex'):
    return AuthorCandidate(source, author_id, 'Xiang Zhou', ['Harvard University'], orcid=orcid)


def anchored(*pairs):
    anchors = IdentityAnchors()
    for author_id, count in pairs:
        for index in range(count):
            anchors.add('openalex', author_id, f'author of paper {index}')
    return anchors


class ChooseOrcidTests(unittest.TestCase):
    def test_one_orcid_or_none_is_kept_as_before(self):
        self.assertEqual(choose_orcid([profile('A1'), profile('A2', OWN)]).orcid, OWN)
        self.assertIsNone(choose_orcid([profile('A1'), profile('A2')]).orcid)
        self.assertIsNone(choose_orcid([profile('A1', OWN), profile('A2', OWN)]).evidence)

    def test_the_supplied_or_discovered_orcid_decides_a_disagreement(self):
        anchors = IdentityAnchors(orcid=OWN)
        choice = choose_orcid([profile('A0', STRANGER), profile('A1', OWN)], anchors)
        self.assertEqual(choice.orcid, OWN)
        self.assertIn('supplied or discovered', choice.evidence)

    def test_otherwise_the_orcid_of_the_most_anchored_profiles_wins_wherever_it_comes_in_the_group(self):
        anchors = anchored(('A0', 2), ('A1', 7))
        choice = choose_orcid([profile('A0', STRANGER), profile('A1', OWN)], anchors)
        self.assertEqual(choice.orcid, OWN)
        self.assertIn('7 of the anchors', choice.evidence)

    def test_a_tie_uses_no_orcid_and_says_why(self):
        choice = choose_orcid([profile('A0', STRANGER), profile('A1', OWN)], anchored(('A0', 1), ('A1', 1)))
        self.assertIsNone(choice.orcid)
        self.assertIn('different ORCIDs', choice.evidence)

    def test_the_scored_identity_carries_the_chosen_orcid_and_its_evidence(self):
        query = ProfessorQuery(name='Xiang Zhou', university='Harvard University')
        anchors = anchored(('A0', 2), ('A1', 7))
        scored = ProfileScorer().score(query, [profile('A0', STRANGER), profile('A1', OWN)], anchors)
        self.assertEqual(scored.orcid, OWN)
        self.assertTrue(any('7 of the anchors' in item for item in scored.evidence))


if __name__ == '__main__':
    unittest.main()
