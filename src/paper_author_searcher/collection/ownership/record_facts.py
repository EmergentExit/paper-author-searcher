"""What one record says about one identity: the evidence queries every ownership rule shares.

``RecordFacts`` is built once per identity (``PaperOwnershipValidator.validate``) from the profile and the precision
guard's context for it. Its queries read a record and never change it, so any rule may ask them in any order.
"""

import re
from collections.abc import Iterable, Sequence
from typing import Any

from paper_author_searcher.collection.ownership_evidence import NONSTANDARD_MERGED, NONSTANDARD_TYPE, ORCID_CONFLICT
from paper_author_searcher.collection.paper_authorship import byline_forms, canonical_author_id, signature_names
from paper_author_searcher.collection.paper_deduplication import author_signatures_match
from paper_author_searcher.collection.precision_guards import DepartmentMatcher, GuardContext, target_affiliations
from paper_author_searcher.config import OwnershipConfig
from paper_author_searcher.matching import canonical_orcid, institution_similarity, name_similarity, normalize
from paper_author_searcher.models import AnchorEvidence, CareerPosition, Paper, PaperRecord, ProfessorProfile
from paper_author_searcher.source_traits import traits_of
from paper_author_searcher.status import AnchorKind

LISTED_CLAIM = "Listed on the researcher's own publication list (homepage or supplied ORCID) with a compatible byline"

# Run-together initials ("GC") are at most this many capitals; a department name needs this many words to compare.
MAXIMUM_JOINED_INITIALS = 3
MINIMUM_DEPARTMENT_WORDS = 2
# A byline needs a family name and at least one given name.
MINIMUM_NAME_TOKENS = 2

AuthorDetail = dict[str, Any]


def abbreviated(name: str) -> bool:
    """Whether a byline gives the given names as initials only ("G Chen", "G. H. Chen", "Chen, G.")."""
    if ',' in name:
        family, _, given = name.partition(',')
        return bool(family.strip()) and _initials_only(given)
    tokens = [token for token in re.split(r'[\s.]+', name) if token]
    if len(tokens) < MINIMUM_NAME_TOKENS:
        return False
    # "G Chen" / "GC Chen" put the initials first; Medline's "Chen G" / "Chen GC" put them last.
    return (_initials_only(' '.join(tokens[:-1])) and len(tokens[-1]) > 1) or (
        _initials_only(' '.join(tokens[1:])) and len(tokens[0]) > 1
    )


def _initials_only(given: str) -> bool:
    parts = [part for part in re.split(r'[\s.\-]+', given) if part]
    # Run-together initials ("GC") are capitals with no vowel, so an upper-case name ("LI Wei") is not one.
    return bool(parts) and all(
        len(part) == 1 or (part.isupper() and len(part) <= MAXIMUM_JOINED_INITIALS and not set(part) & set('AEIOUY'))
        for part in parts
    )


def describe_position(position: CareerPosition) -> str:
    """How ownership evidence names a career position: 'career: orcid 2016-2020', 'career: stated'."""
    years = '-'.join('' if year is None else str(year) for year in (position.start_year, position.end_year))
    return f'career: {position.source}' + (f' {years}' if years != '-' else '')


def record_fields(record: PaperRecord) -> set[str]:
    """The research fields a record states: OpenAlex's topic field, or what its source only covers."""
    topic = (record.raw_metadata or {}).get('primary_topic') if isinstance(record.raw_metadata, dict) else None
    field = ((topic or {}).get('field') or {}).get('display_name')
    implied = traits_of(record.source).implied_field
    return {value for value in (field, implied) if value}


