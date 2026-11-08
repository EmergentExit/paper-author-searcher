"""Cross-source agreement does not verify a record whose byline contradicts where the person's own papers place them."""

import unittest

from paper_author_searcher.collection.ownership.place_timeline import PlaceTimeline


def timeline():
    """A sociologist: a PhD at Michigan until 2015, Harvard since."""
    places = PlaceTimeline()
    for year in (2012, 2014, 2015):
        places.add(year, {'University of Michigan'})
    for year in range(2016, 2026):
        places.add(year, {'Harvard University'})
    return places


class PlaceTimelineTests(unittest.TestCase):
    def test_a_school_left_long_before_contradicts_a_later_byline(self):
        self.assertTrue(timeline().contradicts(2021, {'University of Michigan'}, window_years=1, lag_years=3))

    def test_a_former_school_shortly_after_a_move_does_not(self):
        self.assertFalse(timeline().contradicts(2017, {'University of Michigan'}, window_years=1, lag_years=3))

    def test_a_school_the_verified_papers_never_print_says_nothing(self):
        self.assertFalse(timeline().contradicts(2021, {'Princeton University'}, window_years=1, lag_years=3))

    def test_the_current_school_or_one_shared_place_never_contradicts(self):
        self.assertFalse(timeline().contradicts(2021, {'Harvard University'}, window_years=1, lag_years=3))
        both = {'University of Michigan', 'Harvard University'}
        self.assertFalse(timeline().contradicts(2021, both, window_years=1, lag_years=3))

    def test_no_year_no_places_or_no_verified_papers_nearby_never_contradict(self):
        self.assertFalse(timeline().contradicts(None, {'University of Michigan'}, window_years=1, lag_years=3))
        self.assertFalse(timeline().contradicts(2021, set(), window_years=1, lag_years=3))
        self.assertFalse(timeline().contradicts(2030, {'University of Michigan'}, window_years=1, lag_years=3))

    def test_names_are_compared_with_the_given_similarity(self):
        places = PlaceTimeline(same=lambda left, right: left.lower() == right.lower())
        places.add(2012, {'University of Michigan'})
        places.add(2021, {'Harvard University'})
        self.assertTrue(places.contradicts(2021, {'UNIVERSITY OF MICHIGAN'}, window_years=1, lag_years=3))


if __name__ == '__main__':
    unittest.main()
