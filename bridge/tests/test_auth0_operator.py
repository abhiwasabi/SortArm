import http.client
import importlib.util
import json
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bridge.auth0_operator import Auth0Config, Auth0Verifier, AuthError
from bridge.server import make_server


class Auth0ConfigTests(unittest.TestCase):
    def test_unconfigured_and_partial_configuration(self):
        self.assertIsNone(Auth0Config.from_env({}))
        with self.assertRaisesRegex(ValueError, 'together'):
            Auth0Config.from_env({'AUTH0_DOMAIN': 'example.us.auth0.com'})
        with self.assertRaisesRegex(ValueError, 'hostname'):
            Auth0Config.from_env({'AUTH0_DOMAIN': 'https://example.us.auth0.com',
                                  'AUTH0_CLIENT_ID': 'abc', 'AUTH0_AUDIENCE': 'https://sorter'})

    def test_verifier_requires_audience_issuer_signature_and_operator_permission(self):
        captured = {}

        class FakeJwks:
            def __init__(self, url, **options):
                captured['url'] = url

            def get_signing_key_from_jwt(self, token):
                return SimpleNamespace(key='test-public-key')

        def decode(token, key, **options):
            captured.update(options)
            return {'sub': 'auth0|operator', 'permissions': ['sorter:operate']}

        fake_jwt = SimpleNamespace(PyJWKClient=FakeJwks, decode=decode)
        with patch.dict(sys.modules, {'jwt': fake_jwt}):
            verifier = Auth0Verifier(Auth0Config('example.us.auth0.com', 'client', 'https://sorter'))
        self.assertEqual(verifier.verify('Bearer signed-token')['sub'], 'auth0|operator')
        self.assertEqual(captured['url'], 'https://example.us.auth0.com/.well-known/jwks.json')
        self.assertEqual(captured['issuer'], 'https://example.us.auth0.com/')
        self.assertEqual(captured['audience'], 'https://sorter')
        self.assertEqual(captured['algorithms'], ['RS256'])
        fake_jwt.decode = lambda *args, **kwargs: {'permissions': []}
        with self.assertRaises(AuthError) as error:
            verifier.verify('Bearer signed-token')
        self.assertEqual(error.exception.status, 403)
        with self.assertRaises(AuthError):
            verifier.verify('Bearer ')

    @unittest.skipUnless(importlib.util.find_spec('jwt'), 'optional Auth0 dependencies not installed')
    def test_real_rs256_signature_and_audience_validation(self):
        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        config = Auth0Config('example.us.auth0.com', 'client', 'https://sorter')
        verifier = Auth0Verifier(config)
        verifier.jwks = SimpleNamespace(get_signing_key_from_jwt=lambda token:
                                        SimpleNamespace(key=private_key.public_key()))
        now = int(time.time())
        claims = {'sub': 'auth0|operator', 'iss': 'https://example.us.auth0.com/',
                  'aud': 'https://sorter', 'iat': now, 'exp': now + 60,
                  'permissions': ['sorter:operate']}
        token = jwt.encode(claims, private_key, algorithm='RS256')
        self.assertEqual(verifier.verify('Bearer ' + token)['sub'], 'auth0|operator')
        for invalid in (jwt.encode({**claims, 'aud': 'https://other'}, private_key, algorithm='RS256'),
                        jwt.encode(claims, other_key, algorithm='RS256')):
            with self.assertRaises(AuthError):
                verifier.verify('Bearer ' + invalid)


class Auth0ServerTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

        class FakeAuth:
            config = Auth0Config('example.us.auth0.com', 'client', 'https://sorter')

            def verify(self, header):
                if header == 'Bearer allowed':
                    return {'sub': 'operator'}
                raise AuthError(403 if header == 'Bearer viewer' else 401)

        bridge = SimpleNamespace(snapshot=lambda: {'arm_state': 'READY'},
                                 home=lambda: self.calls.append('HOME') or True,
                                 stop=lambda: self.calls.append('STOP') or True)
        web = Path(__file__).resolve().parents[2] / 'web'
        self.server = make_server(bridge, web, 0, auth=FakeAuth())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, method, path, token=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = token
        connection.request(method, path, body=b'{}' if method == 'POST' else None, headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read())
        connection.close()
        return response.status, data

    def test_config_and_protected_commands_with_public_stop(self):
        status, data = self.request('GET', '/api/auth-config')
        self.assertEqual(status, 200)
        self.assertEqual(data['audience'], 'https://sorter')
        self.assertNotIn('secret', data)
        self.assertEqual(self.request('GET', '/api/operator')[0], 401)
        self.assertEqual(self.request('GET', '/api/operator', 'Bearer viewer')[0], 403)
        self.assertEqual(self.request('GET', '/api/operator', 'Bearer allowed'),
                         (200, {'authorized': True, 'sub': 'operator'}))
        self.assertEqual(self.request('POST', '/api/home')[0], 401)
        self.assertEqual(self.request('POST', '/api/home', 'Bearer viewer')[0], 403)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.request('POST', '/api/home', 'Bearer allowed')[0], 200)
        self.assertEqual(self.request('POST', '/api/stop')[0], 200)
        self.assertEqual(self.calls, ['HOME', 'STOP'])


if __name__ == '__main__':
    unittest.main()
