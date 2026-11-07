"""The first ownership decision for each source record, from what that record alone shows.

The steps run in order and the first that decides wins: identity and metadata checks, contradictions that reject,
blockers that hold for review, then the claims that verify (paper ORCID, the person's own list, a curated profile,
a paper-level institution). Paper-level rules (``passes``) revisit these decisions with evidence across records.
"""

from paper_author_searcher.collection.ownership_evidence import (
    INSUFFICIENT_BYLINE_EVIDENCE,
    MULTIPLE_ORCIDS,
    PROFILE_ORCID_NOT_INDEPENDENT,
    TARGET_ORCID_CONFLICT,
)
from paper_author_searcher.collection.paper_authorship import canonical_author_id
from paper_author_searcher.config import OwnershipConfig
from paper_author_searcher.matching import canonical_orcid
from paper_author_searcher.models import PaperRecord
from paper_author_searcher.status import OwnershipStatus

from .record_facts import RecordFacts

FOREIGN_FRAGMENT = (
    "Common-name guard: this author ID belongs to a fragment outside the person's department profile; "
    'only paper-level evidence (a matching paper ORCID) can claim the paper'
)

Decision = tuple[OwnershipStatus, list[str]]


class RecordDecision:
    def __init__(self, config: OwnershipConfig) -> None:
        self.config = config

    def evaluate(self, record: PaperRecord, facts: RecordFacts) -> Decision:
        config, profile = self.config, facts.profile
        evidence = list(record.attribution_evidence)
        evidence.extend('Metadata warning: ' + message for message in record.metadata_errors)
        requested_id = canonical_author_id(record.source, record.source_author_id)
        if requested_id not in facts.selected_ids(record.source):
            # A work the person lists themselves is claimed on its own, under whichever author ID holds it.
            claim = facts.listed_claim(record)
            if claim:
                return OwnershipStatus.VERIFIED, [*evidence, claim]
            return OwnershipStatus.NEEDS_REVIEW, [*evidence, 'Source Author ID is not part of the selected identity']
        if not record.title or not record.source_paper_id:
            return OwnershipStatus.NEEDS_REVIEW, [*evidence, 'Insufficient publication metadata']
        details = facts.target_details(record)
        if record.target_author_present is False and not details:
            return OwnershipStatus.REJECTED, [*evidence, 'Source metadata explicitly contradicts target authorship']
        if record.author_details and not details:
            all_ids = all(author.get('author_id') for author in record.author_details)
            if all_ids and record.authors_complete is not False:
                return OwnershipStatus.REJECTED, [*evidence, 'Target ID absent from explicit authorship metadata']
            return OwnershipStatus.NEEDS_REVIEW, [
                *evidence,
                'Author IDs missing or truncated; cannot verify target authorship',
            ]
        for author in details:
            if author.get('role', 'author') != 'author':
                return OwnershipStatus.REJECTED, [
                    *evidence,
                    'Target is explicitly a contributor, editor or reviewer, not an author',
                ]
        if details:
            evidence.append('Confirmed source-scoped Author ID observed in the paper author list')
        else:
            evidence.append('Explicit target author details unavailable; using source author list')
        evidence.append('Source Author ID association alone does not verify ownership')
        best, signatures = facts.signature_score(details, record)
        evidence.append(f'Target signature similarity: {best:.3f}')
        if details and signatures and best < config.incompatible_name:
            return OwnershipStatus.REJECTED, [*evidence, 'Explicit target author signature is incompatible']
        observed_orcids = facts.observed_orcids(details)
        if len(observed_orcids) > 1:
            return OwnershipStatus.NEEDS_REVIEW, [*evidence, MULTIPLE_ORCIDS]
        if profile.orcid and observed_orcids != {canonical_orcid(profile.orcid)}:
            # An author-profile ORCID is constant across a source author ID, so it says nothing about this paper;
            # merged split profiles legitimately carry different ones. Only paper-level or unscoped ORCIDs reject.
            if facts.observed_orcids(details, paper_level=True) - {canonical_orcid(profile.orcid)}:
                return OwnershipStatus.REJECTED, [*evidence, TARGET_ORCID_CONFLICT]
            if observed_orcids:
                evidence.append('Author-profile ORCID differs from the identity ORCID; not paper-level evidence')
        not_publication = facts.non_publication_reasons(record)
        if not_publication:
            return OwnershipStatus.NEEDS_REVIEW, [
                *evidence,
                *not_publication,
                'Excluded from verified output; metadata was retained',
            ]
        nonstandard = facts.nonstandard_reasons(record)
        evidence.extend(facts.coauthor_blockers(record))
        if nonstandard:
            return OwnershipStatus.NEEDS_REVIEW, [
                *evidence,
                'Authorship role requires manual confirmation; metadata was retained',
            ]
        if not signatures or best < config.name_similarity:
            return OwnershipStatus.NEEDS_REVIEW, [*evidence, INSUFFICIENT_BYLINE_EVIDENCE]
        if profile.orcid and observed_orcids == {canonical_orcid(profile.orcid)}:
            scopes = sorted({author.get('orcid_scope') or 'unspecified' for author in details})
            paper_details = [author for author in details if author.get('orcid_scope') in config.paper_orcid_scopes]
            if facts.observed_orcids(paper_details) == {canonical_orcid(profile.orcid)}:
                return OwnershipStatus.VERIFIED, [
                    *evidence,
                    'Target authorship ORCID agrees with confirmed professor ORCID (scope: {})'.format(
                        ', '.join(scopes)
                    ),
                ]
            evidence.append(
                f'Matching author-profile or unscoped ORCID is {PROFILE_ORCID_NOT_INDEPENDENT} '
                f'(scope: {", ".join(scopes)})'
            )
        claim = facts.listed_claim(record)
        if claim:
            return OwnershipStatus.VERIFIED, [*evidence, claim]
        if facts.guard.foreign(record):
            evidence.append(FOREIGN_FRAGMENT)
        else:
            curated = facts.curated_orcid_claim(record, details)
            if curated:
                return OwnershipStatus.VERIFIED, [*evidence, curated]
        institution = facts.institution_agrees(record, details)
        if details and institution:
            return OwnershipStatus.VERIFIED, [
                *evidence,
                'Explicit target Author ID, compatible byline and paper-level institution agree: ' + institution,
            ]
        return OwnershipStatus.NEEDS_REVIEW, evidence
