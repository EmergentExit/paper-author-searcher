"""Fetch the works a person lists themselves that author-ID collection never returned.

OpenAlex files some of a common name's papers under author IDs nobody selected (Yang Liu, Huan Zhang, Wei Wang),
so collecting the selected IDs cannot find them. The person's own list (homepage, CV, supplied ORCID) names them:
each listed work missing from the collection is looked up once through a chain of ``WorkLookup`` sources, stopping
at the first that has it, and enters the collection under the work's name-compatible author with evidence saying
why. Ownership decides afterwards (``OwnershipConfig.verify_listed_works``).

The sources are asked in order, each for every work still missing, so each work still ends at the first source that
has it and its answer and errors are those of looking it up alone. A source that answers many works per request
(``BatchWorkLookup``, arXiv) gets them all at once; any other is asked work by work, several at a time
(``ListedWorksConfig.lookup_workers``). Results are kept in the works' order. Per-host rules in the HTTP layer apply.
"""

import logging
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Sequence
from functools import partial
from http import HTTPStatus
from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote

from paper_author_searcher.concurrency import Outcome, attempt, in_order
from paper_author_searcher.config import DEFAULT_LISTED_WORKS, DEFAULT_MATCHING, ListedWorksConfig, MatchingConfig
from paper_author_searcher.logging_config import log_isolated_failure
from paper_author_searcher.matching import (
    ARXIV_ABSTRACT_URL,
    ARXIV_DOI_PREFIX,
    DOI_URL,
    canonical_arxiv,
    canonical_doi,
    canonical_orcid,
    name_similarity,
    normalize,
)
from paper_author_searcher.models import PaperRecord, PaperSourceResult, ProfessorProfile
from paper_author_searcher.quality.evaluation_matching import bibliographic_name_agrees
from paper_author_searcher.transport.http import SourceError

from .arxiv_api import ArxivApi, Entries
from .paper_providers import OpenAlexPaperProvider, SemanticScholarPaperProvider, parse_year

logger = logging.getLogger(__name__)

LISTED_SOURCE = 'listed_works'
LISTED_SOURCE_ID = 'listed-works'
RETRIEVED_EVIDENCE = "Retrieved because the researcher's own publication list names this work"
ATOM = '{http://www.w3.org/2005/Atom}'
ARXIV_ATOM = '{http://arxiv.org/schemas/atom}'


class WorkLookup(Protocol):
    """One source in the chain: the work behind a work key, as a record of the person's authorship, or None."""

    name: str

    def find(self, key: str, profile: ProfessorProfile) -> PaperRecord | None: ...


@runtime_checkable
class BatchWorkLookup(WorkLookup, Protocol):
    """A source that answers many work keys per request; each answer is what ``find`` would give for that key."""

    def find_many(self, keys: Sequence[str], profile: ProfessorProfile) -> list[Outcome[PaperRecord | None]]: ...


# Errors a source may raise for one work that leave the other works' lookups usable.
LOOKUP_ERRORS = (SourceError, ValueError, TypeError)


class AuthorMatcher:
    """Picks the one author of a work whose name is the person's; ambiguous or absent means no record."""

    def __init__(self, matching: MatchingConfig = DEFAULT_MATCHING) -> None:
        self.matching = matching

    def compatible(self, profile: ProfessorProfile, names: Iterable[str]) -> bool:
        return self.compatible_names(profile.target_names(), names)

    def compatible_names(self, targets: Iterable[str], names: Iterable[str]) -> bool:
        names = list(names)
        return any(
            name
            and (
                name_similarity(target, name, self.matching) >= self.matching.compatible_name
                or bibliographic_name_agrees(target, name)
            )
            for target in targets
            for name in names
        )

    def single(self, profile: ProfessorProfile, authors: Iterable[tuple[str, Sequence[str]]]) -> str | None:
        matches = {author_id for author_id, names in authors if author_id and self.compatible(profile, names)}
        return next(iter(matches)) if len(matches) == 1 else None

    def pick(self, profile: ProfessorProfile, details: Sequence[dict[str, Any]]) -> str | None:
        """``single`` over author details, leaving out any author whose ORCID differs from the person's."""
        orcid = canonical_orcid(profile.orcid) if profile.orcid else None
        return self.single(
            profile,
            (
                (detail['author_id'], [detail['name']])
                for detail in details
                if not (orcid and detail.get('orcid') and canonical_orcid(detail['orcid']) != orcid)
            ),
        )


def split_key(key: str) -> tuple[str, str]:
    kind, _, value = key.partition(':')
    return kind, value


