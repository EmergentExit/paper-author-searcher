"""Preprint and working-paper repositories: arXiv and NBER."""

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable

from paper_author_searcher.collection.arxiv_api import ArxivApi, Entries
from paper_author_searcher.concurrency import Outcome
from paper_author_searcher.config import DEFAULT_PDF, DEFAULT_SOURCES, PDFConfig, SourceConfig
from paper_author_searcher.fulltext.pdf_identity import (
    arxiv_id,
    arxiv_ids,
    metadata_identity,
    paper_dois,
    related_dois,
    same_arxiv_version,
    source_records,
)
from paper_author_searcher.json_types import JSONList, JSONObject
from paper_author_searcher.matching import normalize_title
from paper_author_searcher.models import PDFCandidate, PDFProviderResult
from paper_author_searcher.status import IdentityStatus, ProviderStatus
from paper_author_searcher.transport.http import HttpTransport, SourceError

from .base import MetadataPDFProvider, make_candidate


class NBERPDFProvider(MetadataPDFProvider):
    """NBER working-paper PDFs, named by the NBER DOI of the paper or of an edition related to it.

    Deduplication relates an edition only on the same normalized title and compatible authors, so the working
    paper of a later journal article is bound to it; the downloaded file must still show the title.
    """

    name = 'nber'

    def __init__(self, client: HttpTransport, config: PDFConfig = DEFAULT_PDF) -> None:
        super().__init__(client)
        self.config = config

    def lookups(self, paper: JSONObject) -> list[str]:
        own = [doi[len('doi:') :] for doi in sorted(paper_dois(paper))]
        numbers = [self._number(doi) for doi in [*own, *related_dois(paper)]]
        return list(dict.fromkeys(number for number in numbers if number))

    def lookup(self, paper: JSONObject, identifier: str) -> tuple[list[PDFCandidate], JSONList]:
        own = {self._number(doi[len('doi:') :]) for doi in paper_dois(paper)}
        if identifier in own:
            identity = (IdentityStatus.VERIFIED, ['Exact DOI agreement with saved paper metadata'])
        else:
            identity = (
                IdentityStatus.VERIFIED,
                ['NBER DOI of an edition deduplication related to this paper (same normalized title and authors)'],
            )
        url = self.config.nber_pdf_url_template.format(number=identifier)
        provenance = {'doi': '10.3386/' + identifier, 'source_paper_id': identifier}
        candidate = make_candidate(
            url, self.name, identity, provenance, 'NBER working papers are free to download', 'submittedVersion'
        )
        return [candidate], []

    def _number(self, doi: str) -> str | None:
        match = re.match(self.config.nber_doi_pattern, doi or '', flags=re.IGNORECASE)
        return match.group(1).lower() if match else None


class ArxivPDFProvider:
    """PDF links from the arXiv API, for papers with arXiv identifiers (or, optionally, an exact title).

    ``prepare`` asks for every paper's entries in a few batched requests (collection.arxiv_api) before the papers
    are looked up one by one; a paper ``prepare`` did not cover is asked for alone, exactly as before.
    """

    name = 'arxiv'
    atom = '{http://www.w3.org/2005/Atom}'
    arxiv = '{http://arxiv.org/schemas/atom}'

    def __init__(
        self, client: HttpTransport, config: PDFConfig = DEFAULT_PDF, sources: SourceConfig = DEFAULT_SOURCES
    ) -> None:
        self.client = client
        self.config = config
        self.api = ArxivApi(client, config.arxiv_url, sources)
        self._by_id: dict[str, Outcome[Entries]] = {}
        self._by_title: dict[str, Outcome[Entries]] = {}

    def prepare(self, papers: Iterable[JSONObject]) -> None:
        identifiers: list[str] = []
        titles: list[str] = []
        for paper in papers:
            ids = arxiv_ids(paper)
            identifiers += ids
            if self._searches(paper, ids):
                titles.append(paper['title'])
        self._by_id = self.api.by_ids(identifiers) if identifiers else {}
        self._by_title = (
            self.api.by_titles(titles, self.config.arxiv_search_results, self._same_title) if titles else {}
        )

    def find(self, paper: JSONObject) -> PDFProviderResult:
        identifiers = arxiv_ids(paper)
        search = self._searches(paper, identifiers)
        if not identifiers and not search:
            return PDFProviderResult(self.name, status=ProviderStatus.NOT_APPLICABLE)
        result = PDFProviderResult(self.name)
        try:
            entries = self._title_entries(paper['title']) if search else self._id_entries(identifiers)
            if not entries and not search:
                raise SourceError('arXiv returned no matching entries')
            for entry in entries:
                result.candidates.extend(self._entry_candidates(paper, identifiers, entry, search))
        except (SourceError, ValueError, TypeError) as exc:
            result.status = ProviderStatus.ERROR
            result.errors.append(str(exc))
        return result

    def _searches(self, paper: JSONObject, identifiers: list[str]) -> bool:
        return not identifiers and self.config.arxiv_title_search and bool(paper.get('title'))

    def _id_entries(self, identifiers: list[str]) -> Entries:
        if not all(identifier in self._by_id for identifier in identifiers):
            return self.api.entries_for(identifiers)
        entries: Entries = []
        for identifier in identifiers:
            entries += [entry for entry in self._by_id[identifier].unwrap() if entry not in entries]
        return entries

    def _title_entries(self, title: str) -> Entries:
        found = self._by_title.get(title)
        if found is None:
            found = self.api.by_titles([title], self.config.arxiv_search_results, self._same_title)[title]
        return found.unwrap()

    @staticmethod
    def _same_title(found: str, wanted: str) -> bool:
        return normalize_title(found) == normalize_title(wanted)

    def _entry_candidates(
        self, paper: JSONObject, requested: list[str], entry: ET.Element, search: bool = False
    ) -> list[PDFCandidate]:
        identifier = arxiv_id(entry.findtext(self.atom + 'id'))
        if not identifier or (not search and not any(same_arxiv_version(value, identifier) for value in requested)):
            raise SourceError('arXiv entry does not match the requested identifier/version')
        title = ' '.join((entry.findtext(self.atom + 'title') or '').split())
        doi = entry.findtext(self.arxiv + 'doi')
        # A supplied arXiv ID identifies a version even when the journal DOI is absent.
        bound_paper = dict(
            paper,
            records=[*list(source_records(paper)), {'source': 'arxiv', 'source_paper_id': identifier}],
        )
        published = entry.findtext(self.atom + 'published') or ''
        authors = [author.findtext(self.atom + 'name') or '' for author in entry.findall(self.atom + 'author')]
        identity = metadata_identity(
            paper if search else bound_paper,
            self.name,
            identifier,
            doi,
            title,
            published[:4],
            authors,
            config=self.config,
        )
        candidates = []
        for link in entry.findall(self.atom + 'link'):
            if link.get('type') != 'application/pdf' or not link.get('href'):
                continue
            candidate = make_candidate(
                link.get('href'),
                self.name,
                identity,
                {
                    'source_paper_id': identifier,
                    'metadata_url': self.config.arxiv_url,
                    'doi': doi,
                    'metadata_title': title,
                    'metadata_year': published[:4],
                    'metadata_authors': authors,
                    'requested_ids': requested,
                },
                'arXiv API PDF link',
            )
            candidate.version_type = 'preprint'
            candidate.provenance[0]['version_type'] = 'preprint'
            candidates.append(candidate)
        return candidates
