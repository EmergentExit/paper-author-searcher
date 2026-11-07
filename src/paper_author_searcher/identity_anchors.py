"""Turn caller hints into author-ID anchors: the source IDs that actually wrote papers known to be the person's.

An anchor is independent of the name search, so it separates namesakes and ties split profiles together.
"""

import logging
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import partial
from html import unescape
from typing import Any, Protocol
from urllib.parse import quote, urljoin, urlsplit

from .concurrency import Outcome, attempt, in_order
from .config import DEFAULT_ANCHORS, DEFAULT_DISCOVERY, DEFAULT_MATCHING, DEFAULT_SOURCES
from .evaluation_matching import bibliographic_name_agrees
from .homepage_citations import (
    ARXIV_IN_TEXT,
    DOI_IN_TEXT,
    CitationParser,
    CitationRanker,
    DocumentParser,
    HomepageCitation,
    HomepageReader,
    HtmlCitationParser,
    PdfCitationParser,
    is_pdf,
    mentions,
    pdf_text,
)
from .html_support import LinkExtractor, decode_response
from .http import HttpTransport, SourceError
from .identity_discovery import Discovery, NoDiscovery
from .matching import (
    ARXIV_DOI_PREFIX,
    canonical_doi,
    canonical_orcid,
    name_similarity,
    normalize,
    normalize_title,
    school_named,
    work_keys,
)
from .models import AnchorEvidence
from .profile_links import ORCID_SOURCE, ProfileLink, ProfileLinkParser
from .publication_extraction import html_text
from .status import AnchorKind
from .timings import phase

logger = logging.getLogger(__name__)

# Added by the resolver itself when a candidate record carries the supplied ORCID; not an anchor-finder result.
SUPPLIED_ORCID_REASON = 'ORCID matches the supplied ORCID'
# Errors a page read or listed source may raise that leave the rest of the search usable.
PAGE_ERRORS = (SourceError, ValueError)
# Errors a paper lookup may raise that the anchors record and pass over, as one failed lookup does.
LOOKUP_ERRORS = (SourceError,)
SEMANTIC_SCHOLAR_FIELDS = 'title,authors'
ARXIV_HINT = re.compile(r'^(?:arxiv:)?(\d{4}\.\d{4,5})(?:v\d+)?$', re.IGNORECASE)


@dataclass
class IdentityAnchors:
    author_ids: dict[str, set[str]] = field(default_factory=dict)
    orcid: str | None = None
    evidence: list[AnchorEvidence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Work keys (matching.work_keys) of every paper the person's own page lists, beyond the anchor budget.
    listed_works: list[str] = field(default_factory=list)
    # What discovery found (identity_discovery.Discovery.to_dict), recorded so a replay can restore it.
    discovered: dict[str, Any] = field(default_factory=dict)
    # Citations on the person's pages that link a PDF (listed_pdf), for the PDF source 'homepage'.
    listed_pdfs: list[dict[str, Any]] = field(default_factory=list)
    # Listed works whose only compatible author carries another ORCID: (source, author ID) -> {work key: reason}.
    contested: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)

    def add(
        self, source: str, author_id: str, reason: str, kind: AnchorKind = AnchorKind.PAPER, work: str = ''
    ) -> None:
        self.author_ids.setdefault(source, set()).add(str(author_id))
        self.evidence.append(AnchorEvidence(source, str(author_id), reason, kind, work))

    def contest(self, source: str, author_id: str, reason: str, work: str) -> None:
        self.contested.setdefault((source, str(author_id)), {})[work or reason] = reason

    def settle_contested(self, minimum_works: int) -> None:
        """Anchor the contested authors that hold at least ``minimum_works`` distinct listed works."""
        for (source, author_id), works in self.contested.items():
            if len(works) >= minimum_works:
                for work, reason in works.items():
                    self.add(source, author_id, reason, AnchorKind.CONTESTED, work if work != reason else '')
        self.contested = {}

    def orcid_outweighed(self, source: str, author_id: str) -> bool:
        """Whether the person's own works outweigh the ORCID this author ID carries in its database."""
        return any(item.kind == AnchorKind.CONTESTED for item in self.evidence_for(source, author_id))

    def evidence_for(self, source: str, author_id: str) -> list[AnchorEvidence]:
        return [item for item in self.evidence if item.source == source and item.author_id == str(author_id)]

    def contains(self, source: str, author_id: str) -> bool:
        return str(author_id) in self.author_ids.get(source, set())

    def __bool__(self) -> bool:
        return bool(self.author_ids or self.orcid)