def same_title(found: str | None, wanted: str) -> bool:
    return normalize(found or '') == wanted


class OpenAlexWorkLookup:
    name = 'openalex'

    def __init__(self, papers: OpenAlexPaperProvider, config: ListedWorksConfig, authors: AuthorMatcher) -> None:
        self.papers, self.config, self.authors = papers, config, authors

    def find(self, key: str, profile: ProfessorProfile) -> PaperRecord | None:
        work = self._work(key)
        if work is None:
            return None
        authorships = work.get('authorships') or []
        author_id = self.authors.single(
            profile,
            (
                (
                    (item.get('author') or {}).get('id') or '',
                    [(item.get('author') or {}).get('display_name') or '', item.get('raw_author_name') or ''],
                )
                for item in authorships
            ),
        )
        return self.papers.parse_work(work, author_id) if author_id else None

    def _work(self, key: str) -> dict | None:
        kind, value = split_key(key)
        provider = self.papers.provider
        fields = {'select': OpenAlexPaperProvider.FIELDS, 'api_key': provider.api_key}
        if kind in ('doi', 'arxiv'):
            doi = value if kind == 'doi' else ARXIV_DOI_PREFIX + value
            return _missing_is_none(
                lambda: provider.client.get(provider.base_url + '/works/doi:' + quote(doi, safe='/'), fields)
            )
        data = provider.client.get(
            provider.base_url + '/works',
            {
                **fields,
                'filter': 'title.search:' + value.replace(',', ' '),
                'per-page': self.config.title_search_results,
            },
        )
        return next((work for work in data.get('results') or [] if same_title(work.get('title'), value)), None)


class SemanticScholarWorkLookup:
    name = 'semantic_scholar'

    def __init__(self, papers: SemanticScholarPaperProvider, authors: AuthorMatcher) -> None:
        self.papers, self.authors = papers, authors

    def find(self, key: str, profile: ProfessorProfile) -> PaperRecord | None:
        paper = self._paper(key)
        if paper is None:
            return None
        author_id = self.authors.single(
            profile,
            ((str(item.get('authorId') or ''), [item.get('name') or '']) for item in paper.get('authors') or []),
        )
        return self.papers.parse_work(paper, author_id) if author_id else None

    def _paper(self, key: str) -> dict | None:
        kind, value = split_key(key)
        provider = self.papers.provider
        fields = {'fields': SemanticScholarPaperProvider.FIELDS}
        headers = {'x-api-key': provider.api_key} if provider.api_key else None
        if kind in ('doi', 'arxiv'):
            paper_id = ('DOI:' if kind == 'doi' else 'ARXIV:') + value
            url = provider.base_url + '/paper/' + quote(paper_id, safe=':/')
            return _missing_is_none(lambda: provider.client.get(url, fields, headers))
        data = _missing_is_none(
            lambda: provider.client.get(provider.base_url + '/paper/search/match', {**fields, 'query': value}, headers)
        )
        return next((paper for paper in (data or {}).get('data') or [] if same_title(paper.get('title'), value)), None)


class CrossrefWorkLookup:
    """Publisher records: no author IDs, but the printed affiliation strings travel with each author."""

    name = 'crossref'

    def __init__(
        self, client: Any, base_url: str, config: ListedWorksConfig, authors: AuthorMatcher, mailto: str | None = None
    ) -> None:
        self.client, self.base_url, self.config, self.authors, self.mailto = client, base_url, config, authors, mailto

    def find(self, key: str, profile: ProfessorProfile) -> PaperRecord | None:
        kind, value = split_key(key)
        if kind == 'arxiv':
            return None  # arXiv DOIs are DataCite's, not Crossref's
        item = self._item(kind, value)
        if item is None:
            return None
        return crossref_item_record(item, lambda details: self.authors.pick(profile, details), self.name)

    def _item(self, kind: str, value: str) -> dict | None:
        params = {'mailto': self.mailto} if self.mailto else {}
        if kind == 'doi':
            data = _missing_is_none(lambda: self.client.get(f'{self.base_url}/works/{quote(value, safe="/")}', params))
            return (data or {}).get('message')
        data = self.client.get(
            self.base_url + '/works', {**params, 'query.bibliographic': value, 'rows': self.config.title_search_results}
        )
        items = ((data or {}).get('message') or {}).get('items') or []
        return next((item for item in items if same_title((item.get('title') or [''])[0], value)), None)


