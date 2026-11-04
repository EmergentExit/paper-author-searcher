"""Web search APIs behind one protocol, for finding a researcher's homepage, publication page or CV.

Each adapter maps its provider's response to ``SearchHit``; which one runs is configuration
(``DiscoveryConfig.web_search``, registered in ``source_catalog.WEB_SEARCH``). A provider without its API key is
not built at all, so discovery simply skips the web step.
"""

import logging
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# A provider answering one of these is out of credit or refuses the key, so it is skipped for the rest of the run
# (Tavily: 432 plan limit, 433 pay-as-you-go limit; Brave and Exa: 402). A rate limit (429) skips only that query.
EXHAUSTED_STATUSES = frozenset({401, 402, 403, 432, 433})


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str = ''
    snippet: str = ''


class WebSearch(Protocol):
    name: str

    def search(self, query: str, count: int) -> list[SearchHit]: ...


def _hits(items: Iterable[Mapping[str, Any]], snippet_field: str) -> list[SearchHit]:
    return [
        SearchHit(str(item['url']), str(item.get('title') or ''), str(item.get(snippet_field) or ''))
        for item in items
        if item.get('url')
    ]


class BraveSearch:
    name = 'brave'

    def __init__(self, client: Any, url: str, api_key: str) -> None:
        self.client, self.url, self.api_key = client, url, api_key

    def search(self, query: str, count: int) -> list[SearchHit]:
        data = self.client.get(
            self.url,
            {'q': query, 'count': count},
            {'Accept': 'application/json', 'X-Subscription-Token': self.api_key},
        )
        return _hits(((data or {}).get('web') or {}).get('results') or [], 'description')


class TavilySearch:
    name = 'tavily'

    def __init__(self, client: Any, url: str, api_key: str) -> None:
        self.client, self.url, self.api_key = client, url, api_key

    def search(self, query: str, count: int) -> list[SearchHit]:
        data = self.client.post_json(
            self.url, {'query': query, 'max_results': count}, headers={'Authorization': 'Bearer ' + self.api_key}
        )
        return _hits((data or {}).get('results') or [], 'content')


class ExaSearch:
    name = 'exa'

    def __init__(self, client: Any, url: str, api_key: str) -> None:
        self.client, self.url, self.api_key = client, url, api_key

    def search(self, query: str, count: int) -> list[SearchHit]:
        payload = {'query': query, 'numResults': count}
        data = self.client.post_json(self.url, payload, headers={'x-api-key': self.api_key})
        return _hits((data or {}).get('results') or [], 'text')


class FallbackWebSearch:
    """Several providers in order: a query goes to the next one when a provider fails or runs out of credit."""

    def __init__(self, providers: Iterable[WebSearch]) -> None:
        self.providers = list(providers)
        if not self.providers:
            raise ValueError('FallbackWebSearch needs at least one provider')
        self.exhausted: set[str] = set()
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        live = [provider.name for provider in self.providers if provider.name not in self.exhausted]
        return (live or [self.providers[-1].name])[0]

    def search(self, query: str, count: int) -> list[SearchHit]:
        error: Exception | None = None
        for provider in self.providers:
            if provider.name in self.exhausted:
                continue
            try:
                return provider.search(query, count)
            except Exception as exc:
                error = exc
                if getattr(exc, 'status_code', None) in EXHAUSTED_STATUSES:
                    with self._lock:
                        self.exhausted.add(provider.name)
                    logger.warning(
                        'Web search %s is out of credit or refused the key; trying the next one', provider.name
                    )
        if error is None:
            raise RuntimeError('every web search provider is out of credit')
        raise error
