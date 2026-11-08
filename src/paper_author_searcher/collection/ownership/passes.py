"""Paper-level ownership rules, run in order after every record has its first decision.

Each rule is one ``OwnershipPass``. Some verify what single records could not (the same work in two databases,
recurring verified coauthors, a rare name, a curated profile's venues); others hold back what a merged namesake
brought in (coauthor groups printing only other institutions). ``PaperOwnershipValidator`` runs them in the order
``default_passes`` gives, so a later rule sees what the earlier ones decided.
"""

import re
import typing
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from paper_author_searcher.collection.authorship_places import AuthorshipPlaces
from paper_author_searcher.collection.listed_works import RETRIEVED_EVIDENCE
from paper_author_searcher.collection.ownership_evidence import (
    CROSS_SOURCE_ORCID_BLOCK,
    CROSS_SOURCE_ORCID_CONFLICT,
    NONSTANDARD_MERGED_BLOCK,
)
from paper_author_searcher.collection.paper_authorship import canonical_author_id
from paper_author_searcher.collection.paper_deduplication import duplicate_evidence
from paper_author_searcher.config import OwnershipConfig
from paper_author_searcher.matching import institution_similarity, normalize
from paper_author_searcher.models import AnchorEvidence, Paper, PaperRecord, ProfessorProfile
from paper_author_searcher.source_traits import OPENALEX
from paper_author_searcher.status import OwnershipStatus

from .place_timeline import PlaceTimeline
from .record_facts import RecordFacts, record_fields

MISPLACED_NOTE = (
    'Cross-source agreement not counted: the byline places the person at {places}, which their verified papers '
    'from these years do not print and print only long before'
)
RARE_NAME_EVIDENCE = 'Explicit target Author ID and compatible byline under a name no other author holds in the source'
CURATED_VENUE_EVIDENCE = (
    "Curated author profile most of whose papers are proven, in a venue where the person's verified papers appear"
)
NAMESAKE_GROUP_NOTE = (
    "Held for review: no coauthor links this paper's group to the person's other papers, "
    'and OpenAlex prints its name at institutions the person never held'
)
UNPLACED_GROUP_NOTE = (
    'Held for review: the identity has no ORCID or own-page anchor, another group of papers prints the requested '
    "school on its byline, and no coauthor or journal links this paper's group to it while its bylines name only "
    'other places'
)
UNPLACED_PAPER_NOTE = (
    'Held for review: the identity has no ORCID or own-page anchor, other papers print the requested school on '
    "their bylines, and this paper's byline names only places that none of the person's other papers print"
)


@dataclass(frozen=True)
class OwnershipContext:
    """One identity's validation: the evidence queries for it, and whether its name is rare in the source."""

    facts: RecordFacts
    rare_name: bool = False

    @property
    def profile(self) -> ProfessorProfile:
        return self.facts.profile


class OwnershipPass(Protocol):
    def apply(self, papers: Sequence[Paper], context: OwnershipContext) -> None: ...


def aggregate(paper: Paper) -> None:
    """The paper's status and evidence from its records' decisions."""
    statuses = {record.ownership_status for record in paper.records}
    if statuses == {OwnershipStatus.REJECTED}:
        paper.ownership_status = OwnershipStatus.REJECTED
    elif OwnershipStatus.VERIFIED in statuses and OwnershipStatus.REJECTED not in statuses:
        paper.ownership_status = OwnershipStatus.VERIFIED
    else:
        paper.ownership_status = OwnershipStatus.NEEDS_REVIEW
    evidence = set()
    for record in paper.records:
        for message in record.ownership_evidence:
            evidence.add(f'{record.source}:{record.source_paper_id}: {message}')
    if OwnershipStatus.VERIFIED in statuses and OwnershipStatus.REJECTED in statuses:
        evidence.add('Conflicting record-level ownership decisions; review required')
    paper.ownership_evidence = sorted(evidence)


def _has_rejected_record(paper: Paper) -> bool:
    return OwnershipStatus.REJECTED in {record.ownership_status for record in paper.records}