class ArxivWorkLookup:
    """Preprints, asked for in batches (collection.arxiv_api); the HTTP layer keeps arXiv's request rules."""

    name = 'arxiv'

    def __init__(self, api: ArxivApi, config: ListedWorksConfig, authors: AuthorMatcher) -> None:
        self.api, self.config, self.authors = api, config, authors

    def find(self, key: str, profile: ProfessorProfile) -> PaperRecord | None:
        return self.find_many([key], profile)[0].unwrap()

    def find_many(self, keys: Sequence[str], profile: ProfessorProfile) -> list[Outcome[PaperRecord | None]]:
        kinds = [split_key(key) for key in keys]
        ids = [value for kind, value in kinds if kind == 'arxiv']
        titles = [value for kind, value in kinds if kind not in ('arxiv', 'doi')]
        by_id = self.api.by_ids(ids) if ids else {}
        by_title = self.api.by_titles(titles, self.config.title_search_results, same_title) if titles else {}
        found: list[Outcome[PaperRecord | None]] = []
        for kind, value in kinds:
            if kind == 'doi':
                found.append(Outcome(value=None))
                continue
            entries = (by_id if kind == 'arxiv' else by_title)[value]
            if entries.error is not None:
                found.append(Outcome(error=entries.error))
                continue
            found.append(Outcome(value=self._record(entries.unwrap(), profile)))
        return found

    def _record(self, entries: Entries, profile: ProfessorProfile) -> PaperRecord | None:
        """The first entry that yields a record of the person's authorship."""
        for entry in entries:
            record = arxiv_entry_record(entry, lambda details: self.authors.single(profile, _named(details)))
            if record is not None:
                return record
        return None


def crossref_item_record(
    item: dict,
    choose: Callable[[list[dict[str, Any]]], str | None],
    source: str = 'crossref',
    relabel: str | None = None,
) -> PaperRecord | None:
    """One Crossref work as a record of the author ``choose`` picks (None: no record). Authors carry their printed
    affiliations and ORCIDs; ``relabel`` gives the chosen author a stable ID instead (a name-search key)."""
    doi = canonical_doi(item.get('DOI') or '').removeprefix('doi:') or None
    details: list[dict[str, Any]] = []
    for index, author in enumerate(item.get('author') or []):
        name = (
            ' '.join(part for part in (author.get('given'), author.get('family')) if part) or author.get('name') or ''
        )
        details.append(
            {
                'name': name,
                'author_id': f'crossref:{doi}#{index}',
                'role': 'author',
                'orcid': author.get('ORCID'),
                'orcid_scope': 'paper_authorship',
                'affiliations': [
                    {'raw_affiliation_string': entry['name']}
                    for entry in author.get('affiliation') or []
                    if entry.get('name')
                ],
                'affiliation_scope': 'paper_authorship',
            }
        )
    author_id = relabel_choice(details, choose(details), relabel)
    if not author_id:
        return None
    title = ' '.join((item.get('title') or [''])[0].split())
    issued = ((item.get('issued') or {}).get('date-parts') or [[None]])[0][0]
    return PaperRecord(
        title=title,
        authors=[detail['name'] for detail in details],
        year=parse_year(issued),
        doi=doi,
        venue=(item.get('container-title') or [None])[0],
        abstract=None,
        source=source,
        source_paper_id=doi or title,
        source_author_id=author_id,
        paper_url=DOI_URL + doi if doi else None,
        publication_type=item.get('type'),
        author_details=details,
        target_author_present=True,
        raw_metadata={'crossref': item},
    )


def relabel_choice(details: list[dict[str, Any]], author_id: str | None, relabel: str | None) -> str | None:
    """The chosen author's ID, renamed to ``relabel`` (in ``details`` too) when one is given."""
    if not author_id or not relabel:
        return author_id
    for detail in details:
        if detail['author_id'] == author_id:
            detail['author_id'] = relabel
    return relabel


def _named(details: Sequence[dict[str, Any]]) -> Iterable[tuple[str, Sequence[str]]]:
    return ((detail['author_id'], [detail['name']]) for detail in details)


