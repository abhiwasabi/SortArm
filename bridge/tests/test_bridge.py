import json
from pathlib import Path
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bridge.adapters import MockSource
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
        self.bridge.camera_frame((b'new', 'image/jpeg'))
        self.bridge.set_sorting(True)
        ticket = self.bridge.begin_prediction()
        self.bridge.set_sorting(False)
        self.assertFalse(self.bridge.accept_prediction(ticket, prediction()))

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


if __name__ == '__main__':
    unittest.main()
