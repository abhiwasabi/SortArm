import json
from pathlib import Path
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bridge.adapters import MockSource
from bridge.camera_sources import CameraSourceManager
from bridge.core import Bridge, validate_prediction
from bridge.server import make_server


class FakeArm:
    def __init__(self):
        self.state = 'ERROR'
        self.detail = 'not homed'
        self.connected = True
        self.last_rejection = None
        self.commands = []
        self.listeners = []

    def on_state(self, listener):
        self.listeners.append(listener)

    def emit(self, state, detail=None):
        self.state, self.detail = state, detail
        for listener in self.listeners:
            listener(state, detail)

    def home(self):
        if self.state == 'BUSY':
            return False
        self.emit('DONE', 'HOME')
        self.emit('READY')
        return True

    def sort(self, bin_name):
        if self.state != 'READY':
            return False
        self.commands.append(bin_name)
        self.emit('BUSY')
        return True

    def finish(self):
        self.emit('DONE', 'SORT ' + self.commands[-1])
        self.emit('READY')

    def stop(self):
        self.emit('ERROR', 'stopped')
        return True


def prediction(**changes):
    return dict(dict(label='Plastic bottle', bin='PLASTIC', confidence=None,
                     item_present=True, needs_review=False), **changes)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.arm = FakeArm()
        self.bridge = Bridge(self.arm, simulation=True, clock=lambda: self.now)
        self.bridge.camera_frame((b'frame', 'image/jpeg'))
        self.bridge.home()
        self.bridge.set_sorting(True)

    def result(self, raw=None):
        self.now += .1
        self.bridge.camera_frame((b'frame', 'image/jpeg'))
        return self.bridge.accept_prediction(self.bridge.begin_prediction(), raw or prediction())

    def test_one_sort_per_item_and_only_done_increments(self):
        self.assertTrue(self.result())
        self.assertEqual(self.bridge.counts['PLASTIC'], 0)
        self.arm.finish()
        self.assertEqual(self.bridge.counts['PLASTIC'], 1)
        self.assertFalse(self.result())
        empty = prediction(label='EMPTY', bin=None, item_present=False)
        self.result(empty)
        self.result(empty)
        self.assertTrue(self.result())
        self.arm.finish()
        self.assertEqual(self.arm.commands, ['PLASTIC', 'PLASTIC'])
        self.assertEqual(self.bridge.counts['PLASTIC'], 2)

    def test_history_uses_accepted_item_and_deduplicates_done(self):
        self.result()
        self.bridge.prediction = None
        self.arm.finish()
        self.arm.emit('DONE', 'SORT PLASTIC')
        rows = self.bridge.snapshot()['recent_sorts']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['label'], 'Plastic bottle')
        self.assertEqual(rows[0]['bin'], 'PLASTIC')
        self.assertTrue(rows[0]['id'])
        self.assertTrue(rows[0]['completed_at'])
        self.assertEqual(self.bridge.counts['PLASTIC'], 1)

    def test_replayed_empty_frame_cannot_rearm_item(self):
        self.assertTrue(self.result())
        self.arm.finish()
        self.now += .1
        self.bridge.camera_frame((b'empty', 'image/jpeg'))
        ticket = self.bridge.begin_prediction()
        empty = prediction(label='EMPTY', bin=None, item_present=False)
        self.bridge.accept_prediction(ticket, empty)
        self.bridge.accept_prediction(ticket, empty)
        self.assertTrue(self.bridge.awaiting_clear)

    def test_unknown_review_low_confidence_and_missing_bin_do_not_move(self):
        for changes in (dict(label='UNKNOWN'), dict(needs_review=True),
                        dict(confidence=.2), dict(bin=None)):
            self.assertFalse(self.result(prediction(**changes)))
            self.assertIsNone(self.bridge.snapshot()['bin'])
        self.assertEqual(self.arm.commands, [])

    def test_stop_invalidates_pending_result_even_after_operator_home(self):
        ticket = self.bridge.begin_prediction()
        self.bridge.stop()
        self.bridge.home()
        self.bridge.set_sorting(True)
        self.assertFalse(self.bridge.accept_prediction(ticket, prediction()))
        self.assertEqual(self.arm.commands, [])
        self.assertEqual(self.bridge.counts['PLASTIC'], 0)

    def test_camera_loss_and_pause_invalidate_model_results(self):
        ticket = self.bridge.begin_prediction()
        self.bridge.camera_lost()
        self.assertFalse(self.bridge.accept_prediction(ticket, prediction()))
        self.assertFalse(self.bridge.enabled)
        self.assertIsNotNone(self.bridge.snapshot()['camera_error'])
        self.bridge.camera_frame((b'new', 'image/jpeg'))
        self.assertIsNone(self.bridge.snapshot()['camera_error'])
        self.assertEqual(self.bridge.snapshot()['status'], 'Camera feed restored. Sorting remains paused.')
        self.assertFalse(self.bridge.enabled)
        self.bridge.set_sorting(True)
        ticket = self.bridge.begin_prediction()
        self.bridge.set_sorting(False)
        self.assertFalse(self.bridge.accept_prediction(ticket, prediction()))

    def test_camera_source_change_pauses_and_invalidates_in_flight_result(self):
        ticket = self.bridge.begin_prediction()
        self.bridge.set_camera_source('esp32', 'http://192.168.4.1/capture', vision_ready=False)
        self.assertFalse(self.bridge.enabled)
        self.assertIsNone(self.bridge.frame)
        self.assertIsNone(self.bridge.snapshot()['prediction_at'])
        with self.assertRaisesRegex(ValueError, 'Gemini is not configured'):
            self.bridge.set_sorting(True)
        self.assertFalse(self.bridge.accept_prediction(ticket, prediction()))
        self.assertEqual(self.arm.commands, [])

    def test_slow_result_cannot_move_or_refresh_prediction_timestamp(self):
        ticket = self.bridge.begin_prediction()
        self.now += 9
        self.bridge.camera_frame((b'new', 'image/jpeg'))
        self.assertFalse(self.bridge.accept_prediction(ticket, prediction()))
        self.assertIsNone(self.bridge.snapshot()['prediction_at'])

    def test_simulation_can_accept_slow_result_with_fresh_camera(self):
        self.bridge.prediction_max_age = 30
        ticket = self.bridge.begin_prediction()
        self.now += 12
        self.bridge.camera_frame((b'current frame', 'image/jpeg'))
        self.assertTrue(self.bridge.accept_prediction(ticket, prediction()))

    def test_stale_frame_busy_or_disconnected_arm_cannot_start_prediction(self):
        self.now += 9
        self.assertIsNone(self.bridge.begin_prediction())
        self.bridge.camera_frame((b'new', 'image/jpeg'))
        self.arm.connected = False
        self.assertIsNone(self.bridge.begin_prediction())
        self.arm.connected = True
        self.arm.emit('BUSY')
        self.assertIsNone(self.bridge.begin_prediction())

    def test_prediction_error_pauses_and_home_is_not_counted(self):
        self.bridge.prediction_failed(self.bridge.begin_prediction())
        self.assertFalse(self.bridge.enabled)
        self.assertEqual(self.bridge.counts, {'PAPER': 0, 'PLASTIC': 0, 'METAL': 0, 'ORGANIC': 0})

    def test_stop_does_not_hold_lock_while_waiting_for_callback(self):
        def threaded_stop():
            thread = threading.Thread(target=lambda: self.arm.emit('ERROR', 'stopped'))
            thread.start()
            thread.join(1)
            self.assertFalse(thread.is_alive())
        self.arm.stop = threaded_stop
        self.bridge.stop()

    def test_prediction_validation_rejects_malformed_cloud_output(self):
        for raw in ([], {}, prediction(bin='COMPOST'), prediction(confidence=True),
                    prediction(confidence=float('nan')), prediction(item_present='false'),
                    prediction(item_present=False)):
            with self.assertRaises(ValueError):
                validate_prediction(raw)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.web = Path(__file__).resolve().parents[2] / 'web'
        cls.arm = FakeArm()
        cls.bridge = Bridge(cls.arm, simulation=True, mock_camera=True, max_age=60)
        cls.source = MockSource(cls.web)
        cls.bridge.camera_frame(cls.source.capture())
        cls.server = make_server(cls.bridge, cls.web, 0, cls.source)
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        request = Request(self.url + path, data=data, headers=headers or {})
        return urlopen(request, timeout=2)

    def test_dashboard_state_frame_home_sorting_and_stop(self):
        with self.request('/') as response:
            self.assertIn(b'id="sortingButton"', response.read())
        with self.request('/api/frame.jpg') as response:
            self.assertEqual(response.headers['Content-Type'], 'image/svg+xml')
        with self.request('/api/home', {}) as response:
            self.assertTrue(json.load(response)['accepted'])
        with self.request('/api/sorting', {'enabled': True}) as response:
            self.assertTrue(json.load(response)['state']['sorting_enabled'])
        with self.request('/api/stop', {}) as response:
            self.assertEqual(json.load(response)['state']['arm_state'], 'ERROR')
        with self.request('/api/state') as response:
            self.assertTrue(json.load(response)['simulation'])
        with self.assertRaises(HTTPError):
            self.request('/../bridge/core.py')

    def test_cross_origin_and_unexpected_host_rejected(self):
        for headers in ({'Origin': 'https://other.example'}, {'Host': 'other.example'}):
            with self.assertRaises(HTTPError) as error:
                self.request('/api/home', {}, headers)
            self.assertEqual(error.exception.code, 403)

    def test_mock_scenario_clears_pending_frame(self):
        with self.request('/api/mock', {'scenario': 'paper'}) as response:
            self.assertTrue(json.load(response)['accepted'])
        with self.assertRaises(HTTPError) as error:
            self.request('/api/frame.jpg')
        self.assertEqual(error.exception.code, 503)
        self.bridge.camera_frame(self.source.capture())