class RecordFacts:
    """Evidence queries about records, for one identity (``profile``) under one precision-guard context."""

    def __init__(
        self,
        config: OwnershipConfig,
        profile: ProfessorProfile,
        guard: GuardContext | None = None,
        departments: DepartmentMatcher | None = None,
    ) -> None:
        self.config = config
        self.profile = profile
        # Stricter evidence for common names (precision_guards); the null context keeps every ordinary rule.
        self.guard = guard if guard is not None else GuardContext()
        self.departments = departments if departments is not None else DepartmentMatcher()

    # Identity ----------------------------------------------------------------------------------------------------

    def selected_ids(self, source: str) -> set[str]:
        values = list(self.profile.author_id_aliases.get(source) or [])
        if self.profile.author_ids.get(source):
            values.append(self.profile.author_ids[source])
        return {canonical_author_id(source, value) for value in values}

    def anchored_ids(self, source: str) -> set[str]:
        anchors = (AnchorEvidence.coerce(item) for item in self.profile.identity_anchors)
        return {canonical_author_id(source, anchor.author_id) for anchor in anchors if anchor.source == source}

    def corroborating_ids(self, source: str) -> set[str]:
        """IDs whose source attribution may corroborate another database's attribution.

        With caller anchors, a split profile merged in without an anchor of its own (even the representative)
        rests on resolver similarity alone, so two databases agreeing on it is not independent evidence.
        """
        selected = self.selected_ids(source)
        if not self.profile.identity_anchors:
            return selected
        return selected & self.anchored_ids(source)

    def history_ids(self, source: str) -> set[str]:
        """Author IDs tied to the person by an anchor strong enough to vouch for their past institutions."""
        anchors = (AnchorEvidence.coerce(item) for item in self.profile.identity_anchors)
        return {
            canonical_author_id(source, anchor.author_id)
            for anchor in anchors
            if anchor.source == source and anchor.kind in self.config.history_anchor_kinds
        }

    def identity_eligible(self, record: PaperRecord) -> bool:
        requested = canonical_author_id(record.source, record.source_author_id)
        if requested not in self.selected_ids(record.source):
            return False
        for message in record.ownership_evidence:
            if ORCID_CONFLICT in message or NONSTANDARD_MERGED in message:
                return False
        return True

    def names_target(self, name: str) -> bool:
        return any(
            author_signatures_match(target, form)
            for target in self.profile.target_names()
            for form in byline_forms(name)
        )

    # Bylines -----------------------------------------------------------------------------------------------------

    def target_details(self, record: PaperRecord, expected: set[str] | None = None) -> list[AuthorDetail]:
        """The record's author entries under the identity's author IDs (or under ``expected``)."""
        if expected is None:
            expected = self.selected_ids(record.source)
        return _details_under(record, expected)

    @staticmethod
    def requested_details(record: PaperRecord) -> list[AuthorDetail]:
        """The record's author entries under the author ID it was collected for, whatever the identity holds."""
        return _details_under(record, {canonical_author_id(record.source, record.source_author_id)})

    def printed_places(self, record: PaperRecord) -> set[str]:
        """The institutions the record's byline prints for the person (not those of their author profile)."""
        return {
            institution['display_name']
            for author in self.target_details(record)
            if author.get('affiliation_scope') != 'author_profile'
            for institution in author.get('institutions') or []
            if institution.get('display_name')
        }

    def signature_score(self, details: Sequence[AuthorDetail], record: PaperRecord) -> tuple[float, list[str]]:
        signatures = []
        for author in details:
            signatures.extend(signature_names(author))
        if not details:
            for byline in record.authors:
                signatures.extend(byline_forms(byline))
        best = 0.0
        for signature in signatures:
            for name in self.profile.target_names():
                best = max(best, name_similarity(name, signature))
        return best, signatures

    def compatible_signature(self, record: PaperRecord) -> bool:
        details = self.target_details(record)
        score, signatures = self.signature_score(details, record)
        return bool(signatures) and score >= self.config.name_similarity

    def observed_orcids(self, details: Iterable[AuthorDetail], paper_level: bool = False) -> set[str | None]:
        values = set()
        for author in details:
            if paper_level and author.get('orcid_scope') in self.config.author_level_orcid_scopes:
                continue
            orcids = list(author.get('orcids') or [])
            if author.get('orcid'):
                orcids.append(author['orcid'])
            for orcid in orcids:
                values.add(canonical_orcid(orcid))
        return values

    # Blockers ----------------------------------------------------------------------------------------------------

    def nonstandard_reasons(self, record: PaperRecord) -> list[str]:
        reasons = []
        if record.paper_type in self.config.nonstandard_types:
            reasons.append(NONSTANDARD_TYPE + record.paper_type)
        title = normalize(record.title)
        for prefix in self.config.nonstandard_title_prefixes:
            if title == prefix or title.startswith(prefix + ' '):
                reasons.append('Possible nonstandard contributor list: title prefix ' + prefix)
                break
        return reasons

    def non_publication_reasons(self, record: PaperRecord) -> list[str]:
        if record.paper_type in self.config.non_publication_types:
            return ['Not a research publication: ' + record.paper_type]
        title = (record.title or '').strip().lower()
        if title.startswith(self.config.non_publication_title_prefixes):
            return ['Not a research publication: the title names a correction or a data supplement']
        return []

    def verification_blockers(self, record: PaperRecord) -> list[str]:
        return self.non_publication_reasons(record) + self.nonstandard_reasons(record)

    def coauthor_blockers(self, record: PaperRecord) -> list[str]:
        reasons = self.verification_blockers(record)
        count = max(len(record.authors), len(record.author_details))
        if count > self.config.maximum_coauthor_authors:
            reasons.append(f'Large author list ({count}); coauthor overlap cannot verify ownership')
        if record.authors_complete is False:
            reasons.append('Incomplete author list; coauthor evidence disabled')
        return reasons

    # Coauthors ---------------------------------------------------------------------------------------------------

    def coauthor_eligible(self, record: PaperRecord) -> bool:
        if not self.identity_eligible(record):
            return False
        if self.guard.foreign(record) or self.guard.contradicted(record, self.target_details(record)):
            return False
        if self.coauthor_blockers(record):
            return False
        if record.author_details and not self.target_details(record):
            return False
        return self.compatible_signature(record)

    def coauthors(self, record: PaperRecord) -> set[str]:
        names = set()
        excluded: set[str] = set()
        for detail in self.target_details(record):
            excluded.update(normalize(name) for name in signature_names(detail))
            if detail.get('name'):
                excluded.add(normalize(detail['name']))
        for detail in record.author_details:
            if detail.get('role', 'author') != 'author':
                excluded.update(normalize(name) for name in signature_names(detail))
        for author in record.authors:
            if normalize(author) in excluded or not self.guard.counts_as_coauthor(record, author):
                continue
            is_target = any(
                author_signatures_match(name, form)
                for name in self.profile.target_names()
                for form in byline_forms(author)
            )
            if not is_target:
                names.add(normalize(author))
        return names

    def paper_coauthors(self, paper: Paper) -> set[str]:
        return {name for record in paper.records for name in self.coauthors(record)}

    # Claims ------------------------------------------------------------------------------------------------------

    def listed_claim(self, record: PaperRecord) -> str | None:
        """Evidence that the person's own list (homepage, supplied ORCID) names this work with their byline."""
        profile = self.profile
        if not self.config.verify_listed_works or not record.work_keys() & set(profile.listed_works):
            return None
        if self.verification_blockers(record):
            return None
        details = self.requested_details(record)
        score, signatures = self.signature_score(details, record)
        if not signatures or score < self.config.name_similarity:
            return None
        orcids = self.observed_orcids(details, paper_level=True)
        if profile.orcid and orcids - {canonical_orcid(profile.orcid)}:
            return None
        return LISTED_CLAIM

    def listed(self, record: PaperRecord, listed: set[str]) -> bool:
        """The person's own publication page lists this work and the record's byline is theirs."""
        return (
            bool(record.work_keys() & listed) and self.identity_eligible(record) and not self.coauthor_blockers(record)
        )

    def curated_orcid_claim(self, record: PaperRecord, details: Sequence[AuthorDetail]) -> str | None:
        """The paper lists, by ID, a curated source's author profile that the person's ORCID anchors, or whose
        own affiliation record settled the identity (byline_disambiguation's curated position)."""
        if record.source not in self.config.curated_orcid_sources or not details:
            return None
        listed = {canonical_author_id(record.source, author.get('author_id')) for author in details}
        anchors = (AnchorEvidence.coerce(item) for item in self.profile.identity_anchors)
        orcid_ids = {
            canonical_author_id(record.source, anchor.author_id)
            for anchor in anchors
            if anchor.source == record.source and anchor.kind == AnchorKind.ORCID
        }
        if orcid_ids & listed:
            return f"Curated {record.source} author profile carrying the person's ORCID is on the author list"
        positioned = {
            canonical_author_id(record.source, value)
            for value in self.profile.curated_author_ids.get(record.source, [])
        }
        if positioned & listed:
            return (
                f'Curated {record.source} author profile whose own affiliation record placed the person at the '
                'requested school and department is on the author list'
            )
        return None

    # Places ------------------------------------------------------------------------------------------------------

    def institution_agrees(self, record: PaperRecord, details: Sequence[AuthorDetail]) -> str | None:
        profile = self.profile
        confirmed = profile.matched_affiliations
        if not confirmed and profile.university:
            confirmed = [profile.university]
        if not confirmed and not profile.requested_university:
            # Library callers may explicitly supply confirmed institutions without a query.
            confirmed = profile.affiliations
        credible_history = len(profile.affiliations) <= self.config.maximum_history_institutions
        history_ids = self.history_ids(record.source) if credible_history else set()
        for author in details:
            if author.get('affiliation_scope') == 'author_profile':
                continue
            # An ORCID-anchored author ID is the person throughout, so its earlier institutions count too; for any
            # other ID the affiliation union of merged fragments would admit namesakes' institutions.
            past = canonical_author_id(record.source, author.get('author_id')) in history_ids
            if self.guard.foreign(record) or not self.guard.department_agrees(record, author):
                continue
            if self.abbreviated_in_other_unit(record, author):
                continue
            for institution in author.get('institutions') or []:
                observed = institution.get('display_name')
                if not observed:
                    continue
                for expected in confirmed:
                    if institution_similarity(observed, expected) >= self.config.institution_similarity:
                        return observed
                if past and any(
                    institution_similarity(observed, expected) >= self.config.institution_similarity
                    for expected in profile.affiliations
                ):
                    return observed + ' (past affiliation of an ORCID-anchored author ID)'
                position = self.career_position(observed, record.year)
                if position is not None:
                    return f'{observed} ({describe_position(position)})'
        return None

    def career_position(self, place: str, year: int | None = None, any_year: bool = False) -> CareerPosition | None:
        """The career position (career.CareerTimeline) at ``place`` that covers ``year``, give or take
        ``career_year_tolerance``; with ``any_year`` the years are not compared."""
        for item in self.profile.career:
            position = CareerPosition.coerce(item)
            in_time = any_year or position.during(year, self.config.career_year_tolerance)
            if in_time and institution_similarity(place, position.institution) >= self.config.institution_similarity:
                return position
        return None

    def on_career(self, bylines: Iterable[Iterable[str]]) -> bool:
        """Whether any printed place of a group is a school of the person's career."""
        return any(self.career_position(place, any_year=True) is not None for places in bylines for place in places)

    def abbreviated_in_other_unit(self, record: PaperRecord, author: AuthorDetail) -> bool:
        """An initials-only byline ("G Chen") whose printed unit is not the requested department.

        A big school holds many people of one surname and initial: "G Chen, Interdepartmental Division of Critical
        Care Medicine, University of Toronto" is not the computer scientist the search asked for. A byline with the
        full given name, or one that prints no unit, is unaffected, and so is a search without a department or with
        an abbreviation ("CS") too short to compare.
        """
        requested = self.profile.requested_department or ''
        wanted = self.departments.words(requested)
        if len(wanted) < MINIMUM_DEPARTMENT_WORDS or not abbreviated(
            author.get('raw_author_name') or author.get('name') or ''
        ):
            return False
        phrases = [
            phrase
            for affiliation in target_affiliations(record, author)
            for phrase in self.departments.phrases(affiliation)
        ]
        return bool(phrases) and not any(self.departments.agree(phrase, requested) for phrase in phrases)


def _details_under(record: PaperRecord, expected: set[str]) -> list[AuthorDetail]:
    details = []
    for author in record.author_details:
        actual = canonical_author_id(record.source, author.get('author_id'))
        if actual and actual in expected:
            details.append(author)
    return details