class ListedWorkSource(Protocol):
    """Adds works to the person's own list (``IdentityAnchors.listed_works``), e.g. an OpenReview profile."""

    name: str

    def works(self, query) -> set[str]: ...


class AnchorFinder(Protocol):
    def find(self, query) -> IdentityAnchors: ...


class Discoverer(Protocol):
    def discover(self, query) -> Discovery: ...


@dataclass(frozen=True)
class PageToRead:
    url: str
    # A page the caller supplied may be a personal page listing only the person's works; on a discovered page (and
    # on a lab page) only the citations that name the person count.
    strict: bool
    # A search hit may be a namesake's page: without the requested school in its text it is not read for papers.
    must_name_school: bool = False


@dataclass(frozen=True)
class _ReadPage:
    citations: list[HomepageCitation]
    text: str


def paper_hints(values: list[str]) -> dict[str, list[str]]:
    """Split free-form known papers into DOIs, arXiv IDs and titles."""
    hints: dict[str, list[str]] = {'doi': [], 'arxiv': [], 'title': []}
    for value in values:
        value = value.strip()
        doi = DOI_IN_TEXT.search(value)
        arxiv = ARXIV_HINT.match(value) or ARXIV_IN_TEXT.search(value)
        if doi:
            hints['doi'].append(doi.group(0).rstrip('.,;').lower())
        elif arxiv:
            hints['arxiv'].append(arxiv.group(1))
        elif value:
            hints['title'].append(value)
    return hints


def homepage_papers(html: str) -> list[str]:
    """DOIs and arXiv IDs linked from a researcher's page, in order of appearance."""
    text = unescape(html)
    found = [match.group(0).rstrip('.,;') for match in DOI_IN_TEXT.finditer(text)]
    found += [match.group(1) for match in ARXIV_IN_TEXT.finditer(text)]
    return list(dict.fromkeys(found))


def own_citations(citations: list[HomepageCitation], name: str, strict: bool = False) -> list[HomepageCitation]:
    """On a lab page only the citations naming the person are theirs; a supplied personal page lists only theirs."""
    named = [citation for citation in citations if mentions(name, citation.text)]
    return named if named or strict else citations


def _listed_works(citations: list[HomepageCitation], name: str) -> set[str]:
    own = own_citations(citations, name)
    keys: set[str] = set()
    for citation in own:
        keys |= work_keys(citation.dois, citation.arxiv_ids, citation.titles)
    return keys


def listed_pdfs(citations: list[HomepageCitation], page: str, config=DEFAULT_ANCHORS) -> list[dict[str, Any]]:
    """One record per PDF link of a single citation; a block longer than one citation holds several, so its links
    are not tied to any one of them."""
    records: dict[str, dict[str, Any]] = {}
    for citation in citations:
        if not citation.pdf_links or len(citation.text) > config.listed_pdf_citation_characters:
            continue
        for url in citation.pdf_links:
            records.setdefault(
                url,
                {
                    'url': url,
                    'page': page,
                    'citation': citation.text,
                    'dois': list(citation.dois),
                    'arxiv_ids': list(citation.arxiv_ids),
                },
            )
    return list(records.values())


def _key(dois: Iterable[str] = (), arxiv_ids: Iterable[str] = (), titles: Iterable[str] = ()) -> str:
    return next(iter(sorted(work_keys(dois, arxiv_ids, titles))), '')


def _merge(*lists: list[str]) -> list[str]:
    return list(dict.fromkeys(value for values in lists for value in values))


