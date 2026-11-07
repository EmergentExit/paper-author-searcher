"""Turn caller hints into author-ID anchors (see the ``anchoring`` package); this module keeps the old import path."""

from .anchoring.finder import PAGE_ERRORS, ScholarlyAnchorFinder
from .anchoring.model import (
    SUPPLIED_ORCID_REASON,
    AnchorFinder,
    Discoverer,
    IdentityAnchors,
    ListedWorkSource,
    NoAnchors,
    RecordedAnchorFinder,
)
from .anchoring.pages import (
    ARXIV_HINT,
    PageToRead,
    homepage_papers,
    listed_pdfs,
    own_citations,
    paper_hints,
    publication_links,
)
from .anchoring.paper_authors import LOOKUP_ERRORS, SEMANTIC_SCHOLAR_FIELDS

__all__ = [
    'ARXIV_HINT',
    'LOOKUP_ERRORS',
    'PAGE_ERRORS',
    'SEMANTIC_SCHOLAR_FIELDS',
    'SUPPLIED_ORCID_REASON',
    'AnchorFinder',
    'Discoverer',
    'IdentityAnchors',
    'ListedWorkSource',
    'NoAnchors',
    'PageToRead',
    'RecordedAnchorFinder',
    'ScholarlyAnchorFinder',
    'homepage_papers',
    'listed_pdfs',
    'own_citations',
    'paper_hints',
    'publication_links',
]
