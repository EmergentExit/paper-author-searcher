"""Where a benchmark run spent its time, summed over people: per person, per phase and per HTTP host.

Reads the ``timings`` block each workflow report carries (reports from before it existed are counted as missing).
A replay answers every request from the cache, so its HTTP seconds are not network time; compare live runs for that.
"""

from pathlib import Path
from statistics import median
from typing import Any

from paper_author_searcher.jsonio import read_json

from .runner import INDEX

# How many of the slowest people the markdown lists.
SLOWEST_PEOPLE = 10
PRECISION = 1


def _report_timings(run_root: Path, entry: dict[str, Any]) -> list[dict[str, Any]]:
    found = []
    for key in ('report', 'fulltext_report'):
        if entry.get(key):
            path = run_root / entry[key] / 'report.json'
            if path.exists():
                timings = read_json(path).get('timings')
                if timings:
                    found.append(timings)
    return found


def summarize(runs_root: Path, run_id: str) -> dict[str, Any]:
    run_root = runs_root / run_id
    index = read_json(run_root / INDEX)
    people: dict[str, float] = {}
    phases: dict[str, list[float]] = {}
    hosts: dict[str, dict[str, float]] = {}
    missing = []
    for person, entry in sorted(index.get('people', {}).items()):
        reports = _report_timings(run_root, entry)
        if not reports:
            missing.append(person)
            continue
        people[person] = sum(timings.get('total_seconds', 0.0) for timings in reports)
        for timings in reports:
            for name, item in (timings.get('phases') or {}).items():
                phases.setdefault(name, []).append(item['seconds'])
            for host, item in (timings.get('http_hosts') or {}).items():
                total = hosts.setdefault(host, {'calls': 0, 'failures': 0, 'seconds': 0.0})
                for key in total:
                    total[key] += item.get(key, 0)
    return {
        'run_id': run_id,
        'people_timed': len(people),
        'people_missing_timings': missing,
        'total_seconds': round(sum(people.values()), PRECISION),
        'people': dict(sorted(people.items(), key=lambda item: -item[1])),
        'phases': {
            name: {
                'people': len(values),
                'total_seconds': round(sum(values), PRECISION),
                'median_seconds': round(median(values), PRECISION),
                'max_seconds': round(max(values), PRECISION),
            }
            for name, values in sorted(phases.items(), key=lambda item: -sum(item[1]))
        },
        'http_hosts': dict(sorted(hosts.items(), key=lambda item: -item[1]['seconds'])),
    }


def markdown(summary: dict[str, Any]) -> str:
    lines = [
        f'# Timings: run {summary["run_id"]}',
        '',
        f'- People timed: {summary["people_timed"]}; total {summary["total_seconds"]} s',
    ]
    if summary['people_missing_timings']:
        lines.append(f'- No timings recorded for: {", ".join(summary["people_missing_timings"])}')
    lines += [
        '- Phases nest (a stage holds its steps), so phase totals do not add up; HTTP seconds include pacing waits',
        '',
        '## Phases',
        '',
        '| Phase | People | Total s | Median s | Max s |',
        '|---|---:|---:|---:|---:|',
    ]
    lines += [
        f'| {name} | {item["people"]} | {item["total_seconds"]} | {item["median_seconds"]} | {item["max_seconds"]} |'
        for name, item in summary['phases'].items()
    ]
    lines += ['', '## HTTP hosts', '', '| Host | Calls | Failures | Seconds |', '|---|---:|---:|---:|']
    lines += [
        f'| {host} | {int(item["calls"])} | {int(item["failures"])} | {round(item["seconds"], PRECISION)} |'
        for host, item in summary['http_hosts'].items()
    ]
    lines += ['', f'## Slowest {SLOWEST_PEOPLE} people', '', '| Person | Seconds |', '|---|---:|']
    lines += [
        f'| {person} | {round(seconds, PRECISION)} |'
        for person, seconds in list(summary['people'].items())[:SLOWEST_PEOPLE]
    ]
    return '\n'.join(lines) + '\n'
