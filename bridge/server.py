"""Same-origin local HTTP API plus the existing static dashboard."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlsplit


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, bridge, source=None, directory, **kwargs):
        self.bridge = bridge
        self.source = source
        super().__init__(*args, directory=directory, **kwargs)

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
        if path == '/api/state':
            return self.send_json(200, self.bridge.snapshot())
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

    def do_POST(self):
        origin = self.headers.get('Origin')
        if (not self.valid_host() or (origin and origin != 'http://' + self.headers.get('Host', ''))
                or self.headers.get('Sec-Fetch-Site') == 'cross-site'):
            return self.send_json(403, {'error': 'Use controls on the local dashboard'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 0 or length > 4096 or self.headers.get('Transfer-Encoding'):
                raise ValueError('Invalid request size')
            body = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(body, dict):
                raise ValueError('Expected a JSON object')
            path = urlsplit(self.path).path
            if path == '/api/stop':
                accepted = self.bridge.stop()
            elif path == '/api/classify':
                self.bridge.classify_once()
                accepted = True
            elif path == '/api/home':
                accepted = self.bridge.home()
            elif path == '/api/sorting':
                self.bridge.set_sorting(body.get('enabled'))
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
            self.send_json(200 if accepted else 409,
                           {'accepted': accepted, 'state': self.bridge.snapshot()})
        except (ValueError, TypeError) as exc:
            self.send_json(400, {'error': str(exc)})
        except Exception:
            # Do not expose cloud credentials, request details, or hardware tracebacks.
            self.send_json(503, {'error': 'Controller operation failed'})


def make_server(bridge, web, port=4173, source=None):
    handler = partial(Handler, bridge=bridge, source=source, directory=str(Path(web).resolve()))
    return ThreadingHTTPServer(('127.0.0.1', port), handler)
