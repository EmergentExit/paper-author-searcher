from google.protobuf import descriptor_pb2 as _descriptor_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Mode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MODE_UNSPECIFIED: _ClassVar[Mode]
    MODE_METADATA: _ClassVar[Mode]
    MODE_PDF_URLS: _ClassVar[Mode]
    MODE_FULL: _ClassVar[Mode]

class OnAmbiguity(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ON_AMBIGUITY_UNSPECIFIED: _ClassVar[OnAmbiguity]
    ON_AMBIGUITY_CONFIRM: _ClassVar[OnAmbiguity]
    ON_AMBIGUITY_COLLECT_CONTENDERS: _ClassVar[OnAmbiguity]

class SearchStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SEARCH_STATUS_UNSPECIFIED: _ClassVar[SearchStatus]
    SEARCH_STATUS_QUEUED: _ClassVar[SearchStatus]
    SEARCH_STATUS_RUNNING: _ClassVar[SearchStatus]
    SEARCH_STATUS_NEEDS_CONFIRMATION: _ClassVar[SearchStatus]
    SEARCH_STATUS_SUCCEEDED: _ClassVar[SearchStatus]
    SEARCH_STATUS_FAILED: _ClassVar[SearchStatus]
    SEARCH_STATUS_CANCELLED: _ClassVar[SearchStatus]

class RunStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    RUN_STATUS_UNSPECIFIED: _ClassVar[RunStatus]
    RUN_STATUS_COMPLETED: _ClassVar[RunStatus]
    RUN_STATUS_PARTIAL: _ClassVar[RunStatus]

class OwnershipStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    OWNERSHIP_STATUS_UNSPECIFIED: _ClassVar[OwnershipStatus]
    OWNERSHIP_STATUS_VERIFIED: _ClassVar[OwnershipStatus]
    OWNERSHIP_STATUS_NEEDS_REVIEW: _ClassVar[OwnershipStatus]

class Confidence(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CONFIDENCE_UNSPECIFIED: _ClassVar[Confidence]
    CONFIDENCE_HIGH: _ClassVar[Confidence]
    CONFIDENCE_MEDIUM: _ClassVar[Confidence]
    CONFIDENCE_LOW: _ClassVar[Confidence]

class FullTextStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    FULL_TEXT_STATUS_UNSPECIFIED: _ClassVar[FullTextStatus]
    FULL_TEXT_STATUS_FILE: _ClassVar[FullTextStatus]
    FULL_TEXT_STATUS_LINK: _ClassVar[FullTextStatus]
    FULL_TEXT_STATUS_NONE: _ClassVar[FullTextStatus]

class PublisherLinkKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    PUBLISHER_LINK_KIND_UNSPECIFIED: _ClassVar[PublisherLinkKind]
    PUBLISHER_LINK_KIND_PDF: _ClassVar[PublisherLinkKind]
    PUBLISHER_LINK_KIND_PAGE: _ClassVar[PublisherLinkKind]

class AgentStatus(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    AGENT_STATUS_UNSPECIFIED: _ClassVar[AgentStatus]
    AGENT_STATUS_READY: _ClassVar[AgentStatus]
    AGENT_STATUS_NOTHING_USABLE: _ClassVar[AgentStatus]
    AGENT_STATUS_PENDING: _ClassVar[AgentStatus]
    AGENT_STATUS_NEEDS_CONFIRMATION: _ClassVar[AgentStatus]
    AGENT_STATUS_FAILED: _ClassVar[AgentStatus]
    AGENT_STATUS_CANCELLED: _ClassVar[AgentStatus]

class ConfirmedBy(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CONFIRMED_BY_UNSPECIFIED: _ClassVar[ConfirmedBy]
    CONFIRMED_BY_EVIDENCE: _ClassVar[ConfirmedBy]
    CONFIRMED_BY_CALLER: _ClassVar[ConfirmedBy]

class WithheldReason(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    WITHHELD_REASON_UNSPECIFIED: _ClassVar[WithheldReason]
    WITHHELD_REASON_IDENTITY_UNCONFIRMED: _ClassVar[WithheldReason]
    WITHHELD_REASON_OWNERSHIP_UNVERIFIED: _ClassVar[WithheldReason]
MODE_UNSPECIFIED: Mode
MODE_METADATA: Mode
MODE_PDF_URLS: Mode
MODE_FULL: Mode
ON_AMBIGUITY_UNSPECIFIED: OnAmbiguity
ON_AMBIGUITY_CONFIRM: OnAmbiguity
ON_AMBIGUITY_COLLECT_CONTENDERS: OnAmbiguity
SEARCH_STATUS_UNSPECIFIED: SearchStatus
SEARCH_STATUS_QUEUED: SearchStatus
SEARCH_STATUS_RUNNING: SearchStatus
SEARCH_STATUS_NEEDS_CONFIRMATION: SearchStatus
SEARCH_STATUS_SUCCEEDED: SearchStatus
SEARCH_STATUS_FAILED: SearchStatus
SEARCH_STATUS_CANCELLED: SearchStatus
RUN_STATUS_UNSPECIFIED: RunStatus
RUN_STATUS_COMPLETED: RunStatus
RUN_STATUS_PARTIAL: RunStatus
OWNERSHIP_STATUS_UNSPECIFIED: OwnershipStatus
OWNERSHIP_STATUS_VERIFIED: OwnershipStatus
OWNERSHIP_STATUS_NEEDS_REVIEW: OwnershipStatus
CONFIDENCE_UNSPECIFIED: Confidence
CONFIDENCE_HIGH: Confidence
CONFIDENCE_MEDIUM: Confidence
CONFIDENCE_LOW: Confidence
FULL_TEXT_STATUS_UNSPECIFIED: FullTextStatus
FULL_TEXT_STATUS_FILE: FullTextStatus
FULL_TEXT_STATUS_LINK: FullTextStatus
FULL_TEXT_STATUS_NONE: FullTextStatus
PUBLISHER_LINK_KIND_UNSPECIFIED: PublisherLinkKind
PUBLISHER_LINK_KIND_PDF: PublisherLinkKind
PUBLISHER_LINK_KIND_PAGE: PublisherLinkKind
AGENT_STATUS_UNSPECIFIED: AgentStatus
AGENT_STATUS_READY: AgentStatus
AGENT_STATUS_NOTHING_USABLE: AgentStatus
AGENT_STATUS_PENDING: AgentStatus
AGENT_STATUS_NEEDS_CONFIRMATION: AgentStatus
AGENT_STATUS_FAILED: AgentStatus
AGENT_STATUS_CANCELLED: AgentStatus
CONFIRMED_BY_UNSPECIFIED: ConfirmedBy
CONFIRMED_BY_EVIDENCE: ConfirmedBy
CONFIRMED_BY_CALLER: ConfirmedBy
WITHHELD_REASON_UNSPECIFIED: WithheldReason
WITHHELD_REASON_IDENTITY_UNCONFIRMED: WithheldReason
WITHHELD_REASON_OWNERSHIP_UNVERIFIED: WithheldReason
REST_VALUE_FIELD_NUMBER: _ClassVar[int]
rest_value: _descriptor.FieldDescriptor

class CreateSearchRequest(_message.Message):
    __slots__ = ("search", "idempotency_key")
    SEARCH_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    search: SearchRequest
    idempotency_key: str
    def __init__(self, search: _Optional[_Union[SearchRequest, _Mapping]] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class GetSearchRequest(_message.Message):
    __slots__ = ("search_id", "include_needs_review")
    SEARCH_ID_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_NEEDS_REVIEW_FIELD_NUMBER: _ClassVar[int]
    search_id: str
    include_needs_review: bool
    def __init__(self, search_id: _Optional[str] = ..., include_needs_review: bool = ...) -> None: ...

class ConfirmSearchRequest(_message.Message):
    __slots__ = ("search_id", "selection", "idempotency_key")
    SEARCH_ID_FIELD_NUMBER: _ClassVar[int]
    SELECTION_FIELD_NUMBER: _ClassVar[int]
    IDEMPOTENCY_KEY_FIELD_NUMBER: _ClassVar[int]
    search_id: str
    selection: str
    idempotency_key: str
    def __init__(self, search_id: _Optional[str] = ..., selection: _Optional[str] = ..., idempotency_key: _Optional[str] = ...) -> None: ...

class CancelSearchRequest(_message.Message):
    __slots__ = ("search_id",)
    SEARCH_ID_FIELD_NUMBER: _ClassVar[int]
    search_id: str
    def __init__(self, search_id: _Optional[str] = ...) -> None: ...

class GetAgentResultRequest(_message.Message):
    __slots__ = ("search_id",)
    SEARCH_ID_FIELD_NUMBER: _ClassVar[int]
    search_id: str
    def __init__(self, search_id: _Optional[str] = ...) -> None: ...

class DownloadPaperPdfRequest(_message.Message):
    __slots__ = ("search_id", "paper_id")
    SEARCH_ID_FIELD_NUMBER: _ClassVar[int]
    PAPER_ID_FIELD_NUMBER: _ClassVar[int]
    search_id: str
    paper_id: str
    def __init__(self, search_id: _Optional[str] = ..., paper_id: _Optional[str] = ...) -> None: ...

class PdfChunk(_message.Message):
    __slots__ = ("data", "url")
    DATA_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    data: bytes
    url: str
    def __init__(self, data: _Optional[bytes] = ..., url: _Optional[str] = ...) -> None: ...

class SearchRequest(_message.Message):
    __slots__ = ("name", "university", "department", "hints", "mode", "on_ambiguity", "include_needs_review", "limits")
    NAME_FIELD_NUMBER: _ClassVar[int]
    UNIVERSITY_FIELD_NUMBER: _ClassVar[int]
    DEPARTMENT_FIELD_NUMBER: _ClassVar[int]
    HINTS_FIELD_NUMBER: _ClassVar[int]
    MODE_FIELD_NUMBER: _ClassVar[int]
    ON_AMBIGUITY_FIELD_NUMBER: _ClassVar[int]
    INCLUDE_NEEDS_REVIEW_FIELD_NUMBER: _ClassVar[int]
    LIMITS_FIELD_NUMBER: _ClassVar[int]
    name: str
    university: str
    department: str
    hints: Hints
    mode: Mode
    on_ambiguity: OnAmbiguity
    include_needs_review: bool
    limits: Limits
    def __init__(self, name: _Optional[str] = ..., university: _Optional[str] = ..., department: _Optional[str] = ..., hints: _Optional[_Union[Hints, _Mapping]] = ..., mode: _Optional[_Union[Mode, str]] = ..., on_ambiguity: _Optional[_Union[OnAmbiguity, str]] = ..., include_needs_review: bool = ..., limits: _Optional[_Union[Limits, _Mapping]] = ...) -> None: ...

class Hints(_message.Message):
    __slots__ = ("orcid", "homepage", "email", "scholar_url", "profile_urls", "publication_pages", "known_papers", "research_interests", "university_aliases", "previous_universities")
    ORCID_FIELD_NUMBER: _ClassVar[int]
    HOMEPAGE_FIELD_NUMBER: _ClassVar[int]
    EMAIL_FIELD_NUMBER: _ClassVar[int]
    SCHOLAR_URL_FIELD_NUMBER: _ClassVar[int]
    PROFILE_URLS_FIELD_NUMBER: _ClassVar[int]
    PUBLICATION_PAGES_FIELD_NUMBER: _ClassVar[int]
    KNOWN_PAPERS_FIELD_NUMBER: _ClassVar[int]
    RESEARCH_INTERESTS_FIELD_NUMBER: _ClassVar[int]
    UNIVERSITY_ALIASES_FIELD_NUMBER: _ClassVar[int]
    PREVIOUS_UNIVERSITIES_FIELD_NUMBER: _ClassVar[int]
    orcid: str
    homepage: str
    email: str
    scholar_url: str
    profile_urls: _containers.RepeatedScalarFieldContainer[str]
    publication_pages: _containers.RepeatedScalarFieldContainer[str]
    known_papers: _containers.RepeatedScalarFieldContainer[str]
    research_interests: _containers.RepeatedScalarFieldContainer[str]
    university_aliases: _containers.RepeatedScalarFieldContainer[str]
    previous_universities: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, orcid: _Optional[str] = ..., homepage: _Optional[str] = ..., email: _Optional[str] = ..., scholar_url: _Optional[str] = ..., profile_urls: _Optional[_Iterable[str]] = ..., publication_pages: _Optional[_Iterable[str]] = ..., known_papers: _Optional[_Iterable[str]] = ..., research_interests: _Optional[_Iterable[str]] = ..., university_aliases: _Optional[_Iterable[str]] = ..., previous_universities: _Optional[_Iterable[str]] = ...) -> None: ...

class Limits(_message.Message):
    __slots__ = ("max_pdf_papers", "max_downloads")
    MAX_PDF_PAPERS_FIELD_NUMBER: _ClassVar[int]
    MAX_DOWNLOADS_FIELD_NUMBER: _ClassVar[int]
    max_pdf_papers: int
    max_downloads: int
    def __init__(self, max_pdf_papers: _Optional[int] = ..., max_downloads: _Optional[int] = ...) -> None: ...

class Search(_message.Message):
    __slots__ = ("id", "status", "created_at", "updated_at", "request", "selection", "progress", "candidates", "result", "error")
    ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    CREATED_AT_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    REQUEST_FIELD_NUMBER: _ClassVar[int]
    SELECTION_FIELD_NUMBER: _ClassVar[int]
    PROGRESS_FIELD_NUMBER: _ClassVar[int]
    CANDIDATES_FIELD_NUMBER: _ClassVar[int]
    RESULT_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    id: str
    status: SearchStatus
    created_at: _timestamp_pb2.Timestamp
    updated_at: _timestamp_pb2.Timestamp
    request: SearchRequest
    selection: str
    progress: Progress
    candidates: _containers.RepeatedCompositeFieldContainer[Candidate]
    result: SearchResult
    error: Failure
    def __init__(self, id: _Optional[str] = ..., status: _Optional[_Union[SearchStatus, str]] = ..., created_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ..., updated_at: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ..., request: _Optional[_Union[SearchRequest, _Mapping]] = ..., selection: _Optional[str] = ..., progress: _Optional[_Union[Progress, _Mapping]] = ..., candidates: _Optional[_Iterable[_Union[Candidate, _Mapping]]] = ..., result: _Optional[_Union[SearchResult, _Mapping]] = ..., error: _Optional[_Union[Failure, _Mapping]] = ...) -> None: ...

class Progress(_message.Message):
    __slots__ = ("stage", "completed", "total")
    STAGE_FIELD_NUMBER: _ClassVar[int]
    COMPLETED_FIELD_NUMBER: _ClassVar[int]
    TOTAL_FIELD_NUMBER: _ClassVar[int]
    stage: str
    completed: int
    total: int
    def __init__(self, stage: _Optional[str] = ..., completed: _Optional[int] = ..., total: _Optional[int] = ...) -> None: ...

class Candidate(_message.Message):
    __slots__ = ("name", "selections", "affiliations", "orcid", "match_score", "evidence")
    NAME_FIELD_NUMBER: _ClassVar[int]
    SELECTIONS_FIELD_NUMBER: _ClassVar[int]
    AFFILIATIONS_FIELD_NUMBER: _ClassVar[int]
    ORCID_FIELD_NUMBER: _ClassVar[int]
    MATCH_SCORE_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELD_NUMBER: _ClassVar[int]
    name: str
    selections: _containers.RepeatedScalarFieldContainer[str]
    affiliations: _containers.RepeatedScalarFieldContainer[str]
    orcid: str
    match_score: float
    evidence: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, name: _Optional[str] = ..., selections: _Optional[_Iterable[str]] = ..., affiliations: _Optional[_Iterable[str]] = ..., orcid: _Optional[str] = ..., match_score: _Optional[float] = ..., evidence: _Optional[_Iterable[str]] = ...) -> None: ...

class Failure(_message.Message):
    __slots__ = ("code", "message", "details")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    DETAILS_FIELD_NUMBER: _ClassVar[int]
    code: str
    message: str
    details: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, code: _Optional[str] = ..., message: _Optional[str] = ..., details: _Optional[_Iterable[str]] = ...) -> None: ...

class SearchResult(_message.Message):
    __slots__ = ("run_status", "identity", "papers", "statistics", "warnings", "notices")
    RUN_STATUS_FIELD_NUMBER: _ClassVar[int]
    IDENTITY_FIELD_NUMBER: _ClassVar[int]
    PAPERS_FIELD_NUMBER: _ClassVar[int]
    STATISTICS_FIELD_NUMBER: _ClassVar[int]
    WARNINGS_FIELD_NUMBER: _ClassVar[int]
    NOTICES_FIELD_NUMBER: _ClassVar[int]
    run_status: RunStatus
    identity: Identity
    papers: _containers.RepeatedCompositeFieldContainer[Paper]
    statistics: Statistics
    warnings: _containers.RepeatedScalarFieldContainer[str]
    notices: _containers.RepeatedCompositeFieldContainer[Notice]
    def __init__(self, run_status: _Optional[_Union[RunStatus, str]] = ..., identity: _Optional[_Union[Identity, _Mapping]] = ..., papers: _Optional[_Iterable[_Union[Paper, _Mapping]]] = ..., statistics: _Optional[_Union[Statistics, _Mapping]] = ..., warnings: _Optional[_Iterable[str]] = ..., notices: _Optional[_Iterable[_Union[Notice, _Mapping]]] = ...) -> None: ...

class Identity(_message.Message):
    __slots__ = ("name", "status", "orcid", "author_ids", "confidence", "evidence")
    class AuthorIdsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    NAME_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    ORCID_FIELD_NUMBER: _ClassVar[int]
    AUTHOR_IDS_FIELD_NUMBER: _ClassVar[int]
    CONFIDENCE_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELD_NUMBER: _ClassVar[int]
    name: str
    status: str
    orcid: str
    author_ids: _containers.ScalarMap[str, str]
    confidence: float
    evidence: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, name: _Optional[str] = ..., status: _Optional[str] = ..., orcid: _Optional[str] = ..., author_ids: _Optional[_Mapping[str, str]] = ..., confidence: _Optional[float] = ..., evidence: _Optional[_Iterable[str]] = ...) -> None: ...

class Paper(_message.Message):
    __slots__ = ("id", "title", "authors", "year", "venue", "doi", "abstract", "ownership_status", "confidence", "evidence", "full_text", "editions")
    ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    AUTHORS_FIELD_NUMBER: _ClassVar[int]
    YEAR_FIELD_NUMBER: _ClassVar[int]
    VENUE_FIELD_NUMBER: _ClassVar[int]
    DOI_FIELD_NUMBER: _ClassVar[int]
    ABSTRACT_FIELD_NUMBER: _ClassVar[int]
    OWNERSHIP_STATUS_FIELD_NUMBER: _ClassVar[int]
    CONFIDENCE_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELD_NUMBER: _ClassVar[int]
    FULL_TEXT_FIELD_NUMBER: _ClassVar[int]
    EDITIONS_FIELD_NUMBER: _ClassVar[int]
    id: str
    title: str
    authors: _containers.RepeatedScalarFieldContainer[str]
    year: int
    venue: str
    doi: str
    abstract: str
    ownership_status: OwnershipStatus
    confidence: Confidence
    evidence: _containers.RepeatedScalarFieldContainer[str]
    full_text: FullText
    editions: _containers.RepeatedCompositeFieldContainer[Edition]
    def __init__(self, id: _Optional[str] = ..., title: _Optional[str] = ..., authors: _Optional[_Iterable[str]] = ..., year: _Optional[int] = ..., venue: _Optional[str] = ..., doi: _Optional[str] = ..., abstract: _Optional[str] = ..., ownership_status: _Optional[_Union[OwnershipStatus, str]] = ..., confidence: _Optional[_Union[Confidence, str]] = ..., evidence: _Optional[_Iterable[str]] = ..., full_text: _Optional[_Union[FullText, _Mapping]] = ..., editions: _Optional[_Iterable[_Union[Edition, _Mapping]]] = ...) -> None: ...

class Edition(_message.Message):
    __slots__ = ("id", "title", "year", "venue", "doi", "full_text")
    ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    YEAR_FIELD_NUMBER: _ClassVar[int]
    VENUE_FIELD_NUMBER: _ClassVar[int]
    DOI_FIELD_NUMBER: _ClassVar[int]
    FULL_TEXT_FIELD_NUMBER: _ClassVar[int]
    id: str
    title: str
    year: int
    venue: str
    doi: str
    full_text: FullText
    def __init__(self, id: _Optional[str] = ..., title: _Optional[str] = ..., year: _Optional[int] = ..., venue: _Optional[str] = ..., doi: _Optional[str] = ..., full_text: _Optional[_Union[FullText, _Mapping]] = ...) -> None: ...

class FullText(_message.Message):
    __slots__ = ("status", "url", "version", "download", "browser_url", "publisher_link")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    DOWNLOAD_FIELD_NUMBER: _ClassVar[int]
    BROWSER_URL_FIELD_NUMBER: _ClassVar[int]
    PUBLISHER_LINK_FIELD_NUMBER: _ClassVar[int]
    status: FullTextStatus
    url: str
    version: str
    download: str
    browser_url: str
    publisher_link: PublisherLink
    def __init__(self, status: _Optional[_Union[FullTextStatus, str]] = ..., url: _Optional[str] = ..., version: _Optional[str] = ..., download: _Optional[str] = ..., browser_url: _Optional[str] = ..., publisher_link: _Optional[_Union[PublisherLink, _Mapping]] = ...) -> None: ...

class PublisherLink(_message.Message):
    __slots__ = ("url", "kind", "publisher")
    URL_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    PUBLISHER_FIELD_NUMBER: _ClassVar[int]
    url: str
    kind: PublisherLinkKind
    publisher: str
    def __init__(self, url: _Optional[str] = ..., kind: _Optional[_Union[PublisherLinkKind, str]] = ..., publisher: _Optional[str] = ...) -> None: ...

class Statistics(_message.Message):
    __slots__ = ("papers", "verified", "full_text_files", "full_text_links")
    PAPERS_FIELD_NUMBER: _ClassVar[int]
    VERIFIED_FIELD_NUMBER: _ClassVar[int]
    FULL_TEXT_FILES_FIELD_NUMBER: _ClassVar[int]
    FULL_TEXT_LINKS_FIELD_NUMBER: _ClassVar[int]
    papers: int
    verified: int
    full_text_files: int
    full_text_links: int
    def __init__(self, papers: _Optional[int] = ..., verified: _Optional[int] = ..., full_text_files: _Optional[int] = ..., full_text_links: _Optional[int] = ...) -> None: ...

class Notice(_message.Message):
    __slots__ = ("code", "message")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    code: str
    message: str
    def __init__(self, code: _Optional[str] = ..., message: _Optional[str] = ...) -> None: ...

class AgentResult(_message.Message):
    __slots__ = ("search_id", "status", "usable", "researcher", "papers", "withheld", "notes")
    SEARCH_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    USABLE_FIELD_NUMBER: _ClassVar[int]
    RESEARCHER_FIELD_NUMBER: _ClassVar[int]
    PAPERS_FIELD_NUMBER: _ClassVar[int]
    WITHHELD_FIELD_NUMBER: _ClassVar[int]
    NOTES_FIELD_NUMBER: _ClassVar[int]
    search_id: str
    status: AgentStatus
    usable: bool
    researcher: Researcher
    papers: _containers.RepeatedCompositeFieldContainer[AgentPaper]
    withheld: _containers.RepeatedCompositeFieldContainer[WithheldPaper]
    notes: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, search_id: _Optional[str] = ..., status: _Optional[_Union[AgentStatus, str]] = ..., usable: bool = ..., researcher: _Optional[_Union[Researcher, _Mapping]] = ..., papers: _Optional[_Iterable[_Union[AgentPaper, _Mapping]]] = ..., withheld: _Optional[_Iterable[_Union[WithheldPaper, _Mapping]]] = ..., notes: _Optional[_Iterable[str]] = ...) -> None: ...

class Researcher(_message.Message):
    __slots__ = ("requested", "identity")
    REQUESTED_FIELD_NUMBER: _ClassVar[int]
    IDENTITY_FIELD_NUMBER: _ClassVar[int]
    requested: RequestedResearcher
    identity: AgentIdentity
    def __init__(self, requested: _Optional[_Union[RequestedResearcher, _Mapping]] = ..., identity: _Optional[_Union[AgentIdentity, _Mapping]] = ...) -> None: ...

class RequestedResearcher(_message.Message):
    __slots__ = ("name", "university", "department")
    NAME_FIELD_NUMBER: _ClassVar[int]
    UNIVERSITY_FIELD_NUMBER: _ClassVar[int]
    DEPARTMENT_FIELD_NUMBER: _ClassVar[int]
    name: str
    university: str
    department: str
    def __init__(self, name: _Optional[str] = ..., university: _Optional[str] = ..., department: _Optional[str] = ...) -> None: ...

class AgentIdentity(_message.Message):
    __slots__ = ("confirmed", "confirmed_by", "name", "orcid", "author_ids", "confidence", "evidence")
    class AuthorIdsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    CONFIRMED_FIELD_NUMBER: _ClassVar[int]
    CONFIRMED_BY_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    ORCID_FIELD_NUMBER: _ClassVar[int]
    AUTHOR_IDS_FIELD_NUMBER: _ClassVar[int]
    CONFIDENCE_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELD_NUMBER: _ClassVar[int]
    confirmed: bool
    confirmed_by: ConfirmedBy
    name: str
    orcid: str
    author_ids: _containers.ScalarMap[str, str]
    confidence: float
    evidence: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, confirmed: bool = ..., confirmed_by: _Optional[_Union[ConfirmedBy, str]] = ..., name: _Optional[str] = ..., orcid: _Optional[str] = ..., author_ids: _Optional[_Mapping[str, str]] = ..., confidence: _Optional[float] = ..., evidence: _Optional[_Iterable[str]] = ...) -> None: ...

