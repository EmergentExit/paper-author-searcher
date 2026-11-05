"""The gRPC server: ``paper-search-api serve-grpc``.

It serves the PaperSearch service (proto/paper_author_searcher/rpc/v1/paper_search.proto) and the standard
grpc.health.v1 health service over the same job store as the REST API; searches run in the worker processes.
"""

import logging
import signal
import threading
from concurrent import futures
from pathlib import Path

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from paper_author_searcher.api.auth import ApiKeys
from paper_author_searcher.config.service import ServiceConfig
from paper_author_searcher.service.facade import SearchService

from .codec import default_codec
from .servicer import PaperSearchServicer
from .v1 import paper_search_pb2, paper_search_pb2_grpc

logger = logging.getLogger(__name__)

SERVICE_NAME = paper_search_pb2.DESCRIPTOR.services_by_name['PaperSearch'].full_name
# Seconds a stopping server lets calls in flight finish, and a health check waits for its answer.
SHUTDOWN_GRACE_SECONDS = 10.0
HEALTH_CHECK_SECONDS = 4.0
# The name a health check expects on the server's own certificate (one made for the local origin).
LOCAL_SERVER_NAME = 'localhost'


def credentials(config: ServiceConfig) -> grpc.ServerCredentials | None:
    """TLS credentials when the certificate and key are configured, else None (plaintext)."""
    if not config.grpc_tls_certificate_file:
        return None
    certificate = Path(config.grpc_tls_certificate_file).read_bytes()
    key = Path(config.grpc_tls_key_file).read_bytes()
    return grpc.ssl_server_credentials([(key, certificate)])


def build_server(service: SearchService, keys: ApiKeys, config: ServiceConfig) -> tuple[grpc.Server, int]:
    """A server (not started) with the search and health services; returns it and the port it is bound to."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=config.grpc_threads, thread_name_prefix='grpc'))
    servicer = PaperSearchServicer(service, keys, default_codec(), config.grpc_chunk_bytes)
    paper_search_pb2_grpc.add_PaperSearchServicer_to_server(servicer, server)
    checks = health.HealthServicer()
    for name in ('', SERVICE_NAME):
        checks.set(name, health_pb2.HealthCheckResponse.SERVING)
    health_pb2_grpc.add_HealthServicer_to_server(checks, server)
    address = f'{config.grpc_host}:{config.grpc_port}'
    secure = credentials(config)
    port = server.add_secure_port(address, secure) if secure else server.add_insecure_port(address)
    return server, port


def serve_until_stopped(server: grpc.Server, port: int, secure: bool) -> None:
    """Start ``server`` and block until SIGTERM or Ctrl-C, then let calls in flight finish."""
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    server.start()
    logger.warning('Serving gRPC on port %s (%s)', port, 'TLS' if secure else 'plaintext')
    try:
        stopped.wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop(SHUTDOWN_GRACE_SECONDS).wait()


def healthy(config: ServiceConfig) -> bool:
    """Whether the server at grpc_port of this machine answers SERVING (the container's health check). Over TLS it
    trusts the configured certificate itself, for the name localhost."""
    target = f'127.0.0.1:{config.grpc_port}'
    if config.grpc_tls_certificate_file:
        trusted = grpc.ssl_channel_credentials(Path(config.grpc_tls_certificate_file).read_bytes())
        channel = grpc.secure_channel(target, trusted, (('grpc.ssl_target_name_override', LOCAL_SERVER_NAME),))
    else:
        channel = grpc.insecure_channel(target)
    with channel:
        try:
            answer = health_pb2_grpc.HealthStub(channel).Check(
                health_pb2.HealthCheckRequest(service=SERVICE_NAME), timeout=HEALTH_CHECK_SECONDS
            )
        except grpc.RpcError:
            return False
    return answer.status == health_pb2.HealthCheckResponse.SERVING
