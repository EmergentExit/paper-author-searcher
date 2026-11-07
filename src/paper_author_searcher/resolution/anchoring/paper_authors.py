"""Who wrote the person's known papers: OpenAlex and Semantic Scholar author IDs, and the ORCID record's works."""

import logging
from collections.abc import Callable, Iterable, Mapping
from functools import partial
from typing import Any
from urllib.parse import quote

from paper_author_searcher.concurrency import Outcome, attempt, in_order
from paper_author_searcher.config import (
    DEFAULT_ANCHORS,
    DEFAULT_MATCHING,
    DEFAULT_SOURCES,
    AnchorConfig,
    MatchingConfig,
    SourceConfig,
)
from paper_author_searcher.json_types import JSONObject
from paper_author_searcher.matching import (
    ARXIV_DOI_PREFIX,
    canonical_doi,
    canonical_orcid,
    name_similarity,
    normalize_title,
    work_keys,
)
from paper_author_searcher.models import ProfessorQuery
from paper_author_searcher.quality.evaluation_matching import bibliographic_name_agrees
from paper_author_searcher.status import AnchorKind
from paper_author_searcher.transport.http import HttpTransport, SourceError

from .model import IdentityAnchors

logger = logging.getLogger(__name__)

# Errors a paper lookup may raise that the anchors record and pass over, as one failed lookup does.
LOOKUP_ERRORS = (SourceError,)
SEMANTIC_SCHOLAR_FIELDS = 'title,authors'


def work_key(dois: Iterable[str] = (), arxiv_ids: Iterable[str] = (), titles: Iterable[str] = ()) -> str:
    """The one work key an anchor records for the paper it came from."""
    return next(iter(sorted(work_keys(dois, arxiv_ids, titles))), '')