def _hold(papers: Iterable[Paper], note: str, only: typing.Callable[[Paper], bool] | None = None) -> None:
    """Send the verified records of these papers back to review with ``note``."""
    for paper in papers:
        if only is not None and not only(paper):
            continue
        for record in paper.records:
            if record.ownership_status == OwnershipStatus.VERIFIED:
                record.ownership_status = OwnershipStatus.NEEDS_REVIEW
                record.ownership_evidence.append(note)
        aggregate(paper)


def rare_name_eligible(record: PaperRecord, facts: RecordFacts) -> bool:
    details = facts.target_details(record)
    return (
        any(author.get('author_id') for author in details)
        and facts.identity_eligible(record)
        and facts.compatible_signature(record)
        and not facts.guard.foreign(record)
        and not facts.guard.contradicted(record, details)
        and not facts.coauthor_blockers(record)
        and not facts.non_publication_reasons(record)
    )


def coauthor_groups(papers: Iterable[Paper], facts: RecordFacts, config: OwnershipConfig) -> list[list[Paper]]:
    """Verified papers joined by shared coauthors, largest group first.

    Papers with very many authors draw no links: one consortium paper would join two people.
    """
    parents: dict[Any, Any] = {}

    def root(item: Any) -> Any:
        parents.setdefault(item, item)
        while parents[item] != item:
            parents[item] = parents[parents[item]]
            item = parents[item]
        return item

    verified = [paper for paper in papers if paper.ownership_status == OwnershipStatus.VERIFIED]
    for index, paper in enumerate(verified):
        root(index)
        coauthors = facts.paper_coauthors(paper)
        if len(coauthors) <= config.namesake_group_maximum_coauthors:
            for name in coauthors:
                parents[root(name)] = root(index)
    groups: dict[Any, list[Paper]] = {}
    for index, paper in enumerate(verified):
        groups.setdefault(root(index), []).append(paper)
    return sorted(groups.values(), key=len, reverse=True)


def _doi_bylines(
    places: AuthorshipPlaces, groups: Iterable[Sequence[Paper]], facts: RecordFacts
) -> dict[str, set[str]]:
    """The places each group paper's byline prints for the person, by lower-case DOI."""
    dois = sorted({paper.doi.lower() for group in groups for paper in group if paper.doi})
    return places.on_bylines(dois, facts.names_target)


def _group_bylines(group: Sequence[Paper], on_byline: dict[str, set[str]]) -> list[set[str]]:
    return [on_byline[paper.doi.lower()] for paper in group if paper.doi and paper.doi.lower() in on_byline]


