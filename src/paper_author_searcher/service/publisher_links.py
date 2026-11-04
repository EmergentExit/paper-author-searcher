"""Where a person can open a paper at its publisher, from its DOI alone.

Some publishers serve every paper's PDF at an address built from the DOI and let anyone read it in a browser, yet
answer programs with a bot check and record no open licence, so open-access indexes call those papers closed. ACM is
one: ``https://dl.acm.org/doi/pdf/<DOI>`` opens the PDF that its "FREE ACCESS" page offers. The service never
fetches these addresses; it only hands them to the person, as a link to click.

The rules are configuration (``ServiceConfig.publisher_pdf_links``): a DOI prefix, the publisher's name and an
address template. A DOI no rule covers gets its resolver page, where the publisher says whether it can be read.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import quote

from paper_author_searcher.status import StrEnum

DOI_PLACEHOLDER = '{doi}'
# Characters a DOI keeps unescaped in a URL path: its prefix/suffix separator and common suffix punctuation.
DOI_SAFE_CHARACTERS = '/:;()._-'


class LinkKind(StrEnum):
    # The publisher's own PDF address for this paper.
    PDF = 'pdf'
    # The DOI resolver's landing page: the publisher decides what a reader gets there.
    PAGE = 'page'


@dataclass(frozen=True)
class PublisherRule:
    """DOIs starting with ``prefix`` (case-insensitive) have their PDF at ``template`` with ``{doi}`` filled in."""

    prefix: str
    publisher: str
    template: str

    def __post_init__(self) -> None:
        if not self.prefix or not self.publisher or DOI_PLACEHOLDER not in self.template:
            raise ValueError(f'A publisher rule needs a prefix, a name and a template with {DOI_PLACEHOLDER}')

    def covers(self, doi: str) -> bool:
        return doi.lower().startswith(self.prefix.lower())


@dataclass(frozen=True)
class PublisherLink:
    url: str
    kind: LinkKind
    publisher: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {'url': self.url, 'kind': self.kind.value, 'publisher': self.publisher}


class PublisherLinks:
    """The first rule covering a DOI gives its PDF address; any other DOI gets ``page_template``."""

    def __init__(self, rules: Iterable[PublisherRule] = (), page_template: str | None = None) -> None:
        if page_template is not None and DOI_PLACEHOLDER not in page_template:
            raise ValueError(f'The DOI page template needs {DOI_PLACEHOLDER}')
        self.rules = tuple(rules)
        self.page_template = page_template

    @classmethod
    def from_config(cls, rules: Iterable[tuple[str, str, str]], page_template: str | None) -> 'PublisherLinks':
        """Rules as configuration writes them: (DOI prefix, publisher name, address template)."""
        return cls((PublisherRule(*rule) for rule in rules), page_template or None)

    def for_doi(self, doi: str | None) -> PublisherLink | None:
        value = (doi or '').strip()
        if not value:
            return None
        escaped = quote(value, safe=DOI_SAFE_CHARACTERS)
        for rule in self.rules:
            if rule.covers(value):
                return PublisherLink(rule.template.replace(DOI_PLACEHOLDER, escaped), LinkKind.PDF, rule.publisher)
        if self.page_template is None:
            return None
        return PublisherLink(self.page_template.replace(DOI_PLACEHOLDER, escaped), LinkKind.PAGE)
