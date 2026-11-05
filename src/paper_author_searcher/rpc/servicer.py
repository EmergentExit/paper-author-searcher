"""gRPC adapter of the search service: authentication, validation, status codes; behaviour lives in SearchService.

Like the REST API it only translates. Requests are checked against the same JSON Schemas, answers are built from the
same views (``service.views.search_view``, ``service.agent.agent_view``), and the codec changes their shape.
"""

import functools
import logging
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar

import grpc

from paper_author_searcher.api.auth import ApiKeys
from paper_author_searcher.api.schema import validator
from paper_author_searcher.service.facade import SearchService
from paper_author_searcher.service.model import ErrorKind, Job, ServiceError
from paper_author_searcher.service.ports import StoredFile
from paper_author_searcher.service.views import search_view

from .codec import MessageCodec
from .v1 import paper_search_pb2 as pb
from .v1 import paper_search_pb2_grpc as pb_grpc

logger = logging.getLogger(__name__)

MAXIMUM_IDEMPOTENCY_KEY = 255
# Front-end framing in the shared views that the gRPC messages do not carry.
REST_FRAMING = ('api_version', 'links')
# Trailing metadata of a failed call: the service's own error code, and when a refused call may retry.
ERROR_CODE_KEY = 'error-code'
RETRY_AFTER_KEY = 'retry-after-seconds'
STATUS_OF: dict[str, grpc.StatusCode] = {
    ErrorKind.INVALID: grpc.StatusCode.INVALID_ARGUMENT,
    ErrorKind.NOT_FOUND: grpc.StatusCode.NOT_FOUND,
    ErrorKind.CONFLICT: grpc.StatusCode.FAILED_PRECONDITION,
    ErrorKind.IDEMPOTENCY_REUSED: grpc.StatusCode.INVALID_ARGUMENT,
    ErrorKind.IDEMPOTENCY_IN_PROGRESS: grpc.StatusCode.ABORTED,
    ErrorKind.RATE_LIMITED: grpc.StatusCode.RESOURCE_EXHAUSTED,
}

Answer = TypeVar('Answer')


@dataclass(frozen=True)
class CallError(Exception):
    """A call refused by this adapter (no key, a malformed request) before it reaches the service."""

    status: grpc.StatusCode
    code: str
    message: str
    details: list[str] = field(default_factory=list)


def answers(method: Callable[..., Answer]) -> Callable[..., Answer]:
    """Turns refusals and service errors into gRPC statuses; an unexpected error becomes INTERNAL, logged."""

    @functools.wraps(method)
    def call(self: 'PaperSearchServicer', request: Any, context: grpc.ServicerContext) -> Answer:
        try:
            return method(self, request, context)
        except CallError as error:
            _abort(context, error.status, error.code, error.message, error.details)
        except ServiceError as error:
            status = STATUS_OF.get(error.kind, grpc.StatusCode.INVALID_ARGUMENT)
            _abort(context, status, error.code, error.message, error.details, error.retry_after_seconds)
        except Exception:
            logger.exception('Unhandled error in %s', method.__name__)
            _abort(context, grpc.StatusCode.INTERNAL, 'internal_error', 'The server could not answer this call')
        raise AssertionError('unreachable: abort raises')

    return call


def _abort(
    context: grpc.ServicerContext,
    status: grpc.StatusCode,
    code: str,
    message: str,
    details: list[str] | None = None,
    retry_after_seconds: int | None = None,
) -> None:
    trailing = [(ERROR_CODE_KEY, code)]
    if retry_after_seconds is not None:
        trailing.append((RETRY_AFTER_KEY, str(retry_after_seconds)))
    context.set_trailing_metadata(tuple(trailing))
    context.abort(status, f'{message}: {"; ".join(details)}' if details else message)


