"""Loopback HTTP API and dashboard, with optional public operator sessions."""
from .gemini_settings import connect_gemini, disconnect_gemini, gemini_status
from .auth0_operator import Auth0Config, Auth0Verifier, AuthError
from functools import partial
import hmac
from http.cookies import CookieError, SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import socket
import sys
import threading
import time
from threading import Thread as _Thread
from urllib.parse import parse_qs, urlsplit


class OperatorAuth:
    """Process-local sessions and a shared lockout; Funnel hides client IPs."""
    lifetime = 43200
    lockout_base = 60
    lockout_cap = 15 * 60

    def __init__(self, password, *, clock=time.monotonic):
        self.password = password.encode('utf-8')
        self.clock = clock
        self.lock = threading.Lock()
        self.sessions = {}
        self.failure_count = 0
        self.blocked_until = 0

    def login(self, password):
        with self.lock:
            now = self.clock()
            if now < self.blocked_until:
                return 429, None
            if not isinstance(password, str) or not hmac.compare_digest(
                    password.encode('utf-8'), self.password):
                # Keep every failure until a correct password. Waiting out one
                # block must not restore a fresh set of guesses.
                self.failure_count += 1
                if self.failure_count >= 5:
                    delay = min(self.lockout_base * (2 ** (self.failure_count - 5)),
                                self.lockout_cap)
                    self.blocked_until = now + delay
                return 401, None
            self.failure_count = 0
            self.blocked_until = 0
            self._expire(now)
            token = secrets.token_urlsafe(32)
            self.sessions[token] = now + self.lifetime
            return 200, token

    def retry_after(self):
        with self.lock:
            remaining = self.blocked_until - self.clock()
        if remaining <= 0:
            return 1
        return int(remaining + 0.999)

    def _expire(self, now):
        for token, expiry in list(self.sessions.items()):
            if expiry <= now:
                del self.sessions[token]

    def _token(self, cookie):
        try:
            cookies = SimpleCookie()
            cookies.load(cookie or '')
            value = cookies.get('sorter_session')
            return value.value if value is not None else None
        except CookieError:
            return None

    def valid_session(self, cookie):
        with self.lock:
            self._expire(self.clock())
            return self._token(cookie) in self.sessions

    def logout(self, cookie):
        with self.lock:
            # One logout ends every browser. A request with no live session
            # must not be able to kick a different operator out.
            token = self._token(cookie)
            if token in self.sessions:
                self.sessions.clear()

    def cookie(self, token, *, max_age=lifetime):
        return (f'sorter_session={token}; HttpOnly; Secure; SameSite=Strict; '
                f'Path=/; Max-Age={max_age}')


