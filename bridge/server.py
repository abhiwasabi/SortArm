"""Same-origin local HTTP API plus the existing static dashboard."""
from .gemini_settings import connect_gemini, disconnect_gemini, gemini_status
from .auth0_operator import Auth0Config, Auth0Verifier, AuthError
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, bridge, source=None, camera_manager=None, auth=None, directory, **kwargs):
        self.bridge = bridge
        self.source = source
        self.camera_manager = camera_manager
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
        super().end_headers()

    def send_data(self, status, data, mime):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, status, data):
        self.send_data(status, json.dumps(data).encode(), 'application/json')

    def valid_host(self):
        return self.headers.get('Host') in (
            f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

    def do_GET(self):
        if not self.valid_host():
            return self.send_json(403, {'error': 'Use the local dashboard URL'})
        path = urlsplit(self.path).path
        if path == '/api/auth-config':
            return self.send_json(200, self.auth.config.public() if self.auth else {'enabled': False})
        if path == '/api/operator' and self.auth is not None:
            try:
                claims = self.auth.verify(self.headers.get('Authorization'))
            except AuthError as exc:
                return self.send_json(exc.status, {'authorized': False})
            return self.send_json(200, {'authorized': True, 'sub': claims['sub']})
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
            return self.send_json(200, self.bridge.snapshot())
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
        if (not self.valid_host() or (origin and origin != 'http://' + self.headers.get('Host', ''))
                or self.headers.get('Sec-Fetch-Site') == 'cross-site'):
            return self.send_json(403, {'error': 'Use controls on the local dashboard'})
        path = urlsplit(self.path).path
        if self.auth is not None and path != '/api/stop':
            try:
                self.auth.verify(self.headers.get('Authorization'))
            except AuthError as exc:
                return self.send_json(exc.status, {'error': 'Operator login required' if exc.status == 401
                                                   else 'Operator permission required'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 0 or length > 4096 or self.headers.get('Transfer-Encoding'):
                raise ValueError('Invalid request size')
            body = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(body, dict):
                raise ValueError('Expected a JSON object')
            path = urlsplit(self.path).path
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
            result = {'accepted': accepted, 'state': self.bridge.snapshot()}
            if not accepted and path in ('/api/home', '/api/sort'):
                result['error'] = self.bridge.rejection_reason() or (
                    'Arm rejected HOME.' if path == '/api/home' else 'Arm rejected the manual sort.')
            self.send_json(200 if accepted else 409, result)
        except (ValueError, TypeError) as exc:
            self.send_json(400, {'error': str(exc)})
        except Exception:
            # Do not expose cloud credentials, request details, or hardware tracebacks.
            self.send_json(503, {'error': 'Controller operation failed'})


def make_server(bridge, web, port=4173, source=None, camera_manager=None, auth=None):
    if auth is None:
        config = Auth0Config.from_env()
        auth = Auth0Verifier(config) if config else None
    handler = partial(Handler, bridge=bridge, source=source, camera_manager=camera_manager, auth=auth,
                      directory=str(Path(web).resolve()))
    return ThreadingHTTPServer(('127.0.0.1', port), handler)
