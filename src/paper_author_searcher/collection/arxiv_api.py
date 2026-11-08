"""The arXiv API, asked in batches: many identifiers or titles per request.

arXiv allows about one request every 3 s and throttles a client that asks a lot (transport/refusals.py), so the
number of requests is what limits a search. One request answers up to
``arxiv_id_batch_size`` identifiers (``id_list``) or ``arxiv_title_batch_size`` titles (``ti:"..." OR ti:"..."``).

Batching never loses an answer a single request would give:

- Identifiers: a failed batch, and any identifier no returned entry answers, are asked for one at a time.
- Titles: a batch whose answer fills ``arxiv_title_batch_results`` may be truncated, so it is split in halves and
  asked again, down to single titles. An unsaturated answer holds every entry matching any of the batch's title
  phrases, so it holds at least what each title's own request would have returned.

A single identifier list or title is asked exactly as one request always was.
"""

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Sequence
from functools import partial

from paper_author_searcher.concurrency import Outcome, attempt
from paper_author_searcher.config import DEFAULT_SOURCES, SourceConfig
from paper_author_searcher.transport.http import MetadataReader, SourceError

ATOM = '{http://www.w3.org/2005/Atom}'
# Entries the API returns when a request names no max_results.
DEFAULT_RESULTS = 10
VERSION = re.compile(r'v\d+$')
# Errors one arXiv request may raise that leave the other answers usable.
REQUEST_ERRORS = (SourceError, ValueError, TypeError)

Entries = list[ET.Element]
# (title on an entry, title asked for) -> whether the entry bears the title.
TitleMatch = Callable[[str, str], bool]


def entry_identifier(entry: ET.Element) -> str:
    """The arXiv identifier an entry is served under, with its version ('2101.00001v2', 'hep-th/9901001v1')."""
    return (entry.findtext(ATOM + 'id') or '').rsplit('/abs/', 1)[-1].strip().lower()


def entry_title(entry: ET.Element) -> str:
    return ' '.join((entry.findtext(ATOM + 'title') or '').split())


def answers(requested: str, served: str) -> bool:
    """Whether an entry served as ``served`` answers ``requested``: the same version, or any version of it."""
    requested = requested.strip().lower()
    return requested == served if VERSION.search(requested) else requested == VERSION.sub('', served)


def title_phrase(title: str) -> str:
    return 'ti:"' + ' '.join(title.replace('"', ' ').split()) + '"'


class ArxivApi:
    def __init__(self, client: MetadataReader, url: str, config: SourceConfig = DEFAULT_SOURCES) -> None:
        self.client = client
        self.url = url
        self.config = config

    # Identifiers -------------------------------------------------------------------------------------------------

    def by_ids(self, identifiers: Iterable[str]) -> dict[str, Outcome[Entries]]:
        """Each identifier's entries (in the API's order), or the error asking for it raised."""
        unique = list(dict.fromkeys(identifiers))
        size = self.config.arxiv_id_batch_size
        found: dict[str, Outcome[Entries]] = {}
        for start in range(0, len(unique), size):
            found.update(self._id_batch(unique[start : start + size]))
        return found

    def entries_for(self, identifiers: Sequence[str]) -> Entries:
        """One request for exactly these identifiers, as a single paper's lookup always asked."""
        params: dict[str, object] = {'id_list': ','.join(identifiers)}
        if len(identifiers) > DEFAULT_RESULTS:
            params['max_results'] = len(identifiers)
        return self._entries(params)

    def _id_batch(self, batch: list[str]) -> dict[str, Outcome[Entries]]:
        try:
            entries = self.entries_for(batch)
        except REQUEST_ERRORS as exc:
            if len(batch) == 1:
                return {batch[0]: Outcome(error=exc)}
            # One bad identifier can fail the whole list: ask each alone, so only its own answer fails.
            return {
                identifier: attempt(partial(self.entries_for, [identifier]), REQUEST_ERRORS) for identifier in batch
            }
        if len(batch) == 1:
            return {batch[0]: Outcome(value=entries)}
        found: dict[str, Outcome[Entries]] = {}
        for identifier in batch:
            own = [entry for entry in entries if answers(identifier, entry_identifier(entry))]
            # No entry answers it: ask it alone, so its answer is whatever a single request returns.
            found[identifier] = (
                Outcome(value=own) if own else attempt(partial(self.entries_for, [identifier]), REQUEST_ERRORS)
            )
        return found

    # Titles ------------------------------------------------------------------------------------------------------

    def by_titles(self, titles: Iterable[str], per_title: int, same: TitleMatch) -> dict[str, Outcome[Entries]]:
        """Each title's entries bearing it (``same``), in the API's order, or the error asking for it raised.

        ``per_title`` is how many results a title asked on its own requests, as before.
        """
        unique = list(dict.fromkeys(titles))
        size = self.config.arxiv_title_batch_size
        found: dict[str, Outcome[Entries]] = {}
        for start in range(0, len(unique), size):
            self._title_batch(unique[start : start + size], per_title, same, found)
        return found

    def _title_batch(
        self, batch: list[str], per_title: int, same: TitleMatch, found: dict[str, Outcome[Entries]]
    ) -> None:
        if len(batch) == 1:
            title = batch[0]
            outcome = attempt(partial(self._title_entries, [title], per_title), REQUEST_ERRORS)
            found[title] = outcome if outcome.error else Outcome(value=self._bearing(outcome.unwrap(), title, same))
            return
        limit = self.config.arxiv_title_batch_results
        outcome = attempt(partial(self._title_entries, batch, limit), REQUEST_ERRORS)
        if outcome.error is not None or len(outcome.unwrap()) >= limit:
            # Failed, or possibly truncated: halve the batch (binary splitting) until each part is answered whole.
            middle = len(batch) // 2
            self._title_batch(batch[:middle], per_title, same, found)
            self._title_batch(batch[middle:], per_title, same, found)
            return
        entries = outcome.unwrap()
        for title in batch:
            found[title] = Outcome(value=self._bearing(entries, title, same))

    def _title_entries(self, titles: Sequence[str], results: int) -> Entries:
        query = ' OR '.join(title_phrase(title) for title in titles)
        return self._entries({'search_query': query, 'max_results': results})

    @staticmethod
    def _bearing(entries: Entries, title: str, same: TitleMatch) -> Entries:
        return [entry for entry in entries if same(entry_title(entry), title)]

    def _entries(self, params: dict[str, object]) -> Entries:
        feed = self.client.get(self.url, params=params, xml=True)
        return list(feed.findall(ATOM + 'entry'))
