"""Bounded HTTP requests with verified TLS and explicit format diagnostics."""

import hashlib
import ipaddress
import json
import logging
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from http.client import HTTPException
from typing import Any, Protocol, TypeVar
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request

from .config import DEFAULT_DOWNLOAD, DEFAULT_HTTP, DownloadConfig, HttpConfig
from .http_status import is_server_error, is_success
from .http_transport import KeepAliveTransport
from .network_safety import secure_download_opener
from .redaction import redact

logger = logging.getLogger(__name__)

T = TypeVar('T')


class MetadataReader(Protocol):
    """Reads one metadata document: the HTTP client, or a local index that answers the same URLs (DBLP dump)."""

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        xml: bool = False,
    ) -> Any: ...


class HttpTransport(MetadataReader, Protocol):
    """What services ask of an HTTP client: ``HttpClient`` and every layer the composition root wraps around it."""

    def post_json(
        self,
        url: str,
        payload: Any,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected_type: type = dict,
    ) -> Any: ...

    def probe(
        self,
        url: str,
        maximum_bytes: int = 1024,
        html_bytes: int = 0,
        maximum_redirects: int = 5,
        redirect_guard: Callable[[str], None] | None = None,
    ) -> Any: ...

    def stream_pdf(self, url: str, destination: Any, config: Any) -> Any: ...


# Characters up to this one (space) never appear in a URL we should fetch.
LAST_CONTROL_CHARACTER = 32
# A download whose first bytes, this many of them, show no PDF signature is not a PDF.
PDF_SIGNATURE_WINDOW = 1024


class SourceError(RuntimeError):
    def __init__(
        self,
        message: object,
        status_code: int | None = None,
        error_type: str | None = None,
        retry_after: float | None = None,
        endpoint_host: str | None = None,
        request_context: dict[str, Any] | None = None,
    ) -> None:
        # Source errors end up in logs and saved reports: never let a key or contact address ride along.
        super().__init__(redact(str(message)))
        self.status_code = status_code
        self.retry_after = retry_after
        self.error_type = error_type or self._error_type(status_code)
        self.endpoint_host = endpoint_host
        self.request_context = request_context or {}

    @staticmethod
    def _error_type(status: int | None) -> str:
        categories = {
            400: 'invalid_request',
            401: 'authentication_required',
            403: 'access_restricted',
            404: 'not_found',
            429: 'rate_limited',
        }
        if status in categories:
            return categories[status]
        if status is not None and is_server_error(status):
            return 'server_error'
        return 'source_error'


def require_public_http_url(url: object) -> None:
    if not isinstance(url, str) or any(ord(character) <= LAST_CONTROL_CHARACTER for character in url):
        raise SourceError('Malformed resource URL')
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise SourceError('Resource URL must be public HTTP(S) without credentials')
    hostname = parsed.hostname.lower()
    if hostname == 'localhost' or hostname.endswith(('.localhost', '.local')):
        raise SourceError('Local resource URLs are not supported')
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise SourceError('Nonpublic resource addresses are not supported')


class PublicRedirectHandler(HTTPRedirectHandler):
    def __init__(self, maximum_redirects: int, redirect_guard: Callable[[str], None] | None = None) -> None:
        # Per-instance limits shadow urllib's class-level defaults on purpose.
        self.max_redirections = maximum_redirects  # type: ignore[misc]
        self.max_repeats = maximum_redirects  # type: ignore[misc]
        self.redirect_guard = redirect_guard

    def redirect_request(
        self, request: Request, response: Any, code: int, message: str, headers: Any, new_url: str
    ) -> Request | None:
        require_public_http_url(new_url)
        if self.redirect_guard:
            self.redirect_guard(new_url)
        return super().redirect_request(request, response, code, message, headers, new_url)


@dataclass
class URLProbe:
    url: str
    status: int
    content_type: str
    headers: dict
    prefix: bytes
    body_complete: bool = False
    request_context: dict = field(default_factory=dict)


def has_pdf_signature(prefix: bytes | bytearray) -> bool:
    return prefix.lstrip().startswith(b'%PDF-')


@dataclass
class DownloadReceipt:
    final_url: str
    status: int
    content_type: str
    file_size: int
    sha256: str