class ScholarlyAnchorFinder:
    """Resolve hints through OpenAlex and Semantic Scholar paper/author lookups."""

    def __init__(
        self,
        client: HttpTransport,
        source_config=DEFAULT_SOURCES,
        matching_config=DEFAULT_MATCHING,
        anchor_config=DEFAULT_ANCHORS,
        openalex_api_key: str | None = None,
        semantic_scholar_api_key: str | None = None,
        citation_parser: CitationParser | None = None,
        pdf_parser: DocumentParser | None = None,
        ranker: CitationRanker | None = None,
        listed_sources: Sequence[ListedWorkSource] = (),
        discovery: Discoverer | None = None,
        profile_links: ProfileLinkParser | None = None,
        discovery_config=DEFAULT_DISCOVERY,
    ) -> None:
        self.client = client
        self.discovery = discovery or NoDiscovery()
        self.profile_links = profile_links or ProfileLinkParser()
        self.discovery_config = discovery_config
        self.citation_parser = citation_parser or HtmlCitationParser(anchor_config)
        self.reader = HomepageReader(self.citation_parser, pdf_parser or PdfCitationParser(anchor_config))
        self.ranker = ranker or CitationRanker(anchor_config)
        self.listed_sources = list(listed_sources)
        self.openalex_url = source_config.openalex_url.rstrip('/')
        self.orcid_url = source_config.orcid_api_url.rstrip('/')
        self.semantic_scholar_url = source_config.semantic_scholar_url.rstrip('/')
        self.matching = matching_config
        self.config = anchor_config
        self.openalex_key = openalex_api_key
        self.semantic_scholar_key = semantic_scholar_api_key

    def find(self, query) -> IdentityAnchors:
        links = self.profile_links.parse_all(query.links())
        supplied_orcid = query.orcid or next((link.identifier for link in links if link.source == ORCID_SOURCE), None)
        if supplied_orcid and not query.orcid:
            query = replace(query, orcid=supplied_orcid)
        with phase('anchors: discovery'):
            discovery = self.discovery.discover(query)
        orcid = canonical_orcid(supplied_orcid) if supplied_orcid else discovery.orcid
        anchors = IdentityAnchors(orcid=orcid, discovered=discovery.to_dict())
        anchors.errors.extend(f'discovery {error}' for error in discovery.errors)
        self._profile_anchors(anchors, links)
        if anchors.orcid:
            with phase('anchors: ORCID'):
                self._guard(anchors, self._by_orcid, query, anchors.orcid)
                if self.config.orcid_listed_works:
                    self._guard(anchors, self._orcid_works, query, anchors.orcid)
        hints = paper_hints(list(query.known_papers))
        page_titles: list[str] = []
        supplied = query.pages()
        pages = [PageToRead(url, strict=False) for url in supplied]
        pages += [
            PageToRead(page.url, strict=True, must_name_school=not page.vouched)
            for page in discovery.best_pages(self.discovery_config.maximum_pages_read)
            if page.url not in supplied
        ]
        # Pages and listed sources are fetched side by side, then used in order exactly as if read one by one; a
        # page past the room left is fetched but not used.
        fetches: list[Callable[[], Outcome[Any]]] = [
            partial(attempt, partial(self._read_page, page.url), PAGE_ERRORS) for page in pages
        ]
        fetches += [partial(attempt, partial(source.works, query), PAGE_ERRORS) for source in self.listed_sources]
        with phase('anchors: page reads and listed sources'):
            fetched = in_order(fetches, self.config.lookup_workers, 'anchor-pages')
        room = self.discovery_config.maximum_pages
        for page, outcome in zip(pages, fetched, strict=False):
            if page.strict and room < 1:
                break
            if outcome.error is not None:
                anchors.errors.append(f'page {page.url}: {outcome.error}')
                room -= page.strict
                continue
            read = outcome.unwrap()
            if page.must_name_school and not self._names_school(query, page.url, read.text):
                anchors.discovered['evidence'].append(f'Page {page.url} not used: it does not name the school')
                continue
            room -= page.strict
            citations = self.ranker.rank(read.citations, query.name)
            # Every citation's PDF stays available: a personal page's citations often omit the person's own name
            # ("with A and B"), and a PDF only ever serves a paper its citation names and its content shows.
            pdf_citations = citations
            if page.strict:
                citations = own_citations(citations, query.name, strict=True)
            # The caller's own hints first, then the page's citations that name the person, then the rest.
            hints['doi'] = _merge(hints['doi'], [doi.lower() for item in citations for doi in item.dois])
            hints['arxiv'] = _merge(hints['arxiv'], [arxiv for item in citations for arxiv in item.arxiv_ids])
            page_titles = _merge(page_titles, [title for item in citations for title in item.titles])
            anchors.listed_works = sorted(set(anchors.listed_works) | _listed_works(citations, query.name))
            known = {item['url'] for item in anchors.listed_pdfs}
            new = [item for item in listed_pdfs(pdf_citations, page.url, self.config) if item['url'] not in known]
            anchors.listed_pdfs += new[: max(0, self.config.maximum_listed_pdfs - len(anchors.listed_pdfs))]
        for source, outcome in zip(self.listed_sources, fetched[len(pages) :], strict=True):
            if outcome.error is not None:
                anchors.errors.append(f'{source.name}: {outcome.error}')
            else:
                anchors.listed_works = sorted(set(anchors.listed_works) | outcome.unwrap())
        budget = self.config.maximum_anchor_papers
        dois, arxiv_ids = hints['doi'][:budget], hints['arxiv'][:budget]
        # A page that links no identifiers (titles only) is still usable: exact-title lookups fill the budget left.
        title_budget = min(self.config.maximum_title_anchors, max(0, budget - len(dois) - len(arxiv_ids)))
        titles = hints['title'][:budget] + page_titles[:title_budget]
        with phase('anchors: paper lookups'):
            self._paper_anchors(anchors, query, dois, arxiv_ids, titles)
        anchors.settle_contested(self.config.minimum_contested_works)
        return anchors

    @staticmethod
    def _guard(anchors, lookup, query, value) -> None:
        try:
            lookup(anchors, query, value)
        except SourceError as exc:
            anchors.errors.append(f'{value}: {exc}')

    def _profile_anchors(self, anchors: IdentityAnchors, links: list[ProfileLink]) -> None:
        """A caller-supplied profile link of an author-ID source anchors that ID as given."""
        for link in links:
            if link.source in self.config.profile_anchor_sources:
                anchors.add(link.source, link.identifier, 'profile link ' + link.url, AnchorKind.PROFILE)

    def _names_school(self, query, url: str, text: str) -> bool:
        # Host words count as printed in capitals, so economics.mit.edu names MIT.
        host = (urlsplit(url).hostname or '').replace('.', ' ').upper()
        return school_named([query.university, *query.university_aliases], f'{host}\n{text}', self.matching)

    def _read_page(self, url: str) -> _ReadPage:
        """The page's own text, and its citations plus those of the publication list or CV it links to on the same
        site. A linked document does not speak for the page: a namesake's CV names every school they attended."""
        page, html, served = self._read_citations(url)
        if html is None or not self.discovery_config.follow_publication_links:
            return page
        citations = list(page.citations)
        for link in publication_links(html, served, self.discovery_config)[
            : self.discovery_config.maximum_followed_links
        ]:
            try:
                citations += self._read_citations(link)[0].citations
            except (SourceError, ValueError) as exc:
                logger.debug('Publication link %s unreadable: %s', link, exc)
        return _ReadPage(citations, page.text)

    def _read_citations(self, url: str) -> tuple[_ReadPage, str | None, str]:
        """One page's citations and text, its HTML (None for a PDF) for following, and the URL it was served from."""
        # One limit for both kinds: an HTML page's text and a PDF CV's whole body.
        limit = self.config.homepage_bytes
        response = self.client.probe(url, limit, limit, self.config.maximum_redirects)
        decoded: list[str] = []

        def decode() -> str:
            decoded.append(decode_response(response))
            return decoded[-1]

        citations = self.reader.citations(response.content_type, response.prefix, decode)
        # Relative links resolve against the page as served, after redirects.
        base = getattr(response, 'url', None) or url
        citations = [
            replace(item, pdf_links=tuple(urljoin(base, unescape(link)) for link in item.pdf_links))
            for item in citations
        ]
        html = decoded[-1] if decoded else None
        pdf = is_pdf(response.content_type, response.prefix)
        text = pdf_text(response.prefix) if pdf else html_text(html or decode())
        return _ReadPage(citations, text), html, base

    def _compatible(self, query, name: str | None) -> bool:
        if not name:
            return False
        # Paper bylines often abbreviate given names ("M. Lin"), which plain similarity rejects.
        return name_similarity(
            query.name, name, self.matching
        ) >= self.matching.compatible_name or bibliographic_name_agrees(query.name, name)

    def _by_orcid(self, anchors, query, orcid) -> None:
        data = self.client.get(
            self.openalex_url + '/authors',
            {'filter': 'orcid:' + orcid, 'select': 'id,display_name', 'api_key': self.openalex_key},
        )
        for item in data.get('results') or []:
            anchors.add('openalex', item['id'].rsplit('/', 1)[-1], 'ORCID ' + orcid, AnchorKind.ORCID)

    def _orcid_works(self, anchors, query, orcid) -> None:
        """The ORCID record's works (identifiers and titles) join the person's own list."""
        data = self.client.get(f'{self.orcid_url}/{orcid}/works', None, {'Accept': 'application/json'})
        keys: set[str] = set()
        for group in data.get('group') or []:
            identifiers = (group.get('external-ids') or {}).get('external-id') or []
            dois = [item['external-id-value'] for item in identifiers if item.get('external-id-type') == 'doi']
            arxiv = [item['external-id-value'] for item in identifiers if item.get('external-id-type') == 'arxiv']
            summaries = group.get('work-summary') or [{}]
            title = (((summaries[0] or {}).get('title') or {}).get('title') or {}).get('value')
            keys |= work_keys(dois, arxiv, [] if dois or arxiv else [title] if title else [])
        anchors.listed_works = sorted(set(anchors.listed_works) | keys)

    def _openalex_authorships(self, anchors, query, work, reason, work_key='') -> None:
        """Anchor the work's compatible authors. One whose author record carries another ORCID is only contested
        (see ``IdentityAnchors.settle_contested``), and only when no compatible author agrees with the ORCID."""
        agreeing: list[str] = []
        conflicting: list[str] = []
        for authorship in work.get('authorships') or []:
            author = authorship.get('author') or {}
            names = [author.get('display_name') or '', authorship.get('raw_author_name') or '']
            if not author.get('id') or not any(self._compatible(query, name) for name in names):
                continue
            orcid = canonical_orcid(author.get('orcid')) if author.get('orcid') else None
            target = conflicting if anchors.orcid and orcid and orcid != anchors.orcid else agreeing
            target.append(author['id'].rsplit('/', 1)[-1])
        for author_id in agreeing:
            anchors.add('openalex', author_id, reason, AnchorKind.PAPER, work_key)
        for author_id in conflicting if not agreeing else []:
            anchors.contest('openalex', author_id, reason, work_key)

    def _paper_anchors(self, anchors, query, dois, arxiv_ids, titles) -> None:
        """Anchor the authors of the hinted papers. Every OpenAlex lookup runs side by side and Semantic Scholar
        answers all papers in one batch; the answers are then used in the hints' order, so the anchors, their order
        and the errors are those of one lookup after another."""
        lookups: list[Callable[[], Any]] = [partial(self._openalex_work, doi) for doi in dois]
        lookups += [partial(self._openalex_work, ARXIV_DOI_PREFIX + arxiv.lower()) for arxiv in arxiv_ids]
        lookups += [partial(self._openalex_titles, title) for title in titles]
        found: list[Outcome[Any]] = in_order(
            [partial(attempt, lookup, LOOKUP_ERRORS) for lookup in lookups], self.config.lookup_workers, 'anchor-works'
        )
        by_doi, by_arxiv, by_title = (
            found[: len(dois)],
            found[len(dois) : len(dois) + len(arxiv_ids)],
            found[len(dois) + len(arxiv_ids) :],
        )
        wanted = [f'DOI:{doi}' for doi, work in zip(dois, by_doi, strict=True) if work.error is None]
        wanted += [f'ARXIV:{arxiv}' for arxiv, work in zip(arxiv_ids, by_arxiv, strict=True) if work.error is None]
        for title, works in zip(titles, by_title, strict=True):
            if works.error is None:
                wanted += [f'DOI:{doi}' for _, doi in self._title_matches(title, works.unwrap()) if doi]
        papers = self._semantic_scholar_papers(wanted)
        for doi, work in zip(dois, by_doi, strict=True):
            if work.error is not None:
                anchors.errors.append(f'{doi}: {work.error}')
                continue
            self._openalex_authorships(anchors, query, work.unwrap(), 'author of DOI ' + doi, _key(dois=[doi]))
            self._semantic_scholar_anchors(anchors, query, 'DOI:' + doi, papers)
        for arxiv, work in zip(arxiv_ids, by_arxiv, strict=True):
            if work.error is not None:
                anchors.errors.append(f'{arxiv}: {work.error}')
                continue
            self._openalex_authorships(
                anchors, query, work.unwrap(), 'author of arXiv ' + arxiv, _key(arxiv_ids=[arxiv])
            )
            self._semantic_scholar_anchors(anchors, query, 'ARXIV:' + arxiv, papers)
        for title, works in zip(titles, by_title, strict=True):
            if works.error is not None:
                anchors.errors.append(f'{title}: {works.error}')
                continue
            for match, doi in self._title_matches(title, works.unwrap()):
                self._openalex_authorships(
                    anchors, query, match, f'author of title "{title[:80]}"', _key(titles=[title])
                )
                # Anchor Semantic Scholar as well, as a DOI hint does: once anchors exist, an unanchored ID cannot
                # corroborate the other database, so a one-sided anchor would cost the cross-source evidence.
                if doi:
                    self._semantic_scholar_anchors(anchors, query, 'DOI:' + doi, papers)

    def _openalex_work(self, doi: str) -> dict:
        # OpenAlex indexes arXiv preprints under their DataCite DOI; Semantic Scholar takes the arXiv ID.
        return self.client.get(
            self.openalex_url + '/works/doi:' + quote(doi, safe='/'),
            {'select': 'id,doi,authorships', 'api_key': self.openalex_key},
        )

    def _openalex_titles(self, title: str) -> list[dict]:
        data = self.client.get(
            self.openalex_url + '/works',
            {
                'filter': 'title.search:' + title.replace(',', ' '),
                'per-page': self.config.title_search_results,
                'select': 'id,title,doi,authorships',
                'api_key': self.openalex_key,
            },
        )
        return data.get('results') or []

    @staticmethod
    def _title_matches(title: str, works: list[dict]) -> list[tuple[dict, str | None]]:
        """The search results bearing exactly this title, each with its bare DOI (None without one)."""
        return [
            (work, canonical_doi(work['doi']).removeprefix('doi:') if work.get('doi') else None)
            for work in works
            if normalize_title(work.get('title') or '') == normalize_title(title)
        ]

    def _semantic_scholar_papers(self, paper_ids: list[str]) -> dict[str, Outcome[dict]]:
        """Each paper's Semantic Scholar record, by ID, in batches. A paper the batch does not return, or a batch
        that fails, is asked for on its own, so the answer and any error are those of a single lookup."""
        unique = list(dict.fromkeys(paper_ids))
        size = self.config.semantic_scholar_batch_size
        papers: dict[str, Outcome[dict]] = {}
        for start in range(0, len(unique), size):
            batch = unique[start : start + size]
            try:
                answers = self.client.post_json(
                    self.semantic_scholar_url + '/paper/batch',
                    {'ids': batch},
                    params={'fields': SEMANTIC_SCHOLAR_FIELDS},
                    headers=self._semantic_scholar_headers(),
                    expected_type=list,
                )
            except SourceError as exc:
                logger.info('Semantic Scholar anchor batch failed, asking one paper at a time: %s', exc)
                answers = []
            if len(answers) != len(batch):
                answers = [None] * len(batch)
            for paper_id, answer in zip(batch, answers, strict=True):
                if isinstance(answer, dict) and not answer.get('error'):
                    papers[paper_id] = Outcome(value=answer)
                else:
                    papers[paper_id] = attempt(partial(self._semantic_scholar_paper, paper_id), LOOKUP_ERRORS)
        return papers

    def _semantic_scholar_headers(self) -> dict[str, str] | None:
        return {'x-api-key': self.semantic_scholar_key} if self.semantic_scholar_key else None

    def _semantic_scholar_paper(self, paper_id: str) -> dict:
        return self.client.get(
            '{}/paper/{}'.format(self.semantic_scholar_url, quote(paper_id, safe=':/')),
            {'fields': SEMANTIC_SCHOLAR_FIELDS},
            self._semantic_scholar_headers(),
        )

    def _semantic_scholar_anchors(self, anchors, query, paper_id: str, papers: Mapping[str, Outcome[dict]]) -> None:
        paper = papers[paper_id]
        if paper.error is not None:
            anchors.errors.append(f'{paper_id}: {paper.error}')
            return
        namespace, _, value = paper_id.partition(':')
        work_key = _key(dois=[value]) if namespace == 'DOI' else _key(arxiv_ids=[value])
        for author in paper.unwrap().get('authors') or []:
            if author.get('authorId') and self._compatible(query, author.get('name')):
                anchors.add('semantic_scholar', author['authorId'], 'author of ' + paper_id, AnchorKind.PAPER, work_key)


