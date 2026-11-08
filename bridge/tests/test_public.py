from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import socket
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bridge.auth0_operator import Auth0Config, AuthError
from bridge.server import OperatorAuth, make_server, stop_server
from bridge.tests.test_bridge import Bridge, CameraSourceManager, FakeArm, MockSource, bridge_main


class FakeAuth0:
    config = Auth0Config('example.us.auth0.com', 'client', 'https://sorter')

    def verify(self, header):
        if header == 'Bearer allowed':
            return {'sub': 'operator'}
        raise AuthError(403 if header == 'Bearer viewer' else 401)


class PublicHttpTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict('os.environ', {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.now = 100.
        self.password = 'test-operator-p\u00e4ssword'
        self.host = 'sorter.tail1234.ts.net'
        self.web = Path(__file__).resolve().parents[2] / 'web'
        self.arm = FakeArm()
        self.bridge = Bridge(self.arm, simulation=True, mock_camera=True, clock=lambda: self.now)
        self.source = MockSource(self.web)
        self.bridge.camera_frame(self.source.capture())
        self.camera = CameraSourceManager(self.bridge, self.web, allow_mock=True)
        self.addCleanup(self.camera.close)
        self.auth = OperatorAuth(self.password, clock=lambda: self.now)
        self.public_server = self.start_server(public=True, public_host=self.host, operator_auth=self.auth)
        self.local_server = self.start_server()

    def enable_auth0(self):
        verifier = FakeAuth0()
        self.public_server = self.start_server(auth=verifier, public=True,
                                               public_host=self.host, operator_auth=self.auth)
        self.local_server = self.start_server(auth=verifier)

    def start_server(self, **kwargs):
        server = make_server(self.bridge, self.web, 0, self.source, self.camera, **kwargs)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
        server.serve_thread = thread
        thread.start()

        def close():
            stop_server(server, thread)
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())

        self.addCleanup(close)
        return server

    def request(self, path, body=None, headers=None, *, local=False, method=None):
        server = self.local_server if local else self.public_server
        data = json.dumps(body).encode() if body is not None else None
        request = Request(f'http://127.0.0.1:{server.server_port}' + path,
                          data=data, headers=headers or {}, method=method)
        try:
            response = urlopen(request, timeout=2)
        except HTTPError as error:
            response = error
        with response:
            result = response.read()
            if response.headers.get_content_type() == 'application/json':
                result = json.loads(result)
            return response.code, result, response.headers

    def login(self):
        status, result, headers = self.request('/api/login', {'password': self.password})
        self.assertEqual(status, 200)
        self.assertTrue(result['operator'])
        self.assertTrue(result['state']['operator'])
        self.assertTrue(result['state']['public'])
        cookie = headers['Set-Cookie']
        for attribute in ('HttpOnly', 'Secure', 'SameSite=Strict', 'Path=/', 'Max-Age=43200'):
            self.assertIn(attribute, cookie.split('; '))
        self.assertTrue(cookie.startswith('sorter_session='))
        return {'Cookie': cookie.split(';', 1)[0]}

    def test_visitors_can_view_state_frame_static_and_gemini(self):
        status, state, _ = self.request('/api/state')
        self.assertEqual(status, 200)
        self.assertEqual(state, dict(self.bridge.snapshot(), public=True, operator=False))
        self.assertNotIn('public', self.bridge.snapshot())
        for path, expected in (('/', b'id="sortingButton"'),
                               ('/api/frame.jpg', b'<svg')):
            with self.subTest(path=path):
                status, data, _ = self.request(path)
                self.assertEqual(status, 200)
                self.assertIn(expected, data)
        status, data, _ = self.request('/api/gemini')
        self.assertEqual(status, 200)
        self.assertEqual(data, {'configured': False, 'model': None})

    def test_stop_is_open_and_interrupts_the_shared_arm(self):
        self.bridge.home()
        self.bridge.set_sorting(False)  # a simulated HOME re-arms automatic sorting
        self.bridge.manual_sort('PAPER')
        self.assertEqual(self.arm.state, 'BUSY')
        status, result, _ = self.request('/api/stop', {})
        self.assertEqual(status, 200)
        self.assertTrue(result['accepted'])
        self.assertEqual(self.arm.state, 'ERROR')
        self.assertEqual(result['state']['arm_state'], 'ERROR')
        self.assertFalse(result['state']['operator'])
        self.assertTrue(result['state']['public'])
        self.assertEqual(self.request('/api/state', local=True)[1]['arm_state'], 'ERROR')

    def test_every_other_control_requires_a_session(self):
        before = self.bridge.snapshot()
        controls = (('/api/home', {}), ('/api/sort', {'bin': 'PAPER'}),
                    ('/api/sorting', {'enabled': True}), ('/api/camera', {'source': 'usb'}),
                    ('/api/mock', {'scenario': 'paper'}), ('/api/gemini', {'key': 'unused'}),
                    ('/api/cameras', None), ('/api/unknown', {}))
        with patch.object(self.camera, 'select') as select:
            for path, body in controls:
                with self.subTest(path=path):
                    status, result, _ = self.request(path, body)
                    self.assertEqual(status, 401)
                    self.assertEqual(result, {'error': 'Operator login required'})
            select.assert_not_called()
        self.assertEqual(self.bridge.snapshot(), before)
        self.assertEqual(self.arm.commands, [])

    def test_wrong_password_and_global_lockout_expire_with_clock(self):
        for attempt in range(5):
            with self.subTest(attempt=attempt):
                status, result, headers = self.request('/api/login', {'password': 'wrong'})
                self.assertEqual(status, 401)
                self.assertEqual(result, {'error': 'Wrong password.'})
                self.assertIsNone(headers.get('Set-Cookie'))
        status, result, _ = self.request('/api/login', {'password': self.password})
        self.assertEqual(status, 429)
        self.assertEqual(result, {'error': 'Too many attempts. Try again in 60 seconds.'})
        self.assertEqual(self.request('/api/home', {}, local=True)[0], 200)
        self.now += 59
        self.assertEqual(self.request('/api/login', {'password': self.password})[0], 429)
        self.now += 1
        self.login()

    def test_failed_attempts_are_kept_and_the_lockout_doubles(self):
        for _ in range(4):
            self.assertEqual(self.request('/api/login', {'password': 'wrong'})[0], 401)
        self.now += 3600
        self.assertEqual(self.request('/api/login', {'password': 'wrong'})[0], 401)
        status, result, _ = self.request('/api/login', {'password': self.password})
        self.assertEqual(status, 429)
        self.assertEqual(result, {'error': 'Too many attempts. Try again in 60 seconds.'})
        self.now += 60
        self.assertEqual(self.request('/api/login', {'password': 'wrong'})[0], 401)
        status, result, _ = self.request('/api/login', {'password': self.password})
        self.assertEqual(status, 429)
        self.assertEqual(result, {'error': 'Too many attempts. Try again in 120 seconds.'})
        self.now += 120
        self.login()
        for _ in range(4):
            self.assertEqual(self.request('/api/login', {'password': 'wrong'})[0], 401)
        self.login()

    def test_lockout_caps_at_fifteen_minutes(self):
        for _ in range(5):
            self.assertEqual(self.auth.login('wrong')[0], 401)
        self.assertEqual(self.auth.login(self.password)[0], 429)
        delay = 0
        for _ in range(20):
            self.now = self.auth.blocked_until
            self.assertEqual(self.auth.login('wrong')[0], 401)
            delay = self.auth.blocked_until - self.now
            self.assertLessEqual(delay, 900)
            if delay == 900:
                break
        self.assertEqual(delay, 900)
        self.now = self.auth.blocked_until
        self.assertEqual(self.auth.login(self.password)[0], 200)

    def test_login_allows_home_and_manual_sort(self):
        headers = self.login()
        status, result, _ = self.request('/api/home', {}, headers)
        self.assertEqual(status, 200)
        self.assertTrue(result['accepted'])
        self.assertTrue(result['state']['operator'])
        # A simulated HOME re-arms automatic sorting; pausing it is also operator-only.
        status, result, _ = self.request('/api/sorting', {'enabled': False}, headers)
        self.assertEqual(status, 200)
        status, result, _ = self.request('/api/sort', {'bin': 'METAL'}, headers)
        self.assertEqual(status, 200)
        self.assertTrue(result['accepted'])
        self.assertTrue(result['state']['operator'])
        self.assertEqual(self.arm.commands, ['METAL'])
        self.arm.finish()
        status, state, _ = self.request('/api/state', headers=headers)
        self.assertEqual(status, 200)
        self.assertTrue(state['operator'])
        self.assertEqual(state['counts']['METAL'], 1)

    def test_login_allows_other_operator_controls(self):
        headers = self.login()
        self.request('/api/home', {}, headers)
        for path, body in (('/api/sorting', {'enabled': True}),
                           ('/api/sorting', {'enabled': False}),
                           ('/api/mock', {'scenario': 'paper'}),
                           ('/api/camera', {'source': 'mock'}),
                           ('/api/gemini', {'disconnect': True})):
            with self.subTest(path=path, body=body):
                status, result, _ = self.request(path, body, headers)
                self.assertEqual(status, 200)
                if 'state' in result:
                    self.assertTrue(result['state']['operator'])
                    self.assertTrue(result['state']['public'])
        with patch('bridge.camera_devices.enumerate_camera_devices', return_value=[]):
            status, result, _ = self.request('/api/cameras', headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(result, {'devices': []})

    def test_logout_invalidates_cookie_and_is_open_to_visitors(self):
        headers = self.login()
        status, result, response_headers = self.request('/api/logout', {}, headers)
        self.assertEqual(status, 200)
        self.assertFalse(result['operator'])
        self.assertFalse(result['state']['operator'])
        self.assertIn('Max-Age=0', response_headers['Set-Cookie'])
        self.assertEqual(self.request('/api/home', {}, headers)[0], 401)
        self.assertEqual(self.request('/api/logout', {})[0], 200)
        self.assertEqual(self.auth.sessions, {})

    def test_logout_ends_every_operator_session(self):
        first = self.login()
        second = self.login()
        self.assertEqual(len(self.auth.sessions), 2)
        self.assertEqual(self.request('/api/logout', {}, first)[0], 200)
        self.assertEqual(self.auth.sessions, {})
        self.assertEqual(self.request('/api/home', {}, first)[0], 401)
        self.assertEqual(self.request('/api/home', {}, second)[0], 401)

    def test_visitor_logout_does_not_end_an_operator_session(self):
        headers = self.login()
        self.assertEqual(self.request('/api/logout', {})[0], 200)
        self.assertEqual(len(self.auth.sessions), 1)
        self.assertEqual(self.request('/api/home', {}, headers)[0], 200)

    def test_partial_request_does_not_block_server_close(self):
        from bridge.server import Handler
        server = self.public_server
        previous = Handler.timeout
        # Long enough that joining the handler would miss the one-second limit.
        Handler.timeout = 5
        sock = socket.create_connection(('127.0.0.1', server.server_port), timeout=2)
        self.addCleanup(sock.close)
        try:
            sock.sendall(
                f'POST /api/login HTTP/1.1\r\nHost: {self.host}\r\nContent-Length: 80\r\n\r\n'.encode())
            sock.settimeout(0.2)
            with self.assertRaises(TimeoutError):
                sock.recv(1)
            started = time.monotonic()
            stop_server(server, server.serve_thread, timeout=1)
            self.assertLess(time.monotonic() - started, 1)
        finally:
            Handler.timeout = previous

    def test_unknown_malformed_and_expired_cookies_are_not_sessions(self):
        headers = self.login()
        for cookie in ('sorter_session=unknown', 'sorter_session="unterminated'):
            self.assertEqual(self.request('/api/home', {}, {'Cookie': cookie})[0], 401)
        self.now += 43200
        self.assertEqual(self.request('/api/home', {}, headers)[0], 401)
        self.assertFalse(self.request('/api/state', headers=headers)[1]['operator'])
        self.assertEqual(self.auth.sessions, {})

    def test_public_and_loopback_hosts_and_origins_are_allowed(self):
        port = self.public_server.server_port
        for host in (self.host, self.host + ':443', f'127.0.0.1:{port}', f'localhost:{port}'):
            with self.subTest(host=host):
                self.assertEqual(self.request('/api/state', headers={'Host': host})[0], 200)
        for origin in ('https://' + self.host, f'http://127.0.0.1:{port}', f'http://localhost:{port}'):
            with self.subTest(origin=origin):
                headers = {'Host': self.host + ':443', 'Origin': origin}
                self.assertEqual(self.request('/api/stop', {}, headers)[0], 200)

    def test_foreign_host_origin_and_cross_site_posts_are_rejected(self):
        for host in ('other.example', self.host + ':80', '127.0.0.1:1', self.host + '.evil.test'):
            for path, body in (('/', None), ('/api/state', None), ('/api/stop', {})):
                with self.subTest(host=host, path=path):
                    self.assertEqual(self.request(path, body, {'Host': host})[0], 403)
        for extra in ({'Origin': 'https://other.example'}, {'Origin': 'null'},
                      {'Origin': 'http://' + self.host}, {'Sec-Fetch-Site': 'cross-site'}):
            for path in ('/api/stop', '/api/login', '/api/logout'):
                with self.subTest(headers=extra, path=path):
                    self.assertEqual(self.request(path, {}, extra)[0], 403)

    def test_public_responses_include_security_headers_including_errors(self):
        for path, body, extra in (('/', None, {}), ('/missing-file', None, {}),
                                   ('/api/state', None, {}), ('/api/home', {}, {}),
                                   ('/api/login', {'password': 'wrong'}, {}),
                                   ('/api/stop', {}, {'Host': 'bad.example'})):
            with self.subTest(path=path):
                _, _, headers = self.request(path, body, extra)
                self.assertEqual(headers['X-Frame-Options'], 'DENY')
                self.assertEqual(headers['Content-Security-Policy'], "frame-ancestors 'none'")
                self.assertEqual(headers['Referrer-Policy'], 'same-origin')
                if path.startswith('/api/'):
                    self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_local_control_is_password_free_with_local_state_flags(self):
        for path, body in (('/api/state', None), ('/api/home', {}),
                           ('/api/sorting', {'enabled': False}),
                           ('/api/sort', {'bin': 'ORGANIC'}), ('/api/stop', {})):
            with self.subTest(path=path):
                status, result, headers = self.request(path, body, local=True)
                self.assertEqual(status, 200)
                state = result if body is None else result['state']
                self.assertFalse(state['public'])
                self.assertTrue(state['operator'])
                self.assertIsNone(headers.get('Set-Cookie'))
                self.assertIsNone(headers.get('X-Frame-Options'))
        self.assertEqual(self.request('/api/login', {'password': self.password}, local=True)[0], 404)
        self.assertEqual(self.request('/api/home', {}, {'Host': self.host}, local=True)[0], 403)

    def test_auth0_token_alone_allows_home_and_marks_state_as_operator(self):
        self.enable_auth0()
        headers = {'Authorization': 'Bearer allowed'}
        status, result, _ = self.request('/api/home', {}, headers)
        self.assertEqual(status, 200)
        self.assertTrue(result['accepted'])
        self.assertTrue(result['state']['operator'])
        status, state, _ = self.request('/api/state', headers=headers)
        self.assertEqual(status, 200)
        self.assertTrue(state['operator'])
        self.assertTrue(state['public'])

    def test_auth0_password_session_alone_and_invalid_token_fallback(self):
        self.enable_auth0()
        headers = self.login()
        for authorization in (None, 'Bearer expired'):
            with self.subTest(authorization=authorization):
                credentials = dict(headers)
                if authorization:
                    credentials['Authorization'] = authorization
                status, result, _ = self.request('/api/home', {}, credentials)
                self.assertEqual(status, 200)
                self.assertTrue(result['state']['operator'])
                self.assertTrue(self.request('/api/state', headers=credentials)[1]['operator'])
        self.assertEqual(self.request('/api/logout', {}, headers)[0], 200)
        self.assertEqual(self.request('/api/home', {}, headers)[0], 401)

    def test_auth0_permission_denial_takes_precedence_over_password_session(self):
        self.enable_auth0()
        cookie = self.login()
        for credentials in ({}, cookie):
            headers = dict(credentials, Authorization='Bearer viewer')
            for path, body in (('/api/home', {}), ('/api/cameras', None)):
                with self.subTest(cookie=bool(credentials), path=path):
                    status, result, _ = self.request(path, body, headers)
                    self.assertEqual(status, 403)
                    self.assertEqual(result, {'error': 'Operator permission required'})
            self.assertFalse(self.request('/api/state', headers=headers)[1]['operator'])
        self.assertFalse(self.bridge.home_confirmed)

    def test_auth0_missing_or_invalid_credentials_require_login(self):
        self.enable_auth0()
        for headers in ({}, {'Authorization': 'Bearer expired'}):
            for path, body in (('/api/home', {}), ('/api/cameras', None)):
                with self.subTest(headers=headers, path=path):
                    status, result, _ = self.request(path, body, headers)
                    self.assertEqual(status, 401)
                    self.assertEqual(result, {'error': 'Operator login required'})
            self.assertFalse(self.request('/api/state', headers=headers)[1]['operator'])

    def test_auth0_token_allows_protected_camera_list(self):
        self.enable_auth0()
        with patch('bridge.camera_devices.enumerate_camera_devices', return_value=[]):
            status, result, _ = self.request('/api/cameras', headers={'Authorization': 'Bearer allowed'})
        self.assertEqual(status, 200)
        self.assertEqual(result, {'devices': []})

    def test_auth0_config_and_operator_are_public_and_verify_tokens_independently(self):
        self.enable_auth0()
        status, config, _ = self.request('/api/auth-config')
        self.assertEqual(status, 200)
        self.assertEqual(config, dict(FakeAuth0.config.public(), public=True))
        self.assertEqual(self.request('/api/auth-config', local=True)[1]['public'], False)
        cookie = self.login()
        for headers, expected, result in (({}, 401, {'authorized': False}),
                                          (cookie, 401, {'authorized': False}),
                                          ({'Authorization': 'Bearer viewer'}, 403, {'authorized': False}),
                                          ({'Authorization': 'Bearer allowed'}, 200,
                                           {'authorized': True, 'sub': 'operator'})):
            with self.subTest(headers=headers):
                status, data, _ = self.request('/api/operator', headers=headers)
                self.assertEqual((status, data), (expected, result))

    def test_auth0_stop_and_sort_history_stay_open_to_viewers(self):
        self.enable_auth0()
        self.bridge.sort_log = Mock()
        self.bridge.sort_log.history.return_value = {'enabled': True, 'ok': True, 'recent': []}
        for path in ('/', '/api/state', '/api/frame.jpg', '/api/gemini', '/api/sort-history'):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 200)
        self.bridge.sort_log.history.assert_called_once_with(60, simulated=True)
        for local in (False, True):
            with self.subTest(local=local):
                self.assertEqual(self.request('/api/stop', {}, local=local)[0], 200)

    def test_local_auth0_requires_token_for_posts_but_keeps_gets_open(self):
        self.enable_auth0()
        cookie = self.login()
        for headers in ({}, cookie, {'Authorization': 'Bearer expired'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request('/api/home', {}, headers, local=True)[0], 401)
        status, state, _ = self.request('/api/state', local=True)
        self.assertEqual(status, 200)
        self.assertFalse(state['operator'])
        self.assertFalse(state['public'])
        with patch('bridge.camera_devices.enumerate_camera_devices', return_value=[]):
            self.assertEqual(self.request('/api/cameras', local=True)[0], 200)
        headers = {'Authorization': 'Bearer allowed'}
        status, result, _ = self.request('/api/home', {}, headers, local=True)
        self.assertEqual(status, 200)
        self.assertTrue(result['state']['operator'])
        for path in ('/api/login', '/api/logout'):
            self.assertEqual(self.request(path, {}, local=True)[0], 401)
            self.assertEqual(self.request(path, {}, headers, local=True)[0], 404)

    def test_auth0_disabled_config_includes_public_flag(self):
        for local in (False, True):
            with self.subTest(local=local):
                status, config, _ = self.request('/api/auth-config', local=local)
                self.assertEqual(status, 200)
                self.assertEqual(config, {'enabled': False, 'public': not local})

    def test_make_server_still_builds_auth0_from_env_by_default(self):
        verifier = FakeAuth0()
        with patch('bridge.server.Auth0Config.from_env', return_value=verifier.config) as config, \
                patch('bridge.server.Auth0Verifier', return_value=verifier) as auth0:
            self.local_server = self.start_server()
        config.assert_called_once_with()
        auth0.assert_called_once_with(verifier.config)
        self.assertEqual(self.request('/api/home', {}, local=True)[0], 401)
        self.assertEqual(self.request('/api/home', {}, {'Authorization': 'Bearer allowed'}, local=True)[0], 200)


class PublicStartupTests(unittest.TestCase):
    def test_auth0_configuration_errors_exit_before_opening_hardware(self):
        complete = {'AUTH0_DOMAIN': 'example.us.auth0.com', 'AUTH0_CLIENT_ID': 'client',
                    'AUTH0_AUDIENCE': 'https://sorter'}
        for env, failure, expected in (({'AUTH0_DOMAIN': 'example.us.auth0.com'}, None, 'together'),
                                        (complete, RuntimeError('Install Auth0 dependencies\nbefore enabling'),
                                         'Install Auth0 dependencies before enabling')):
            with self.subTest(env=env):
                stderr = io.StringIO()
                with patch('sys.argv', ['bridge']), patch.dict('os.environ', env, clear=True), \
                        patch('bridge.__main__.Auth0Verifier', side_effect=failure) as verifier, \
                        patch('bridge.__main__.Camera') as camera, \
                        patch('bridge.__main__.Esp32SnapshotSource') as esp32, \
                        patch('bridge.__main__.CameraSourceManager') as manager, \
                        patch('arm.ArmController.from_config') as arm, \
                        redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
                    bridge_main()
                self.assertEqual(error.exception.code, 2)
                self.assertIn(expected, stderr.getvalue())
                self.assertEqual(len(stderr.getvalue().splitlines()), 1)
                if failure is None:
                    verifier.assert_not_called()
                else:
                    verifier.assert_called_once()
                camera.assert_not_called()
                esp32.assert_not_called()
                manager.assert_not_called()
                arm.assert_not_called()

    def test_missing_or_short_password_exits_before_opening_hardware(self):
        for password in (None, '', 'short', '1234567'):
            with self.subTest(password=password):
                env = {} if password is None else {'BRIDGE_OPERATOR_PASSWORD': password}
                stderr = io.StringIO()
                with patch('sys.argv', ['bridge', '--public-port', '4174', '--public-host', 'sorter.ts.net']), \
                        patch.dict('os.environ', env, clear=True), \
                        patch('bridge.__main__.Camera') as camera, \
                        patch('bridge.__main__.Esp32SnapshotSource') as esp32, \
                        patch('bridge.__main__.CameraSourceManager') as manager, \
                        patch('arm.ArmController.from_config') as arm, \
                        redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
                    bridge_main()
                self.assertEqual(error.exception.code, 2)
                self.assertEqual(len(stderr.getvalue().splitlines()), 1)
                self.assertIn('BRIDGE_OPERATOR_PASSWORD', stderr.getvalue())
                self.assertIn('8 characters', stderr.getvalue())
                camera.assert_not_called()
                esp32.assert_not_called()
                manager.assert_not_called()
                arm.assert_not_called()

    def test_public_host_is_required_before_opening_hardware(self):
        stderr = io.StringIO()
        with patch('sys.argv', ['bridge', '--public-port', '4174']), \
                patch.dict('os.environ', {'BRIDGE_OPERATOR_PASSWORD': 'test-password'}, clear=True), \
                patch('bridge.__main__.Camera') as camera, \
                patch('arm.ArmController.from_config') as arm, \
                redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
            bridge_main()
        self.assertEqual(error.exception.code, 2)
        self.assertIn('--public-host is required', stderr.getvalue())
        camera.assert_not_called()
        arm.assert_not_called()

    def test_two_servers_share_bridge_and_close_on_interrupt(self):
        local = Mock(server_port=4173)
        public = Mock(server_port=4174)
        local.serve_forever.side_effect = KeyboardInterrupt
        arm = Mock(state='ERROR', connected=True)
        stdout = io.StringIO()
        verifier = FakeAuth0()
        env = {'BRIDGE_OPERATOR_PASSWORD': 'test-password', 'AUTH0_DOMAIN': 'example.us.auth0.com',
               'AUTH0_CLIENT_ID': 'client', 'AUTH0_AUDIENCE': 'https://sorter'}
        with patch('sys.argv', ['bridge', '--mock', '--public-port', '4174',
                                '--public-host', 'sorter.ts.net']), \
                patch.dict('os.environ', env, clear=True), \
                patch('bridge.__main__.Auth0Verifier', return_value=verifier) as auth0, \
                patch('bridge.__main__.SortLog.from_env') as sort_log, \
                patch('arm.ArmController.from_config', return_value=arm), \
                patch('bridge.__main__.CameraSourceManager') as camera, \
                patch('bridge.__main__.threading.Thread') as thread, \
                patch('bridge.__main__.make_server', side_effect=[local, public]) as servers, \
                redirect_stdout(stdout):
            bridge_main()
        calls = servers.call_args_list
        auth0.assert_called_once_with(FakeAuth0.config)
        self.assertIs(calls[0].kwargs['auth'], verifier)
        self.assertIs(calls[1].kwargs['auth'], verifier)
        self.assertIs(calls[0].args[0].sort_log, sort_log.return_value)
        sort_log.return_value.close.assert_called_once()
        self.assertEqual([call.args[2] for call in calls], [4173, 4174])
        self.assertIs(calls[0].args[0], calls[1].args[0])
        self.assertNotIn('public', calls[0].kwargs)
        self.assertTrue(calls[1].kwargs['public'])
        self.assertEqual(calls[1].kwargs['public_host'], 'sorter.ts.net')
        self.assertIsInstance(calls[1].kwargs['operator_auth'], OperatorAuth)
        self.assertIs(calls[0].kwargs['camera_manager'], calls[1].kwargs['camera_manager'])
        self.assertTrue(any(call.kwargs.get('target') == public.serve_forever
                            and call.kwargs.get('daemon') is True for call in thread.call_args_list))
        # The local server's serve_forever() already returned on the main thread,
        # so only the public server (on its own thread) needs shutdown().
        local.shutdown.assert_not_called()
        public.shutdown.assert_called_once()
        for server in (local, public):
            server.server_close.assert_called_once()
        arm.stop.assert_called_once()
        arm.close.assert_called_once()
        camera.return_value.close.assert_called_once()
        self.assertIn('Dashboard: http://127.0.0.1:4173', stdout.getvalue())
        self.assertIn('Sort log: writing sort outcomes to Tiger Data', stdout.getvalue())
        self.assertIn('Public port for Funnel only: http://127.0.0.1:4174 -> https://sorter.ts.net', stdout.getvalue())
        self.assertNotIn('test-password', stdout.getvalue())

    def test_stuck_public_shutdown_still_closes_resources(self):
        local = Mock(server_port=4173)
        public = Mock(server_port=4174)
        local.serve_forever.side_effect = KeyboardInterrupt
        public.shutdown.side_effect = lambda: time.sleep(30)
        arm = Mock(state='ERROR', connected=True)
        started = time.monotonic()
        with patch('sys.argv', ['bridge', '--mock', '--public-port', '4174',
                                '--public-host', 'sorter.ts.net']), \
                patch.dict('os.environ', {'BRIDGE_OPERATOR_PASSWORD': 'test-password'}, clear=True), \
                patch('arm.ArmController.from_config', return_value=arm), \
                patch('bridge.__main__.CameraSourceManager'), \
                patch('bridge.__main__.threading.Thread'), \
                patch('bridge.__main__.make_server', side_effect=[local, public]):
            bridge_main()
        self.assertLess(time.monotonic() - started, 5)
        local.shutdown.assert_not_called()
        public.server_close.assert_called_once()
        local.server_close.assert_called_once()
        arm.stop.assert_called_once()
        arm.close.assert_called_once()

    def test_public_bind_failure_closes_local_socket_and_resources(self):
        local = Mock(server_port=4173)
        arm = Mock(state='ERROR', connected=True)
        stderr = io.StringIO()
        with patch('sys.argv', ['bridge', '--mock', '--public-port', '4174',
                                '--public-host', 'sorter.ts.net']), \
                patch.dict('os.environ', {'BRIDGE_OPERATOR_PASSWORD': 'test-password'}, clear=True), \
                patch('arm.ArmController.from_config', return_value=arm), \
                patch('bridge.__main__.CameraSourceManager') as camera, \
                patch('bridge.__main__.make_server', side_effect=[local, OSError('Address already in use')]), \
                redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
            bridge_main()
        self.assertEqual(error.exception.code, 2)
        local.shutdown.assert_not_called()
        local.server_close.assert_called_once()
        arm.stop.assert_called_once()
        arm.close.assert_called_once()
        camera.return_value.close.assert_called_once()
        self.assertIn('4174', stderr.getvalue())
        self.assertIn('--public-port', stderr.getvalue())
        self.assertNotIn('test-password', stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
