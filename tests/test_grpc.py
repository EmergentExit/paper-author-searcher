"""The gRPC adapter: contract, codec, keys, status codes, PDF streaming and TLS, over a real server and the core."""

import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc

from paper_author_searcher.api.schema import schema
from paper_author_searcher.config.service import ServiceConfig
from paper_author_searcher.rpc.codec import EnumMapping, MessageCodec, default_codec
from paper_author_searcher.rpc.server import SERVICE_NAME, build_server, healthy
from paper_author_searcher.rpc.servicer import ERROR_CODE_KEY, RETRY_AFTER_KEY
from paper_author_searcher.rpc.v1 import paper_search_pb2 as pb
from paper_author_searcher.rpc.v1 import paper_search_pb2_grpc as pb_grpc
from paper_author_searcher.service.model import JobStatus, SubmissionLimits
from paper_author_searcher.service.ports import SearchOutcome
from paper_author_searcher.service.views import search_view
from test_api import RESULT, Harness
from test_service import CANDIDATES, REQUEST

PDF = b'%PDF-1.7 ' + b'x' * 300
CONFIG = ServiceConfig(grpc_port=1, grpc_threads=4, grpc_chunk_bytes=128)


def _enum_values(enum_type) -> set[str]:
    mapping = default_codec().enums.to_rest[enum_type.DESCRIPTOR.full_name]
    return set(mapping.values())


class ContractTests(unittest.TestCase):
    """The .proto and the REST JSON Schemas describe the same values."""

    def test_every_enum_value_names_its_rest_value(self):
        mapping = EnumMapping.of(pb.DESCRIPTOR)
        for enum in pb.DESCRIPTOR.enum_types_by_name.values():
            self.assertEqual(len(mapping.to_proto[enum.full_name]), len(enum.values) - 1, enum.full_name)

    def test_enums_match_the_json_schemas(self):
        request = schema('search-request')['properties']
        search = schema('search')
        agent = schema('agent-result')
        pairs = [
            (pb.Mode, request['mode']['enum']),
            (pb.OnAmbiguity, request['on_ambiguity']['enum']),
            (pb.SearchStatus, search['properties']['status']['enum']),
            (pb.RunStatus, search['$defs']['result']['properties']['run_status']['enum']),
            (pb.OwnershipStatus, search['$defs']['paper']['properties']['ownership_status']['enum']),
            (pb.Confidence, search['$defs']['paper']['properties']['confidence']['enum']),
            (pb.FullTextStatus, search['$defs']['full_text']['properties']['status']['enum']),
            (pb.AgentStatus, agent['properties']['status']['enum']),
            (pb.WithheldReason, agent['$defs']['withheld']['properties']['reason']['enum']),
        ]
        for enum_type, values in pairs:
            self.assertEqual(_enum_values(enum_type), set(values), enum_type.DESCRIPTOR.name)

    def test_a_full_search_request_round_trips(self):
        codec = MessageCodec(EnumMapping.of(pb.DESCRIPTOR), ignore_unknown_fields=False)
        request = dict(
            REQUEST,
            mode='pdf-urls',
            on_ambiguity='collect_contenders',
            include_needs_review=False,
            hints={'orcid': '0000-0002-1825-0097', 'previous_universities': ['Old U'], 'known_papers': ['Notes']},
            limits={'max_downloads': 5},
        )
        message = codec.to_message(request, pb.SearchRequest)
        self.assertEqual(message.mode, pb.MODE_PDF_URLS)
        self.assertEqual(codec.to_dict(message), request)

    def test_every_field_of_a_result_has_a_place_in_the_messages(self):
        strict = MessageCodec(EnumMapping.of(pb.DESCRIPTOR), ignore_unknown_fields=False)
        paper = dict(
            RESULT['papers'][0],
            confidence='high',
            evidence=['e'],
            abstract=None,
            full_text={
                'status': 'link',
                'url': 'https://x.example/p.pdf',
                'browser_url': None,
                'publisher_link': {'url': 'https://dl.acm.org/doi/pdf/10.1145/1', 'kind': 'pdf', 'publisher': 'ACM'},
            },
            editions=[{'id': '1', 'title': 'Notes (preprint)', 'year': None, 'full_text': {'status': 'none'}}],
        )
        result = dict(
            RESULT,
            identity=dict(RESULT['identity'], author_ids={'openalex': 'A1'}, confidence=0.9),
            papers=[paper],
            notices=[{'code': 'time_limit', 'message': 'Stopped early'}],
        )
        message = strict.to_message(result, pb.SearchResult)
        self.assertEqual(message.papers[0].full_text.publisher_link.kind, pb.PUBLISHER_LINK_KIND_PDF)
        self.assertEqual(message.papers[0].editions[0].full_text.status, pb.FULL_TEXT_STATUS_NONE)
        self.assertFalse(message.papers[0].HasField('abstract'))
        self.assertEqual(message.identity.author_ids['openalex'], 'A1')

    def test_fields_a_newer_view_adds_are_left_out_by_default(self):
        message = default_codec().to_message(dict(REQUEST, colour='blue', mode='later'), pb.SearchRequest)
        self.assertEqual((message.name, message.mode), ('Ada Lovelace', pb.MODE_UNSPECIFIED))


