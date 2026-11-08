"""Where the person's strongly verified papers place them, year by year.

Two databases can merge the same namesake into a person's profiles (OpenAlex and Semantic Scholar both filed a
Michigan biostatistician's papers under the Harvard sociologist Xiang Zhou), and then cross-source agreement is no
independent evidence. A byline can still tell: the sociologist's own papers print Michigan only until 2015 (his PhD)
and Harvard from then on, so a 2021 paper printing him at Michigan is not his.

A record contradicts the timeline when the person's strong papers from around its year (``window_years``) print none
of its places, and every one of its places was theirs only long before (more than ``lag_years``): a database keeps
printing a former school for a year or two, a namesake's stays. A place the strong papers never print says nothing,
so a move to a new school, or a byline the strong papers do not cover, is never a contradiction.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field


@dataclass
class PlaceTimeline:
    """(year, places printed) for each strongly verified record."""

    entries: list[tuple[int, frozenset[str]]] = field(default_factory=list)
    # Whether two printed institution names are the same school.
    same: Callable[[str, str], bool] = lambda left, right: left == right

    def add(self, year: int | None, places: Iterable[str]) -> None:
        printed = frozenset(places)
        if year is not None and printed:
            self.entries.append((year, printed))

    def contradicts(self, year: int | None, places: Iterable[str], window_years: int, lag_years: int) -> bool:
        places = set(places)
        if year is None or not places:
            return False
        around = [printed for seen, printed in self.entries if abs(seen - year) <= window_years]
        if not around or any(self._printed(place, around) for place in places):
            return False
        for place in places:
            years = [seen for seen, printed in self.entries if self._printed(place, [printed])]
            if not years or year - max(years) <= lag_years:
                return False
        return True

    def _printed(self, place: str, sets: Iterable[frozenset[str]]) -> bool:
        return any(self.same(place, other) for printed in sets for other in printed)
