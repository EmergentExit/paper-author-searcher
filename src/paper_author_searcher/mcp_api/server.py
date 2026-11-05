"""The MCP server: ``paper-search-api serve-mcp`` (Streamable HTTP, official MCP Python SDK).

It only adapts: the SDK speaks the protocol, ``ApiKeyVerifier`` checks the bearer token against the service's API
keys, and each tool hands its arguments to ``SearchTools`` for the caller the token belongs to. Service errors
become tool errors the model can read; searches run in the worker processes.
"""

from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl, Field
from starlette.applications import Starlette

from paper_author_searcher.api.auth import ApiKeys
from paper_author_searcher.config.service import ServiceConfig
from paper_author_searcher.service.model import ServiceError

from .tools import SearchTools, WaitPolicy

SERVER_NAME = 'paper-search'
INSTRUCTIONS = """Finds every paper by one researcher, for writing to them about their work.
1. start_search with the researcher's name and current school (add previous schools, ORCID or homepage when known).
2. get_search_status with wait_seconds=50, repeatedly, until the status is succeeded, needs_confirmation or failed.
   A search takes a few minutes.
3. needs_confirmation: ask the user which candidate is the person, then confirm_researcher. Never guess.
4. get_citable_papers: cite only its papers; never cite withheld ones."""

Mode = Literal['metadata', 'pdf-urls', 'full']
Texts = list[str] | None


class ApiKeyVerifier:
    """The SDK's bearer-token check, answered by the service's API keys; the token's client is the key's ID."""

    def __init__(self, keys: ApiKeys) -> None:
        self.keys = keys

    async def verify_token(self, token: str) -> AccessToken | None:
        key = self.keys.authenticate({'authorization': f'Bearer {token}'})
        return AccessToken(token=token, client_id=key.id, scopes=[]) if key else None


def caller() -> str:
    token = get_access_token()
    if token is None:
        # The SDK's bearer middleware refuses unauthenticated requests before any tool runs.
        raise ToolError('Not authenticated')
    return token.client_id


def answered(action: Any) -> dict[str, Any]:
    """Runs a tool action for the caller; a refusal becomes a tool error the model reads."""
    try:
        return action(caller())
    except ServiceError as error:
        details = f' ({"; ".join(error.details)})' if error.details else ''
        raise ToolError(f'{error.code}: {error.message}{details}') from error


def register_tools(server: MCPServer, tools: SearchTools) -> None:
    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True))
    def start_search(
        name: Annotated[str, Field(description="The researcher's full name, e.g. 'Alice Gao'")],
        university: Annotated[str, Field(description='Their current school or institution')],
        department: Annotated[str | None, Field(description='e.g. Computer Science')] = None,
        previous_universities: Annotated[Texts, Field(description='PhD, postdoc, earlier schools')] = None,
        orcid: Annotated[str | None, Field(description='ORCID iD, 0000-0000-0000-0000')] = None,
        homepage: Annotated[str | None, Field(description='Their own web page (https://...)')] = None,
        known_papers: Annotated[Texts, Field(description='Titles of papers known to be theirs')] = None,
        mode: Annotated[Mode, Field(description='metadata: papers; pdf-urls: +links; full: +PDFs')] = 'full',
    ) -> dict[str, Any]:
        """Start finding every paper by one researcher. Returns a search_id at once; the search takes minutes."""
        hints = {
            key: value
            for key, value in {
                'previous_universities': previous_universities,
                'orcid': orcid,
                'homepage': homepage,
                'known_papers': known_papers,
            }.items()
            if value
        }
        request: dict[str, Any] = {'name': name, 'university': university, 'mode': mode}
        if department:
            request['department'] = department
        if hints:
            request['hints'] = hints
        return answered(lambda client: tools.start_search(client, request))

    @server.tool(annotations=read_only)
    def get_search_status(
        search_id: str,
        wait_seconds: Annotated[
            float, Field(ge=0, description='Wait up to this long (max 50) for a running search to finish')
        ] = 0,
    ) -> dict[str, Any]:
        """Where a search stands, and what to do next. Candidates are listed when a namesake must be picked."""
        return answered(lambda client: tools.search_status(client, search_id, wait_seconds))

    @server.tool(annotations=read_only)
    def get_citable_papers(search_id: str) -> dict[str, Any]:
        """A finished search's papers that may be cited (title, year, venue, abstract, links, confidence, evidence),
        and the withheld ones that must not be."""
        return answered(lambda client: tools.citable_papers(client, search_id))

    @server.tool(annotations=read_only)
    def get_full_result(
        search_id: str,
        include_needs_review: Annotated[bool, Field(description='Also papers whose ownership is unverified')] = False,
    ) -> dict[str, Any]:
        """Everything a finished search found, with each paper's evidence and full-text links. Larger than
        get_citable_papers; use it to inspect, not to cite."""
        return answered(lambda client: tools.full_result(client, search_id, include_needs_review))

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True))
    def confirm_researcher(
        search_id: str,
        selection: Annotated[str, Field(description="One of the chosen candidate's selections, e.g. 'openalex:A1'")],
    ) -> dict[str, Any]:
        """Pick which namesake is the researcher (only after the user said which); the search continues."""
        return answered(lambda client: tools.confirm_researcher(client, search_id, selection))

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False))
    def cancel_search(search_id: str) -> dict[str, Any]:
        """Stop a search that is queued, running or waiting for confirmation."""
        return answered(lambda client: tools.cancel_search(client, search_id))


def build_server(tools: SearchTools, keys: ApiKeys, config: ServiceConfig) -> MCPServer:
    public = urlsplit(config.mcp_public_url)
    auth = AuthSettings(
        issuer_url=AnyHttpUrl(f'{public.scheme}://{public.netloc}'),
        resource_server_url=AnyHttpUrl(config.mcp_public_url),
    )
    server = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS, token_verifier=ApiKeyVerifier(keys), auth=auth)
    register_tools(server, tools)
    return server


def build_app(tools: SearchTools, keys: ApiKeys, config: ServiceConfig) -> Starlette:
    """The ASGI app: stateless Streamable HTTP with JSON answers (no session to keep behind a proxy)."""
    security = TransportSecuritySettings(allowed_hosts=list(config.mcp_allowed_hosts))
    return build_server(tools, keys, config).streamable_http_app(
        streamable_http_path=config.mcp_path,
        stateless_http=True,
        json_response=True,
        transport_security=security,
        host=config.mcp_host,
    )


def wait_policy(config: ServiceConfig) -> WaitPolicy:
    return WaitPolicy(config.mcp_maximum_wait_seconds, config.mcp_poll_seconds)
