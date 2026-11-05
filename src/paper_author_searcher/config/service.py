"""The search service: HTTP, gRPC and MCP front ends, job queue and workers, result and file storage, index refresh.

Every field can be set as ``PAPER_SEARCH__SERVICE__<FIELD>``; object-store credentials come from
``AWS_ACCESS_KEY_ID`` and ``AWS_SECRET_ACCESS_KEY`` (settings.Credentials), never from here.
"""

from dataclasses import dataclass

MAXIMUM_TCP_PORT = 65535
API_KEY_ENTRY_SEPARATORS = 2
FILE_STORES = ('local', 's3')
S3_ADDRESSING = ('path', 'virtual')


@dataclass(frozen=True)
class ServiceConfig:
    host: str = '127.0.0.1'
    port: int = 8080
    # Jobs database, run directories, the local file store and the keys file live here unless set otherwise.
    data_directory: str = 'output/api'
    # API keys file (default: DATA_DIRECTORY/api_keys.json) and/or keys given as "id:name:sha256" entries,
    # e.g. from a secret store; the SHA-256 is of the key itself (paper-search-api create-key prints both).
    keys_file: str = ''
    api_key_hashes: tuple[str, ...] = ()
    # Worker threads inside `serve`; 0 leaves every search to separate `paper-search-api worker` processes.
    workers: int = 1
    maximum_cost_usd: float = 0.5
    # A search past this stops asking sources and returns what it has found, with a notice (0: no limit). One
    # prolific author would otherwise hold a worker for hours while other searches wait.
    maximum_search_seconds: float = 3600.0
    # Every worker of a deployment shares these (host, seconds between requests) intervals and daily budgets, so
    # adding workers does not add traffic past what a source allows. They replace http.host_request_intervals.
    shared_host_intervals: tuple[tuple[str, float], ...] = (
        ('api.openalex.org', 0.1),
        ('api.semanticscholar.org', 1.0),
        ('api.crossref.org', 0.1),
        ('api.unpaywall.org', 0.1),
        ('www.ebi.ac.uk', 0.1),
        ('api.openaire.eu', 0.5),
        ('export.arxiv.org', 3.0),
        # MIT's repository answers 429 to faster downloads.
        ('dspace.mit.edu', 2.0),
        ('api.tavily.com', 0.5),
        ('api.search.brave.com', 1.0),
        ('api.exa.ai', 0.5),
    )
    # A metered host's spend per day (UTC) across all searches; past it, that source is refused for the day and
    # results say a source did not respond. OpenAlex's free quota is $1 a day. Raise it after buying more.
    maximum_daily_cost_usd: float = 1.0
    maximum_body_bytes: int = 64 * 1024
    # gRPC front end (paper-search-api serve-grpc; contract in proto/). It speaks TLS when both files are set;
    # Cloudflare Tunnel needs that, since it reaches a gRPC origin only over HTTP/2 with TLS. Without them it is
    # plaintext, for local use. Calls run on grpc_threads threads; a PDF streams in grpc_chunk_bytes pieces.
    grpc_host: str = '127.0.0.1'
    grpc_port: int = 50051
    grpc_threads: int = 8
    grpc_tls_certificate_file: str = ''
    grpc_tls_key_file: str = ''
    grpc_chunk_bytes: int = 64 * 1024
    # MCP front end (paper-search-api serve-mcp): Streamable HTTP at mcp_path, for AI assistants. Callers send an API
    # key as a bearer token. mcp_public_url is the address clients use (the OAuth resource identifier the server
    # advertises); mcp_allowed_hosts are the Host headers it answers (DNS-rebinding protection; "name:*" = any port).
    # A status call may wait up to mcp_maximum_wait_seconds for a search to change, looking every mcp_poll_seconds;
    # keep it under the proxy's request limit (Cloudflare: about 100 seconds).
    mcp_host: str = '127.0.0.1'
    mcp_port: int = 8090
    mcp_path: str = '/mcp'
    mcp_public_url: str = 'http://127.0.0.1:8090/mcp'
    mcp_allowed_hosts: tuple[str, ...] = ('127.0.0.1:*', 'localhost:*')
    mcp_maximum_wait_seconds: float = 50.0
    mcp_poll_seconds: float = 2.0
    # Per API key: searches waiting or running at once, and submitted in the last hour (0: no limit). A refusal is
    # 429 with Retry-After. Each search costs real money, so a runaway caller must stop by itself.
    maximum_active_searches: int = 20
    maximum_searches_per_hour: int = 60
    rate_limit_retry_seconds: int = 60
    # A search that took down its worker this many times fails instead of being queued again.
    maximum_attempts: int = 3
    # Finished searches and their files are deleted after retention_days, HTTP cache entries older than
    # http_cache_retention_days (0 keeps them). Workers check every housekeeping_hours between searches.
    retention_days: float = 30.0
    http_cache_retention_days: float = 90.0
    housekeeping_hours: float = 6.0
    idempotency_hours: float = 24.0
    # Where checked PDFs, zips and identity checkpoints are kept: 'local' (DATA_DIRECTORY/files) or 's3'
    # (any S3-compatible store: AWS, MinIO, R2). Downloads from S3 are redirects to short-lived signed URLs.
    file_store: str = 'local'
    s3_endpoint: str = ''
    # Where callers reach the store, if not s3_endpoint (inside compose the service uses http://minio:9000, a
    # browser http://localhost:9000). Download links are signed for this host.
    s3_public_endpoint: str = ''
    s3_bucket: str = ''
    s3_region: str = 'us-east-1'
    s3_prefix: str = 'paper-search/'
    # 'path' (endpoint/bucket/key: MinIO and most compatible stores) or 'virtual' (bucket.endpoint/key: AWS).
    s3_addressing: str = 'path'
    presign_seconds: int = 900
    # HTTP response cache shared by all searches ('' disables it); mode as RunOptions.http_cache_mode.
    http_cache_directory: str = ''
    http_cache_mode: str = 'record'
    # A worker looks for queued jobs this often, reports it is alive this often, and a running job whose worker
    # has not reported for stale_after_seconds is queued again (the worker died; the search restarts).
    worker_poll_seconds: float = 2.0
    heartbeat_seconds: float = 15.0
    stale_after_seconds: float = 120.0
    # Run directories are scratch once their files are stored; keep them only to debug a run.
    keep_run_directories: bool = False
    # Readiness also requires the DBLP index and catalog store (dblp_dump.index_path, catalogs.store_path).
    require_indexes: bool = False
    # Index refresh (paper-search-api refresh-indexes): the dump to download and how often to rebuild.
    dblp_dump_url: str = 'https://dblp.org/xml/dblp.xml.gz'
    refresh_hours: float = 168.0
    # Agent contract (GET /v1/searches/{id}/agent): a verified paper is 'high' confidence when one of these
    # ownership findings backs it (the person's own list, a paper-level ORCID, a curated DBLP profile), otherwise
    # 'medium' (author ID with matching byline and institution, verified coauthors). Needs-review papers are
    # withheld. Each paper carries at most maximum_paper_evidence of its findings.
    strong_ownership_evidence: tuple[str, ...] = (
        "Listed on the researcher's own publication list",
        "Retrieved because the researcher's own publication list names this work",
        'Target authorship ORCID agrees with confirmed professor ORCID',
        "Curated dblp author profile carrying the person's ORCID",
        'Curated dblp author profile whose own affiliation record placed the person',
    )
    supporting_ownership_evidence: tuple[str, ...] = (
        'Explicit target Author ID, compatible byline and paper-level institution agree',
        'Compatible signature plus recurring independently verified coauthors',
        'Cross-source stable work identity and confirmed source-scoped target Author IDs',
        'Explicit target Author ID and compatible byline under a name no other author holds in the source',
    )
    maximum_paper_evidence: int = 3
    # A link for the person to open each paper at its publisher (service/publisher_links). A DOI starting with a
    # rule's prefix gets that publisher's PDF address, (DOI prefix, publisher name, template with {doi}); ACM serves
    # every paper there, free to read in a browser though indexes call it closed. Any other DOI gets doi_page_template
    # (empty: no link). The service never fetches these addresses.
    publisher_pdf_links: tuple[tuple[str, str, str], ...] = (('10.1145/', 'ACM', 'https://dl.acm.org/doi/pdf/{doi}'),)
    doi_page_template: str = 'https://doi.org/{doi}'

    def __post_init__(self) -> None:
        if self.file_store not in FILE_STORES:
            raise ValueError(f'file_store must be one of {", ".join(FILE_STORES)}')
        if self.s3_addressing not in S3_ADDRESSING:
            raise ValueError(f's3_addressing must be one of {", ".join(S3_ADDRESSING)}')
        if self.file_store == 's3' and not (self.s3_endpoint and self.s3_bucket):
            raise ValueError('file_store s3 needs s3_endpoint and s3_bucket')
        if self.workers < 0 or not 0 < self.port <= MAXIMUM_TCP_PORT:
            raise ValueError('workers must not be negative and port must be a TCP port')
        if not (0 < self.grpc_port <= MAXIMUM_TCP_PORT and 0 < self.mcp_port <= MAXIMUM_TCP_PORT):
            raise ValueError('grpc_port and mcp_port must be TCP ports')
        if not self.mcp_path.startswith('/'):
            raise ValueError('mcp_path must start with /')
        if bool(self.grpc_tls_certificate_file) != bool(self.grpc_tls_key_file):
            raise ValueError('grpc_tls_certificate_file and grpc_tls_key_file are set together')
        for name in (
            'maximum_cost_usd',
            'maximum_daily_cost_usd',
            'idempotency_hours',
            'worker_poll_seconds',
            'heartbeat_seconds',
            'refresh_hours',
            'rate_limit_retry_seconds',
            'maximum_attempts',
            'housekeeping_hours',
            'grpc_threads',
            'grpc_chunk_bytes',
            'mcp_maximum_wait_seconds',
            'mcp_poll_seconds',
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f'{name} must be positive')
        if self.stale_after_seconds <= self.heartbeat_seconds:
            raise ValueError('stale_after_seconds must exceed heartbeat_seconds')
        templates = [template for *_, template in self.publisher_pdf_links]
        if self.doi_page_template:
            templates.append(self.doi_page_template)
        if any('{doi}' not in template for template in templates):
            raise ValueError('publisher_pdf_links and doi_page_template need {doi} in each address')
        for name in (
            'maximum_search_seconds',
            'maximum_active_searches',
            'maximum_searches_per_hour',
            'retention_days',
            'http_cache_retention_days',
        ):
            if getattr(self, name) < 0:
                raise ValueError(f'{name} must not be negative')
        for entry in self.api_key_hashes:
            if entry.count(':') != API_KEY_ENTRY_SEPARATORS:
                raise ValueError('api_key_hashes entries look like id:name:sha256')


DEFAULT_SERVICE = ServiceConfig()