class CrossSourceAgreement:
    """The same work in two databases, each under one of the person's confirmed author IDs (or with shared
    coauthors), verifies both records; conflicting paper ORCIDs or a nonstandard classification hold them."""

    def __init__(self, config: OwnershipConfig) -> None:
        self.config = config

    def apply(self, papers: Sequence[Paper], context: OwnershipContext) -> None:
        timeline = self._timeline(papers, context.facts)
        for paper in papers:
            self._cross_validate(paper, context.facts, timeline)

    def _timeline(self, papers: Sequence[Paper], facts: RecordFacts) -> PlaceTimeline:
        """Where the records verified so far place the person; only strong evidence has verified a record yet."""
        timeline = PlaceTimeline(
            same=lambda left, right: institution_similarity(left, right) >= self.config.institution_similarity
        )
        for paper in papers:
            for record in paper.records:
                if record.ownership_status == OwnershipStatus.VERIFIED:
                    timeline.add(record.year, facts.printed_places(record))
        return timeline

    def _misplaced(self, record: PaperRecord, facts: RecordFacts, timeline: PlaceTimeline) -> bool:
        if record.ownership_status == OwnershipStatus.VERIFIED:
            return False
        places = facts.printed_places(record)
        if not timeline.contradicts(
            record.year, places, self.config.place_timeline_window_years, self.config.place_timeline_lag_years
        ):
            return False
        record.ownership_evidence.append(MISPLACED_NOTE.format(places=', '.join(sorted(places))))
        return True

    def _cross_validate(self, paper: Paper, facts: RecordFacts, timeline: PlaceTimeline) -> None:
        if any(facts.nonstandard_reasons(record) for record in paper.records):
            for record in paper.records:
                if record.ownership_status != OwnershipStatus.REJECTED:
                    record.ownership_status = OwnershipStatus.NEEDS_REVIEW
                    record.ownership_evidence.append(NONSTANDARD_MERGED_BLOCK)
            return
        eligible = []
        for record in paper.records:
            if record.ownership_status == OwnershipStatus.REJECTED or facts.verification_blockers(record):
                continue
            if facts.guard.foreign(record) or facts.guard.contradicted(record, facts.target_details(record)):
                continue
            if not facts.identity_eligible(record):
                continue
            if facts.compatible_signature(record) and not self._misplaced(record, facts, timeline):
                eligible.append(record)
        upgrades: dict[int, set[str]] = {}
        for index, left in enumerate(eligible):
            for right in eligible[index + 1 :]:
                if left.source == right.source:
                    continue
                left_orcids = facts.observed_orcids(facts.target_details(left), paper_level=True)
                right_orcids = facts.observed_orcids(facts.target_details(right), paper_level=True)
                if left_orcids and right_orcids and left_orcids != right_orcids:
                    for record in (left, right):
                        record.ownership_status = OwnershipStatus.NEEDS_REVIEW
                        record.ownership_evidence.append(CROSS_SOURCE_ORCID_BLOCK)
                    continue
                message = self._evidence(left, right, facts)
                if message:
                    upgrades.setdefault(id(left), set()).add(message)
                    upgrades.setdefault(id(right), set()).add(message)
        for record in eligible:
            conflict = any(CROSS_SOURCE_ORCID_CONFLICT in message for message in record.ownership_evidence)
            if id(record) in upgrades and not conflict:
                record.ownership_status = OwnershipStatus.VERIFIED
                record.ownership_evidence.extend(sorted(upgrades[id(record)]))

    def _evidence(self, left: PaperRecord, right: PaperRecord, facts: RecordFacts) -> str | None:
        links = duplicate_evidence(left, right)
        if not any(message.startswith('Shared stable identifier:') for message in links):
            return None
        left_details = facts.target_details(left, facts.corroborating_ids(left.source))
        right_details = facts.target_details(right, facts.corroborating_ids(right.source))
        uncorroborated = [
            record
            for record, details in ((left, left_details), (right, right_details))
            if not details and facts.target_details(record)
        ]
        if uncorroborated:
            return None
        if left_details and right_details:
            large = max(len(left.authors), len(right.authors), len(left.author_details), len(right.author_details))
            independent = (
                left.ownership_status == OwnershipStatus.VERIFIED or right.ownership_status == OwnershipStatus.VERIFIED
            )
            if large > self.config.maximum_coauthor_authors and not independent:
                return None
            # IDs from different databases are compared to their own verified namespace.
            return (
                'Cross-source stable work identity and confirmed source-scoped target Author IDs '
                'with compatible bylines'
            )
        if facts.coauthor_eligible(left) and facts.coauthor_eligible(right):
            coauthors = facts.coauthors(left) & facts.coauthors(right)
            if len(coauthors) >= self.config.minimum_shared_coauthors:
                return 'Cross-source stable work identity, compatible target signatures and shared coauthors'
        return None


