"""Fake clients answer Semantic Scholar's paper batch from their own single-paper answers."""

from paper_author_searcher.config import DEFAULT_SOURCES
from paper_author_searcher.transport.http import SourceError


class BatchFromSingleLookups:
    """Mix into a fake client whose ``get`` serves ``/paper/<id>``: a batch request answers each ID as that lookup
    would, and ``None`` where it raises, as Semantic Scholar does for a paper it does not have."""

    def post_json(self, url, payload, params=None, headers=None, expected_type=dict):
        assert url == DEFAULT_SOURCES.semantic_scholar_url + '/paper/batch', url
        self.batches = [*getattr(self, 'batches', []), list(payload['ids'])]
        answers = []
        for paper_id in payload['ids']:
            try:
                answers.append(self.get(f'{DEFAULT_SOURCES.semantic_scholar_url}/paper/{paper_id}', params, headers))
            except SourceError:
                answers.append(None)
        return answers
