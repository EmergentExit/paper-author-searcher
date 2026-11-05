import tempfile
import unittest
from pathlib import Path

from paper_author_searcher.benchmark import decisions, timing_report
from paper_author_searcher.jsonio import write_json

IDENTITY = {
    'name': 'Ada',
    'status': 'resolved',
    'confirmation': 'resolver_resolved',
    'author_ids': {'openalex': ['A1']},
}


def paper(status, *records):
    return {
        'title': 'T',
        'ownership_status': status,
        'records': [{'source': source, 'source_paper_id': paper_id} for source, paper_id in records],
    }


class RunBuilder:
    def __init__(self, root: Path):
        self.root = root

    def run(self, run_id, people, timings=None):
        run_root = self.root / run_id
        index = {'run_id': run_id, 'dataset_id': 'v1', 'metadata': {'code_version': 'abc'}, 'people': {}}
        for person, (status, papers) in people.items():
            report_dir = run_root / person / 'search'
            report_dir.mkdir(parents=True)
            report = {'artifacts': {'papers': 'papers.json'}, 'identity': {'profile': IDENTITY}}
            if timings:
                report['timings'] = timings[person]
            write_json(report_dir / 'report.json', report)
            write_json(report_dir / 'papers.json', {'collection_identity': IDENTITY, 'collection': {'papers': papers}})
            index['people'][person] = {'status': status, 'report': f'{person}/search'}
        write_json(run_root / 'index.json', index)
        return run_id


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runs = Path(self.temp.name)
        self.builder = RunBuilder(self.runs)
        self.papers = [paper('verified', ('openalex', 'W1'), ('dblp', 'x')), paper('needs_review', ('openalex', 'W2'))]

    def test_identical_runs_have_no_differences_whatever_the_record_order(self):
        reordered = [paper('verified', ('dblp', 'x'), ('openalex', 'W1')), self.papers[1]]
        self.builder.run('a', {'ada': ('completed', self.papers)})
        self.builder.run('b', {'ada': ('completed', list(reversed(reordered)))})
        baseline, candidate = decisions.snapshot(self.runs, 'a'), decisions.snapshot(self.runs, 'b')
        self.assertEqual(decisions.compare(baseline, candidate), [])
        self.assertIn('identical', decisions.markdown(baseline, candidate, []))

    def test_gained_lost_and_other_changes_are_listed_separately(self):
        self.builder.run('a', {'ada': ('completed', self.papers)})
        changed = [paper('needs_review', ('openalex', 'W1'), ('dblp', 'x')), paper('verified', ('openalex', 'W2'))]
        changed.append(paper('rejected', ('openalex', 'W3')))
        self.builder.run('b', {'ada': ('partial', changed)})
        [difference] = decisions.compare(decisions.snapshot(self.runs, 'a'), decisions.snapshot(self.runs, 'b'))
        self.assertEqual(difference.status, ('completed', 'partial'))
        self.assertEqual(difference.verified_gained, ['openalex:W2'])
        self.assertEqual(difference.verified_lost, ['dblp:x|openalex:W1'])
        self.assertEqual(difference.other_changes, {'openalex:W3': (None, 'rejected')})
        self.assertIsNone(difference.identity)

    def test_a_split_deduplication_group_is_a_difference(self):
        self.builder.run('a', {'ada': ('completed', self.papers)})
        split = [paper('verified', ('openalex', 'W1')), paper('verified', ('dblp', 'x')), self.papers[1]]
        self.builder.run('b', {'ada': ('completed', split)})
        [difference] = decisions.compare(decisions.snapshot(self.runs, 'a'), decisions.snapshot(self.runs, 'b'))
        self.assertEqual(difference.verified_lost, ['dblp:x|openalex:W1'])
        self.assertEqual(difference.verified_gained, ['dblp:x', 'openalex:W1'])

    def test_a_person_missing_from_one_run_differs_and_saved_snapshots_load_back(self):
        self.builder.run('a', {'ada': ('completed', self.papers), 'bob': ('completed', [])})
        self.builder.run('b', {'ada': ('completed', self.papers)})
        saved = self.runs / 'a.decisions.json'
        write_json(saved, decisions.snapshot(self.runs, 'a'))
        baseline = decisions.load_snapshot(self.runs, str(saved))
        [difference] = decisions.compare(baseline, decisions.load_snapshot(self.runs, 'b'))
        self.assertEqual(difference.person_id, 'bob')
        self.assertEqual(difference.status, ('completed', None))

    def test_a_json_file_that_is_not_a_snapshot_is_refused(self):
        other = self.runs / 'other.json'
        write_json(other, {'people': {}})
        with self.assertRaises(ValueError):
            decisions.load_snapshot(self.runs, str(other))


class TimingReportTests(unittest.TestCase):
    def test_summary_sums_people_phases_and_hosts(self):
        with tempfile.TemporaryDirectory() as directory:
            runs = Path(directory)
            timings = {
                person: {
                    'total_seconds': seconds,
                    'phases': {'identity: anchors': {'count': 1, 'seconds': seconds / 2}},
                    'http_hosts': {'api.openalex.org': {'calls': 3, 'failures': 1, 'seconds': seconds / 4}},
                }
                for person, seconds in (('ada', 10.0), ('bob', 30.0))
            }
            RunBuilder(runs).run('r', {'ada': ('completed', []), 'bob': ('completed', [])}, timings)
            summary = timing_report.summarize(runs, 'r')
        self.assertEqual(summary['total_seconds'], 40.0)
        self.assertEqual(list(summary['people']), ['bob', 'ada'])
        self.assertEqual(
            summary['phases']['identity: anchors'],
            {'people': 2, 'total_seconds': 20.0, 'median_seconds': 10.0, 'max_seconds': 15.0},
        )
        self.assertEqual(summary['http_hosts']['api.openalex.org'], {'calls': 6, 'failures': 2, 'seconds': 10.0})
        self.assertIn('| identity: anchors | 2 |', timing_report.markdown(summary))

    def test_reports_without_timings_are_listed_as_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            runs = Path(directory)
            RunBuilder(runs).run('r', {'ada': ('completed', [])})
            summary = timing_report.summarize(runs, 'r')
        self.assertEqual(summary['people_missing_timings'], ['ada'])
        self.assertEqual(summary['people_timed'], 0)


if __name__ == '__main__':
    unittest.main()
