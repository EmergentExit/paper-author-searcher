"""PDF locations already in the collected metadata (OpenAlex records the search fetched)."""

from collections.abc import Callable

from paper_author_searcher.config import DEFAULT_PDF, PDFConfig
from paper_author_searcher.http import SourceError
from paper_author_searcher.json_types import JSONObject
from paper_author_searcher.models import PDFProviderResult
from paper_author_searcher.pdf_identity import source_records
from paper_author_searcher.source_traits import OPENALEX, SEMANTIC_SCHOLAR
from paper_author_searcher.status import ProviderStatus

from .openalex import openalex_candidates, openalex_landing_pages
from .semantic_scholar import semantic_candidates


def _openalex_locations(
    provider: 'ExistingPDFProvider', result: PDFProviderResult, paper: JSONObject, metadata: JSONObject
) -> None:
    result.candidates.extend(openalex_candidates(paper, metadata, provider.name, config=provider.config))
    result.landing_pages.extend(openalex_landing_pages(metadata))


def _semantic_scholar_locations(
    provider: 'ExistingPDFProvider', result: PDFProviderResult, paper: JSONObject, metadata: JSONObject
) -> None:
    result.candidates.extend(semantic_candidates(paper, metadata, provider.name, config=provider.config))


# Each source that saves PDF locations in its records, and how to read them.
LOCATION_READERS: dict[str, Callable[..., None]] = {
    OPENALEX: _openalex_locations,
    SEMANTIC_SCHOLAR: _semantic_scholar_locations,
}


class ExistingPDFProvider:
    name = 'existing_metadata'

    def __init__(self, config: PDFConfig = DEFAULT_PDF) -> None:
        self.config = config

    def find(self, paper: JSONObject) -> PDFProviderResult:
        result = PDFProviderResult(self.name)
        for record in source_records(paper):
            metadata = record.get('raw_metadata') or {}
            reader = LOCATION_READERS.get(record.get('source') or '')
            if reader is None:
                continue
            try:
                reader(self, result, paper, metadata)
            except (SourceError, ValueError, TypeError) as exc:
                result.errors.append(str(exc))
        if result.errors:
            result.status = ProviderStatus.PARTIAL
        return result