class HttpClient:
    """Bounded JSON/XML API client with retries; metadata calls reuse pooled keep-alive connections."""

    def __init__(
        self,
        timeout: float | None = None,
        retries: int | None = None,
        config: HttpConfig = DEFAULT_HTTP,
        transport: Any = None,
    ) -> None:
        overrides: dict[str, Any] = {}
        if timeout is not None:
            overrides['timeout'] = timeout
        if retries is not None:
            overrides['retries'] = retries
        self.config = replace(config, **overrides)
        self.timeout = self.config.timeout
        self.retries = self.config.retries
        self.transport_statistics: dict[str, dict[str, Any]] = {}
        self._statistics_lock = threading.Lock()
        # Strategy for metadata requests: anything with urlopen's open(request, timeout) contract.
        self.transport = transport or KeepAliveTransport()

    def _count(self, counters: dict[str, Any], key: str) -> None:
        with self._statistics_lock:
            counters[key] = counters.get(key, 0) + 1

    def close(self) -> None:
        close = getattr(self.transport, 'close', None)
        if callable(close):
            close()

    def __enter__(self) -> 'HttpClient':
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        xml: bool = False,
    ) -> Any:
        request = self._request(url, params, headers, xml)
        return self._with_retries(request, lambda: self._read_response(request, xml), 'metadata_get')

    def post_json(
        self,
        url: str,
        payload: Any,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected_type: type = dict,
    ) -> Any:
        request = self._request(url, params, headers, xml=False)
        request.data = json.dumps(payload).encode('utf-8')
        request.add_header('Content-Type', 'application/json')
        return self._with_retries(request, lambda: self._read_response(request, False, expected_type), 'metadata_post')

    def _with_retries(self, request: Request, read_response: Callable[[], T], operation: str = 'request') -> T:
        with self._statistics_lock:
            statistics = self.transport_statistics.setdefault(
                operation,
                {
                    'operation_attempts': 0,
                    'completed_operations': 0,
                    'http_error_responses': {},
                    'network_errors': 0,
                    'response_errors': 0,
                    'deferred_operations': 0,
                    'scope': 'Read/transfer attempts include retries; redirect hops are not individual attempts',
                },
            )
        # A rate-limited host may get more retries for its 429s (HttpConfig.rate_limit_retries); a client set to
        # no retries at all keeps none.
        policy = {host: (retries, wait) for host, retries, wait in self.config.rate_limit_retries}
        rate_limited, longest_wait = policy.get(request.host, (self.retries, self.config.maximum_retry_delay))
        rate_limited = max(rate_limited, self.retries) if self.retries else 0
        for attempt in range(max(self.retries, rate_limited) + 1):
            self._count(statistics, 'operation_attempts')
            try:
                response = read_response()
                self._count(statistics, 'completed_operations')
                return response
            except HTTPError as exc:
                code = str(exc.code)
                self._count(statistics['http_error_responses'], code)
                retryable = exc.code in self.config.retry_statuses
                retry_after = self._retry_after(exc)
                throttled = exc.code == HTTPStatus.TOO_MANY_REQUESTS
                longest = longest_wait if throttled else self.config.maximum_retry_delay
                wait_exceeds_budget = retry_after is not None and retry_after > longest
                allowed = rate_limited if throttled else self.retries
                if not retryable or attempt >= allowed or wait_exceeds_budget:
                    endpoint_host = urlsplit(exc.filename or request.full_url).hostname or request.host
                    message = f'HTTP {exc.code} from {endpoint_host}'
                    # Do not retry sooner than the server permits; let callers defer this source.
                    raise SourceError(
                        message, status_code=exc.code, retry_after=retry_after, endpoint_host=endpoint_host
                    ) from exc
                delay = min(self.config.retry_backoff_base**attempt if retry_after is None else retry_after, longest)
                logger.info(
                    'HTTP %s from %s; retry %d/%d in %.1fs', exc.code, request.host, attempt + 1, allowed, delay
                )
                time.sleep(delay)
            except (OSError, HTTPException) as exc:
                self._count(statistics, 'network_errors')
                if attempt >= self.retries:
                    kind = 'network_error'
                    if isinstance(exc, TimeoutError) or 'timed out' in str(exc).lower():
                        kind = 'timeout'
                    raise SourceError(f'{request.host}: {exc}', error_type=kind) from exc
                delay = min(self.config.retry_backoff_base**attempt, self.config.maximum_retry_delay)
                logger.info('%s: %s; retry %d/%d in %.1fs', request.host, exc, attempt + 1, self.retries, delay)
                time.sleep(delay)
            except (ValueError, ET.ParseError) as exc:
                self._count(statistics, 'response_errors')
                raise SourceError(f'{request.host}: {exc}') from exc
            except SourceError as exc:
                if exc.error_type == 'source_deferred':
                    self._count(statistics, 'deferred_operations')
                else:
                    self._count(statistics, 'response_errors')
                raise
        # Every attempt returns or raises: the last one has no retries left.
        raise AssertionError('unreachable: retries exhausted without an error')

    def probe(
        self,
        url: str,
        maximum_bytes: int = 1024,
        html_bytes: int = 0,
        maximum_redirects: int = 5,
        redirect_guard: Callable[[str], None] | None = None,
    ) -> URLProbe:
        """Read a bounded prefix, even when a server ignores Range; never save a body."""
        require_public_http_url(url)
        if maximum_bytes < 1 or html_bytes < 0:
            raise ValueError('Probe limits must be positive')
        request = self._request(
            url,
            headers={
                'Range': f'bytes=0-{max(maximum_bytes, html_bytes) - 1}',
                'Accept': 'application/pdf,text/html;q=0.8,*/*;q=0.1',
                'Accept-Encoding': 'identity',
            },
            params=None,
            xml=False,
        )
        opener = secure_download_opener(
            PublicRedirectHandler(maximum_redirects, redirect_guard), DEFAULT_DOWNLOAD.allowed_ports
        )

        def read_prefix() -> URLProbe:
            with opener.open(request, timeout=self.timeout) as response:
                content_type = response.headers.get('Content-Type', '').lower().split(';', 1)[0]
                limit = maximum_bytes
                if html_bytes and content_type in ('text/html', 'application/xhtml+xml'):
                    limit = html_bytes
                prefix = response.read(limit)
                final_url = response.geturl()
                require_public_http_url(final_url)
                complete = len(prefix) < limit
                length = response.headers.get('Content-Length', '')
                if response.status == HTTPStatus.OK and length.isdigit() and int(length) == len(prefix):
                    complete = True
                if response.status == HTTPStatus.PARTIAL_CONTENT:
                    value = response.headers.get('Content-Range', '')
                    match = re.fullmatch(r'bytes 0-(\d+)/(\d+)', value)
                    if match and int(match.group(1)) + 1 == int(match.group(2)) == len(prefix):
                        complete = True
                return URLProbe(
                    final_url, response.status, content_type, dict(response.headers.items()), prefix, complete
                )

        return self._with_retries(request, read_prefix, 'pdf_probe')

    def stream_pdf(self, url: str, destination: Any, config: DownloadConfig) -> DownloadReceipt:
        """Restart failed transfers from byte zero; validate public DNS on every connection."""
        require_public_http_url(url)
        request = self._request(
            url,
            params=None,
            headers={
                'Accept': 'application/pdf,application/octet-stream;q=0.8',
                'Accept-Encoding': 'identity',
            },
            xml=False,
        )
        opener = secure_download_opener(PublicRedirectHandler(config.maximum_redirects), config.allowed_ports)

        def transfer() -> DownloadReceipt:
            deadline = time.monotonic() + config.maximum_transfer_seconds
            with opener.open(request, timeout=self.timeout) as response:
                final_url = response.geturl()
                require_public_http_url(final_url)
                content_type = response.headers.get('Content-Type', '').lower().split(';', 1)[0]
                expected_size = self._download_size(response, content_type, config)
                digest = hashlib.sha256()
                size = 0
                prefix = bytearray()
                signature_confirmed = False
                read_block = getattr(response, 'read1', response.read)
                with open(destination, 'wb') as handle:
                    while True:
                        if time.monotonic() > deadline:
                            raise SourceError('Download exceeded the configured total transfer deadline')
                        block = read_block(min(config.chunk_bytes, config.maximum_file_bytes - size + 1))
                        if not block:
                            break
                        if not signature_confirmed:
                            prefix.extend(block[: max(0, PDF_SIGNATURE_WINDOW - len(prefix))])
                            signature_confirmed = has_pdf_signature(prefix)
                            if len(prefix) >= PDF_SIGNATURE_WINDOW and not signature_confirmed:
                                raise SourceError('Download does not begin with a PDF file signature')
                        size += len(block)
                        if size > config.maximum_file_bytes:
                            raise SourceError('Download exceeds the configured file size limit')
                        handle.write(block)
                        digest.update(block)
                    handle.flush()
                    os.fsync(handle.fileno())
                if not signature_confirmed:
                    raise SourceError('Download does not begin with a PDF file signature')
                if not size or (expected_size is not None and size != expected_size):
                    raise OSError('Downloaded size does not match the complete HTTP response')
                return DownloadReceipt(final_url, response.status, content_type, size, digest.hexdigest())

        return self._with_retries(request, transfer, 'pdf_download')

    def _download_size(self, response: Any, content_type: str, config: DownloadConfig) -> int | None:
        if response.status not in (200, 206):
            raise SourceError(f'Unexpected download HTTP {response.status}', response.status)
        if content_type in ('text/html', 'application/xhtml+xml'):
            raise SourceError('Download is an HTML page, not a PDF')
        encoding = response.headers.get('Content-Encoding', 'identity').lower()
        if encoding not in ('', 'identity'):
            raise SourceError('Unexpected compressed HTTP response')
        length = response.headers.get('Content-Length')
        expected = None
        if length is not None:
            if not length.isdigit():
                raise SourceError('Invalid Content-Length')
            expected = int(length)
        if response.status == HTTPStatus.PARTIAL_CONTENT:
            match = re.fullmatch(r'bytes 0-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
            if not match or int(match.group(1)) + 1 != int(match.group(2)):
                raise SourceError('Unexpected partial download; a complete response is required')
            total = int(match.group(2))
            if expected is not None and expected != total:
                raise SourceError('Content-Length conflicts with Content-Range')
            expected = total
        if expected is not None and expected > config.maximum_file_bytes:
            raise SourceError('Declared download size exceeds the configured limit')
        return expected

    def _request(self, url: str, params: dict[str, Any] | None, headers: dict[str, str] | None, xml: bool) -> Request:
        if params:
            supplied_params = {}
            for key, value in params.items():
                if value is not None:
                    supplied_params[key] = value
            url += '?' + urlencode(supplied_params)
        accept = 'application/json'
        if xml:
            accept = 'application/xml,text/xml'
        request_headers = {'User-Agent': self.config.user_agent, 'Accept': accept}
        request_headers.update(headers or {})
        return Request(url, headers=request_headers)

    def _read_response(self, request: Request, xml: bool, expected_type: type = dict) -> Any:
        with self.transport.open(request, timeout=self.timeout) as response:
            data = response.read(self.config.response_limit + 1)
            status = response.status
            content_type = response.headers.get('Content-Type', '').lower()
            endpoint = response.geturl().split('?', 1)[0]
        if len(data) > self.config.response_limit:
            raise SourceError('response exceeds size limit')
        diagnostic = '{} HTTP {} Content-Type {}'.format(
            endpoint,
            status,
            content_type or 'missing',
        )
        if not is_success(status):
            raise SourceError(diagnostic, status_code=status)
        return self._parse_response(data, content_type, diagnostic, xml, expected_type)

    def _parse_response(
        self, data: bytes, content_type: str, diagnostic: str, xml: bool, expected_type: type = dict
    ) -> Any:
        expected = 'json'
        if xml:
            expected = 'xml'
        if expected not in content_type:
            hint = ''
            beginning = data[: self.config.html_preview_bytes].lower()
            if b'<html' in beginning or b'<!doctype html' in beginning:
                hint = ' (HTML anti-bot/interstitial response)'
            raise SourceError(diagnostic + ': expected ' + expected.upper() + hint, error_type='invalid_content_type')
        if not data.strip():
            raise SourceError(diagnostic + ': empty response', error_type='empty_response')
        try:
            if xml:
                return ET.fromstring(data)
            parsed = json.loads(data)
        except (ValueError, ET.ParseError) as exc:
            raise SourceError(diagnostic + ': malformed ' + expected.upper(), error_type='malformed_response') from exc
        if not isinstance(parsed, expected_type):
            label = 'array' if expected_type is list else 'object'
            raise SourceError(diagnostic + ': expected JSON ' + label, error_type='malformed_response')
        return parsed

    @staticmethod
    def _retry_after(error: HTTPError) -> float | None:
        value = (error.headers.get('Retry-After', '') if error.headers else '').strip()
        if value.isdigit():
            return float(value)
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None
