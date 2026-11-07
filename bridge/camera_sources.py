"""Switchable local camera sources for the laptop bridge."""
import ipaddress
import http.client
import logging
import threading
import time
from urllib.parse import urlsplit

from .adapters import Camera, MockSource


MAX_JPEG_BYTES = 2 * 1024 * 1024
ESP32_SNAPSHOT_DEADLINE_SECONDS = 5
_LOCAL_NETWORKS = tuple(ipaddress.ip_network(cidr) for cidr in (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8',
    '169.254.0.0/16',
))


def validate_esp32_url(value):
    """Accept only a direct HTTP snapshot endpoint on a local IPv4 address."""
    if not isinstance(value, str) or not value or len(value) > 512 or any(c.isspace() for c in value):
        raise ValueError('Enter a local ESP32 snapshot URL, such as http://192.168.1.50/capture')
    try:
        parts = urlsplit(value)
        address = ipaddress.ip_address(parts.hostname or '')
        port = parts.port
    except ValueError as exc:
        raise ValueError('Use a local ESP32 IPv4 address in the camera URL') from exc
    if (parts.scheme != 'http' or not isinstance(address, ipaddress.IPv4Address)
            or not any(address in network for network in _LOCAL_NETWORKS)
            or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment or port == 0):
        raise ValueError('Use a plain HTTP local IPv4 snapshot URL without credentials or a query')
    return value


