"""The decisions a benchmark run made, and whether another run made exactly the same ones.

A refactor or a speed change must not move any decision. ``snapshot`` keeps, per person, the run status, the
identity the system settled on and every collected paper's ownership status; ``compare`` lists every difference
between two snapshots. An offline replay of the same recorded run with the old and the new code should match
exactly; any difference is a behaviour change to score and explain before merging.

A paper is keyed by the source records merged into it, so a change in deduplication also shows up as a difference.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from paper_author_searcher.jsonio import read_json
from paper_author_searcher.status import OwnershipStatus

from .runner import INDEX

SNAPSHOT_TYPE = 'benchmark_decisions'
SNAPSHOT_VERSION = 1
# How many changed papers a markdown report lists per person; the JSON keeps all of them.
LISTED_PAPERS = 10


def paper_key(paper: Mapping[str, Any]) -> str:
    """The merged source records (``source:paper_id``), sorted; a paper with no records falls back to its own."""
    records = paper.get('records') or [paper]
    return '|'.join(sorted({f'{record.get("source")}:{record.get("source_paper_id")}' for record in records}))


def _identity(report: Mapping[str, Any], archive: Mapping[str, Any]) -> dict[str, Any]:
    collected = archive.get('collection_identity')
    if collected:
        return {
            'status': collected.get('status'),
            'confirmation': collected.get('confirmation'),
            'author_ids': {source: sorted(ids) for source, ids in sorted((collected.get('author_ids') or {}).items())},
        }
    profile = (report.get('identity') or {}).get('profile') or {}
    return {
        'status': profile.get('status'),
        'confirmation': None,
        'author_ids': dict(sorted((profile.get('author_ids') or {}).items())),
    }


def person_decisions(run_root: Path, entry: Mapping[str, Any]) -> dict[str, Any]:
    decisions: dict[str, Any] = {'status': entry.get('status'), 'identity': None, 'papers': {}}
    if not entry.get('report'):
        return decisions
    report_dir = run_root / entry['report']
    report = read_json(report_dir / 'report.json')
    papers_file = (report.get('artifacts') or {}).get('papers')
    archive = read_json(report_dir / papers_file) if papers_file else {}
    decisions['identity'] = _identity(report, archive)
    papers = (archive.get('collection') or {}).get('papers') or []
    decisions['papers'] = dict(sorted((paper_key(paper), paper.get('ownership_status')) for paper in papers))
    return decisions


def snapshot(runs_root: Path, run_id: str) -> dict[str, Any]:
    run_root = runs_root / run_id
    index = read_json(run_root / INDEX)
    return {
        'snapshot_type': SNAPSHOT_TYPE,
        'snapshot_version': SNAPSHOT_VERSION,
        'run_id': run_id,
        'dataset_id': index.get('dataset_id'),
        'code_version': (index.get('metadata') or {}).get('code_version'),
        'people': {
            person: person_decisions(run_root, entry) for person, entry in sorted(index.get('people', {}).items())
        },
    }


def load_snapshot(runs_root: Path, reference: str) -> dict[str, Any]:
    """A saved snapshot file, or a run ID to snapshot now."""
    path = Path(reference)
    if path.suffix == '.json' and path.is_file():
        saved = read_json(path)
        if saved.get('snapshot_type') != SNAPSHOT_TYPE:
            raise ValueError(f'{reference} is not a benchmark decisions snapshot')
        return saved
    return snapshot(runs_root, reference)


@dataclass
class PersonDifference:
    person_id: str
    status: tuple[Any, Any] | None = None
    identity: tuple[Any, Any] | None = None
    verified_gained: list[str] = field(default_factory=list)
    verified_lost: list[str] = field(default_factory=list)
    # Changes among the other statuses (needs_review, rejected) and papers that appeared or disappeared.
    other_changes: dict[str, tuple[Any, Any]] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.status or self.identity or self.verified_gained or self.verified_lost or self.other_changes)

    def to_dict(self) -> dict[str, Any]:
        return {
            'person_id': self.person_id,
            'status': list(self.status) if self.status else None,
            'identity': list(self.identity) if self.identity else None,
            'verified_gained': self.verified_gained,
            'verified_lost': self.verified_lost,
            'other_changes': {key: list(change) for key, change in self.other_changes.items()},
        }


def _compare_person(person: str, before: Mapping[str, Any], after: Mapping[str, Any]) -> PersonDifference:
    difference = PersonDifference(person)
    if before.get('status') != after.get('status'):
        difference.status = (before.get('status'), after.get('status'))
    if before.get('identity') != after.get('identity'):
        difference.identity = (before.get('identity'), after.get('identity'))
    old, new = before.get('papers') or {}, after.get('papers') or {}
    verified = OwnershipStatus.VERIFIED
    for key in sorted(set(old) | set(new)):
        was, now = old.get(key), new.get(key)
        if was == now:
            continue
        if now == verified:
            difference.verified_gained.append(key)
        elif was == verified:
            difference.verified_lost.append(key)
        else:
            difference.other_changes[key] = (was, now)
    return difference


def compare(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> list[PersonDifference]:
    """Every person whose decisions differ; a person present in only one snapshot differs from an empty record."""
    empty: dict[str, Any] = {'status': None, 'identity': None, 'papers': {}}
    old, new = baseline.get('people') or {}, candidate.get('people') or {}
    people = sorted(set(old) | set(new))
    differences = (_compare_person(person, old.get(person, empty), new.get(person, empty)) for person in people)
    return [difference for difference in differences if difference]


def _listed(keys: Iterable[str]) -> str:
    keys = list(keys)
    shown = ', '.join(f'`{key}`' for key in keys[:LISTED_PAPERS])
    return shown + (f' … (+{len(keys) - LISTED_PAPERS})' if len(keys) > LISTED_PAPERS else '')


def markdown(baseline: Mapping[str, Any], candidate: Mapping[str, Any], differences: list[PersonDifference]) -> str:
    people = len(set(baseline.get('people') or {}) | set(candidate.get('people') or {}))
    lines = [
        f'# Decision comparison: {candidate.get("run_id")} against {baseline.get("run_id")}',
        '',
        f'- People compared: {people}',
        f'- People with any difference: **{len(differences)}**',
    ]
    if not differences:
        lines += ['', 'Every identity, run status and paper ownership decision is identical.']
        return '\n'.join(lines) + '\n'
    lines += [
        f'- Verified papers gained: {sum(len(item.verified_gained) for item in differences)}',
        f'- Verified papers lost: {sum(len(item.verified_lost) for item in differences)}',
        '',
        'Score both runs (`paper-benchmark score`) to see whether these changes are right before merging.',
    ]
    for item in differences:
        lines += ['', f'## {item.person_id}']
        if item.status:
            lines.append(f'- Run status: {item.status[0]} → {item.status[1]}')
        if item.identity:
            lines.append(f'- Identity: {item.identity[0]} → {item.identity[1]}')
        if item.verified_gained:
            lines.append(f'- Verified gained ({len(item.verified_gained)}): {_listed(item.verified_gained)}')
        if item.verified_lost:
            lines.append(f'- Verified lost ({len(item.verified_lost)}): {_listed(item.verified_lost)}')
        if item.other_changes:
            lines.append(f'- Other status changes: {len(item.other_changes)}')
    return '\n'.join(lines) + '\n'
