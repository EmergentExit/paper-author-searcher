"""Paper ownership: whether each collected paper is the person's.

``record_facts`` answers what one record shows about the identity, ``record_decision`` makes each record's first
decision from that record alone, and ``passes`` holds the paper-level rules that revisit those decisions with
evidence across records. ``paper_ownership.PaperOwnershipValidator`` runs them in order.
"""

from .passes import OwnershipContext, OwnershipPass, aggregate, default_passes
from .record_decision import RecordDecision
from .record_facts import RecordFacts

__all__ = ['OwnershipContext', 'OwnershipPass', 'RecordDecision', 'RecordFacts', 'aggregate', 'default_passes']