class CameraHttpTests(unittest.TestCase):
    def setUp(self):
        self.web = Path(__file__).resolve().parents[2] / 'web'
        self.arm = FakeArm()
        self.bridge = Bridge(self.arm, simulation=True, mock_camera=True, mock_available=True)
        self.camera = CameraSourceManager(self.bridge, self.web, allow_mock=True)
        self.bridge.camera_frame(self.camera.source.capture())
        self.server = make_server(self.bridge, self.web, 0, camera_manager=self.camera)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.camera.close()

    def request(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        return urlopen(Request(self.url + path, data=data), timeout=3)

    def test_switch_to_esp32_preview_and_back_to_mock(self):
        self.bridge.home()
        self.bridge.set_sorting(True)
        ticket = self.bridge.begin_prediction()
        with self.request('/api/camera', {'source': 'esp32', 'url': 'http://127.0.0.1:9/capture'}) as response:
            state = json.load(response)['state']
        self.assertEqual(state['camera_source'], 'esp32')
        self.assertFalse(state['vision_ready'])
        self.assertFalse(state['mock_camera'])
        self.assertFalse(state['sorting_enabled'])
        self.assertTrue(state['mock_available'])
        self.assertFalse(self.bridge.accept_prediction(ticket, prediction()))
        with self.assertRaises(HTTPError) as error:
            self.request('/api/frame.jpg')
        self.assertEqual(error.exception.code, 503)
        self.assertFalse(self.camera.capture_once())
        self.assertIn('ESP32 camera unreachable', self.bridge.snapshot()['camera_error'])
        with self.request('/api/camera', {'source': 'mock'}) as response:
            state = json.load(response)['state']
        self.assertTrue(state['vision_ready'])
        self.assertTrue(state['mock_camera'])
        self.assertIsNone(state['camera_error'])
        self.assertTrue(self.camera.capture_once())
        with self.request('/api/frame.jpg') as response:
            self.assertEqual(response.headers['Content-Type'], 'image/svg+xml')

    def test_rejects_public_ip_without_changing_source(self):
        with self.assertRaises(HTTPError) as error:
            self.request('/api/camera', {'source': 'esp32', 'url': 'http://8.8.8.8/capture'})
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.bridge.snapshot()['camera_source'], 'mock')

    def test_applied_switch_reports_success_even_if_previous_camera_close_fails(self):
        class FailingCloseSource:
            def close(self):
                raise OSError('device close failed')

        self.camera.source = FailingCloseSource()
        with self.assertLogs(level='WARNING'):
            with self.request('/api/camera', {'source': 'esp32', 'url': 'http://127.0.0.1:9/capture'}) as response:
                result = json.load(response)
        self.assertTrue(result['accepted'])
        self.assertEqual(result['state']['camera_source'], 'esp32')


