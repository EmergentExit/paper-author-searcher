"""Identity anchors: the source author IDs that actually wrote papers known to be the person's.

An anchor is independent of the name search, so it separates namesakes and ties split profiles together.
``model`` holds the anchors and the finder interfaces, ``pages`` reads the person's own pages, ``paper_authors``
turns known papers (and the ORCID record) into author IDs, and ``finder.ScholarlyAnchorFinder`` runs them in order.
"""

from .finder import PAGE_ERRORS, ScholarlyAnchorFinder
from .model import (
    SUPPLIED_ORCID_REASON,
    AnchorFinder,
    Discoverer,
    IdentityAnchors,
    ListedWorkSource,
    NoAnchors,
    RecordedAnchorFinder,
)
from .pages import (
    ARXIV_HINT,
    PageToRead,
    homepage_papers,
    listed_pdfs,
    own_citations,
    paper_hints,
    publication_links,
)
from .paper_authors import LOOKUP_ERRORS, SEMANTIC_SCHOLAR_FIELDS

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
