"""The MCP front end: the tool layer over the core, and the real SDK server driven by the SDK's own client."""

import asyncio
import socket
import tempfile
import threading
import time
import unittest
from dataclasses import replace

import httpx2
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from paper_author_searcher.config.service import ServiceConfig
from paper_author_searcher.mcp_api.server import build_app
from paper_author_searcher.mcp_api.tools import NEXT_STEPS, SearchTools, WaitPolicy
from paper_author_searcher.service.model import JobStatus, ServiceError
from paper_author_searcher.service.ports import SearchOutcome
from test_api import Harness
from test_service import CANDIDATES, REQUEST

TOOL_NAMES = {
    'start_search',
    'get_search_status',
    'get_citable_papers',
    'get_full_result',
    'confirm_researcher',
    'cancel_search',
}


class FakeClock:
    """Time that moves only when the code under test sleeps; ``on_sleep`` runs at each sleep."""

    def __init__(self, on_sleep=None):
        self.now = 0.0
        self.sleeps = []
        self.on_sleep = on_sleep

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep:
            self.on_sleep()


class ToolTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.harness = Harness(directory.name, [])
        self.client = self.harness.keys.keys[0].id
        self.clock = FakeClock()
        self.tools = SearchTools(self.harness.core.service, WaitPolicy(50, 2), self.clock, self.clock.sleep)

    def test_a_search_is_started_checked_and_reported_with_its_next_step(self):
        self.harness.core.runner.outcomes.append(self.harness.succeed())
        started = self.tools.start_search(self.client, dict(REQUEST, mode='metadata'))
        self.assertEqual(started['next_step'], NEXT_STEPS[JobStatus.QUEUED])
        status = self.tools.search_status(self.client, started['search_id'])
        self.assertEqual(status['status'], JobStatus.SUCCEEDED)
        self.assertEqual(status['next_step'], NEXT_STEPS[JobStatus.SUCCEEDED])
        self.assertEqual(status['statistics']['verified'], 1)
        with self.assertRaises(ServiceError) as refused:
            self.tools.start_search(self.client, {'name': 'Ada'})
        self.assertIn('$.university: required', refused.exception.details)

    def test_a_status_call_waits_until_the_search_finishes(self):
        service = self.harness.core.service
        service.on_submit = None
        started = self.tools.start_search(self.client, REQUEST)
        self.harness.core.runner.outcomes.append(self.harness.succeed())
        polls = []

        def worker_turn():
            polls.append(self.clock.now)
            if len(polls) == 3:
                self.harness.core.worker.run_once()

        self.clock.on_sleep = worker_turn
        status = self.tools.search_status(self.client, started['search_id'], wait_seconds=30)
        self.assertEqual(status['status'], JobStatus.SUCCEEDED)
        self.assertEqual(self.clock.sleeps, [2, 2, 2])

    def test_waiting_is_capped_and_finished_searches_answer_at_once(self):
        self.harness.core.service.on_submit = None
        started = self.tools.start_search(self.client, REQUEST)
        status = self.tools.search_status(self.client, started['search_id'], wait_seconds=500)
        self.assertEqual(status['status'], JobStatus.QUEUED)
        self.assertEqual(self.clock.now, 50)
        cancelled = self.tools.cancel_search(self.client, started['search_id'])
        self.clock.sleeps.clear()
        self.tools.search_status(self.client, cancelled['search_id'], wait_seconds=30)
        self.assertEqual(self.clock.sleeps, [])

    def test_a_namesake_is_confirmed_and_the_agent_view_is_given(self):
        checkpoint = self.harness.core.pdf('professor.json', b'{}')
        self.harness.core.runner.outcomes += [
            SearchOutcome(JobStatus.NEEDS_CONFIRMATION, CANDIDATES, identity_checkpoint=checkpoint),
            self.harness.succeed(),
        ]
        started = self.tools.start_search(self.client, REQUEST)
        waiting = self.tools.search_status(self.client, started['search_id'])
        self.assertEqual(waiting['candidates'], CANDIDATES)
        self.assertIn('Do not guess', waiting['next_step'])
        self.tools.confirm_researcher(self.client, waiting['search_id'], 'openalex:A2')
        papers = self.tools.citable_papers(self.client, waiting['search_id'])
        self.assertNotIn('api_version', papers)
        self.assertEqual((papers['status'], [paper['title'] for paper in papers['papers']]), ('ready', ['Notes']))
        full = self.tools.full_result(self.client, waiting['search_id'])
        self.assertEqual(full['result']['papers'][0]['title'], 'Notes')