class GrpcHarness(Harness):
    """The real core and a real gRPC server on a free local port; ``stub`` calls it as ``secret``."""

    def __init__(self, directory, outcomes, config=CONFIG, channel_credentials=None, host='127.0.0.1'):
        super().__init__(directory, outcomes)
        config = replace(config, grpc_host=host, grpc_port=_free_port())
        self.server, self.port = build_server(self.core.service, self.keys, config)
        self.server.start()
        target = f'{host}:{self.port}'
        if channel_credentials is None:
            self.channel = grpc.insecure_channel(target)
        else:
            self.channel = grpc.secure_channel(target, channel_credentials)
        self.stub = pb_grpc.PaperSearchStub(self.channel)

    def auth(self, secret=None):
        return (('authorization', 'Bearer ' + (secret or self.secret)),)

    def create(self, request=REQUEST, **fields):
        search = default_codec().to_message(request, pb.SearchRequest)
        return self.stub.CreateSearch(pb.CreateSearchRequest(search=search, **fields), metadata=self.auth())

    def close(self):
        self.channel.close()
        self.server.stop(None).wait()


def _free_port():
    import socket

    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class GrpcServiceTests(unittest.TestCase):
    def harness(self, outcomes=(), **options):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        harness = GrpcHarness(directory.name, list(outcomes), **options)
        self.addCleanup(harness.close)
        return harness

    def assertRefused(self, call, status, code=None):
        with self.assertRaises(grpc.RpcError) as caught:
            call()
        self.assertEqual(caught.exception.code(), status)
        if code is not None:
            self.assertEqual(dict(caught.exception.trailing_metadata())[ERROR_CODE_KEY], code)
        return caught.exception

    def test_a_search_runs_and_answers_like_the_rest_view(self):
        api = self.harness()
        api.core.runner.outcomes.append(api.succeed())
        created = api.create()
        self.assertEqual(created.request.name, 'Ada Lovelace')
        found = api.stub.GetSearch(pb.GetSearchRequest(search_id=created.id), metadata=api.auth())
        self.assertEqual(found.status, pb.SEARCH_STATUS_SUCCEEDED)
        self.assertEqual(found.created_at.ToJsonString()[:4], created.created_at.ToJsonString()[:4])
        paper = found.result.papers[0]
        self.assertEqual((paper.title, paper.year), ('Notes', 1843))
        self.assertEqual(paper.ownership_status, pb.OWNERSHIP_STATUS_VERIFIED)
        self.assertEqual(paper.full_text.status, pb.FULL_TEXT_STATUS_FILE)
        rest = search_view(api.core.service.job(api.keys.keys[0].id, created.id))
        self.assertEqual(found.result.statistics.verified, rest['result']['statistics']['verified'])

    def test_calls_need_a_current_key_and_see_only_their_own_searches(self):
        api = self.harness()
        api.core.runner.outcomes.append(api.succeed())
        self.assertRefused(
            lambda: api.stub.GetSearch(pb.GetSearchRequest(search_id='x')),
            grpc.StatusCode.UNAUTHENTICATED,
            'unauthorized',
        )
        bad = (('authorization', 'Bearer pas_wrong'),)
        self.assertRefused(
            lambda: api.stub.GetSearch(pb.GetSearchRequest(search_id='x'), metadata=bad),
            grpc.StatusCode.UNAUTHENTICATED,
        )
        created = api.create()
        self.assertRefused(
            lambda: api.stub.GetSearch(pb.GetSearchRequest(search_id=created.id), metadata=api.auth(api.other)),
            grpc.StatusCode.NOT_FOUND,
            'not_found',
        )

    def test_requests_are_checked_against_the_search_request_schema(self):
        api = self.harness()
        error = self.assertRefused(
            lambda: api.create(dict(REQUEST, name='')), grpc.StatusCode.INVALID_ARGUMENT, 'invalid_request'
        )
        self.assertIn('$.name: required', error.details())
        self.assertRefused(lambda: api.create(idempotency_key='k' * 256), grpc.StatusCode.INVALID_ARGUMENT)
        self.assertEqual(api.core.runner.calls, [])

    def test_idempotent_retries_act_on_the_same_search(self):
        api = self.harness()
        api.core.runner.outcomes.append(api.succeed())
        first = api.create(idempotency_key='retry-1')
        again = api.create(idempotency_key='retry-1')
        self.assertEqual(first.id, again.id)
        self.assertEqual(len(api.core.runner.calls), 1)
        self.assertRefused(
            lambda: api.create(dict(REQUEST, name='Someone Else'), idempotency_key='retry-1'),
            grpc.StatusCode.INVALID_ARGUMENT,
            'idempotency_key_reused',
        )

    def test_an_ambiguous_search_is_confirmed_over_grpc(self):
        api = self.harness()
        checkpoint = api.core.pdf('professor.json', b'{}')
        api.core.runner.outcomes += [
            SearchOutcome(JobStatus.NEEDS_CONFIRMATION, CANDIDATES, identity_checkpoint=checkpoint),
            api.succeed(),
        ]
        created = api.create()
        waiting = api.stub.GetSearch(pb.GetSearchRequest(search_id=created.id), metadata=api.auth())
        self.assertEqual(waiting.status, pb.SEARCH_STATUS_NEEDS_CONFIRMATION)
        self.assertEqual([list(c.selections) for c in waiting.candidates], [['openalex:A1'], ['openalex:A2']])
        confirm = pb.ConfirmSearchRequest(search_id=created.id, selection='openalex:A9')
        self.assertRefused(
            lambda: api.stub.ConfirmSearch(confirm, metadata=api.auth()),
            grpc.StatusCode.INVALID_ARGUMENT,
            'unknown_selection',
        )
        confirm.selection = 'openalex:A2'
        accepted = api.stub.ConfirmSearch(confirm, metadata=api.auth())
        self.assertEqual(accepted.selection, 'openalex:A2')
        done = api.stub.GetSearch(pb.GetSearchRequest(search_id=created.id), metadata=api.auth())
        self.assertEqual(done.status, pb.SEARCH_STATUS_SUCCEEDED)
        self.assertRefused(
            lambda: api.stub.ConfirmSearch(confirm, metadata=api.auth()),
            grpc.StatusCode.FAILED_PRECONDITION,
            'not_awaiting_confirmation',
        )

    def test_a_queued_search_is_cancelled_and_a_finished_one_is_not(self):
        api = self.harness()
        api.core.service.on_submit = None
        created = api.create()
        cancelled = api.stub.CancelSearch(pb.CancelSearchRequest(search_id=created.id), metadata=api.auth())
        self.assertEqual(cancelled.status, pb.SEARCH_STATUS_CANCELLED)
        api.core.service.on_submit = api.core.worker.run_once
        api.core.runner.outcomes.append(api.succeed())
        finished = api.create(dict(REQUEST, name='Grace Hopper'))
        self.assertRefused(
            lambda: api.stub.CancelSearch(pb.CancelSearchRequest(search_id=finished.id), metadata=api.auth()),
            grpc.StatusCode.FAILED_PRECONDITION,
            'already_finished',
        )

    def test_the_agent_result_cites_verified_papers(self):
        api = self.harness()
        api.core.runner.outcomes.append(api.succeed())
        created = api.create()
        agent = api.stub.GetAgentResult(pb.GetAgentResultRequest(search_id=created.id), metadata=api.auth())
        self.assertEqual((agent.status, agent.usable), (pb.AGENT_STATUS_READY, True))
        self.assertEqual(agent.researcher.identity.confirmed_by, pb.CONFIRMED_BY_EVIDENCE)
        self.assertEqual([paper.title for paper in agent.papers], ['Notes'])
        self.assertEqual(agent.papers[0].confidence, pb.CONFIDENCE_MEDIUM)

    def test_a_checked_pdf_streams_in_chunks(self):
        api = self.harness()
        api.core.runner.outcomes.append(
            SearchOutcome(JobStatus.SUCCEEDED, result=RESULT, files={'0': api.core.pdf('paper.pdf', PDF)})
        )
        created = api.create()
        request = pb.DownloadPaperPdfRequest(search_id=created.id, paper_id='0')
        chunks = list(api.stub.DownloadPaperPdf(request, metadata=api.auth()))
        self.assertGreater(len(chunks), 1)
        self.assertEqual(b''.join(chunk.data for chunk in chunks), PDF)
        request.paper_id = '7'
        self.assertRefused(
            lambda: list(api.stub.DownloadPaperPdf(request, metadata=api.auth())), grpc.StatusCode.NOT_FOUND
        )

    def test_refused_submissions_say_when_to_retry(self):
        api = self.harness()
        api.core.service.on_submit = None
        api.core.service.limits = SubmissionLimits(maximum_active=1, retry_after_seconds=9)
        api.create()
        error = self.assertRefused(api.create, grpc.StatusCode.RESOURCE_EXHAUSTED, 'too_many_active_searches')
        self.assertEqual(dict(error.trailing_metadata())[RETRY_AFTER_KEY], '9')

    def test_unexpected_errors_are_internal_and_reveal_nothing(self):
        api = self.harness()

        def broken(*_):
            raise RuntimeError('database password is hunter2')

        api.core.service.job = broken
        error = self.assertRefused(
            lambda: api.stub.GetSearch(pb.GetSearchRequest(search_id='x'), metadata=api.auth()),
            grpc.StatusCode.INTERNAL,
            'internal_error',
        )
        self.assertNotIn('hunter2', error.details())

    def test_health_reports_serving(self):
        api = self.harness()
        self.assertTrue(healthy(replace(CONFIG, grpc_port=api.port)))
        self.assertFalse(healthy(replace(CONFIG, grpc_port=_free_port())))
        checks = health_pb2_grpc.HealthStub(api.channel)
        for name in ('', SERVICE_NAME):
            answer = checks.Check(health_pb2.HealthCheckRequest(service=name))
            self.assertEqual(answer.status, health_pb2.HealthCheckResponse.SERVING)


