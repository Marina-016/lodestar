"""Rejected local POSTs should return an observable denial, not reset mid-body."""
import threading
from http.client import HTTPConnection
import unittest
from unittest.mock import Mock

import requests

from lodestar.web.server import make_server


class RejectionTests(unittest.TestCase):
    def test_unauthorized_body_is_consumed_before_closing_socket(self):
        app = Mock(token='offline-token')
        server = make_server(app, 0)
        original_setup = server.RequestHandlerClass.setup
        consumed = []

        class ObservedInput:
            def __init__(self, original):
                self.original = original

            def read(self, length):
                self_timeout = server.RequestHandlerClass.timeout
                self_test.assertGreater(self_timeout, 0)
                body = self.original.read(length)
                consumed.append(len(body))
                return body

            def __getattr__(self, name):
                return getattr(self.original, name)

        self_test = self

        def setup(handler):
            original_setup(handler)
            handler.rfile = ObservedInput(handler.rfile)

        server.RequestHandlerClass.setup = setup
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = requests.Session()
        client.trust_env = False
        body = b'x' * 64000
        try:
            response = client.post(f'http://127.0.0.1:{server.server_port}/api/send',
                data=body, timeout=5)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(sum(consumed), len(body))
            app.submit.assert_not_called()
        finally:
            client.close()
            server.shutdown()
            thread.join(5)
            server.server_close()

    def test_repeated_rejected_large_bodies_return_denials_without_reset(self):
        app = Mock(token='offline-token')
        server = make_server(app, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = requests.Session()
        client.trust_env = False
        try:
            url = f'http://127.0.0.1:{server.server_port}/api/send'
            for i in range(40):
                headers = ({'Host': 'evil.example'} if i % 3 == 0 else
                    {'Origin': 'https://evil.example', 'X-Lodestar-Token': app.token} if i % 3 == 1 else {})
                response = client.post(url, data=b'x' * 64000, headers=headers, timeout=3)
                self.assertEqual(response.status_code, 403)
            app.submit.assert_not_called()
        finally:
            client.close()
            server.shutdown()
            thread.join(5)
            server.server_close()

    def test_partial_authenticated_post_times_out_without_dispatching_actions(self):
        app = Mock(token='offline-token')
        server = make_server(app, 0)
        server.RequestHandlerClass.timeout = .05
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            client.putrequest('POST', '/api/send')
            client.putheader('Content-Length', '100')
            client.putheader('X-Lodestar-Token', app.token)
            client.endheaders()  # A client that never sends the promised body.
            response = client.getresponse()
            self.assertEqual(response.status, 408)
            response.read()
            app.submit.assert_not_called()
        finally:
            client.close()
            server.shutdown()
            thread.join(5)
            server.server_close()


if __name__ == '__main__':
    unittest.main()
