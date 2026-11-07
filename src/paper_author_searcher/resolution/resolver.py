"""Resolve a query to one person: collect candidates, group them, score the groups and settle close contests.

Grouping is ``identity_grouping``, scoring is ``identity_scoring``; this module runs the steps in order and owns the
parts that talk to sources (candidate search, anchor finding, anchored profile lookups) and the contest settlers
(byline disambiguation, LLM adjudication).
"""

import logging
from collections.abc import Iterable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from contextvars import copy_context
from functools import partial
from typing import Any

from paper_author_searcher.concurrency import Outcome, attempt, in_order
from paper_author_searcher.config import DEFAULT_ANCHORS, DEFAULT_MATCHING, MatchingConfig
from paper_author_searcher.logging_config import log_isolated_failure
from paper_author_searcher.models import AuthorCandidate, ProfessorProfile, ProfessorQuery, ResolutionResult
from paper_author_searcher.status import ResolutionStatus, UniversityStatus
from paper_author_searcher.timings import phase

from .anchoring.model import AnchorFinder, IdentityAnchors
from .byline_disambiguation import BylineDisambiguator
from .identity import compare_identity
from .identity_adjudication import IdentityAdjudicator, accepted
from .identity_grouping import (
    MINIMUM_CONTEST_SIZE,
    CandidateGroup,
    group_candidates,
    merge_anchored,
    separate_orcid_conflicts,
)
from .identity_ranking import IdentityRanking
from .identity_scoring import ProfileScorer, ProfileSignals
from .providers import AuthorProvider, ProfileLookup

logger = logging.getLogger(__name__)

__all__ = ['MINIMUM_CONTEST_SIZE', 'ProfessorResolver', 'ProfileSignals']

Scored = list[tuple[ProfessorProfile, CandidateGroup]]


