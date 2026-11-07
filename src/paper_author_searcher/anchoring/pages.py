"""The person's own pages: the citations they list, and the paper hints those citations give."""

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlsplit

from paper_author_searcher.config import (
    DEFAULT_ANCHORS,
    DEFAULT_DISCOVERY,
    DEFAULT_MATCHING,
    AnchorConfig,
    DiscoveryConfig,
    MatchingConfig,
)
from paper_author_searcher.homepage_citations import (
    ARXIV_IN_TEXT,
    DOI_IN_TEXT,
    HomepageCitation,
    HomepageReader,
    is_pdf,
    mentions,
    pdf_text,
)
from paper_author_searcher.html_support import LinkExtractor, decode_response
from paper_author_searcher.http import HttpTransport, SourceError
from paper_author_searcher.matching import normalize, school_named, work_keys
from paper_author_searcher.models import ProfessorQuery
from paper_author_searcher.publication_extraction import html_text

logger = logging.getLogger(__name__)

ARXIV_HINT = re.compile(r'^(?:arxiv:)?(\d{4}\.\d{4,5})(?:v\d+)?$', re.IGNORECASE)


@dataclass(frozen=True)
class PageToRead:
    url: str
    # A page the caller supplied may be a personal page listing only the person's works; on a discovered page (and
    # on a lab page) only the citations that name the person count.
    strict: bool
    # A search hit may be a namesake's page: without the requested school in its text it is not read for papers.
    must_name_school: bool = False


@dataclass(frozen=True)
class ReadPage:
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


def listed_works(citations: list[HomepageCitation], name: str) -> set[str]:
    """Work keys of the citations that are the person's own."""
    own = own_citations(citations, name)
    keys: set[str] = set()
    for citation in own:
        keys |= work_keys(citation.dois, citation.arxiv_ids, citation.titles)
    return keys


def listed_pdfs(
    citations: list[HomepageCitation], page: str, config: AnchorConfig = DEFAULT_ANCHORS
) -> list[dict[str, Any]]:
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


def publication_links(html: str, base_url: str, config: DiscoveryConfig = DEFAULT_DISCOVERY) -> list[str]:
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


def merged(*lists: Iterable[str]) -> list[str]:
    """The values of every list, first occurrence first."""
    return list(dict.fromkeys(value for values in lists for value in values))


class PageReader:
    """Reads one of the person's pages: its text, and its citations plus those of the publication list or CV it
    links to on the same site."""

    def __init__(
        self,
        client: HttpTransport,
        reader: HomepageReader,
        anchor_config: AnchorConfig = DEFAULT_ANCHORS,
        discovery_config: DiscoveryConfig = DEFAULT_DISCOVERY,
        matching_config: MatchingConfig = DEFAULT_MATCHING,
    ) -> None:
        self.client = client
        self.reader = reader
        self.config = anchor_config
        self.discovery_config = discovery_config
        self.matching = matching_config

    def read(self, url: str) -> ReadPage:
        """A linked document does not speak for the page: a namesake's CV names every school they attended, so the
        page's text is its own."""
        page, html, served = self.read_citations(url)
        if html is None or not self.discovery_config.follow_publication_links:
            return page
        citations = list(page.citations)
        for link in publication_links(html, served, self.discovery_config)[
            : self.discovery_config.maximum_followed_links
        ]:
            try:
                citations += self.read_citations(link)[0].citations
            except (SourceError, ValueError) as exc:
                logger.debug('Publication link %s unreadable: %s', link, exc)
        return ReadPage(citations, page.text)

    def read_citations(self, url: str) -> tuple[ReadPage, str | None, str]:
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
        return ReadPage(citations, text), html, base

    def names_school(self, query: ProfessorQuery, url: str, text: str) -> bool:
        # Host words count as printed in capitals, so economics.mit.edu names MIT.
        host = (urlsplit(url).hostname or '').replace('.', ' ').upper()
        return school_named([query.university, *query.university_aliases], f'{host}\n{text}', self.matching)