class RecurringCoauthors:
    """A compatible byline with enough coauthors who recur on the person's independently verified papers.

    The coauthors are counted once, from a fixed seed set, so unverified records cannot reinforce one another. Every
    paper's status is settled from its records afterwards.
    """

    def __init__(self, config: OwnershipConfig) -> None:
        self.config = config

    def apply(self, papers: Sequence[Paper], context: OwnershipContext) -> None:
        facts = context.facts
        known_coauthors = self._trusted_coauthors(papers, facts)
        for paper in papers:
            for record in paper.records:
                if record.ownership_status != OwnershipStatus.NEEDS_REVIEW or not facts.coauthor_eligible(record):
                    continue
                overlap = facts.coauthors(record) & known_coauthors
                if len(overlap) >= self.config.minimum_known_coauthors:
                    record.ownership_status = OwnershipStatus.VERIFIED
                    record.ownership_evidence.append(
                        'Compatible signature plus recurring independently verified coauthors: '
                        + ', '.join(sorted(overlap))
                    )
            aggregate(paper)

    def _trusted_coauthors(self, papers: Iterable[Paper], facts: RecordFacts) -> set[str]:
        counts: typing.Counter[str] = Counter()
        seed_titles = set()
        listed = set(facts.profile.listed_works)
        for paper in papers:
            if _has_rejected_record(paper):
                continue
            # Seeds: verified papers, plus every paper the person's own page lists (not only the anchored ones).
            seeds = [
                record
                for record in paper.records
                if (record.ownership_status == OwnershipStatus.VERIFIED and facts.coauthor_eligible(record))
                or (listed and facts.listed(record, listed) and facts.compatible_signature(record))
            ]
            if not seeds:
                continue
            title = normalize(paper.title)
            if title in seed_titles:
                continue
            seed_titles.add(title)
            coauthors = set()
            for record in seeds:
                coauthors.update(facts.coauthors(record))
            counts.update(coauthors)
        own = self._own_paper_coauthors(papers, facts)
        return {
            name
            for name, count in counts.items()
            if count >= self.config.seed_papers_per_coauthor
            or (name in own and count >= self.config.own_paper_seed_papers_per_coauthor)
        }

    @staticmethod
    def _own_paper_coauthors(papers: Iterable[Paper], facts: RecordFacts) -> set[str]:
        """Coauthors on the person's own papers: those their list names and those their anchors came from."""
        profile = facts.profile
        own_works = set(profile.listed_works) | {
            AnchorEvidence.coerce(anchor).work for anchor in profile.identity_anchors
        } - {''}
        names: set[str] = set()
        for paper in papers:
            for record in paper.records:
                if record.work_keys() & own_works:
                    names |= facts.coauthors(record)
        return names


class RareNameFields:
    """Under a rare name, papers under the person's author ID in fields their proven papers are in.

    A rare name is not enough: a source may merge two people into one ID (MIT economist Christian Wolf's holds a
    robotics researcher's papers too), and then nobody else holds the name. The other person's papers show in
    another field. Records without a real author ID (found by a name search) never qualify.
    """

    def __init__(self, config: OwnershipConfig) -> None:
        self.config = config

    def apply(self, papers: Sequence[Paper], context: OwnershipContext) -> None:
        if not context.rare_name:
            return
        facts = context.facts
        fields = self._proven_fields(papers)
        for paper in papers:
            if _has_rejected_record(paper):
                continue
            paper_fields = {field for record in paper.records for field in record_fields(record)}
            if not paper_fields & fields:
                continue
            for record in paper.records:
                if record.ownership_status == OwnershipStatus.NEEDS_REVIEW and rare_name_eligible(record, facts):
                    record.ownership_status = OwnershipStatus.VERIFIED
                    record.ownership_evidence.append(RARE_NAME_EVIDENCE)
            aggregate(paper)

    def _proven_fields(self, papers: Iterable[Paper]) -> set[str]:
        """Fields holding a real share of the papers verified so far; too few of them prove nothing."""
        counts: typing.Counter[str] = Counter()
        for paper in papers:
            if any(record.ownership_status == OwnershipStatus.VERIFIED for record in paper.records):
                counts.update({field for record in paper.records for field in record_fields(record)})
        total = sum(counts.values())
        if total < self.config.rare_name_minimum_proven_papers:
            return set()
        return {field for field, count in counts.items() if count / total >= self.config.rare_name_minimum_field_share}