class Handler(SimpleHTTPRequestHandler):
    # Bound a client that connects and never finishes the request. Gemini
    # connect is the slowest handler work and gives up after 15 seconds.
    timeout = 30

    def __init__(self, *args, bridge, source=None, camera_manager=None, auth=None, directory,
                 public=False, public_host=None, operator_auth=None, **kwargs):
        self.bridge = bridge
        self.source = source
        self.camera_manager = camera_manager
        self.public = public
        self.public_host = public_host
        self.operator_auth = operator_auth
        self.auth = auth
        super().__init__(*args, directory=directory, **kwargs)

    def log_request(self, code='-', size='-'):
        # The preview polls cached JPEGs frequently; omit routine successes
        # so an unattended terminal session does not fill with frame logs.
        if code in (200, 503) and urlsplit(self.path).path == '/api/frame.jpg':
            return
        super().log_request(code, size)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if self.public:
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy', "frame-ancestors 'none'")
            self.send_header('Referrer-Policy', 'same-origin')
        super().end_headers()

    def send_data(self, status, data, mime, *, headers=None):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, status, data, *, headers=None):
        self.send_data(status, json.dumps(data).encode(), 'application/json', headers=headers)

    def access(self):
        authorization = self.headers.get('Authorization')
        if self.auth is not None and authorization is not None:
            try:
                self.auth.verify(authorization)
                return None
            except AuthError as exc:
                if exc.status == 403:
                    return 403, 'Operator permission required'
        if self.public and self.operator_auth.valid_session(self.headers.get('Cookie')):
            return None
        if not self.public and self.auth is None:
            return None
        return 401, 'Operator login required'

    def operator(self):
        return self.access() is None

    def state(self, *, operator=None):
        return dict(self.bridge.snapshot(), public=self.public,
                    operator=self.operator() if operator is None else operator)

    def valid_host(self):
        hosts = (
            f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')
        if self.public:
            hosts += (self.public_host, f'{self.public_host}:443')
        return self.headers.get('Host') in hosts

    def valid_origin(self, origin):
        if self.public:
            return origin in (f'https://{self.public_host}',
                              f'http://127.0.0.1:{self.server.server_port}',
                              f'http://localhost:{self.server.server_port}')
        return origin == 'http://' + self.headers.get('Host', '')

    def do_HEAD(self):
        if self.public:
            if not self.valid_host():
                return self.send_error(403)
            if urlsplit(self.path).path.startswith('/api/'):
                return self.send_error(405)
        super().do_HEAD()

    def do_GET(self):
        if not self.valid_host():
            return self.send_json(403, {'error': 'Use the local dashboard URL'})
        path = urlsplit(self.path).path
        if path == '/api/auth-config':
            config = self.auth.config.public() if self.auth else {'enabled': False}
            return self.send_json(200, dict(config, public=self.public))
        if path == '/api/operator' and self.auth is not None:
            try:
                claims = self.auth.verify(self.headers.get('Authorization'))
            except AuthError as exc:
                return self.send_json(exc.status, {'authorized': False})
            return self.send_json(200, {'authorized': True, 'sub': claims['sub']})
        if (self.public and path.startswith('/api/')
                and path not in ('/api/state', '/api/frame.jpg', '/api/gemini', '/api/sort-history',
                                 '/api/auth-config', '/api/operator')):
            denied = self.access()
            if denied is not None:
                status, message = denied
                return self.send_json(status, {'error': message})
        if path == '/api/cameras':
            try:
                from .camera_devices import enumerate_camera_devices
                devices = [{'id': d.path, 'name': d.name, 'index': d.index}
                           for d in enumerate_camera_devices() if d.path]
                return self.send_json(200, {'devices': devices})
            except Exception:
                return self.send_json(503, {'error': 'Camera list unavailable. Check webcam dependencies.'})
        if path == '/api/gemini' and self.camera_manager is not None:
            return self.send_json(200, gemini_status(self.camera_manager))
        if path == '/api/state':
            return self.send_json(200, self.state())
        if path == '/api/sort-history':
            return self.send_sort_history()
        if path == '/api/frame.jpg':
            with self.bridge.lock:
                frame = self.bridge.frame if self.bridge.fresh_frame() else None
            if frame is None:
                return self.send_json(503, {'error': 'Camera unavailable'})
            return self.send_data(200, frame[0], frame[1])
        if path.startswith('/api/'):
            return self.send_json(404, {'error': 'Unknown endpoint'})
        # Static serving is rooted in web/, never the repository or its credentials.
        super().do_GET()

    def send_sort_history(self):
        # Read-only Tiger Data summary (#47). Never touches the bridge lock or the arm.
        sort_log = getattr(self.bridge, 'sort_log', None)
        if sort_log is None:
            return self.send_json(200, {'enabled': False})
        query = parse_qs(urlsplit(self.path).query)
        try:
            minutes = int((query.get('minutes') or ['60'])[0])
        except ValueError:
            return self.send_json(400, {'error': 'minutes must be a whole number'})
        # One mode at a time; by default the mode this bridge runs in, so a physical demo never
        # shows simulated cycles and a mock run never claims physical drops.
        mode = (query.get('mode') or ['simulated' if self.bridge.simulation else 'physical'])[0]
        if mode not in ('simulated', 'physical'):
            return self.send_json(400, {'error': 'mode must be simulated or physical'})
        try:
            return self.send_json(200, sort_log.history(minutes, simulated=mode == 'simulated'))
        except Exception:
            # Never echo database errors: they can include the host or user name.
            return self.send_json(200, {'enabled': True, 'ok': False,
                                        'error': 'Tiger Data is unreachable. Sorting is unaffected.'})

    def do_POST(self):
        origin = self.headers.get('Origin')
        if (not self.valid_host() or (origin and not self.valid_origin(origin))
                or self.headers.get('Sec-Fetch-Site') == 'cross-site'):
            return self.send_json(403, {'error': 'Use controls on the local dashboard'})
        path = urlsplit(self.path).path
        if path != '/api/stop' and not (self.public and path in ('/api/login', '/api/logout')):
            denied = self.access()
            if denied is not None:
                status, message = denied
                return self.send_json(status, {'error': message})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 0 or length > 4096 or self.headers.get('Transfer-Encoding'):
                raise ValueError('Invalid request size')
            body = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(body, dict):
                raise ValueError('Expected a JSON object')
            if self.public and path == '/api/login':
                status, token = self.operator_auth.login(body.get('password'))
                if status != 200:
                    if status == 401:
                        message = 'Wrong password.'
                    else:
                        seconds = self.operator_auth.retry_after()
                        unit = 'second' if seconds == 1 else 'seconds'
                        message = f'Too many attempts. Try again in {seconds} {unit}.'
                    return self.send_json(status, {'error': message})
                return self.send_json(200, {'operator': True, 'state': self.state(operator=True)},
                                      headers={'Set-Cookie': self.operator_auth.cookie(token)})
            if self.public and path == '/api/logout':
                self.operator_auth.logout(self.headers.get('Cookie'))
                return self.send_json(200, {'operator': False, 'state': self.state(operator=False)},
                                      headers={'Set-Cookie': self.operator_auth.cookie('', max_age=0)})
            if path == '/api/gemini' and self.camera_manager is not None:
                result = (disconnect_gemini(self.camera_manager) if body.get('disconnect') is True
                          else connect_gemini(self.camera_manager, body))
                return self.send_json(200, result)
            if path == '/api/stop':
                accepted = self.bridge.stop()
            elif path == '/api/home':
                accepted = self.bridge.home()
            elif path == '/api/sort':
                accepted = self.bridge.manual_sort(body.get('bin'))
            elif path == '/api/sorting':
                self.bridge.set_sorting(body.get('enabled'))
                accepted = True
            elif path == '/api/camera' and self.camera_manager is not None:
                self.camera_manager.select(body.get('source'), url=body.get('url'), index=body.get('index'), device_id=body.get('device_id'))
                accepted = True
            elif path == '/api/mock' and self.camera_manager is not None:
                self.camera_manager.select_mock_scenario(body.get('scenario'))
                accepted = True
            elif path == '/api/mock' and self.source is not None:
                # Changing a sample cannot apply a result from the previous image.
                with self.bridge.lock:
                    self.source.select(body.get('scenario'))
                    self.bridge.invalidate('Mock item changed.')
                    self.bridge.frame = None
                    self.bridge.frame_at = None
                accepted = True
            else:
                return self.send_json(404, {'error': 'Unknown endpoint'})
            result = {'accepted': accepted, 'state': self.state()}
            if not accepted and path in ('/api/home', '/api/sort'):
                result['error'] = self.bridge.rejection_reason() or (
                    'Arm rejected HOME.' if path == '/api/home' else 'Arm rejected the manual sort.')
            self.send_json(200 if accepted else 409, result)
        except (ValueError, TypeError) as exc:
            self.send_json(400, {'error': str(exc)})
        except Exception:
            # Do not expose cloud credentials, request details, or hardware tracebacks.
            self.send_json(503, {'error': 'Controller operation failed'})


class DashboardServer(ThreadingHTTPServer):
    """Request threads are daemons, so closing the port does not wait on them."""
    daemon_threads = True
    block_on_close = False

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (TimeoutError, socket.timeout)):
            return
        super().handle_error(request, client_address)