@unittest.skipIf(shutil.which('openssl') is None, 'needs openssl to make a test certificate')
class TlsTests(unittest.TestCase):
    def test_the_server_speaks_tls_with_the_configured_certificate(self):
        with tempfile.TemporaryDirectory() as directory:
            certificate, key = Path(directory) / 'origin.crt', Path(directory) / 'origin.key'
            subprocess.run(
                [
                    'openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=localhost',
                    '-addext', 'subjectAltName=DNS:localhost', '-keyout', str(key), '-out', str(certificate),
                ],
                check=True,
                capture_output=True,
            )  # fmt: skip
            config = replace(CONFIG, grpc_tls_certificate_file=str(certificate), grpc_tls_key_file=str(key))
            trusted = grpc.ssl_channel_credentials(root_certificates=certificate.read_bytes())
            api = GrpcHarness(directory, [], config=config, channel_credentials=trusted, host='localhost')
            try:
                api.core.service.on_submit = None
                self.assertEqual(api.create().status, pb.SEARCH_STATUS_QUEUED)
                self.assertTrue(healthy(replace(config, grpc_port=api.port)))
            finally:
                api.close()

    def test_certificate_and_key_are_set_together(self):
        with self.assertRaises(ValueError):
            ServiceConfig(grpc_tls_certificate_file='origin.crt')


if __name__ == '__main__':
    unittest.main()
