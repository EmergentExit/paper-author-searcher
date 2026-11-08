"""Which ORCID an identity carries when its merged profiles disagree.

A person's profiles in several databases are merged into one identity, and a database can file a namesake's ORCID on
one of them (OpenAlex put another Xiang Zhou's ORCID on a fragment of the sociologist's profiles). The identity's
ORCID then drives ownership: a paper whose byline ORCID agrees is verified, one whose ORCID differs is rejected, and
the person's career is read from that ORCID's record. Taking whichever profile happened to come first can give the
identity a stranger's ORCID, so when the profiles disagree the ORCID is chosen by evidence:

1. The ORCID the caller supplied or discovery found (``IdentityAnchors.orcid``).
2. Otherwise the ORCID of the profiles the person's own papers anchor most often (``IdentityAnchors.evidence_for``).
3. Otherwise (a tie) none: no stranger's ORCID can reject the person's papers, and the identity says why.

With one ORCID among the profiles, or none, the identity carries it as before.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from paper_author_searcher.matching import canonical_orcid
from paper_author_searcher.models import AuthorCandidate

from .anchoring.model import IdentityAnchors


@dataclass(frozen=True)
class OrcidChoice:
    orcid: str | None
    # Why the identity carries this ORCID (or none) when its profiles disagreed; None when they did not.
    evidence: str | None = None


def choose_orcid(group: Sequence[AuthorCandidate], anchors: IdentityAnchors | None = None) -> OrcidChoice:
    held = [canonical_orcid(record.orcid) for record in group if record.orcid]
    distinct = list(dict.fromkeys(orcid for orcid in held if orcid))
    if len(distinct) <= 1:
        return OrcidChoice(distinct[0] if distinct else None)
    anchors = anchors or IdentityAnchors()
    if anchors.orcid:
        return OrcidChoice(anchors.orcid, f'ORCID {anchors.orcid}: the supplied or discovered ORCID; profiles disagree')
    support: Counter[str] = Counter({orcid: 0 for orcid in distinct})
    for record in group:
        orcid = canonical_orcid(record.orcid) if record.orcid else None
        if orcid:
            support[orcid] += len(anchors.evidence_for(record.source, str(record.author_id)))
    ranked = support.most_common()
    if ranked[0][1] > ranked[1][1]:
        orcid, count = ranked[0]
        return OrcidChoice(orcid, f'ORCID {orcid}: its profiles hold {count} of the anchors; profiles disagree')
    return OrcidChoice(None, 'No ORCID used: the merged profiles carry different ORCIDs and no evidence decides')