class PaperSearchServicer(pb_grpc.PaperSearchServicer):
    def __init__(self, service: SearchService, keys: ApiKeys, codec: MessageCodec, chunk_bytes: int) -> None:
        self.service = service
        self.keys = keys
        self.codec = codec
        self.chunk_bytes = chunk_bytes

    @answers
    def CreateSearch(self, request: pb.CreateSearchRequest, context: grpc.ServicerContext) -> pb.Search:
        client = self._client(context)
        body = self._checked(self.codec.to_dict(request.search), 'search-request')
        submission = self.service.submit(client, body, self._idempotency_key(request.idempotency_key))
        return self._search(submission.job)

    @answers
    def GetSearch(self, request: pb.GetSearchRequest, context: grpc.ServicerContext) -> pb.Search:
        client = self._client(context)
        include = request.include_needs_review if request.HasField('include_needs_review') else None
        return self._search(self.service.job(client, request.search_id), include)

    @answers
    def ConfirmSearch(self, request: pb.ConfirmSearchRequest, context: grpc.ServicerContext) -> pb.Search:
        client = self._client(context)
        body = self._checked({'selection': request.selection}, 'confirmation-request')
        submission = self.service.confirm(
            client, request.search_id, body['selection'], self._idempotency_key(request.idempotency_key)
        )
        return self._search(submission.job)

    @answers
    def CancelSearch(self, request: pb.CancelSearchRequest, context: grpc.ServicerContext) -> pb.Search:
        return self._search(self.service.cancel(self._client(context), request.search_id))

    @answers
    def GetAgentResult(self, request: pb.GetAgentResultRequest, context: grpc.ServicerContext) -> pb.AgentResult:
        view = self.service.agent_view(self._client(context), request.search_id)
        return self.codec.to_message(_unframed(view), pb.AgentResult)

    @answers
    def DownloadPaperPdf(
        self, request: pb.DownloadPaperPdfRequest, context: grpc.ServicerContext
    ) -> Iterator[pb.PdfChunk]:
        # Found before the first chunk, so a missing file is a NOT_FOUND status rather than a broken stream.
        stored = self.service.paper_file(self._client(context), request.search_id, request.paper_id)
        return self._chunks(stored)

    # Helpers

    def _client(self, context: grpc.ServicerContext) -> str:
        metadata: Mapping[str, str] = {key.lower(): value for key, value in context.invocation_metadata()}
        key = self.keys.authenticate(metadata)
        if key is None:
            raise CallError(
                grpc.StatusCode.UNAUTHENTICATED,
                'unauthorized',
                'A valid API key is required (metadata "authorization: Bearer <key>")',
            )
        return key.id

    def _search(self, job: Job, include_needs_review: bool | None = None) -> pb.Search:
        return self.codec.to_message(_unframed(search_view(job, include_needs_review)), pb.Search)

    def _chunks(self, stored: StoredFile) -> Iterator[pb.PdfChunk]:
        if stored.url is not None:
            yield pb.PdfChunk(url=stored.url)
            return
        if stored.path is None:
            raise ServiceError(ErrorKind.NOT_FOUND, 'not_found', 'The file is no longer stored')
        with stored.path.open('rb') as handle:
            while piece := handle.read(self.chunk_bytes):
                yield pb.PdfChunk(data=piece)

    @staticmethod
    def _checked(body: dict[str, Any], schema_name: str) -> dict[str, Any]:
        errors = validator(schema_name).errors(body)
        if errors:
            raise CallError(
                grpc.StatusCode.INVALID_ARGUMENT,
                'invalid_request',
                f'The request does not match the {schema_name} schema',
                errors,
            )
        return body

    @staticmethod
    def _idempotency_key(token: str) -> str | None:
        if not token:
            return None
        if len(token) > MAXIMUM_IDEMPOTENCY_KEY:
            raise CallError(
                grpc.StatusCode.INVALID_ARGUMENT,
                'invalid_idempotency_key',
                f'idempotency_key must be 1-{MAXIMUM_IDEMPOTENCY_KEY} characters',
            )
        return token


def _unframed(view: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in view.items() if key not in REST_FRAMING}
