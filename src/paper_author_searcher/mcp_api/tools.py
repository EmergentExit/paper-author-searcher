"""What an AI assistant can do with the search service over MCP, independent of the MCP library.

Each method acts for one caller (an API key's ID) through ``SearchService`` and answers a JSON-ready dict built from
the shared views (``service.views.search_view``, ``service.agent.agent_view``). Searches take minutes, so a status
call can wait for a change (``wait_seconds``) and every answer says what to do next, in words a model follows.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from paper_author_searcher.api.schema import validator
from paper_author_searcher.config import DEFAULT_SERVICE
from paper_author_searcher.service.facade import SearchService
from paper_author_searcher.service.model import ErrorKind, Job, JobStatus, ServiceError
from paper_author_searcher.service.views import search_view

# What a caller should do next, by search status.
NEXT_STEPS: dict[str, str] = {
    JobStatus.QUEUED: 'Waiting for a free worker. Call get_search_status again with wait_seconds to wait for progress.',
    JobStatus.RUNNING: 'Still searching (usually a few minutes). Call get_search_status again with wait_seconds.',
    JobStatus.NEEDS_CONFIRMATION: (
        'Several researchers share this name. Ask the user which candidate is the person (school, field, ORCID), '
        "then call confirm_researcher with one of that candidate's selections. Do not guess."
    ),
    JobStatus.SUCCEEDED: (
        'Done. Call get_citable_papers for the papers that may be cited; never cite what it lists as withheld.'
    ),
    JobStatus.FAILED: 'The search failed; see error. Starting it again may help if the error is temporary.',
    JobStatus.CANCELLED: 'The search was stopped. Start a new one to search again.',
}
WAITING = (JobStatus.QUEUED, JobStatus.RUNNING)


@dataclass(frozen=True)
class WaitPolicy:
    """How long a status call may wait for a change, and how often it looks."""

    maximum_seconds: float = DEFAULT_SERVICE.mcp_maximum_wait_seconds
    poll_seconds: float = DEFAULT_SERVICE.mcp_poll_seconds


class SearchTools:
    def __init__(
        self,
        service: SearchService,
        wait: WaitPolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.service = service
        self.wait = wait or WaitPolicy()
        self.clock = clock
        self.sleep = sleep

    def start_search(self, client: str, request: dict[str, Any]) -> dict[str, Any]:
        """Queue a search for a request in the REST search-request shape (checked against its schema)."""
        errors = validator('search-request').errors(request)
        if errors:
            raise ServiceError(ErrorKind.INVALID, 'invalid_request', 'The search request is not valid', errors)
        return self._status(self.service.submit(client, request).job)

    def search_status(self, client: str, search_id: str, wait_seconds: float = 0.0) -> dict[str, Any]:
        """Where a search stands; while it is queued or running, wait up to ``wait_seconds`` for it to finish or
        need the caller (capped by the wait policy)."""
        job = self.service.job(client, search_id)
        deadline = self.clock() + min(max(wait_seconds, 0.0), self.wait.maximum_seconds)
        while job.status in WAITING and self.clock() < deadline:
            self.sleep(min(self.wait.poll_seconds, max(deadline - self.clock(), 0.0)))
            job = self.service.job(client, search_id)
        return self._status(job)

    def citable_papers(self, client: str, search_id: str) -> dict[str, Any]:
        """The agent contract: papers that may be cited, and withheld ones that must not be."""
        return _unframed(self.service.agent_view(client, search_id))

    def full_result(self, client: str, search_id: str, include_needs_review: bool = False) -> dict[str, Any]:
        """Everything a finished search found, with every paper's evidence and full-text links."""
        return search_view(self.service.job(client, search_id), include_needs_review)

    def confirm_researcher(self, client: str, search_id: str, selection: str) -> dict[str, Any]:
        """Pick a candidate of a search waiting for confirmation; the search continues for that person."""
        return self._status(self.service.confirm(client, search_id, selection).job)

    def cancel_search(self, client: str, search_id: str) -> dict[str, Any]:
        return self._status(self.service.cancel(client, search_id))

    @staticmethod
    def _status(job: Job) -> dict[str, Any]:
        view = search_view(job)
        status: dict[str, Any] = {
            'search_id': job.id,
            'status': job.status,
            'request': {key: job.request.get(key) for key in ('name', 'university', 'department')},
            'next_step': NEXT_STEPS.get(job.status, ''),
        }
        for key in ('progress', 'candidates', 'error'):
            if view.get(key):
                status[key] = view[key]
        if job.status == JobStatus.SUCCEEDED and view.get('result'):
            status['statistics'] = view['result']['statistics']
        return status


def _unframed(view: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in view.items() if key != 'api_version'}
