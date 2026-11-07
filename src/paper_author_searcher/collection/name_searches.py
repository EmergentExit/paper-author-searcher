"""Paper sources searched by the person's name (and institution) instead of an author ID.

``PagedNameSearch`` is the template: it pages a source with a cursor, turns each item into a record of the one
author whose name is the person's (``AuthorMatcher.pick``), and isolates failures. A source supplies its first
cursor, one page request and one record parser:

- ``EuropePmcAuthorSearch``: Europe PMC (PubMed, PMC and preprints) by family name with initials and the
  institution; its bylines carry each author's first name, ORCID and affiliation.
- ``CrossrefAuthorSearch``: Crossref by author and affiliation. Crossref ranks by relevance rather than filtering,
  so a work counts only when the person's own byline entry prints the institution, and paging stops at the first
  page that yields nothing.

Every namesake's papers come back too, so the records carry no evidence of their own: ownership verifies them
through recurring coauthors or the person's own list, and the precision guard applies to common names as usual.
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from paper_author_searcher.config import (
    DEFAULT_MATCHING,
    DEFAULT_NAME_SEARCHES,
    ListedWorksConfig,
    MatchingConfig,
    NameSearchConfig,
)
from paper_author_searcher.logging_config import log_isolated_failure
from paper_author_searcher.matching import (
    DOI_URL,
    canonical_doi,
    institution_mentioned,
    institution_similarity,
    normalize,
    split_name,
)
from paper_author_searcher.models import PaperRecord, PaperSourceResult, ProfessorProfile

from .listed_works import AuthorMatcher, crossref_item_record, relabel_choice, same_title, split_key
from .paper_providers import parse_year

logger = logging.getLogger(__name__)


@dataclass
class Page:
    items: list[Any]
    # None: this was the last page.
    next_cursor: Any


class PagedNameSearch(ABC):
    name: str
    evidence: str
    page_size: int
    maximum_results: int
    # Stop at the first page that yields no record (relevance-ranked sources).
    stop_on_empty_page: bool = False

    def __init__(self, matching: MatchingConfig = DEFAULT_MATCHING) -> None:
        self.authors = AuthorMatcher(matching)
        self.matching = matching

    def author_key(self, profile: ProfessorProfile) -> str:
        return 'name:' + normalize(profile.requested_name or profile.name)

    @abstractmethod
    def first_cursor(self) -> Any: ...

    @abstractmethod
    def page(self, profile: ProfessorProfile, cursor: Any, size: int) -> Page: ...

    @abstractmethod
    def record(self, item: Any, profile: ProfessorProfile, key: str) -> PaperRecord | None: ...

    def collect(self, profile: ProfessorProfile) -> PaperSourceResult:
        key = self.author_key(profile)
        result = PaperSourceResult(source=self.name, author_id=key)
        try:
            cursor, received = self.first_cursor(), 0
            while received < self.maximum_results:
                page = self.page(profile, cursor, min(self.page_size, self.maximum_results - received))
                result.pages_fetched += 1
                found = 0
                for item in page.items:
                    result.records_received += 1
                    record = self.record(item, profile, key)
                    if record is not None:
                        record.attribution_evidence.append(self.evidence)
                        result.papers.append(record)
                        found += 1
                received += len(page.items)
                if not page.items or page.next_cursor is None or (self.stop_on_empty_page and not found):
                    result.pagination_complete = True
                    break
                cursor = page.next_cursor
            result.complete = result.pagination_complete
        except Exception as exc:
            log_isolated_failure(logger, '%s author search failed for %s: %s', self.name, key, exc)
            result.error = str(exc)
        return result


def year_range(recent_years: int, current_year: int) -> tuple[int, int]:
    return current_year - recent_years + 1, current_year


def europepmc_record(
    result: dict, choose: Callable[[list[dict[str, Any]]], str | None], relabel: str | None = None
) -> PaperRecord | None:
    """One Europe PMC result (``resultType=core``) as a record of the author ``choose`` picks."""
    identifier = f'{result.get("source") or "MED"}:{result.get("id") or ""}'
    details: list[dict[str, Any]] = []
    for index, author in enumerate((result.get('authorList') or {}).get('author') or []):
        given, family = author.get('firstName'), author.get('lastName')
        name = f'{given} {family}' if given and family else author.get('fullName') or ''
        orcid = author.get('authorId') or {}
        affiliations = ((author.get('authorAffiliationDetailsList') or {}).get('authorAffiliation')) or []
        details.append(
            {
                'name': name,
                'author_id': f'europepmc:{identifier}#{index}',
                'role': 'author',
                'orcid': orcid.get('value') if orcid.get('type') == 'ORCID' else None,
                'orcid_scope': 'paper_authorship',
                'affiliations': [
                    {'raw_affiliation_string': item['affiliation']} for item in affiliations if item.get('affiliation')
                ],
                'affiliation_scope': 'paper_authorship',
            }
        )
    author_id = relabel_choice(details, choose(details), relabel)
    if not author_id:
        return None
    doi = canonical_doi(result.get('doi') or '').removeprefix('doi:') or None
    external = {name: str(result[name]) for name in ('pmid', 'pmcid') if result.get(name)}
    types = (result.get('pubTypeList') or {}).get('pubType') or []
    return PaperRecord(
        title=' '.join(str(result.get('title') or '').split()).rstrip('.'),
        authors=[detail['name'] for detail in details],
        year=parse_year(result.get('pubYear')),
        doi=doi,
        venue=((result.get('journalInfo') or {}).get('journal') or {}).get('title'),
        abstract=result.get('abstractText'),
        source='europepmc',
        source_paper_id=identifier,
        source_author_id=author_id,
        paper_url=DOI_URL + doi if doi else f'https://europepmc.org/article/{identifier.replace(":", "/")}',
        publication_type=types[0] if types else None,
        author_details=details,
        target_author_present=True,
        external_ids=external,
    )


def initials(given: str) -> str:
    return ''.join(part[0] for part in given.replace('-', ' ').split() if part).upper()


class EuropePmcAuthorSearch(PagedNameSearch):
    name = 'europepmc'
    evidence = 'Found by Europe PMC author and institution search; no author ID'

    def __init__(
        self,
        client: Any,
        url: str,
        config: NameSearchConfig = DEFAULT_NAME_SEARCHES,
        matching: MatchingConfig = DEFAULT_MATCHING,
        current_year: int | None = None,
    ) -> None:
        super().__init__(matching)
        self.client, self.url, self.config = client, url, config
        self.page_size, self.maximum_results = config.europepmc_page_size, config.maximum_results
        self.years = year_range(config.recent_years, current_year or date.today().year)

    def first_cursor(self) -> str:
        return '*'

    def query(self, profile: ProfessorProfile) -> str:
        given, family = split_name(profile.requested_name or profile.name)
        author = f'{family} {initials(given)}'.strip().replace('"', ' ')
        school = (profile.requested_university or profile.university or '').replace('"', ' ')
        start, end = self.years
        return f'AUTH:"{author}" AND AFF:"{school}" AND PUB_YEAR:[{start} TO {end}]'

    def page(self, profile: ProfessorProfile, cursor: Any, size: int) -> Page:
        data = self.client.get(
            self.url,
            {
                'query': self.query(profile),
                'format': 'json',
                'resultType': 'core',
                'pageSize': size,
                'cursorMark': cursor,
            },
        )
        items = ((data or {}).get('resultList') or {}).get('result') or []
        following = (data or {}).get('nextCursorMark')
        return Page(items, following if items and following and following != cursor else None)

    def record(self, item: Any, profile: ProfessorProfile, key: str) -> PaperRecord | None:
        return europepmc_record(item, lambda details: self.authors.pick(profile, details), key)


class CrossrefAuthorSearch(PagedNameSearch):
    name = 'crossref'
    evidence = 'Found by Crossref author and affiliation search; no author ID'
    stop_on_empty_page = True

    def __init__(
        self,
        client: Any,
        base_url: str,
        config: NameSearchConfig = DEFAULT_NAME_SEARCHES,
        matching: MatchingConfig = DEFAULT_MATCHING,
        mailto: str | None = None,
        current_year: int | None = None,
    ) -> None:
        super().__init__(matching)
        self.client, self.base_url, self.config, self.mailto = client, base_url.rstrip('/'), config, mailto
        self.page_size, self.maximum_results = config.page_size, config.maximum_results
        self.years = year_range(config.recent_years, current_year or date.today().year)

    def first_cursor(self) -> str:
        return '*'

    def page(self, profile: ProfessorProfile, cursor: Any, size: int) -> Page:
        params = {
            'query.author': profile.requested_name or profile.name,
            'query.affiliation': profile.requested_university or profile.university or '',
            'filter': f'from-pub-date:{self.years[0]}',
            'rows': size,
            'cursor': cursor,
        }
        if self.mailto:
            params['mailto'] = self.mailto
        message = (self.client.get(self.base_url + '/works', params) or {}).get('message') or {}
        items = message.get('items') or []
        return Page(items, message.get('next-cursor') if items else None)

    def record(self, item: Any, profile: ProfessorProfile, key: str) -> PaperRecord | None:
        return crossref_item_record(item, lambda details: self._choose(profile, details), self.name, key)

    def _choose(self, profile: ProfessorProfile, details: list[dict[str, Any]]) -> str | None:
        author_id = self.authors.pick(profile, details)
        if author_id is None or not self.config.crossref_require_affiliation:
            return author_id
        school = profile.requested_university or profile.university or ''
        chosen = next(detail for detail in details if detail['author_id'] == author_id)
        printed = [entry['raw_affiliation_string'] for entry in chosen['affiliations']]
        return author_id if any(institution_mentioned(school, text, self.matching) for text in printed) else None


class EuropePmcWorkLookup:
    """A listed work in Europe PMC, by DOI or exact title."""

    name = 'europepmc'

    def __init__(self, client: Any, url: str, config: ListedWorksConfig, authors: AuthorMatcher) -> None:
        self.client, self.url, self.config, self.authors = client, url, config, authors

    def find(self, key: str, profile: ProfessorProfile) -> PaperRecord | None:
        kind, value = split_key(key)
        if kind == 'arxiv':
            return None
        query = f'DOI:"{value}"' if kind == 'doi' else 'TITLE:"' + value.replace('"', ' ') + '"'
        data = self.client.get(
            self.url,
            {'query': query, 'format': 'json', 'resultType': 'core', 'pageSize': self.config.title_search_results},
        )
        for result in ((data or {}).get('resultList') or {}).get('result') or []:
            if kind == 'title' and not same_title(result.get('title'), value):
                continue
            record = europepmc_record(result, lambda details: self.authors.pick(profile, details))
            if record is not None:
                return record
        return None


class CoreWorkLookup:
    """A listed work in CORE (open-access repository copies aggregated from institutions), by DOI or title."""

    name = 'core'

    def __init__(self, client: Any, url: str, api_key: str, config: ListedWorksConfig, authors: AuthorMatcher) -> None:
        self.client, self.url, self.api_key, self.config, self.authors = (
            client,
            url.rstrip('/'),
            api_key,
            config,
            authors,
        )

    def find(self, key: str, profile: ProfessorProfile) -> PaperRecord | None:
        kind, value = split_key(key)
        if kind == 'arxiv':
            return None
        query = f'doi:"{value}"' if kind == 'doi' else 'title:"' + value.replace('"', ' ') + '"'
        data = self.client.get(
            self.url + '/search/works',
            {'q': query, 'limit': self.config.title_search_results},
            {'Authorization': 'Bearer ' + self.api_key},
        )
        for work in (data or {}).get('results') or []:
            if kind == 'title' and not same_title(work.get('title'), value):
                continue
            record = core_record(work, lambda details: self.authors.pick(profile, details))
            if record is not None:
                return record
        return None


def core_record(work: dict, choose: Callable[[list[dict[str, Any]]], str | None]) -> PaperRecord | None:
    identifier = str(work.get('id') or '')
    details = [
        {'name': str(author.get('name') or ''), 'author_id': f'core:{identifier}#{index}', 'role': 'author'}
        for index, author in enumerate(work.get('authors') or [])
    ]
    author_id = choose(details)
    if not author_id:
        return None
    doi = canonical_doi(work.get('doi') or '').removeprefix('doi:') or None
    return PaperRecord(
        title=' '.join(str(work.get('title') or '').split()),
        authors=[detail['name'] for detail in details],
        year=parse_year(work.get('yearPublished')),
        doi=doi,
        venue=work.get('publisher'),
        abstract=None,
        source='core',
        source_paper_id=identifier,
        source_author_id=author_id,
        paper_url=DOI_URL + doi if doi else work.get('downloadUrl'),
        publication_type=None,
        author_details=details,
        target_author_present=True,
    )


class OpenAlexSiblingSearch(PagedNameSearch):
    """Works of the person's other OpenAlex author records at the requested school.

    OpenAlex often splits one researcher into several author records, and the identity step keeps only those its
    search returned. This asks OpenAlex for every author of the person's name affiliated with an institution of the
    resolved records that matches the requested school, and collects the works of those not already selected.

    A namesake at the same school comes back too, so the records carry no author ID of the person: the chosen
    authorship is relabelled with the name-search key, and ownership verifies a work only through recurring
    independently verified coauthors (or the person's own list), as for every name search.
    """

    name = 'openalex_siblings'
    evidence = 'Found under another OpenAlex author record of the same name at the requested school; no author ID'

    def __init__(
        self,
        papers: Any,
        config: NameSearchConfig = DEFAULT_NAME_SEARCHES,
        matching: MatchingConfig = DEFAULT_MATCHING,
        current_year: int | None = None,
    ) -> None:
        super().__init__(matching)
        self.papers, self.config = papers, config
        self.page_size, self.maximum_results = config.page_size, config.maximum_results
        self.years = year_range(config.recent_years, current_year or date.today().year)
        self._siblings: dict[str, list[str]] = {}

    @property
    def provider(self) -> Any:
        return self.papers.provider

    def first_cursor(self) -> str:
        return '*'

    def collect(self, profile: ProfessorProfile) -> PaperSourceResult:
        if not self.siblings(profile):
            result = PaperSourceResult(source=self.name, author_id=self.author_key(profile))
            result.pagination_complete = result.complete = True
            return result
        return super().collect(profile)

    def siblings(self, profile: ProfessorProfile) -> list[str]:
        """OpenAlex author IDs of the person's name at the matching institutions, minus the selected ones."""
        key = self.author_key(profile)
        if key not in self._siblings:
            self._siblings[key] = self._find_siblings(profile)
        return self._siblings[key]

    def _find_siblings(self, profile: ProfessorProfile) -> list[str]:
        selected = [short_id(value) for value in profile.author_id_aliases.get('openalex') or []]
        selected = [value for value in selected if value][: self.config.openalex_sibling_selected_records]
        institutions = self._institutions(profile, selected)
        if not institutions:
            return []
        data = self.provider.client.get(
            self.provider.base_url + '/authors',
            {
                'search': profile.requested_name or profile.name,
                'filter': 'affiliations.institution.id:' + '|'.join(institutions),
                'per-page': self.config.openalex_sibling_candidates,
                'select': 'id,display_name,display_name_alternatives',
                'api_key': self.provider.api_key,
            },
        )
        found = []
        for item in (data or {}).get('results') or []:
            author_id = short_id(item.get('id'))
            names = [item.get('display_name') or '', *(item.get('display_name_alternatives') or [])]
            if author_id and author_id not in selected and self.authors.compatible(profile, names):
                found.append(author_id)
        return found[: self.config.openalex_sibling_records]

    def _institutions(self, profile: ProfessorProfile, selected: list[str]) -> list[str]:
        schools = [
            school
            for school in (profile.requested_university, *profile.matched_affiliations)
            if isinstance(school, str) and school
        ]
        if not schools or not selected:
            return []
        data = self.provider.client.get(
            self.provider.base_url + '/authors',
            {
                'filter': 'ids.openalex:' + '|'.join(selected),
                'select': 'id,affiliations,last_known_institutions',
                'api_key': self.provider.api_key,
            },
        )
        found: dict[str, None] = {}
        for author in (data or {}).get('results') or []:
            places = [entry.get('institution') or {} for entry in author.get('affiliations') or []]
            places.extend(author.get('last_known_institutions') or [])
            for place in places:
                name, identifier = place.get('display_name') or '', short_id(place.get('id'))
                if identifier and any(
                    institution_similarity(school, name, self.matching) >= self.matching.matched_school
                    for school in schools
                ):
                    found[identifier] = None
        return list(found)

    def page(self, profile: ProfessorProfile, cursor: Any, size: int) -> Page:
        provider = self.provider
        data = provider.client.get(
            provider.base_url + '/works',
            {
                'filter': f'authorships.author.id:{"|".join(self.siblings(profile))},'
                f'from_publication_date:{self.years[0]}-01-01',
                'cursor': cursor,
                'per-page': size,
                'select': self.papers.FIELDS,
                'api_key': provider.api_key,
            },
        )
        items = (data or {}).get('results') or []
        following = ((data or {}).get('meta') or {}).get('next_cursor')
        return Page(items, following if items and following and following != cursor else None)

    def record(self, item: Any, profile: ProfessorProfile, key: str) -> PaperRecord | None:
        siblings = set(self.siblings(profile))
        chosen = [
            short_id((authorship.get('author') or {}).get('id'))
            for authorship in item.get('authorships') or []
            if short_id((authorship.get('author') or {}).get('id')) in siblings
        ]
        if len(chosen) != 1:
            return None
        record = self.papers.parse_work(item, 'https://openalex.org/' + chosen[0])
        for detail in record.author_details:
            if short_id(detail.get('author_id')) == chosen[0]:
                detail['author_id'] = key
        record.source, record.source_author_id, record.target_author_present = self.name, key, True
        return record


def short_id(value: Any) -> str:
    """An OpenAlex ID without its URL prefix ('https://openalex.org/A123' -> 'A123')."""
    return str(value or '').rstrip('/').rsplit('/', 1)[-1]