class AgentPaper(_message.Message):
    __slots__ = ("id", "title", "year", "venue", "authors", "abstract", "doi", "url", "full_text", "confidence", "evidence")
    ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    YEAR_FIELD_NUMBER: _ClassVar[int]
    VENUE_FIELD_NUMBER: _ClassVar[int]
    AUTHORS_FIELD_NUMBER: _ClassVar[int]
    ABSTRACT_FIELD_NUMBER: _ClassVar[int]
    DOI_FIELD_NUMBER: _ClassVar[int]
    URL_FIELD_NUMBER: _ClassVar[int]
    FULL_TEXT_FIELD_NUMBER: _ClassVar[int]
    CONFIDENCE_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELD_NUMBER: _ClassVar[int]
    id: str
    title: str
    year: int
    venue: str
    authors: _containers.RepeatedScalarFieldContainer[str]
    abstract: str
    doi: str
    url: str
    full_text: FullText
    confidence: Confidence
    evidence: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, id: _Optional[str] = ..., title: _Optional[str] = ..., year: _Optional[int] = ..., venue: _Optional[str] = ..., authors: _Optional[_Iterable[str]] = ..., abstract: _Optional[str] = ..., doi: _Optional[str] = ..., url: _Optional[str] = ..., full_text: _Optional[_Union[FullText, _Mapping]] = ..., confidence: _Optional[_Union[Confidence, str]] = ..., evidence: _Optional[_Iterable[str]] = ...) -> None: ...

class WithheldPaper(_message.Message):
    __slots__ = ("id", "title", "year", "reason", "evidence")
    ID_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    YEAR_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    EVIDENCE_FIELD_NUMBER: _ClassVar[int]
    id: str
    title: str
    year: int
    reason: WithheldReason
    evidence: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, id: _Optional[str] = ..., title: _Optional[str] = ..., year: _Optional[int] = ..., reason: _Optional[_Union[WithheldReason, str]] = ..., evidence: _Optional[_Iterable[str]] = ...) -> None: ...