class CuratedProfileVenues:
    """Papers under a curated author ID in venues the person is proven to publish in, once most of it is proven.

    Curated sources print no institutions and new students share too few papers to count as known coauthors, so
    nothing else reaches these. Only for an ID of the form the source gives one separated person: Wei Wang's
    name-based DBLP ID holds a speech researcher's ASRU papers, which pass the venue and coauthor tests.
    """

    def __init__(self, config: OwnershipConfig) -> None:
        self.config = config

    def apply(self, papers: Sequence[Paper], context: OwnershipContext) -> None:
        facts = context.facts
        curated = [paper for paper in papers if self._curated_records(paper, facts)]
        proven = [paper for paper in curated if paper.ownership_status == OwnershipStatus.VERIFIED]
        if not curated or len(proven) / len(curated) < self.config.curated_venue_minimum_verified_share:
            return
        counts: typing.Counter[str] = Counter()
        coauthors: set[str] = set()
        for paper in papers:
            if paper.ownership_status == OwnershipStatus.VERIFIED:
                counts.update(self._paper_venues(paper))
                coauthors |= facts.paper_coauthors(paper)
        venues = {venue for venue, count in counts.items() if count >= self.config.curated_venue_minimum_papers}
        for paper in curated:
            if _has_rejected_record(paper):
                continue
            for record in self._curated_records(paper, facts):
                if (
                    record.ownership_status == OwnershipStatus.NEEDS_REVIEW
                    and self._record_venue(record) in venues
                    and facts.coauthors(record) & coauthors
                    and rare_name_eligible(record, facts)
                ):
                    record.ownership_status = OwnershipStatus.VERIFIED
                    record.ownership_evidence.append(CURATED_VENUE_EVIDENCE)
            aggregate(paper)

    def _curated_records(self, paper: Paper, facts: RecordFacts) -> list[PaperRecord]:
        return [
            record
            for record in paper.records
            if record.source in self.config.curated_orcid_sources
            and canonical_author_id(record.source, record.source_author_id) in facts.selected_ids(record.source)
            and re.fullmatch(self.config.curated_unshared_id_pattern, record.source_author_id or '')
        ]

    def _record_venue(self, record: PaperRecord) -> str:
        venue = normalize(record.venue or '')
        return '' if venue in self.config.curated_venue_excluded else venue

    def _paper_venues(self, paper: Paper) -> set[str]:
        return {venue for venue in map(self._record_venue, paper.records) if venue}


class NamesakeGroups:
    """A curated profile can merge two people: DBLP's Huan Zhang holds a music researcher's papers among hundreds of
    the verification researcher's, and those papers' coauthors then verify the arXiv copies too.

    Neither signal alone will do. A student's real paper can share no coauthor with the rest, and OpenAlex splits a
    person into IDs that know only some of their institutions. So a group of papers linked by coauthors but not to
    the person's main body is held only when the byline institutions on it all contradict the person's.
    """

    def __init__(self, config: OwnershipConfig, places: AuthorshipPlaces | None = None) -> None:
        self.config = config
        self.places = places

    def apply(self, papers: Sequence[Paper], context: OwnershipContext) -> None:
        facts = context.facts
        groups = coauthor_groups(papers, facts, self.config)
        suspects = [
            group
            for group in groups[1:]
            if len(group) >= self.config.namesake_group_minimum_papers
            and any(self._only_unproven_sources(paper) for paper in group)
        ]
        if not self.places or not suspects:
            return
        known = self.places.history(facts.selected_ids(OPENALEX))
        if not known:
            return
        on_byline = _doi_bylines(self.places, suspects, facts)
        for group in suspects:
            seen = _group_bylines(group, on_byline)
            if not seen or any(places & known for places in seen) or facts.on_career(seen):
                continue
            _hold(group, NAMESAKE_GROUP_NOTE, only=self._only_unproven_sources)

    def _only_unproven_sources(self, paper: Paper) -> bool:
        """Only a curated profile, a name or a publication list carried it. Search can find a namesake's list."""
        return all(
            record.source in self.config.namesake_unproven_sources
            or any(item.startswith(RETRIEVED_EVIDENCE) for item in record.attribution_evidence)
            for record in paper.records
        )