class PaperAuthorAnchors:
    """Anchors the compatible authors of papers known to be the person's, and reads the ORCID record."""

    def __init__(
        self,
        client: HttpTransport,
        source_config: SourceConfig = DEFAULT_SOURCES,
        matching_config: MatchingConfig = DEFAULT_MATCHING,
        anchor_config: AnchorConfig = DEFAULT_ANCHORS,
        openalex_api_key: str | None = None,
        semantic_scholar_api_key: str | None = None,
    ) -> None:
        self.client = client
        self.openalex_url = source_config.openalex_url.rstrip('/')
        self.orcid_url = source_config.orcid_api_url.rstrip('/')
        self.semantic_scholar_url = source_config.semantic_scholar_url.rstrip('/')
        self.matching = matching_config
        self.config = anchor_config
        self.openalex_key = openalex_api_key
        self.semantic_scholar_key = semantic_scholar_api_key

    # ORCID -------------------------------------------------------------------------------------------------------

    def by_orcid(self, anchors: IdentityAnchors, query: ProfessorQuery, orcid: str) -> None:
        data = self.client.get(
            self.openalex_url + '/authors',
            {'filter': 'orcid:' + orcid, 'select': 'id,display_name', 'api_key': self.openalex_key},
        )
        for item in data.get('results') or []:
            anchors.add('openalex', item['id'].rsplit('/', 1)[-1], 'ORCID ' + orcid, AnchorKind.ORCID)

    def orcid_works(self, anchors: IdentityAnchors, query: ProfessorQuery, orcid: str) -> None:
        """The ORCID record's works (identifiers and titles) join the person's own list."""
        data = self.client.get(f'{self.orcid_url}/{orcid}/works', None, {'Accept': 'application/json'})
        keys: set[str] = set()
        for group in data.get('group') or []:
            identifiers = (group.get('external-ids') or {}).get('external-id') or []
            dois = [item['external-id-value'] for item in identifiers if item.get('external-id-type') == 'doi']
            arxiv = [item['external-id-value'] for item in identifiers if item.get('external-id-type') == 'arxiv']
            summaries = group.get('work-summary') or [{}]
            title = (((summaries[0] or {}).get('title') or {}).get('title') or {}).get('value')
            keys |= work_keys(dois, arxiv, [] if dois or arxiv else [title] if title else [])
        anchors.listed_works = sorted(set(anchors.listed_works) | keys)

    # Papers ------------------------------------------------------------------------------------------------------

    def anchor(
        self,
        anchors: IdentityAnchors,
        query: ProfessorQuery,
        dois: list[str],
        arxiv_ids: list[str],
        titles: list[str],
    ) -> None:
        """Anchor the authors of the hinted papers. Every OpenAlex lookup runs side by side and Semantic Scholar
        answers all papers in one batch; the answers are then used in the hints' order, so the anchors, their order
        and the errors are those of one lookup after another."""
        lookups: list[Callable[[], Any]] = [partial(self._openalex_work, doi) for doi in dois]
        lookups += [partial(self._openalex_work, ARXIV_DOI_PREFIX + arxiv.lower()) for arxiv in arxiv_ids]
        lookups += [partial(self._openalex_titles, title) for title in titles]
        found: list[Outcome[Any]] = in_order(
            [partial(attempt, lookup, LOOKUP_ERRORS) for lookup in lookups], self.config.lookup_workers, 'anchor-works'
        )
        by_doi, by_arxiv, by_title = (
            found[: len(dois)],
            found[len(dois) : len(dois) + len(arxiv_ids)],
            found[len(dois) + len(arxiv_ids) :],
        )
        wanted = [f'DOI:{doi}' for doi, work in zip(dois, by_doi, strict=True) if work.error is None]
        wanted += [f'ARXIV:{arxiv}' for arxiv, work in zip(arxiv_ids, by_arxiv, strict=True) if work.error is None]
        for title, works in zip(titles, by_title, strict=True):
            if works.error is None:
                wanted += [f'DOI:{doi}' for _, doi in self._title_matches(title, works.unwrap()) if doi]
        papers = self._semantic_scholar_papers(wanted)
        for doi, work in zip(dois, by_doi, strict=True):
            if work.error is not None:
                anchors.errors.append(f'{doi}: {work.error}')
                continue
            self._openalex_authorships(anchors, query, work.unwrap(), 'author of DOI ' + doi, work_key(dois=[doi]))
            self._semantic_scholar_anchors(anchors, query, 'DOI:' + doi, papers)
        for arxiv, work in zip(arxiv_ids, by_arxiv, strict=True):
            if work.error is not None:
                anchors.errors.append(f'{arxiv}: {work.error}')
                continue
            self._openalex_authorships(
                anchors, query, work.unwrap(), 'author of arXiv ' + arxiv, work_key(arxiv_ids=[arxiv])
            )
            self._semantic_scholar_anchors(anchors, query, 'ARXIV:' + arxiv, papers)
        for title, works in zip(titles, by_title, strict=True):
            if works.error is not None:
                anchors.errors.append(f'{title}: {works.error}')
                continue
            for match, match_doi in self._title_matches(title, works.unwrap()):
                self._openalex_authorships(
                    anchors, query, match, f'author of title "{title[:80]}"', work_key(titles=[title])
                )
                # Anchor Semantic Scholar as well, as a DOI hint does: once anchors exist, an unanchored ID cannot
                # corroborate the other database, so a one-sided anchor would cost the cross-source evidence.
                if match_doi:
                    self._semantic_scholar_anchors(anchors, query, 'DOI:' + match_doi, papers)

    def compatible(self, query: ProfessorQuery, name: str | None) -> bool:
        if not name:
            return False
        # Paper bylines often abbreviate given names ("M. Lin"), which plain similarity rejects.
        return name_similarity(
            query.name, name, self.matching
        ) >= self.matching.compatible_name or bibliographic_name_agrees(query.name, name)

    # OpenAlex ----------------------------------------------------------------------------------------------------

    def _openalex_authorships(
        self, anchors: IdentityAnchors, query: ProfessorQuery, work: JSONObject, reason: str, key: str = ''
    ) -> None:
        """Anchor the work's compatible authors. One whose author record carries another ORCID is only contested
        (see ``IdentityAnchors.settle_contested``), and only when no compatible author agrees with the ORCID."""
        agreeing: list[str] = []
        conflicting: list[str] = []
        for authorship in work.get('authorships') or []:
            author = authorship.get('author') or {}
            names = [author.get('display_name') or '', authorship.get('raw_author_name') or '']
            if not author.get('id') or not any(self.compatible(query, name) for name in names):
                continue
            orcid = canonical_orcid(author.get('orcid')) if author.get('orcid') else None
            target = conflicting if anchors.orcid and orcid and orcid != anchors.orcid else agreeing
            target.append(author['id'].rsplit('/', 1)[-1])
        for author_id in agreeing:
            anchors.add('openalex', author_id, reason, AnchorKind.PAPER, key)
        for author_id in conflicting if not agreeing else []:
            anchors.contest('openalex', author_id, reason, key)

    def _openalex_work(self, doi: str) -> JSONObject:
        # OpenAlex indexes arXiv preprints under their DataCite DOI; Semantic Scholar takes the arXiv ID.
        work: JSONObject = self.client.get(
            self.openalex_url + '/works/doi:' + quote(doi, safe='/'),
            {'select': 'id,doi,authorships', 'api_key': self.openalex_key},
        )
        return work

    def _openalex_titles(self, title: str) -> list[JSONObject]:
        data = self.client.get(
            self.openalex_url + '/works',
            {
                'filter': 'title.search:' + title.replace(',', ' '),
                'per-page': self.config.title_search_results,
                'select': 'id,title,doi,authorships',
                'api_key': self.openalex_key,
            },
        )
        results: list[JSONObject] = data.get('results') or []
        return results

    @staticmethod
    def _title_matches(title: str, works: list[JSONObject]) -> list[tuple[JSONObject, str | None]]:
        """The search results bearing exactly this title, each with its bare DOI (None without one)."""
        return [
            (work, canonical_doi(work['doi']).removeprefix('doi:') if work.get('doi') else None)
            for work in works
            if normalize_title(work.get('title') or '') == normalize_title(title)
        ]

    # Semantic Scholar --------------------------------------------------------------------------------------------

    def _semantic_scholar_papers(self, paper_ids: list[str]) -> dict[str, Outcome[JSONObject]]:
        """Each paper's Semantic Scholar record, by ID, in batches. A paper the batch does not return, or a batch
        that fails, is asked for on its own, so the answer and any error are those of a single lookup."""
        unique = list(dict.fromkeys(paper_ids))
        size = self.config.semantic_scholar_batch_size
        papers: dict[str, Outcome[JSONObject]] = {}
        for start in range(0, len(unique), size):
            batch = unique[start : start + size]
            try:
                answers = self.client.post_json(
                    self.semantic_scholar_url + '/paper/batch',
                    {'ids': batch},
                    params={'fields': SEMANTIC_SCHOLAR_FIELDS},
                    headers=self._semantic_scholar_headers(),
                    expected_type=list,
                )
            except SourceError as exc:
                logger.info('Semantic Scholar anchor batch failed, asking one paper at a time: %s', exc)
                answers = []
            if len(answers) != len(batch):
                answers = [None] * len(batch)
            for paper_id, answer in zip(batch, answers, strict=True):
                if isinstance(answer, dict) and not answer.get('error'):
                    papers[paper_id] = Outcome(value=answer)
                else:
                    papers[paper_id] = attempt(partial(self._semantic_scholar_paper, paper_id), LOOKUP_ERRORS)
        return papers

    def _semantic_scholar_headers(self) -> dict[str, str] | None:
        return {'x-api-key': self.semantic_scholar_key} if self.semantic_scholar_key else None

    def _semantic_scholar_paper(self, paper_id: str) -> JSONObject:
        paper: JSONObject = self.client.get(
            '{}/paper/{}'.format(self.semantic_scholar_url, quote(paper_id, safe=':/')),
            {'fields': SEMANTIC_SCHOLAR_FIELDS},
            self._semantic_scholar_headers(),
        )
        return paper

    def _semantic_scholar_anchors(
        self,
        anchors: IdentityAnchors,
        query: ProfessorQuery,
        paper_id: str,
        papers: Mapping[str, Outcome[JSONObject]],
    ) -> None:
        paper = papers[paper_id]
        if paper.error is not None:
            anchors.errors.append(f'{paper_id}: {paper.error}')
            return
        namespace, _, value = paper_id.partition(':')
        key = work_key(dois=[value]) if namespace == 'DOI' else work_key(arxiv_ids=[value])
        for author in paper.unwrap().get('authors') or []:
            if author.get('authorId') and self.compatible(query, author.get('name')):
                anchors.add('semantic_scholar', author['authorId'], 'author of ' + paper_id, AnchorKind.PAPER, key)
