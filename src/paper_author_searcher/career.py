"""The person's career: which schools they were at, and when, from records they keep or the caller vouches for.

A researcher moves: a PhD at one school, a postdoc at another, a faculty post at a third. Bylines print the school
of the time, so a paper from the PhD years names a place the search did not ask about. Ownership accepts such a
byline when the career puts the person there in that year (paper_ownership).

Only trusted records build the career, never an author database's affiliation history, which merges namesakes
(OpenAlex filed an IBM engineer, a driving-vision group and a Toronto student under one Guancheng Chen):

- ``StatedCareer``: the schools the caller names (``ProfessorQuery.previous_universities``), without years.
- ``OrcidCareer``: the employments and educations of the person's ORCID record, with years, when the record is
  theirs: an identity anchor, or a record that itself names the requested school.

With no source answering, the career is empty and every ownership rule stays as it was.
"""

import logging
from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import Protocol

from .logging_config import log_isolated_failure
from .matching import canonical_orcid, institution_similarity
from .models import AnchorEvidence, CareerPosition, ProfessorProfile
from .status import AnchorKind

logger = logging.getLogger(__name__)

STATED_SOURCE = 'stated'


class CareerSource(Protocol):
    name: str

    def positions(self, profile: ProfessorProfile) -> list[CareerPosition]: ...


class StatedCareer:
    """The schools the caller says the person was at; the caller vouches for them, so no years are needed."""

    name = STATED_SOURCE

    def positions(self, profile: ProfessorProfile) -> list[CareerPosition]:
        return [
            CareerPosition(school.strip(), STATED_SOURCE)
            for school in profile.previous_universities
            if isinstance(school, str) and school.strip()
        ]


class OrcidCareer:
    """The positions of the person's ORCID record, read only when the record is known to be theirs.

    An ORCID attached to an author profile by a database may belong to a namesake merged into that profile, so the
    record counts only when an identity anchor ties it to the person, or when the record itself names the school
    the search asked for. A profile whose records carry conflicting ORCIDs reads none.
    """

    name = 'orcid'

    def __init__(self, read: Callable[[str], list[CareerPosition]], school_similarity: float) -> None:
        self.read = read
        self.school_similarity = school_similarity

    def positions(self, profile: ProfessorProfile) -> list[CareerPosition]:
        orcid = canonical_orcid(profile.orcid)
        if not orcid or profile.orcid_conflict:
            return []
        positions = self.read(orcid)
        return positions if self._anchored(profile) or self._names_the_school(profile, positions) else []

    @staticmethod
    def _anchored(profile: ProfessorProfile) -> bool:
        anchors = (AnchorEvidence.coerce(item) for item in profile.identity_anchors)
        return any(anchor.kind == AnchorKind.ORCID for anchor in anchors)

    def _names_the_school(self, profile: ProfessorProfile, positions: Iterable[CareerPosition]) -> bool:
        schools = [school for school in (profile.requested_university, *profile.matched_affiliations) if school]
        return any(
            institution_similarity(position.institution, school) >= self.school_similarity
            for position in positions
            for school in schools
        )


class CareerTimeline:
    """Joins the sources' positions into ``ProfessorProfile.career``; a failing source is skipped, not fatal."""

    def __init__(self, sources: Iterable[CareerSource]) -> None:
        self.sources = list(sources)

    def attach(self, profile: ProfessorProfile) -> ProfessorProfile:
        found: dict[CareerPosition, None] = dict.fromkeys(CareerPosition.coerce(item) for item in profile.career)
        for source in self.sources:
            try:
                found.update(dict.fromkeys(source.positions(profile)))
            except Exception as exc:
                log_isolated_failure(logger, 'Career source %s failed: %s', source.name, exc)
        return replace(profile, career=list(found))
