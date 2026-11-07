"""Identity anchors: the source author IDs that actually wrote papers known to be the person's, and who finds them."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from paper_author_searcher.identity_discovery import Discovery
from paper_author_searcher.matching import canonical_orcid
from paper_author_searcher.models import AnchorEvidence, AuthorCandidate, ProfessorQuery
from paper_author_searcher.status import AnchorKind

# Added by the resolver itself when a candidate record carries the supplied ORCID; not an anchor-finder result.
SUPPLIED_ORCID_REASON = 'ORCID matches the supplied ORCID'


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

    def holds(self, record: AuthorCandidate) -> bool:
        """Whether this candidate profile is the person's by an anchor."""
        if self.orcid and record.orcid and not self.orcid_outweighed(record.source, str(record.author_id)):
            # The record's own ORCID decides: an anchor that a different ORCID contradicts (a source returning a
            # namesake for an ORCID lookup) must not pull that profile into the person's identity, unless enough of
            # the person's own works outweigh that ORCID (AnchorKind.CONTESTED).
            return canonical_orcid(record.orcid) == self.orcid
        return self.contains(record.source, record.author_id)

    def __bool__(self) -> bool:
        return bool(self.author_ids or self.orcid)


class ListedWorkSource(Protocol):
    """Adds works to the person's own list (``IdentityAnchors.listed_works``), e.g. an OpenReview profile."""

    name: str

    def works(self, query: ProfessorQuery) -> set[str]: ...


class AnchorFinder(Protocol):
    def find(self, query: ProfessorQuery) -> IdentityAnchors: ...


class Discoverer(Protocol):
    def discover(self, query: ProfessorQuery) -> Discovery: ...


class RecordedAnchorFinder:
    """Replays the anchors an earlier run found, read from its saved identity, so an offline replay reproduces
    them without fetching homepages or anchor papers again."""

    def __init__(
        self,
        evidence: Iterable[AnchorEvidence],
        listed_works: Iterable[str] = (),
        discovered: Mapping[str, Any] | None = None,
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

    def find(self, query: ProfessorQuery) -> IdentityAnchors:
        orcid = query.orcid or self.discovered.get('orcid')
        anchors = IdentityAnchors(orcid=canonical_orcid(orcid) if orcid else None, discovered=dict(self.discovered))
        for item in self.evidence:
            anchors.add(item.source, item.author_id, item.reason, item.kind, item.work)
        anchors.listed_works = list(self.listed_works)
        anchors.listed_pdfs = [dict(item) for item in self.listed_pdfs]
        return anchors


class NoAnchors:
    def find(self, query: ProfessorQuery) -> IdentityAnchors:
        return IdentityAnchors()