class Esp32SnapshotSource:
    """Fetch one bounded JPEG at a time from an ESP32 camera web server."""

    def __init__(self, url):
        self.url = validate_esp32_url(url)

    @staticmethod
    def _remaining(deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f'ESP32 snapshot exceeded the {ESP32_SNAPSHOT_DEADLINE_SECONDS}-second deadline')
        return remaining

    def capture(self):
        parts = urlsplit(self.url)
        deadline = time.monotonic() + ESP32_SNAPSHOT_DEADLINE_SECONDS
        connection = http.client.HTTPConnection(
            parts.hostname, parts.port or 80, timeout=ESP32_SNAPSHOT_DEADLINE_SECONDS)
        try:
            connection.connect()
            sock = connection.sock
            sock.settimeout(self._remaining(deadline))
            connection.request('GET', parts.path or '/', headers={'Accept': 'image/jpeg'})
            sock.settimeout(self._remaining(deadline))
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError('ESP32 snapshot endpoint returned HTTP ' + str(response.status))
            mime = response.headers.get('Content-Type', '').split(';', 1)[0].lower().strip()
            if mime not in ('image/jpeg', 'image/jpg'):
                raise ValueError('ESP32 snapshot endpoint did not return a JPEG')
            length = response.headers.get('Content-Length')
            if length is not None and (not length.isdecimal() or int(length) > MAX_JPEG_BYTES):
                raise ValueError('ESP32 snapshot is too large')
            expected_length = int(length) if length is not None else None
            chunks = []
            size = 0
            while True:
                if size == expected_length or response.isclosed():
                    break
                sock.settimeout(self._remaining(deadline))
                chunk = response.read1(min(65536, MAX_JPEG_BYTES + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_JPEG_BYTES:
                    raise ValueError('ESP32 snapshot is too large')
                chunks.append(chunk)
            data = b''.join(chunks)
            if expected_length is not None and len(data) != expected_length:
                raise ValueError('ESP32 snapshot was incomplete')
        finally:
            connection.close()
        if len(data) < 4 or not data.startswith(b'\xff\xd8\xff') or not data.endswith(b'\xff\xd9'):
            raise ValueError('ESP32 snapshot is not a complete JPEG')
        return data, 'image/jpeg'


class Esp32LiveSource(Esp32SnapshotSource):
    """Consume the camera's continuous MJPEG feed without a snapshot per frame."""

    def __init__(self, url):
        super().__init__(url)
        self.connection = None
        self.response = None
        self.buffer = b''
        self.stream_available = True
        self.stream_connected = False

    def close(self):
        if self.connection is not None:
            self.connection.close()
        self.connection = self.response = None
        self.buffer = b''

    def capture(self):
        if not self.stream_available:
            return super().capture()
        try:
            if self.response is None:
                parts = urlsplit(self.url)
                # Espressif CameraWebServer serves streaming on the next port.
                self.connection = http.client.HTTPConnection(
                    parts.hostname, (parts.port or 80) + 1, timeout=5)
                self.connection.request('GET', '/stream')
                self.response = self.connection.getresponse()
                mime = self.response.headers.get('Content-Type', '')
                if self.response.status != 200 or not mime.startswith('multipart/x-mixed-replace'):
                    self.close()
                    self.stream_available = False
                    return super().capture()
            self.stream_connected = True
            deadline = time.monotonic() + ESP32_SNAPSHOT_DEADLINE_SECONDS
            while time.monotonic() < deadline:
                start = self.buffer.find(b'\xff\xd8\xff')
                end = self.buffer.find(b'\xff\xd9', max(0, start))
                if start >= 0 and end >= 0:
                    frame = self.buffer[start:end + 2]
                    self.buffer = self.buffer[end + 2:]
                    return frame, 'image/jpeg'
                sock = self.connection.sock or self.response.fp.raw._sock
                sock.settimeout(self._remaining(deadline))
                chunk = self.response.read1(65536)
                if not chunk:
                    raise ConnectionError('Camera stream ended')
                self.buffer += chunk
                if len(self.buffer) > MAX_JPEG_BYTES:
                    raise ValueError('Camera stream frame is too large')
            raise TimeoutError('Camera stream stalled')
        except OSError:
            self.close()
            if not self.stream_connected:
                self.stream_available = False
                return super().capture()
            raise
        except Exception:
            self.close()
            raise


class UsbSource:
    """Open a laptop or USB camera on demand, so an offline camera does not block Settings."""

    def __init__(self, index=0, device_id=None):
        self.index = index
        self.device_id = device_id
        self.camera = None

    def capture(self):
        if self.camera is None:
            self.camera = Camera(self.index, device_id=self.device_id)
        try:
            return self.camera.capture()
        except Exception:
            self.close()
            raise

    def close(self):
        if self.camera is not None:
            self.camera.close()
            self.camera = None


class CameraSourceManager:
    """Serialize captures and source changes so an old frame cannot reappear."""

    def __init__(self, bridge, web, *, allow_mock=False, predictor=None, index=0,
                 initial_source=None, initial_name=None, initial_url=None):
        self.bridge = bridge
        self.web = web
        self.allow_mock = allow_mock
        self.predictor = predictor
        self.lock = threading.RLock()
        self.name = initial_name or ('mock' if allow_mock else 'usb')
        self.index = index
        self.url = initial_url
        self.source = initial_source or (MockSource(web) if allow_mock else UsbSource(index))
        self.last_capture_ok = True

    def capture_once(self):
        # Both paths take locks in manager -> bridge order, including select().
        with self.lock:
            if self.name == 'off':
                return False
            try:
                frame = self.source.capture()
            except Exception as exc:
                self.last_capture_ok = False
                if self.name == 'esp32':
                    if isinstance(exc, ValueError):
                        reason = str(exc)
                    elif isinstance(exc, TimeoutError):
                        reason = ('ESP32 camera timed out. Check its Wi-Fi signal and close '
                                  'other camera stream tabs.')
                    else:
                        reason = 'ESP32 camera unreachable. Check its Wi-Fi connection and snapshot URL.'
                elif self.name == 'usb':
                    reason = ('Install bridge/requirements.txt to use a USB camera.' if isinstance(exc, ImportError)
                              else 'USB camera unavailable. Check its connection and camera index.')
                else:
                    reason = 'Mock camera unavailable. Sorting paused.'
                self.bridge.camera_lost(reason)
                return False
            self.bridge.camera_frame(frame)
            self.last_capture_ok = True
            return True

    def predict(self, ticket):
        with self.lock:
            predictor = self.source if self.name == 'mock' else self.predictor
            with self.bridge.lock:
                if ticket[0] != self.bridge.epoch:
                    return None
        if predictor is None:
            return None
        # Cloud requests run outside the source lock. A switch invalidates the ticket.
        return predictor.predict(ticket[3])

    def select(self, name, *, url=None, index=None, device_id=None):
        if name == 'off':
            new_source = None
            new_url = None
            new_index = self.index
        elif name == 'mock':
            if not self.allow_mock:
                raise ValueError('Mock camera is unavailable in this server mode')
            new_source = MockSource(self.web)
            new_url = None
            new_index = self.index
        elif name == 'usb':
            new_index = self.index if index is None else index
            if type(new_index) is not int or not 0 <= new_index <= 10:
                raise ValueError('USB camera index must be between 0 and 10')
            if device_id is not None and (not isinstance(device_id, str) or len(device_id) > 512):
                raise ValueError('Choose a connected camera')
            new_source = UsbSource(new_index, device_id=device_id)
            new_url = None
        elif name == 'esp32':
            new_source = Esp32LiveSource(url)
            new_url = new_source.url
            new_index = self.index
        else:
            raise ValueError('Choose mock, usb, or esp32 as the camera source')

        # Pause before taking the manager lock: a network/USB capture may hold
        # that lock while a prediction is ready to dispatch a physical sort.
        self.bridge.prepare_camera_switch()
        completed = False
        try:
            with self.lock:
                old_source = self.source
                self.source = new_source
                self.name = name
                self.url = new_url
                self.index = new_index
                self.last_capture_ok = True
                with self.bridge.lock:
                    self.bridge.camera_device_id = device_id if name == 'usb' else None
                self.bridge.set_camera_source(name, new_url, index=new_index,
                                              vision_ready=name != 'off' and (name == 'mock' or self.predictor is not None))
                if name == 'off':
                    self.bridge.camera_lost('Camera off. Start the camera to resume preview.')
                completed = True
                if hasattr(old_source, 'close'):
                    try:
                        old_source.close()
                    except Exception:
                        logging.warning('Previous camera source did not close cleanly')
        finally:
            if not completed:
                self.bridge.cancel_camera_switch()

    def select_mock_scenario(self, scenario):
        with self.lock:
            if self.name != 'mock':
                raise ValueError('Select the mock camera before changing sample items')
            self.source.select(scenario)
            with self.bridge.lock:
                self.bridge.invalidate('Mock item changed.')
                self.bridge.frame = None
                self.bridge.frame_at = None

    def capture_interval(self):
        with self.lock:
            if self.name == 'esp32':
                return (0.001 if isinstance(self.source, Esp32LiveSource) and self.source.stream_available
                        else 0.75) if self.last_capture_ok else 2.0
            return 0.25

    def close(self):
        with self.lock:
            if hasattr(self.source, 'close'):
                self.source.close()