def make_server(bridge, web, port=4173, source=None, camera_manager=None, auth=None, *,
                public=False, public_host=None, operator_auth=None):
    if auth is False:
        auth = None
    elif auth is None:
        config = Auth0Config.from_env()
        auth = Auth0Verifier(config) if config else None
    if public and (not public_host or operator_auth is None):
        raise ValueError('Public server requires public_host and operator authentication')
    handler = partial(Handler, bridge=bridge, source=source, camera_manager=camera_manager,
                      auth=auth, directory=str(Path(web).resolve()), public=public,
                      public_host=public_host, operator_auth=operator_auth)
    return DashboardServer(('127.0.0.1', port), handler)


def stop_server(server, thread=None, *, timeout=2):
    """Stop the accept loop, then close the listening socket.

    shutdown() waits until serve_forever returns. A stuck loop must not keep
    the process in that wait, or the launcher never turns Funnel off. Call
    shutdown() only while its thread is still inside serve_forever; calling it
    after the loop has returned deadlocks.
    """
    if server is None:
        return
    # Bind the thread class at import so a test patch of threading.Thread
    # cannot turn this deadline into a no-op.
    if thread is not None and thread.is_alive():
        stopper = _Thread(target=_shutdown_quietly, args=(server,), daemon=True)
        stopper.start()
        stopper.join(timeout)
    try:
        server.server_close()
    except OSError:
        pass


def _shutdown_quietly(server):
    try:
        server.shutdown()
    except Exception:
        pass
