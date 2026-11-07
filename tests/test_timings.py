import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from paper_author_searcher.models import ProfessorQuery
from paper_author_searcher.timings import HttpMeter, PhaseTimings, TimedHttpClient, phase, recording
from paper_author_searcher.transport.http import SourceError
from paper_author_searcher.workflow import PaperSearchWorkflow
from paper_author_searcher.workflow.stages import WorkflowStage

QUERY = ProfessorQuery(name='Ada Lovelace', university='University of London')


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class PhaseTimingsTests(unittest.TestCase):
    def test_phases_record_seconds_and_entries_and_nest(self):
        clock = Clock()
        timings = PhaseTimings(clock)
        with recording(timings):
            with phase('outer'):
                clock.now += 1
                with phase('inner'):
                    clock.now += 2
            with phase('inner'):
                clock.now += 0.5
        self.assertEqual(
            timings.to_dict(),
            {'inner': {'count': 2, 'seconds': 2.5}, 'outer': {'count': 1, 'seconds': 3.0}},
        )

    def test_phase_is_a_no_op_outside_a_recorded_run_and_recording_ends_with_its_block(self):
        timings = PhaseTimings()
        with phase('unrecorded'):
            pass
        with recording(timings):
            pass
        with phase('after'):
            pass
        self.assertEqual(timings.to_dict(), {})

    def test_a_failing_block_is_still_timed_and_its_error_propagates(self):
        clock = Clock()
        timings = PhaseTimings(clock)
        with self.assertRaises(ValueError), recording(timings), phase('failing'):
            clock.now += 4
            raise ValueError('boom')
        self.assertEqual(timings.to_dict(), {'failing': {'count': 1, 'seconds': 4.0}})


class HttpMeterTests(unittest.TestCase):
    def test_since_reports_only_what_followed_the_snapshot_slowest_host_first(self):
        meter = HttpMeter()
        meter.record('api.openalex.org', 1.0, failed=False)
        before = meter.snapshot()
        meter.record('api.openalex.org', 0.5, failed=True)
        meter.record('api.semanticscholar.org', 2.0, failed=False)
        self.assertEqual(
            meter.since(before),
            {
                'api.semanticscholar.org': {'calls': 1, 'failures': 0, 'seconds': 2.0},
                'api.openalex.org': {'calls': 1, 'failures': 1, 'seconds': 0.5},
            },
        )
        self.assertEqual(meter.since()['api.openalex.org']['calls'], 2)


class TimedHttpClientTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.meter = HttpMeter()
        self.network = Mock()
        self.client = TimedHttpClient(self.network, self.meter, self.clock)

    def test_answers_pass_through_unchanged_and_are_timed_by_host(self):
        def answer(*args):
            self.clock.now += 1.5
            return {'results': [1]}

        self.network.get.side_effect = answer
        self.assertEqual(self.client.get('https://api.openalex.org/works', {'q': 'x'}), {'results': [1]})
        self.network.get.assert_called_once_with('https://api.openalex.org/works', {'q': 'x'}, None, False)
        self.assertEqual(self.meter.since(), {'api.openalex.org': {'calls': 1, 'failures': 0, 'seconds': 1.5}})

    def test_failures_are_counted_and_raised_unchanged(self):
        error = SourceError('HTTP 429 from api.semanticscholar.org', status_code=429)
        self.network.post_json.side_effect = error
        with self.assertRaises(SourceError) as raised:
            self.client.post_json('https://api.semanticscholar.org/graph/v1/paper/batch', {'ids': []})
        self.assertIs(raised.exception, error)
        self.assertEqual(self.meter.since()['api.semanticscholar.org']['failures'], 1)

    def test_probe_and_download_keep_their_arguments(self):
        guard = Mock()
        self.client.probe('https://example.org/a.pdf', 2048, 10, 3, redirect_guard=guard)
        self.network.probe.assert_called_once_with('https://example.org/a.pdf', 2048, 10, 3, redirect_guard=guard)
        self.client.stream_pdf('https://example.org/a.pdf', '/tmp/a.pdf', 'config')
        self.network.stream_pdf.assert_called_once_with('https://example.org/a.pdf', '/tmp/a.pdf', 'config')
        self.assertEqual(self.meter.since()['example.org']['calls'], 2)


class WorkflowTimingTests(unittest.TestCase):
    def test_the_report_records_stage_phases_and_this_runs_share_of_http(self):
        meter = HttpMeter()
        meter.record('earlier.example', 9.0, failed=False)  # another run's request on a shared client

        class Search(WorkflowStage):
            name = 'Searching'

            def run(self, context):
                with phase('step'):
                    meter.record('api.openalex.org', 0.25, failed=False)
                context.halted = True

        with tempfile.TemporaryDirectory() as directory:
            report = PaperSearchWorkflow(stages=[Search()], http_meter=meter).run(
                query=QUERY, output_dir=Path(directory), mode='metadata'
            )
        timings = report['timings']
        self.assertEqual(set(timings['phases']), {'stage: Searching', 'step'})
        self.assertEqual(timings['http_hosts'], {'api.openalex.org': {'calls': 1, 'failures': 0, 'seconds': 0.25}})
        self.assertGreaterEqual(timings['total_seconds'], 0)

    def test_without_a_meter_the_report_still_has_phases(self):
        with tempfile.TemporaryDirectory() as directory:
            report = PaperSearchWorkflow(stages=[]).run(query=QUERY, output_dir=Path(directory), mode='metadata')
        self.assertEqual(report['timings']['http_hosts'], {})
        self.assertEqual(report['timings']['phases'], {})


if __name__ == '__main__':
    unittest.main()
