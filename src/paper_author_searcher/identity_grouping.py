"""Group candidate author profiles into people: compatible records join, anchors merge, other ORCIDs leave."""

from collections.abc import Iterable, Sequence

from .anchoring.model import IdentityAnchors
from .config import MatchingConfig
from .identity import compare_identity, names_may_match
from .matching import canonical_orcid
from .models import AuthorCandidate

# A contest needs two people.
MINIMUM_CONTEST_SIZE = 2

CandidateGroup = list[AuthorCandidate]


def group_candidates(records: Iterable[AuthorCandidate], config: MatchingConfig) -> list[CandidateGroup]:
    """Each record joins the first group it is compatible with, or starts its own."""
    groups: list[CandidateGroup] = []
    for candidate in records:
        matching_group = None
        for group in groups:
            if can_join(candidate, group, config):
                matching_group = group
                break
        if matching_group is None:
            groups.append([candidate])
        else:
            matching_group.append(candidate)
    return groups


def can_join(candidate: AuthorCandidate, group: Sequence[AuthorCandidate], config: MatchingConfig) -> bool:
    # Complete-link prevents incompatible records merging through a bridge. Split-profile fragments of one
    # person are often only chained (A~B, A~C), so one split link suffices when no member conflicts.
    # A member with incompatible names always blocks the join, so a cheap exact bound rules it out first.
    if not all(names_may_match(candidate, member, config) for member in group):
        return False
    links = [compare_identity(candidate, member, config) for member in group]
    if all(link.matched for link in links):
        return True
    return any(link.split_profile for link in links) and not any(link.conflict for link in links)


def merge_anchored(groups: list[CandidateGroup], anchors: IdentityAnchors) -> list[CandidateGroup]:
    """Every group holding an anchored profile belongs to the requested person."""
    if not anchors:
        return groups
    anchored = [group for group in groups if any(anchors.holds(record) for record in group)]
    if len(anchored) < MINIMUM_CONTEST_SIZE:
        return groups
    merged = [record for group in anchored for record in group]
    return [merged] + [group for group in groups if group not in anchored]


def separate_orcid_conflicts(groups: list[CandidateGroup], anchors: IdentityAnchors) -> list[CandidateGroup]:
    """A group holding a record confirmed by the person's ORCID (carrying it, or outweighing another) is theirs;
    members carrying another ORCID are namesakes that shared DOIs or coauthors pulled in, and leave it, one group
    per ORCID. A group without a confirmed record keeps its members, and its conflict counts as before."""
    if not anchors.orcid:
        return groups

    def foreign(record: AuthorCandidate) -> bool:
        orcid = canonical_orcid(record.orcid) if record.orcid else None
        return bool(orcid and orcid != anchors.orcid) and not anchors.orcid_outweighed(
            record.source, str(record.author_id)
        )

    def confirmed(record: AuthorCandidate) -> bool:
        orcid = canonical_orcid(record.orcid) if record.orcid else None
        return orcid == anchors.orcid or anchors.orcid_outweighed(record.source, str(record.author_id))

    separated = []
    for group in groups:
        outsiders = [record for record in group if foreign(record)]
        if not outsiders or not any(confirmed(record) for record in group):
            separated.append(group)
            continue
        separated.append([record for record in group if not foreign(record)])
        by_orcid: dict[str, CandidateGroup] = {}
        for record in outsiders:
            by_orcid.setdefault(str(canonical_orcid(record.orcid)), []).append(record)
        separated.extend(by_orcid.values())
    return separated
