"""Replay-consistency gate: replay a recorded benchmark run with the current code and require identical decisions.

The recorded HTTP cache (gigabytes) lives only on the machine that recorded it, so this runs locally, not in GitHub
CI. Make the baseline once, from the code you trust (usually main):

    PAPER_SEARCH__DBLP_DUMP__INDEX_PATH=output/dblp/dblp.sqlite \
      paper-benchmark run --replay-from career-chg --input-profile minimal --run-id replay-main
    paper-benchmark decisions --run-id replay-main --output baseline.decisions.json

Then, on the branch to check:

    python scripts/ci/replay_gate.py --replay-from career-chg --baseline baseline.decisions.json

Runs that used the local DBLP dump index must be replayed with it too, or DBLP lookups miss the cache and the
replay differs from the recording; --dblp-index defaults to output/dblp/dblp.sqlite when that file exists.

It replays into a new run (offline: cache only, recorded anchors), compares every identity and paper ownership
decision with the baseline, prints the timings, and exits 0 only when nothing differs. The replayed run directory
is deleted afterwards unless --keep is given; its decisions are saved next to the baseline first.
"""

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from paper_author_searcher.benchmark.settings import BenchmarkSettings  # noqa: E402

DEFAULT_CONFIG = ROOT / 'data/benchmark/v1/benchmark.json'
DEFAULT_DBLP_INDEX = ROOT / 'output/dblp/dblp.sqlite'
DBLP_INDEX_VARIABLE = 'PAPER_SEARCH__DBLP_DUMP__INDEX_PATH'


def _benchmark(config: Path, *arguments: str, environment: dict[str, str] | None = None) -> int:
    command = [sys.executable, '-m', 'paper_author_searcher.benchmark.cli', '--config', str(config), *arguments]
    return subprocess.run(command, cwd=ROOT, check=False, env=environment).returncode


def _revision() -> str:
    try:
        return subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return 'unknown'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--replay-from', required=True, help='Recorded run whose cache and anchors are replayed')
    parser.add_argument('--baseline', required=True, help='Decisions file (or run ID) from the trusted code')
    parser.add_argument('--input-profile', default='minimal', choices=('minimal', 'with_hints'))
    parser.add_argument('--person', action='append', default=[], help='Limit the replay to these people')
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        '--dblp-index',
        type=Path,
        default=DEFAULT_DBLP_INDEX if DEFAULT_DBLP_INDEX.exists() else None,
        help='DBLP dump index the recorded run used (default: output/dblp/dblp.sqlite if present)',
    )
    parser.add_argument('--keep', action='store_true', help='Keep the replayed run directory')
    args = parser.parse_args()
    environment = dict(os.environ)
    if args.dblp_index:
        environment[DBLP_INDEX_VARIABLE] = str(args.dblp_index.resolve())

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    run_id = f'replay-gate-{_revision()}-{stamp}'
    people = [argument for person in args.person for argument in ('--person', person)]
    replay = ['run', '--replay-from', args.replay_from, '--input-profile', args.input_profile, '--run-id', run_id]
    if _benchmark(args.config, *replay, *people, environment=environment) != 0:
        print('Replay failed; see the log above.', file=sys.stderr)
        return 2
    baseline = Path(args.baseline)
    saved = (baseline.parent if baseline.suffix == '.json' else Path.cwd()) / f'{run_id}.decisions.json'
    _benchmark(args.config, 'decisions', '--run-id', run_id, '--output', str(saved))
    compared = _benchmark(args.config, 'compare-decisions', '--baseline', args.baseline, '--candidate', str(saved))
    _benchmark(args.config, 'timings', '--run-id', run_id)
    if not args.keep:
        run_dir = BenchmarkSettings.load(args.config).paths.runs / run_id
        shutil.rmtree(run_dir)
        print(f'Removed the replayed run {run_dir}; its decisions are in {saved}')
    print('Replay gate: ' + ('passed, decisions identical' if compared == 0 else 'FAILED, decisions differ'))
    return compared


if __name__ == '__main__':
    raise SystemExit(main())