class RecordedAnchorFinder:
    """Replays the anchors an earlier run found, read from its saved identity, so an offline replay reproduces
    them without fetching homepages or anchor papers again."""

    def __init__(
        self,
        evidence: Iterable[AnchorEvidence],
        listed_works: Iterable[str] = (),
        discovered: Mapping | None = None,
        listed_pdfs: Iterable[Mapping[str, Any]] = (),
    ) -> None:
        self.evidence = [item for item in evidence if item.reason != SUPPLIED_ORCID_REASON]
        self.listed_works = sorted(set(listed_works))
        self.discovered = dict(discovered or {})
        self.listed_pdfs = [dict(item) for item in listed_pdfs]

    @classmethod
    def from_identity(cls, identity: Mapping[str, Any]) -> 'RecordedAnchorFinder':
        profiles = [identity.get('profile') or {}, *(identity.get('candidates') or [])]
        recorded = [item for profile in profiles for item in (profile or {}).get('identity_anchors') or []]
        evidence = [AnchorEvidence.coerce(item) for item in recorded]
        listed = [key for profile in profiles for key in (profile or {}).get('listed_works') or []]
        discovered: Mapping[str, Any] = next(
            (profile['discovered'] for profile in profiles if (profile or {}).get('discovered')), {}
        )
        pdfs: list[Mapping[str, Any]] = next(
            (profile['listed_pdfs'] for profile in profiles if (profile or {}).get('listed_pdfs')), []
        )
        return cls(dict.fromkeys(evidence), listed, discovered, pdfs)

    def find(self, query) -> IdentityAnchors:
        orcid = query.orcid or self.discovered.get('orcid')
        anchors = IdentityAnchors(orcid=canonical_orcid(orcid) if orcid else None, discovered=dict(self.discovered))
        for item in self.evidence:
            anchors.add(item.source, item.author_id, item.reason, item.kind, item.work)
        anchors.listed_works = list(self.listed_works)
        anchors.listed_pdfs = [dict(item) for item in self.listed_pdfs]
        return anchors


class NoAnchors:
    def find(self, query) -> IdentityAnchors:
        return IdentityAnchors()


def publication_links(html: str, base_url: str, config=DEFAULT_DISCOVERY) -> list[str]:
    """Same-site links whose text or path names a publication list or CV, in page order."""
    extractor = LinkExtractor()
    extractor.feed(html)
    extractor.close()
    host = urlsplit(base_url).hostname
    markers = tuple(normalize(marker) for marker in config.publication_markers)
    found = []
    for link in extractor.links:
        url = urljoin(base_url, str(link.get('href') or '')).split('#', 1)[0]
        parts = urlsplit(url)
        label = normalize(f'{link.get("text") or ""} {parts.path}')
        same_site = parts.scheme in ('http', 'https') and parts.hostname == host and url != base_url
        if same_site and any(marker in label.split() or marker in parts.path.lower() for marker in markers):
            found.append(url)
    return list(dict.fromkeys(found))