def _free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class McpServerTests(unittest.TestCase):
    """The real SDK server on a local port, called by the SDK's client in both protocol eras."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.harness = Harness(directory.name, [])
        port = _free_port()
        config = replace(ServiceConfig(), mcp_port=port, mcp_public_url=f'http://127.0.0.1:{port}/mcp')
        tools = SearchTools(self.harness.core.service, WaitPolicy(1, 0.1))
        app = build_app(tools, self.harness.keys, config)
        self.server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
        thread = threading.Thread(target=self.server.run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(setattr, self.server, 'should_exit', True)
        deadline = time.monotonic() + 10
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        self.url = config.mcp_public_url

    def session(self, secret=None, mode='auto'):
        headers = {'Authorization': f'Bearer {secret}'} if secret else {}
        transport = streamable_http_client(self.url, http_client=httpx2.AsyncClient(headers=headers, timeout=10))
        return Client(transport, mode=mode)

    def test_an_assistant_finds_the_tools_and_runs_a_search(self):
        for mode in ('auto', 'legacy'):
            with self.subTest(mode=mode):
                self.harness.core.runner.outcomes.append(self.harness.succeed())

                async def scenario(mode=mode):
                    async with self.session(self.harness.secret, mode) as client:
                        listed = await client.list_tools()
                        started = await client.call_tool('start_search', {**REQUEST, 'mode': 'metadata'})
                        search_id = started.structured_content['search_id']
                        status = await client.call_tool('get_search_status', {'search_id': search_id})
                        papers = await client.call_tool('get_citable_papers', {'search_id': search_id})
                        return listed, started, status, papers

                listed, started, status, papers = asyncio.run(scenario())
                self.assertEqual({tool.name for tool in listed.tools}, TOOL_NAMES)
                self.assertFalse(started.is_error)
                self.assertEqual(status.structured_content['status'], 'succeeded')
                self.assertEqual(papers.structured_content['papers'][0]['title'], 'Notes')

    def test_refusals_reach_the_model_as_tool_errors(self):
        async def scenario():
            async with self.session(self.harness.secret) as client:
                missing = await client.call_tool('get_search_status', {'search_id': 'srch_none'})
                invalid = await client.call_tool('start_search', {'name': 'Ada', 'university': ''})
                return missing, invalid

        missing, invalid = asyncio.run(scenario())
        self.assertTrue(missing.is_error)
        self.assertIn('not_found', missing.content[0].text)
        self.assertTrue(invalid.is_error)
        self.assertIn('$.university: at least 1 characters', invalid.content[0].text)

    def test_callers_see_only_their_own_searches(self):
        self.harness.core.runner.outcomes.append(self.harness.succeed())

        async def scenario():
            async with self.session(self.harness.secret) as client:
                started = await client.call_tool('start_search', REQUEST)
            async with self.session(self.harness.other) as client:
                return await client.call_tool(
                    'get_search_status', {'search_id': started.structured_content['search_id']}
                )

        self.assertTrue(asyncio.run(scenario()).is_error)

    def test_requests_without_a_current_key_are_refused(self):
        for secret in (None, 'pas_wrong'):
            with self.subTest(secret=secret):
                headers = {'Accept': 'application/json, text/event-stream'}
                if secret:
                    headers['Authorization'] = f'Bearer {secret}'
                listing = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}
                answer = httpx2.post(self.url, json=listing, headers=headers)
                self.assertEqual(answer.status_code, 401)
                self.assertIn('Bearer', answer.headers['www-authenticate'])

    def test_unknown_hosts_are_refused(self):
        answer = httpx2.post(
            self.url,
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
            headers={'Host': 'evil.example', 'Authorization': f'Bearer {self.harness.secret}'},
        )
        self.assertIn(answer.status_code, (400, 403, 421))


if __name__ == '__main__':
    unittest.main()
