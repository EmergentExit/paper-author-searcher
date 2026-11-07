"""Score one candidate group against the query: the signals it shows, its match score, and the profile it makes."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from paper_author_searcher.config import DEFAULT_MATCHING, MatchingConfig
from paper_author_searcher.matching import (
    best_similarity,
    canonical_orcid,
    institution_similarity,
    name_similarity,
    research_similarity,
)
from paper_author_searcher.models import AnchorEvidence, AuthorCandidate, ProfessorProfile, ProfessorQuery
from paper_author_searcher.status import AnchorKind, ResolutionStatus, UniversityStatus

from .anchoring.model import SUPPLIED_ORCID_REASON, IdentityAnchors
from .identity import IdentityLink, compare_identity
from .identity_ranking import IdentityRanking


@dataclass
class ProfileSignals:
    name: float
    school: float
    current_school: float
    department: float
    research: float
    university_status: UniversityStatus
    affiliations: list[str]
    topics: list[str]
    links: list[IdentityLink]
    anchors: list[AnchorEvidence]
    orcid_conflict: bool


class ProfileScorer:
    def __init__(self, config: MatchingConfig = DEFAULT_MATCHING, ranking: IdentityRanking | None = None) -> None:
        self.config = config
        self.ranking = ranking or IdentityRanking(config)

    def score(
        self, query: ProfessorQuery, group: Sequence[AuthorCandidate], anchors: IdentityAnchors | None = None
    ) -> ProfessorProfile:
        signals = self.signals(query, group, anchors)
        match_score = self._match_score(signals)
        confidence = match_score
        anchored = bool(signals.anchors) and not signals.orcid_conflict
        # A caller-supplied anchor also covers people whose indexed affiliation is stale or missing.
        school_ok = signals.university_status == UniversityStatus.MATCHED or anchored
        if signals.name < self.config.compatible_name or not school_ok:
            confidence = min(confidence, self.config.review_cap)
        status = ResolutionStatus.NEEDS_REVIEW
        university = None
        if signals.university_status == UniversityStatus.MATCHED:
            university = query.university
        if school_ok and confidence >= self.config.resolved_confidence:
            status = ResolutionStatus.RESOLVED
        author_ids: dict[str, Any] = {}
        author_id_aliases: dict[str, Any] = {}
        matched_affiliations = []
        universities = [query.university, *query.university_aliases]
        for affiliation in signals.affiliations:
            similarity = best_similarity(
                universities,
                [affiliation],
                lambda a, b: institution_similarity(a, b, self.config),
            )
            if similarity >= self.config.matched_school:
                matched_affiliations.append(affiliation)
        if matched_affiliations:
            university = matched_affiliations[0]
        orcid = None
        for candidate in group:
            author_ids.setdefault(candidate.source, candidate.author_id)
            author_id_aliases.setdefault(candidate.source, []).append(candidate.author_id)
            if orcid is None and candidate.orcid:
                orcid = canonical_orcid(candidate.orcid)
        profile = ProfessorProfile(
            name=group[0].name,
            affiliations=signals.affiliations,
            author_ids=author_ids,
            identity_confidence=confidence,
            status=status,
            evidence=self._evidence(query, group, signals),
            orcid=orcid,
            research_interests=signals.topics,
            university=university,
            university_status=signals.university_status,
            match_score=match_score,
            requested_university=query.university,
            matched_affiliations=matched_affiliations,
            author_id_aliases=author_id_aliases,
            name_aliases=sorted({candidate.name for candidate in group}),
            requested_name=query.name,
            identity_anchors=signals.anchors,
            orcid_conflict=signals.orcid_conflict,
            requested_department=query.department,
            previous_universities=list(query.previous_universities),
            listed_works=list(anchors.listed_works) if anchors is not None else [],
            discovered=dict(anchors.discovered) if anchors is not None else {},
            listed_pdfs=list(anchors.listed_pdfs) if anchors is not None else [],
        )
        if profile.status != ResolutionStatus.RESOLVED and self.ranking.resolves(profile, signals.name):
            profile.status = ResolutionStatus.RESOLVED
            profile.evidence.append('Resolved by a precedence identity anchor (the supplied ORCID)')
        return profile

    def signals(
        self, query: ProfessorQuery, group: Sequence[AuthorCandidate], anchors: IdentityAnchors | None = None
    ) -> ProfileSignals:
        names = []
        affiliations = set()
        current_affiliations = set()
        topics = set()
        departments = []
        for candidate in group:
            names.extend([candidate.name, *candidate.aliases])
            affiliations.update(candidate.affiliations)
            affiliations.update(candidate.current_affiliations)
            current_affiliations.update(candidate.current_affiliations)
            topics.update(candidate.topics)
            departments.extend(candidate.departments)
        departments.extend(sorted(affiliations))
        universities = [query.university, *query.university_aliases]

        def school_compare(a: str, b: str) -> float:
            return institution_similarity(a, b, self.config)

        school = best_similarity(universities, sorted(affiliations), school_compare)
        department = 0.0
        if query.department:
            department = best_similarity([query.department], departments, school_compare)
        links = []
        for left, right in combinations(group, 2):
            if left.source != right.source:
                links.append(compare_identity(left, right, self.config))
        anchors = anchors or IdentityAnchors()
        anchor_evidence = []
        for record in group:
            if anchors.holds(record):
                anchor_evidence.extend(anchors.evidence_for(record.source, record.author_id))
                if anchors.orcid and record.orcid and canonical_orcid(record.orcid) == anchors.orcid:
                    anchor_evidence.append(
                        AnchorEvidence(record.source, str(record.author_id), SUPPLIED_ORCID_REASON, AnchorKind.ORCID)
                    )
        orcid_conflict = bool(
            anchors.orcid
            and any(
                record.orcid
                and canonical_orcid(record.orcid) != anchors.orcid
                and not anchors.orcid_outweighed(record.source, str(record.author_id))
                for record in group
            )
        )
        return ProfileSignals(
            name=best_similarity(
                [query.name],
                names,
                lambda a, b: name_similarity(a, b, self.config),
            ),
            school=school,
            current_school=best_similarity(
                universities,
                sorted(current_affiliations),
                school_compare,
            ),
            department=department,
            research=best_similarity(query.research_interests, sorted(topics), research_similarity),
            university_status=self._university_status(affiliations, school),
            affiliations=sorted(affiliations),
            topics=sorted(topics),
            links=links,
            anchors=sorted(set(anchor_evidence)),
            orcid_conflict=orcid_conflict,
        )

    def _university_status(self, affiliations: Iterable[str], similarity: float) -> UniversityStatus:
        if not affiliations:
            return UniversityStatus.UNKNOWN
        if similarity >= self.config.matched_school:
            return UniversityStatus.MATCHED
        return UniversityStatus.MISMATCH

    def _link_bonus(self, links: Iterable[IdentityLink]) -> float:
        verified_links = []
        for link in links:
            if link.matched:
                verified_links.append(link)
        if not verified_links:
            return 0.0
        if any(link.shared_orcid for link in verified_links):
            return self.config.orcid_bonus
        shared_dois = max(link.shared_dois for link in verified_links)
        if shared_dois >= self.config.minimum_shared_dois:
            coverage = min(shared_dois / self.config.doi_coverage_target, 1.0)
            return self.config.doi_base_bonus + self.config.doi_coverage_bonus * coverage
        if shared_dois == 0:
            return self.config.contextual_link_bonus
        # One paper supports grouping but cannot elevate a fragment's rank.
        return 0.0

    def _match_score(self, signals: ProfileSignals) -> float:
        config = self.config
        score = config.name_weight * signals.name
        if signals.university_status == UniversityStatus.MATCHED:
            score += config.school_weight * signals.school
            score += config.current_school_weight * signals.current_school
        elif signals.university_status == UniversityStatus.MISMATCH:
            score -= config.school_mismatch_penalty
        # Missing school information is neutral, not a mismatch.
        score += self._link_bonus(signals.links)
        if signals.anchors:
            score += config.anchor_bonus
        if signals.orcid_conflict:
            score -= config.orcid_conflict_penalty
        score += config.department_weight * signals.department
        score += config.research_weight * signals.research
        return round(max(0, min(score, config.confidence_cap)), config.confidence_precision)

    def _evidence(self, query: ProfessorQuery, group: Iterable[AuthorCandidate], signals: ProfileSignals) -> list[str]:
        evidence = [f'Name similarity: {signals.name:.3f}']
        if signals.university_status == UniversityStatus.UNKNOWN:
            evidence.append('University information missing (neutral); identity unverified')
        elif signals.university_status == UniversityStatus.MISMATCH:
            evidence.append('Observed universities do not match requested university')
        else:
            evidence.append(f'University matched: {signals.school:.3f}')
            if signals.current_school < self.config.matched_school:
                evidence.append('Affiliation is historical or its recency is unknown')
        link_messages = set()
        for link in signals.links:
            link_messages.update(link.evidence)
        evidence.extend(sorted(link_messages))
        evidence.extend(f'Identity anchor: {anchor}' for anchor in signals.anchors)
        if signals.orcid_conflict:
            evidence.append('ORCID conflicts with the supplied ORCID')
        if query.department:
            evidence.append(f'Department similarity: {signals.department:.3f}')
        if query.research_interests:
            evidence.append(f'Research similarity: {signals.research:.3f}')
        sources = sorted({candidate.source for candidate in group})
        evidence.append('Sources: ' + ', '.join(sources))
        return evidence
