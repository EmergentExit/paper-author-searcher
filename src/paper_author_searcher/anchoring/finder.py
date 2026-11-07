"""Turn caller hints into author-ID anchors through the person's pages and the authors of their known papers."""

from collections.abc import Callable, Sequence
from dataclasses import replace
from functools import partial
from typing import Any

from paper_author_searcher.concurrency import Outcome, attempt, in_order
from paper_author_searcher.config import (
    DEFAULT_ANCHORS,
    DEFAULT_DISCOVERY,
    DEFAULT_MATCHING,
    DEFAULT_SOURCES,
    AnchorConfig,
    DiscoveryConfig,
    MatchingConfig,
    SourceConfig,
)
from paper_author_searcher.homepage_citations import (
    CitationParser,
    CitationRanker,
    DocumentParser,
    HomepageReader,
    HtmlCitationParser,
    PdfCitationParser,
)
from paper_author_searcher.http import HttpTransport, SourceError
from paper_author_searcher.identity_discovery import NoDiscovery
from paper_author_searcher.matching import canonical_orcid
from paper_author_searcher.models import ProfessorQuery
from paper_author_searcher.profile_links import ORCID_SOURCE, ProfileLink, ProfileLinkParser
from paper_author_searcher.status import AnchorKind
from paper_author_searcher.timings import phase

from .model import Discoverer, IdentityAnchors, ListedWorkSource
from .pages import PageReader, PageToRead, listed_pdfs, listed_works, merged, own_citations, paper_hints
from .paper_authors import PaperAuthorAnchors

# Errors a page read or listed source may raise that leave the rest of the search usable.
PAGE_ERRORS = (SourceError, ValueError)

# An ORCID step: (anchors, query, ORCID) -> None.
OrcidStep = Callable[[IdentityAnchors, ProfessorQuery, str], None]


class ScholarlyAnchorFinder:
    """Resolve hints through the person's pages and OpenAlex / Semantic Scholar paper and author lookups."""

    def __init__(
        self,
        client: HttpTransport,
        source_config: SourceConfig = DEFAULT_SOURCES,
        matching_config: MatchingConfig = DEFAULT_MATCHING,
        anchor_config: AnchorConfig = DEFAULT_ANCHORS,
        openalex_api_key: str | None = None,
        semantic_scholar_api_key: str | None = None,
        citation_parser: CitationParser | None = None,
        pdf_parser: DocumentParser | None = None,
        ranker: CitationRanker | None = None,
        listed_sources: Sequence[ListedWorkSource] = (),
        discovery: Discoverer | None = None,
        profile_links: ProfileLinkParser | None = None,
        discovery_config: DiscoveryConfig = DEFAULT_DISCOVERY,
    ) -> None:
        self.client = client
        self.discovery = discovery or NoDiscovery()
        self.profile_links = profile_links or ProfileLinkParser()
        self.discovery_config = discovery_config
        self.citation_parser = citation_parser or HtmlCitationParser(anchor_config)
        reader = HomepageReader(self.citation_parser, pdf_parser or PdfCitationParser(anchor_config))
        self.pages = PageReader(client, reader, anchor_config, discovery_config, matching_config)
        self.papers = PaperAuthorAnchors(
            client, source_config, matching_config, anchor_config, openalex_api_key, semantic_scholar_api_key
        )
        self.ranker = ranker or CitationRanker(anchor_config)
        self.listed_sources = list(listed_sources)
        self.config = anchor_config

    def find(self, query: ProfessorQuery) -> IdentityAnchors:
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
                self._guard(anchors, self.papers.by_orcid, query, anchors.orcid)
                if self.config.orcid_listed_works:
                    self._guard(anchors, self.papers.orcid_works, query, anchors.orcid)
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
            partial(attempt, partial(self.pages.read, page.url), PAGE_ERRORS) for page in pages
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
            if page.must_name_school and not self.pages.names_school(query, page.url, read.text):
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
            hints['doi'] = merged(hints['doi'], [doi.lower() for item in citations for doi in item.dois])
            hints['arxiv'] = merged(hints['arxiv'], [arxiv for item in citations for arxiv in item.arxiv_ids])
            page_titles = merged(page_titles, [title for item in citations for title in item.titles])
            anchors.listed_works = sorted(set(anchors.listed_works) | listed_works(citations, query.name))
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
            self.papers.anchor(anchors, query, dois, arxiv_ids, titles)
        anchors.settle_contested(self.config.minimum_contested_works)
        return anchors

    @staticmethod
    def _guard(anchors: IdentityAnchors, step: OrcidStep, query: ProfessorQuery, value: str) -> None:
        try:
            step(anchors, query, value)
        except SourceError as exc:
            anchors.errors.append(f'{value}: {exc}')

    def _profile_anchors(self, anchors: IdentityAnchors, links: list[ProfileLink]) -> None:
        """A caller-supplied profile link of an author-ID source anchors that ID as given."""
        for link in links:
            if link.source in self.config.profile_anchor_sources:
                anchors.add(link.source, link.identifier, 'profile link ' + link.url, AnchorKind.PROFILE)