class ProfessorResolver:
    def __init__(
        self,
        providers: Iterable[AuthorProvider],
        config: MatchingConfig = DEFAULT_MATCHING,
        anchor_finder: AnchorFinder | None = None,
        adjudicator: IdentityAdjudicator | None = None,
        ranking: IdentityRanking | None = None,
        disambiguator: BylineDisambiguator | None = None,
        lookup_workers: int = DEFAULT_ANCHORS.lookup_workers,
    ) -> None:
        self.config = config
        # Sources whose anchored profiles (the name search missed them) are looked up at once.
        self.lookup_workers = lookup_workers
        self.providers = list(providers)
        # Orders candidates and decides contests; see identity_ranking.IdentityRanking.
        self.ranking = ranking or IdentityRanking(config)
        self.scorer = ProfileScorer(config, self.ranking)
        # Turns query hints (ORCID, homepage, known papers) into author-ID anchors; without one, hints are unused.
        self.anchor_finder = anchor_finder
        # Off unless injected; see identity_adjudication.ClaudeIdentityAdjudicator.
        self.adjudicator = adjudicator
        # Off unless injected; see byline_disambiguation.BylineDisambiguator.
        self.disambiguator = disambiguator

    def resolve(self, query: ProfessorQuery) -> ResolutionResult:
        profiles, groups, errors = self.rank(query)
        if not profiles:
            return ResolutionResult(profile=None, candidates=[], source_errors=errors)
        ambiguity = self._mark_ambiguity(profiles, groups)
        return ResolutionResult(
            profile=profiles[0],
            candidates=profiles,
            source_errors=errors,
            ambiguity=ambiguity,
        )

    def rank(self, query: ProfessorQuery) -> tuple[list[ProfessorProfile], list[CandidateGroup], dict[str, str]]:
        """Scored identity profiles, the candidate groups behind them, and per-source errors."""
        records, errors, anchors = self._search_and_find_anchors(query)
        if anchors.errors:
            errors['anchors'] = '; '.join(anchors.errors)
        with phase('identity: anchored profile lookups'):
            records.extend(self._anchored_records(anchors, records, errors))
        with phase('identity: grouping and scoring'):
            groups = separate_orcid_conflicts(merge_anchored(group_candidates(records, self.config), anchors), anchors)
            scored = sorted(
                ((self.scorer.score(query, group, anchors), group) for group in groups),
                key=lambda item: self.ranking.key(item[0]),
            )
        with phase('identity: disambiguation'):
            scored = self._disambiguate(query, scored)
        with phase('identity: adjudication'):
            scored = self._adjudicate(query, scored, anchors)
        return [profile for profile, _ in scored], [group for _, group in scored], errors

    # Sources -----------------------------------------------------------------------------------------------------

    def _search_and_find_anchors(
        self, query: ProfessorQuery
    ) -> tuple[list[AuthorCandidate], dict[str, str], IdentityAnchors]:
        """Candidate search and anchor finding, side by side: neither reads the other's results, so running them
        together changes nothing but the wait."""
        # The finder also discovers hints the caller did not give (identity_discovery), so it runs for every query.
        if self.anchor_finder is None:
            with phase('identity: candidate search'):
                records, errors = self._collect_candidates(query)
            return records, errors, IdentityAnchors()
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix='identity-anchors') as pool:
            # The worker runs in a copy of this context, so its phases are timed in this run.
            anchoring = pool.submit(copy_context().run, self._find_anchors, self.anchor_finder, query)
            with phase('identity: candidate search'):
                records, errors = self._collect_candidates(query)
            anchors = anchoring.result()
        return records, errors, anchors

    @staticmethod
    def _find_anchors(finder: AnchorFinder, query: ProfessorQuery) -> IdentityAnchors:
        with phase('identity: anchors'):
            return finder.find(query)

    def _collect_candidates(self, query: ProfessorQuery) -> tuple[list[AuthorCandidate], dict[str, str]]:
        records = []
        errors = {}
        with ThreadPoolExecutor(max_workers=max(1, len(self.providers))) as pool:
            futures: dict[Future[list[AuthorCandidate]], str] = {}
            for provider in self.providers:
                future = pool.submit(provider.search, query)
                futures[future] = provider.name
            for future in as_completed(futures):
                source = futures[future]
                try:
                    candidates = future.result()
                    records.extend(candidates)
                    warnings = set()
                    for candidate in candidates:
                        warnings.update(candidate.metadata_errors)
                    if warnings:
                        errors[source] = '; '.join(sorted(warnings))
                except Exception as exc:
                    log_isolated_failure(logger, 'Identity source %s failed: %s', source, exc)
                    # Isolate source failures so available evidence is preserved.
                    errors[source] = str(exc)
        records.sort(key=lambda candidate: (candidate.source, candidate.author_id))
        return records, errors

    def _anchored_records(
        self, anchors: IdentityAnchors, records: Iterable[AuthorCandidate], errors: dict[str, str]
    ) -> list[AuthorCandidate]:
        """Fetch anchored profiles that the name search did not return."""
        present = {(record.source, record.author_id) for record in records}
        batches: list[tuple[ProfileLookup, list[str]]] = []
        for provider in self.providers:
            if not isinstance(provider, ProfileLookup):
                continue
            wanted = [
                author_id
                for author_id in sorted(anchors.author_ids.get(provider.name, ()))
                if (provider.name, author_id) not in present
            ]
            if wanted:
                batches.append((provider, wanted))
        # Sources are asked side by side, but each source's profiles one after another, as before: a rate-limited
        # source (Semantic Scholar) answers parallel profile requests with 429s, and a lost profile loses its ID.
        outcomes = in_order(
            [partial(self._look_up_each, source, wanted) for source, wanted in batches],
            self.lookup_workers,
            'anchored-profiles',
        )
        added = []
        for (source, wanted), results in zip(batches, outcomes, strict=True):
            for author_id, outcome in zip(wanted, results, strict=True):
                if outcome.error is None:
                    added.append(outcome.unwrap())
                else:
                    log_isolated_failure(
                        logger, 'Anchor lookup %s:%s failed: %s', source.name, author_id, outcome.error
                    )
                    errors.setdefault(source.name, str(outcome.error))
        return added

    @staticmethod
    def _look_up_each(source: ProfileLookup, author_ids: Sequence[str]) -> list[Outcome[AuthorCandidate]]:
        return [attempt(partial(source.lookup, author_id), (Exception,)) for author_id in author_ids]

    # Contests ----------------------------------------------------------------------------------------------------

    def _disambiguate(self, query: ProfessorQuery, scored: Scored) -> Scored:
        """Let the contenders' own bylines settle a close contest the scores leave open."""
        if (
            self.disambiguator is None
            or len(scored) < MINIMUM_CONTEST_SIZE
            or self.ranking.uncontested(scored[0][0], scored[1][0])
        ):
            return scored
        verdict = self.disambiguator.choose(query, scored)
        if verdict is None:
            return scored
        profile, group = scored[verdict.index]
        profile.disambiguation = verdict.note
        profile.curated_author_ids = verdict.curated_author_ids
        profile.evidence.append(verdict.note)
        return [(profile, group), *(item for index, item in enumerate(scored) if index != verdict.index)]

    def _adjudicate(self, query: ProfessorQuery, scored: Scored, anchors: IdentityAnchors) -> Scored:
        """Ask the adjudicator about a close, unanchored contest between the top two profiles."""
        if self.adjudicator is None or len(scored) < MINIMUM_CONTEST_SIZE:
            return scored
        (first, first_group), (second, second_group) = scored[0], scored[1]
        if first.disambiguation:
            return scored
        if first.match_score - second.match_score >= self.config.ambiguity_margin:
            return scored
        if first.identity_anchors or second.identity_anchors:
            return scored
        verdict = self.adjudicator.judge(query, first_group, second_group)
        if not accepted(verdict, self.config.adjudication_minimum_confidence):
            return scored
        note = f'LLM adjudication ({verdict.model}, confidence {verdict.confidence:.2f}): {verdict.rationale}'
        if verdict.same_person == 'yes' and verdict.target in ('first', 'second', 'both'):
            merged = first_group + second_group
            profile = self.scorer.score(query, merged, anchors)
            profile.adjudication = note
            profile.evidence.append(note)
            rest = sorted(scored[2:], key=lambda item: self.ranking.key(item[0]))
            return [(profile, merged), *rest]
        if verdict.same_person == 'no' and verdict.target in ('first', 'second'):
            chosen = 0 if verdict.target == 'first' else 1
            profile, group = scored[chosen]
            profile.adjudication = note
            profile.evidence.append(note)
            return [scored[chosen], scored[1 - chosen]] + scored[2:]
        return scored

    def _mark_ambiguity(
        self, profiles: Sequence[ProfessorProfile], groups: Sequence[CandidateGroup]
    ) -> dict[str, Any] | None:
        if len(profiles) < MINIMUM_CONTEST_SIZE:
            return None
        best = profiles[0]
        margin = best.match_score - profiles[1].match_score
        if self.ranking.uncontested(best, profiles[1]):
            return None
        best.identity_confidence = round(
            min(best.identity_confidence, self.config.review_cap),
            self.config.confidence_precision,
        )
        best.status = ResolutionStatus.NEEDS_REVIEW
        if best.university_status == UniversityStatus.MATCHED:
            best.status = ResolutionStatus.AMBIGUOUS
        best.evidence.append('Competing identity has a similar score; manual review required')
        reasons = set()
        first_records = self._profile_records(best, groups)
        second_records = self._profile_records(profiles[1], groups)
        for left in first_records:
            for right in second_records:
                comparison = compare_identity(left, right, self.config)
                if not comparison.matched:
                    reasons.add(comparison.reason)
        contenders = []
        for profile in profiles[:2]:
            contenders.append(
                {
                    'name': profile.name,
                    'author_ids': profile.author_id_aliases,
                    'match_score': profile.match_score,
                    'university_status': profile.university_status,
                    'matched_affiliations': profile.matched_affiliations,
                }
            )
        return {
            'score_margin': round(margin, self.config.confidence_precision),
            'required_margin': self.config.ambiguity_margin,
            'reasons': sorted(reasons),
            'contenders': contenders,
        }

    @staticmethod
    def _profile_records(profile: ProfessorProfile, groups: Iterable[CandidateGroup]) -> list[AuthorCandidate]:
        records = []
        for group in groups:
            for record in group:
                ids = profile.author_id_aliases.get(record.source, [])
                if record.author_id in ids:
                    records.append(record)
        return records
