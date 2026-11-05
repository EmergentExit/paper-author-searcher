"""What a caller sees of a search job, whichever front end (REST, gRPC) asks.

Front ends add only their own framing (the REST API its ``api_version`` and links); the job's fields, and which
papers a result shows, are decided here once.
"""

from typing import Any

from .model import Job, JobStatus
from .results import ResultView

# The request field that asks for needs-review papers in a result.
INCLUDE_NEEDS_REVIEW = 'include_needs_review'


def search_view(job: Job, include_needs_review: bool | None = None) -> dict[str, Any]:
    """The job as schema v1 ``search`` (without front-end framing); ``include_needs_review`` overrides the
    request's own choice."""
    body: dict[str, Any] = {
        'id': job.id,
        'status': job.status,
        'created_at': job.created_at,
        'updated_at': job.updated_at,
        'request': job.request,
        'selection': job.selection,
        'progress': job.progress if job.status in (JobStatus.QUEUED, JobStatus.RUNNING) else None,
    }
    if job.candidates:
        body['candidates'] = job.candidates
    if job.status == JobStatus.SUCCEEDED and job.result is not None:
        include = bool(job.request.get(INCLUDE_NEEDS_REVIEW)) if include_needs_review is None else include_needs_review
        body['result'] = ResultView.visible(job.result, include)
    if job.status == JobStatus.FAILED and job.error is not None:
        body['error'] = job.error
    return body
