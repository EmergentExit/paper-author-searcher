"""Identity anchors: the source author IDs that actually wrote papers known to be the person's.

An anchor is independent of the name search, so it separates namesakes and ties split profiles together.
``model`` holds the anchors and the finder interfaces, ``pages`` reads the person's own pages, ``paper_authors``
turns known papers (and the ORCID record) into author IDs, and ``finder.ScholarlyAnchorFinder`` runs them in order.
"""

from .finder import ScholarlyAnchorFinder
from .model import (
    SUPPLIED_ORCID_REASON,
    AnchorFinder,
    Discoverer,
    IdentityAnchors,
    ListedWorkSource,
    NoAnchors,
    RecordedAnchorFinder,
)

__all__ = [
    'SUPPLIED_ORCID_REASON',
    'AnchorFinder',
    'Discoverer',
    'IdentityAnchors',
    'ListedWorkSource',
    'NoAnchors',
    'RecordedAnchorFinder',
    'ScholarlyAnchorFinder',
]
