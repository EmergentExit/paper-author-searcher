"""Conservative, source-scoped ownership decisions with traceable evidence.

Each record first gets a decision from what it alone shows (``ownership.record_decision``); the paper-level rules
(``ownership.passes``) then revisit those decisions with evidence across records, in a fixed order.
"""

from collections.abc import Sequence

from .authorship_places import AuthorshipPlaces
from .config import DEFAULT_OWNERSHIP, OwnershipConfig
from .models import Paper, ProfessorProfile
from .name_rarity import NameRarity
from .ownership import OwnershipContext, OwnershipPass, RecordDecision, RecordFacts, aggregate, default_passes
from .ownership.passes import (
    CURATED_VENUE_EVIDENCE,
    NAMESAKE_GROUP_NOTE,
    RARE_NAME_EVIDENCE,
    UNPLACED_GROUP_NOTE,
    UNPLACED_PAPER_NOTE,
)
from .ownership.record_decision import FOREIGN_FRAGMENT
from .ownership.record_facts import LISTED_CLAIM, abbreviated, describe_position, record_fields
from .precision_guards import DepartmentMatcher, GuardContext, PrecisionGuard
from .source_traits import OPENALEX

# The source whose name statistics decide whether a name is rare.
RARITY_SOURCE = OPENALEX

__all__ = [
    'CURATED_VENUE_EVIDENCE',
    'FOREIGN_FRAGMENT',
    'LISTED_CLAIM',
    'NAMESAKE_GROUP_NOTE',
    'RARE_NAME_EVIDENCE',
    'RARITY_SOURCE',
    'UNPLACED_GROUP_NOTE',
    'UNPLACED_PAPER_NOTE',
    'PaperOwnershipValidator',
    'abbreviated',
    'aggregate',
    'describe_position',
    'record_fields',
]


class PaperOwnershipValidator:
    """Decides every record of every paper for one identity per ``validate`` call; holds no per-identity state."""

    def __init__(
        self,
        config: OwnershipConfig = DEFAULT_OWNERSHIP,
        guard: PrecisionGuard | None = None,
        name_rarity: NameRarity | None = None,
        places: AuthorshipPlaces | None = None,
        passes: Sequence[OwnershipPass] | None = None,
    ) -> None:
        self.config = config
        # Optional stricter evidence for common names (precision_guards); without it every rule is the ordinary one.
        self.guard = guard
        self.name_rarity = name_rarity
        # Optional: where a source places the person, to tell a merged namesake's papers apart.
        self.places = places
        self.departments = guard.departments if guard is not None else DepartmentMatcher()
        self.decision = RecordDecision(config)
        self.passes = list(passes) if passes is not None else default_passes(config, places)

    def facts(self, profile: ProfessorProfile, guard: GuardContext | None = None) -> RecordFacts:
        """The evidence queries for one identity (outside ``validate``, without the precision guard's context)."""
        return RecordFacts(self.config, profile, guard, self.departments)

    def validate(self, papers: Sequence[Paper], profile: ProfessorProfile) -> Sequence[Paper]:
        guard = self.guard.prepare(papers, profile) if self.guard else GuardContext()
        facts = self.facts(profile, guard)
        own_ids = facts.selected_ids(RARITY_SOURCE)
        rare_name = bool(self.name_rarity and own_ids and self.name_rarity.rare(profile.name, own_ids))
        context = OwnershipContext(facts, rare_name)
        for paper in papers:
            for record in paper.records:
                record.ownership_status, record.ownership_evidence = self.decision.evaluate(record, facts)
        for rule in self.passes:
            rule.apply(papers, context)
        return papers