class UnplacedGroups:
    """An identity found by name and school alone can be a merge: DBLP and OpenAlex filed an IBM engineer, a
    driving-vision group and a Toronto student under one Guancheng Chen, and the student's school matched.

    With no ORCID or own page to say which papers are the person's, the school is the anchor. Once one coauthor
    group prints it on a byline, a group no coauthor or journal links to it, whose bylines name only other places,
    is held. A group smaller than ``namesake_group_minimum_papers`` (often one paper) is held only when none of
    those places appears on the linked groups' bylines: the driving-vision papers name South China University of
    Technology, which no Toronto paper prints.
    The largest group is no exception: here the person is the smallest. Nothing is held when no group prints the
    school, or for an anchored identity, whose earlier institutions the bylines may show.
    """

    def __init__(self, config: OwnershipConfig, places: AuthorshipPlaces | None = None) -> None:
        self.config = config
        self.places = places

    def apply(self, papers: Sequence[Paper], context: OwnershipContext) -> None:
        profile, facts = context.profile, context.facts
        if not self.places or profile.orcid or profile.identity_anchors:
            return
        schools = profile.matched_affiliations or [profile.requested_university or profile.university or '']
        schools = [school for school in schools if school]
        groups = coauthor_groups(papers, facts, self.config)
        if not schools or len(groups) <= 1:
            return
        on_byline = _doi_bylines(self.places, groups, facts)

        def at_school(place: str) -> bool:
            return any(
                institution_similarity(place, school) >= self.config.institution_similarity for school in schools
            )

        seen = [_group_bylines(group, on_byline) for group in groups]
        placed = [any(at_school(place) for places in group for place in places) for group in seen]
        if not any(placed):
            return
        # A person who moved keeps publishing in the same journals; merged namesakes in other fields do not.
        school_venues = set().union(
            *(self._group_venues(group) for group, is_placed in zip(groups, placed, strict=True) if is_placed)
        )
        # A group printing a school of the person's career (their ORCID record, a school the caller named) is theirs.
        unlinked = [
            not is_placed
            and bool(places)
            and not self._group_venues(group) & school_venues
            and not facts.on_career(places)
            for group, places, is_placed in zip(groups, seen, placed, strict=True)
        ]
        # A single paper is its own group whenever it has no coauthor, as an economist's solo papers do. A small group
        # is held only when its bylines name no place that the person's kept papers print: a student's solo paper
        # from the school where they wrote their thesis prints that school, as their coauthored papers from then do.
        # Only groups the school or a journal links to the person vouch for a place; two unlinked namesake papers
        # from one other university do not vouch for each other.
        known = [
            place
            for places, suspect in zip(seen, unlinked, strict=True)
            if not suspect
            for byline in places
            for place in byline
        ]
        for group, places, suspect in zip(groups, seen, unlinked, strict=True):
            if not suspect:
                continue
            is_large = len(group) >= self.config.namesake_group_minimum_papers
            if not is_large and self._places_known(places, known):
                continue
            _hold(group, UNPLACED_GROUP_NOTE if is_large else UNPLACED_PAPER_NOTE)

    def _places_known(self, places: Iterable[Iterable[str]], known: Sequence[str]) -> bool:
        """Whether a small group's bylines name a place that the person's linked papers print too."""
        return any(
            institution_similarity(place, other) >= self.config.institution_similarity
            for byline in places
            for place in byline
            for other in known
        )

    def _group_venues(self, group: Iterable[Paper]) -> set[str]:
        """The journals and conferences a group's papers appear in, without servers anyone can post to."""
        venues = {normalize(record.venue) for paper in group for record in paper.records if record.venue}
        return {
            venue
            for venue in venues
            if venue
            and not any(f' {open_venue} ' in f' {venue} ' for open_venue in self.config.unplaced_group_open_venues)
        }


def default_passes(config: OwnershipConfig, places: AuthorshipPlaces | None = None) -> list[OwnershipPass]:
    """The paper-level rules in the order they run."""
    return [
        CrossSourceAgreement(config),
        RecurringCoauthors(config),
        RareNameFields(config),
        CuratedProfileVenues(config),
        NamesakeGroups(config, places),
        UnplacedGroups(config, places),
    ]
