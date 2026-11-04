"""Publisher links built from a DOI: configured PDF addresses first, the DOI resolver page otherwise."""

import unittest
from dataclasses import replace

from paper_author_searcher.config import ServiceConfig
from paper_author_searcher.service.publisher_links import LinkKind, PublisherLink, PublisherLinks, PublisherRule

ACM = ('10.1145/', 'ACM', 'https://dl.acm.org/doi/pdf/{doi}')


class PublisherLinkTests(unittest.TestCase):
    def setUp(self):
        self.links = PublisherLinks.from_config([ACM], 'https://doi.org/{doi}')

    def test_a_covered_doi_gets_the_publishers_pdf_address(self):
        self.assertEqual(
            self.links.for_doi('10.1145/3649405.3659531'),
            PublisherLink('https://dl.acm.org/doi/pdf/10.1145/3649405.3659531', LinkKind.PDF, 'ACM'),
        )

    def test_prefixes_compare_without_case(self):
        rule = PublisherRule('10.1145/', 'ACM', 'https://x/{doi}')
        self.assertTrue(rule.covers('10.1145/ABC'))
        self.assertFalse(rule.covers('10.11450/abc'))

    def test_any_other_doi_gets_its_resolver_page_escaped(self):
        link = self.links.for_doi('10.1002/(SICI)1097 x<y')
        self.assertEqual(link.kind, LinkKind.PAGE)
        self.assertIsNone(link.publisher)
        self.assertEqual(link.url, 'https://doi.org/10.1002/(SICI)1097%20x%3Cy')

    def test_no_doi_or_no_page_template_means_no_link(self):
        self.assertIsNone(self.links.for_doi(None))
        self.assertIsNone(self.links.for_doi('  '))
        only_rules = PublisherLinks.from_config([ACM], '')
        self.assertIsNone(only_rules.for_doi('10.1002/x'))
        self.assertEqual(only_rules.for_doi('10.1145/y').kind, LinkKind.PDF)

    def test_the_first_matching_rule_wins(self):
        links = PublisherLinks.from_config([('10.1145/3', 'A', 'https://a/{doi}'), ACM], None)
        self.assertEqual(links.for_doi('10.1145/3x').publisher, 'A')
        self.assertEqual(links.for_doi('10.1145/4x').publisher, 'ACM')

    def test_a_link_serialises_for_the_result(self):
        link = self.links.for_doi('10.1145/1')
        self.assertEqual(link.to_dict(), {'url': link.url, 'kind': 'pdf', 'publisher': 'ACM'})

    def test_malformed_rules_are_refused(self):
        for rule in (('', 'ACM', 'https://x/{doi}'), ('10.1/', '', 'https://x/{doi}'), ('10.1/', 'A', 'https://x/')):
            with self.assertRaises(ValueError):
                PublisherRule(*rule)
        with self.assertRaises(ValueError):
            PublisherLinks(page_template='https://doi.org/')

    def test_configuration_checks_every_template(self):
        self.assertEqual(ServiceConfig().publisher_pdf_links[0][0], '10.1145/')
        with self.assertRaises(ValueError):
            replace(ServiceConfig(), doi_page_template='https://doi.org/')
        with self.assertRaises(ValueError):
            replace(ServiceConfig(), publisher_pdf_links=(('10.1/', 'A', 'https://a/'),))
        self.assertEqual(replace(ServiceConfig(), doi_page_template='').doi_page_template, '')


if __name__ == '__main__':
    unittest.main()