def arxiv_entry_record(
    entry: ET.Element, choose: Callable[[list[dict[str, Any]]], str | None], relabel: str | None = None
) -> PaperRecord | None:
    """One arXiv Atom entry as a record of the author ``choose`` picks (None: no record). Authors get per-entry
    IDs; ``relabel`` gives the chosen author a stable ID instead (a name-search key)."""
    identifier = canonical_arxiv((entry.findtext(ATOM + 'id') or '').rsplit('/abs/', 1)[-1])
    if not identifier:
        return None
    details: list[dict[str, Any]] = []
    for index, author in enumerate(entry.findall(ATOM + 'author')):
        affiliations = [item.text for item in author.findall(ARXIV_ATOM + 'affiliation') if item.text]
        details.append(
            {
                'name': author.findtext(ATOM + 'name') or '',
                'author_id': f'arxiv:{identifier}#{index}',
                'role': 'author',
                'affiliations': [{'raw_affiliation_string': value} for value in affiliations],
                'affiliation_scope': 'paper_authorship',
            }
        )
    author_id = relabel_choice(details, choose(details), relabel)
    if not author_id:
        return None
    doi = entry.findtext(ARXIV_ATOM + 'doi')
    return PaperRecord(
        title=' '.join((entry.findtext(ATOM + 'title') or '').split()),
        authors=[detail['name'] for detail in details],
        year=parse_year((entry.findtext(ATOM + 'published') or '')[:4]),
        doi=doi or ARXIV_DOI_PREFIX + identifier,
        venue='arXiv',
        abstract=' '.join((entry.findtext(ATOM + 'summary') or '').split()) or None,
        source='arxiv',
        source_paper_id=identifier,
        source_author_id=author_id,
        paper_url=ARXIV_ABSTRACT_URL + identifier,
        publication_type='preprint',
        author_details=details,
        target_author_present=True,
        external_ids={'arxiv': identifier},
    )


class ListedWorkRetriever:
    def __init__(self, lookups: Sequence[WorkLookup], config: ListedWorksConfig = DEFAULT_LISTED_WORKS) -> None:
        self.lookups = list(lookups)
        self.config = config

    def missing(self, profile: ProfessorProfile, records: Iterable[PaperRecord]) -> list[str]:
        covered: set[str] = set()
        for record in records:
            covered |= record.work_keys()
        return sorted(set(profile.listed_works) - covered)

    def retrieve(self, profile: ProfessorProfile, records: Sequence[PaperRecord]) -> PaperSourceResult:
        result = PaperSourceResult(source=LISTED_SOURCE, author_id=LISTED_SOURCE_ID)
        missing = self.missing(profile, records) if self.config.enabled else []
        keys = missing[: self.config.maximum_lookups]
        for record, errors in self._look_up_all(keys, profile):
            # Merged in the works' order, so the result does not depend on which lookup finished first.
            result.metadata_errors.extend(errors)
            if record is not None:
                result.records_received += 1
                record.attribution_evidence.append(f'{RETRIEVED_EVIDENCE} (found in {record.source})')
                result.papers.append(record)
        result.pagination_complete = True
        result.complete = len(missing) <= self.config.maximum_lookups and not result.metadata_errors
        return result

    def _look_up_all(
        self, keys: Sequence[str], profile: ProfessorProfile
    ) -> list[tuple[PaperRecord | None, list[dict[str, str]]]]:
        """Chain of responsibility, one source at a time: each source is asked for every work still missing, so a
        work ends at the first source that has it; a failing source is skipped for that work and its error kept."""
        found: dict[str, PaperRecord] = {}
        errors: dict[str, list[dict[str, str]]] = {key: [] for key in keys}
        pending = list(keys)
        for lookup in self.lookups:
            if not pending:
                break
            still_missing = []
            for key, outcome in zip(pending, self._ask(lookup, pending, profile), strict=True):
                if outcome.error is not None:
                    log_isolated_failure(
                        logger, 'Listed work %s lookup in %s failed: %s', key, lookup.name, outcome.error
                    )
                    errors[key].append({'paper_id': key, 'source': lookup.name, 'error': str(outcome.error)})
                    still_missing.append(key)
                elif outcome.value is not None:
                    found[key] = outcome.value
                else:
                    still_missing.append(key)
            pending = still_missing
        return [(found.get(key), errors[key]) for key in keys]

    def _ask(
        self, lookup: WorkLookup, keys: Sequence[str], profile: ProfessorProfile
    ) -> list[Outcome[PaperRecord | None]]:
        if isinstance(lookup, BatchWorkLookup):
            batch = attempt(partial(lookup.find_many, keys, profile), LOOKUP_ERRORS)
            if batch.error is not None:
                return [Outcome(error=batch.error) for _ in keys]
            return batch.unwrap()
        calls = [partial(attempt, partial(lookup.find, key, profile), LOOKUP_ERRORS) for key in keys]
        return in_order(calls, self.config.lookup_workers, 'listed-works')


def _missing_is_none(fetch: Any) -> Any:
    try:
        return fetch()
    except SourceError as exc:
        if exc.status_code == HTTPStatus.NOT_FOUND:
            return None
        raise
