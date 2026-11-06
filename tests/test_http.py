import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from paper_author_searcher.http import HttpClient, SourceError


class Response:
    def __init__(self, body, content_type, status=200):
        self.body, self.status = body, status
        self.headers = {'Content-Type': content_type}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, size):
        return self.body[:size]

    def geturl(self):
        return 'https://dblp.org/search/author/api?q=secret'


class HttpTests(unittest.TestCase):
    def test_post_json_batch_uses_json_body_and_explicit_array_parser(self):
        response = Response(b'[{"paperId":"example"},null]', 'application/json')
        with patch('paper_author_searcher.http_transport.KeepAliveTransport.open', return_value=response) as opener:
            result = HttpClient().post_json(
                'https://api.semanticscholar.org/graph/v1/paper/batch',
                {'ids': ['one', 'two']},
                params={'fields': 'openAccessPdf'},
                expected_type=list,
            )
        request = opener.call_args[0][0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(json.loads(request.data), {'ids': ['one', 'two']})
        self.assertIn('fields=openAccessPdf', request.full_url)
        self.assertEqual(result, [{'paperId': 'example'}, None])

    def test_batch_html_is_an_api_failure_not_an_empty_result(self):
        response = Response(b'<html>challenge</html>', 'text/html')
        with patch('paper_author_searcher.http_transport.KeepAliveTransport.open', return_value=response):
            with self.assertRaises(SourceError) as raised:
                HttpClient().post_json(
                    'https://api.semanticscholar.org/graph/v1/paper/batch', {'ids': ['one']}, expected_type=list
                )
        self.assertEqual(raised.exception.error_type, 'invalid_content_type')

    def test_html_challenge_diagnostic(self):
        with patch(
            'paper_author_searcher.http_transport.KeepAliveTransport.open',
            return_value=Response(b'<!doctype html><html>bot challenge</html>', 'text/html'),
        ):
            with self.assertRaisesRegex(SourceError, 'HTTP 200 Content-Type text/html.*HTML anti-bot'):
                HttpClient().get('https://dblp.org/search/author/api')

    def test_malformed_and_empty_json(self):
        for body, message in [(b'', 'empty response'), (b'{', 'malformed JSON'), (b'[]', 'expected JSON object')]:
            with patch(
                'paper_author_searcher.http_transport.KeepAliveTransport.open',
                return_value=Response(body, 'application/json'),
            ):
                with self.assertRaisesRegex(SourceError, message):
                    HttpClient().get('https://dblp.org/search/author/api')

    def test_json_accept_header(self):
        with patch(
            'paper_author_searcher.http_transport.KeepAliveTransport.open',
            return_value=Response(b'{"result":{}}', 'application/json'),
        ) as opener:
            self.assertEqual(HttpClient().get('https://dblp.org/search/author/api'), {'result': {}})
            self.assertEqual(opener.call_args[0][0].get_header('Accept'), 'application/json')

    def test_semantic_scholar_429s_wait_out_a_throttling_window_other_hosts_keep_the_default(self):
        def limited(url):
            return HTTPError(url, 429, 'limited', {}, None)

        for url, retries, expected_attempts, longest in (
            ('https://api.semanticscholar.org/graph/v1/paper/x', 2, 9, 10.0),
            ('https://api.openalex.org/works/x', 2, 3, 2),
            ('https://api.semanticscholar.org/graph/v1/paper/x', 0, 1, 0),
        ):
            with self.subTest(url=url, retries=retries):
                client = HttpClient(retries=retries)
                with (
                    patch(
                        'paper_author_searcher.http_transport.KeepAliveTransport.open',
                        side_effect=limited(url),
                    ),
                    patch('paper_author_searcher.http.time.sleep') as sleep,
                    self.assertRaisesRegex(SourceError, 'HTTP 429'),
                ):
                    client.get(url)
                self.assertEqual(client.transport_statistics['metadata_get']['operation_attempts'], expected_attempts)
                waits = [call.args[0] for call in sleep.call_args_list]
                self.assertEqual(max(waits, default=0), longest)

    def test_a_rate_limited_hosts_other_errors_keep_the_default_retries(self):
        url = 'https://api.semanticscholar.org/graph/v1/paper/x'
        client = HttpClient(retries=2)
        with (
            patch(
                'paper_author_searcher.http_transport.KeepAliveTransport.open',
                side_effect=HTTPError(url, 503, 'down', {}, None),
            ),
            patch('paper_author_searcher.http.time.sleep'),
            self.assertRaisesRegex(SourceError, 'HTTP 503'),
        ):
            client.get(url)
        self.assertEqual(client.transport_statistics['metadata_get']['operation_attempts'], 3)

    def test_http_forbidden(self):
        with patch(
            'paper_author_searcher.http_transport.KeepAliveTransport.open',
            side_effect=HTTPError('https://dblp.org', 403, 'forbidden', {}, None),
        ):
            with self.assertRaisesRegex(SourceError, 'HTTP 403'):
                HttpClient().get('https://dblp.org/search/author/api')
