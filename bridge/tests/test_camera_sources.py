from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time
import unittest

from bridge.camera_sources import CameraSourceManager, Esp32SnapshotSource, MAX_JPEG_BYTES, validate_esp32_url
from bridge.core import Bridge


JPEG = b'\xff\xd8\xff\xe0camera test\xff\xd9'


class SnapshotHandler(BaseHTTPRequestHandler):
    trickle_started = threading.Event()
    flaky_requests = 0

    def do_GET(self):
        if self.path == '/flaky':
            self.__class__.flaky_requests += 1
            if self.flaky_requests == 1:
                self.send_error(503)
                return
        if self.path == '/slow':
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', str(len(JPEG)))
            self.end_headers()
            time.sleep(2.7)
            self.wfile.write(JPEG)
            return
        if self.path == '/trickle':
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', str(len(JPEG)))
            self.end_headers()
            self.trickle_started.set()
            for byte in JPEG:
                try:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break
                time.sleep(.35)
            return
        if self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', '/capture')
            self.end_headers()
            return
        if self.path == '/missing':
            self.send_error(503)
            return
        data = JPEG if self.path != '/html' else b'<html>not a camera</html>'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html' if self.path == '/html' else 'image/jpeg')
        self.send_header('Content-Length', str(MAX_JPEG_BYTES + 1 if self.path == '/large' else len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass


class Esp32SnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), SnapshotHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_fetches_bounded_jpeg_snapshot(self):
        self.assertEqual(Esp32SnapshotSource(self.base + '/capture').capture(), (JPEG, 'image/jpeg'))

    def test_accepts_snapshot_slower_than_the_old_two_second_deadline(self):
        self.assertEqual(Esp32SnapshotSource(self.base + '/slow').capture(), (JPEG, 'image/jpeg'))

    def test_failed_camera_backs_off_and_success_restores_capture_rate(self):
        class IdleArm:
            state = 'ERROR'
            detail = 'not homed'
            connected = True

            def on_state(self, listener):
                pass

        web = Path(__file__).resolve().parents[2] / 'web'
        bridge = Bridge(IdleArm(), simulation=True, mock_camera=True)
        manager = CameraSourceManager(bridge, web, allow_mock=True)
        SnapshotHandler.flaky_requests = 0
        manager.select('esp32', url=self.base + '/flaky')
        self.assertFalse(manager.capture_once())
        self.assertGreater(manager.capture_interval(), 0.75)
        self.assertTrue(manager.capture_once())
        self.assertEqual(manager.capture_interval(), 0.75)
        self.assertIsNone(bridge.snapshot()['camera_error'])

    def test_rejects_unexpected_content_redirect_and_oversize(self):
        for path in ('/html', '/large', '/redirect'):
            with self.assertRaises(ValueError):
                Esp32SnapshotSource(self.base + path).capture()

    def test_trickling_response_hits_total_deadline_and_switch_recovers(self):
        class IdleArm:
            state = 'ERROR'
            detail = 'not homed'
            connected = True

            def on_state(self, listener):
                pass

        SnapshotHandler.trickle_started.clear()
        web = Path(__file__).resolve().parents[2] / 'web'
        bridge = Bridge(IdleArm(), simulation=True, mock_camera=True)
        manager = CameraSourceManager(bridge, web, allow_mock=True)
        manager.select('esp32', url=self.base + '/trickle')
        result = []
        capture = threading.Thread(target=lambda: result.append(manager.capture_once()))
        capture.start()
        self.assertTrue(SnapshotHandler.trickle_started.wait(1))
        started = time.monotonic()
        manager.select('mock')
        elapsed = time.monotonic() - started
        capture.join(1)
        self.assertFalse(capture.is_alive())
        self.assertEqual(result, [False])
        self.assertGreater(elapsed, 4)
        self.assertLess(elapsed, 6)
        self.assertEqual(bridge.snapshot()['camera_source'], 'mock')
        self.assertIsNone(bridge.frame)

    def test_only_local_literal_http_urls_without_credentials(self):
        self.assertEqual(validate_esp32_url('http://192.168.4.1/capture'),
                         'http://192.168.4.1/capture')
        for url in (None, '', 'http://localhost/capture', 'http://8.8.8.8/capture',
                    'https://192.168.4.1/capture', 'http://user:pass@192.168.4.1/capture',
                    'http://192.168.4.1/capture?token=secret', 'http://192.168.4.1:0/capture'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_esp32_url(url)


class CameraOffTests(unittest.TestCase):
    def test_stop_releases_camera_and_never_recaptures(self):
        from unittest.mock import Mock
        arm = Mock()
        arm.state = 'READY'
        arm.connected = True
        bridge = Bridge(arm, simulation=True, mock_camera=False)
        source = Mock()
        manager = CameraSourceManager(bridge, Path('.'), initial_source=source, initial_name='usb')
        bridge.frame = (JPEG, 'image/jpeg')
        bridge.enabled = True
        manager.select('off')
        source.close.assert_called_once()
        self.assertFalse(manager.capture_once())
        source.capture.assert_not_called()
        self.assertIsNone(bridge.frame)
        self.assertFalse(bridge.enabled)
        self.assertEqual(bridge.snapshot()['camera_source'], 'off')
        manager.select('usb', index=0)
        self.assertEqual(manager.name, 'usb')


if __name__ == '__main__':
    unittest.main()