class CameraSwitchRaceTests(unittest.TestCase):
    def test_switch_request_blocks_sort_while_old_capture_is_in_flight(self):
        web = Path(__file__).resolve().parents[2] / 'web'
        arm = FakeArm()
        bridge = Bridge(arm, simulation=True, mock_camera=True)
        manager = CameraSourceManager(bridge, web, allow_mock=True)
        started = threading.Event()
        release = threading.Event()
        paused = threading.Event()

        bridge.camera_frame((b'old frame', 'image/jpeg'))
        bridge.home()
        bridge.set_sorting(True)
        ticket = bridge.begin_prediction()
        original_prepare = bridge.prepare_camera_switch

        def record_pause():
            original_prepare()
            paused.set()

        bridge.prepare_camera_switch = record_pause

        class BlockingSource:
            def capture(self):
                started.set()
                release.wait(2)
                return b'old source frame', 'image/jpeg'

        manager.source = BlockingSource()
        capture = threading.Thread(target=manager.capture_once)
        capture.start()
        self.assertTrue(started.wait(1))
        switch = threading.Thread(target=lambda: manager.select('esp32', url='http://127.0.0.1:9/capture'))
        switch.start()
        self.assertTrue(paused.wait(1))
        self.assertFalse(bridge.enabled)
        with self.assertRaisesRegex(ValueError, 'Camera source is changing'):
            bridge.set_sorting(True)
        self.assertFalse(bridge.accept_prediction(ticket, prediction()))
        self.assertEqual(arm.commands, [])
        release.set()
        capture.join(2)
        switch.join(2)
        self.assertFalse(capture.is_alive())
        self.assertFalse(switch.is_alive())
        self.assertIsNone(bridge.frame)
        self.assertEqual(bridge.snapshot()['camera_source'], 'esp32')

    def test_overlapping_switch_requests_keep_sorting_blocked_until_both_finish(self):
        web = Path(__file__).resolve().parents[2] / 'web'
        arm = FakeArm()
        bridge = Bridge(arm, simulation=True, mock_camera=True)
        manager = CameraSourceManager(bridge, web, allow_mock=True)
        capture_started = threading.Event()
        release_capture = threading.Event()
        both_prepared = threading.Event()
        first_finished = threading.Event()
        release_first = threading.Event()
        failures = []

        class BlockingSource:
            def capture(self):
                capture_started.set()
                release_capture.wait(2)
                return b'old frame', 'image/jpeg'

        manager.source = BlockingSource()
        bridge.camera_frame((b'old frame', 'image/jpeg'))
        arm.home()
        bridge.set_sorting(True)
        original_prepare = bridge.prepare_camera_switch
        original_set = bridge.set_camera_source
        calls = 0

        def record_prepare():
            original_prepare()
            if bridge.camera_switches_pending == 2:
                both_prepared.set()

        def hold_first_set(*args, **kwargs):
            nonlocal calls
            calls += 1
            original_set(*args, **kwargs)
            if calls == 1:
                first_finished.set()
                release_first.wait(2)

        bridge.prepare_camera_switch = record_prepare
        bridge.set_camera_source = hold_first_set

        def select(*args, **kwargs):
            try:
                manager.select(*args, **kwargs)
            except Exception as exc:
                failures.append(exc)

        capture = threading.Thread(target=manager.capture_once)
        capture.start()
        self.assertTrue(capture_started.wait(1))
        first = threading.Thread(target=lambda: select('usb', index=0))
        second = threading.Thread(target=lambda: select('esp32', url='http://127.0.0.1:9/capture'))
        first.start()
        second.start()
        self.assertTrue(both_prepared.wait(1))
        release_capture.set()
        self.assertTrue(first_finished.wait(2))
        self.assertTrue(bridge.camera_switching)
        with self.assertRaisesRegex(ValueError, 'Camera source is changing'):
            bridge.set_sorting(True)
        self.assertEqual(arm.commands, [])
        release_first.set()
        for thread in (capture, first, second):
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertFalse(failures)
        self.assertEqual(bridge.camera_switches_pending, 0)
        self.assertFalse(bridge.camera_switching)
        self.assertFalse(bridge.enabled)


if __name__ == '__main__':
    unittest.main()
