from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bridge.adapters import MockSource
from bridge.__main__ import main as bridge_main
from bridge.camera_sources import CameraSourceManager
from bridge.core import Bridge, validate_prediction
from bridge.server import make_server


class FakeArm:
    def __init__(self):
        self.state = 'ERROR'
        self.detail = 'not homed'
        self.connected = True
        self._local = threading.local()
        self.commands = []
        self.listeners = []

    def on_state(self, listener):
        self.listeners.append(listener)

    @property
    def last_rejection(self):
        return getattr(self._local, 'rejection', None)

    @last_rejection.setter
    def last_rejection(self, reason):
        self._local.rejection = reason

    def emit(self, state, detail=None):
        self.state, self.detail = state, detail
        for listener in self.listeners:
            listener(state, detail)

    def home(self):
        if self.state == 'BUSY':
            self._local.rejection = 'Arm is busy; wait before HOME.'
            return False
        self._local.rejection = None
        self.emit('DONE', 'HOME')
        self.emit('READY')
        return True

    def sort(self, bin_name):
        if self.state != 'READY':
            self._local.rejection = 'Arm is not READY; home it before sorting.'
            return False
        self._local.rejection = None
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

    def result(self, raw=None):
        self.now += .1
        self.bridge.camera_frame((b'frame', 'image/jpeg'))
        return self.bridge.accept_prediction(self.bridge.begin_prediction(), raw or prediction())

    def test_ready_simulated_arm_does_not_sort_before_home(self):
        arm = FakeArm()
        arm.state = 'READY'
        bridge = Bridge(arm, simulation=True, clock=lambda: self.now)
        bridge.camera_frame((b'frame', 'image/jpeg'))
        self.assertFalse(bridge.enabled)
        self.assertIsNone(bridge.begin_prediction())
        self.assertTrue(bridge.home())
        self.assertTrue(bridge.enabled)

    def test_rejected_home_leaves_automatic_sorting_paused(self):
        self.arm.emit('BUSY', 'SORT PLASTIC')
        self.arm.last_rejection = 'Arm is busy'
        self.assertFalse(self.bridge.home())
        self.assertFalse(self.bridge.enabled)
        self.assertFalse(self.bridge.auto_sort_armed)
        self.assertFalse(self.bridge.home_confirmed)
        self.assertEqual(self.bridge.status, self.arm.last_rejection)

    def test_one_sort_per_item_and_only_done_increments(self):
        self.assertTrue(self.bridge.enabled)
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

    def test_new_item_updates_during_motion_and_sorts_without_empty_area(self):
        self.assertTrue(self.result())
        metal = prediction(label='Metal can', bin='METAL')
        self.assertFalse(self.result(metal))
        self.assertEqual(self.bridge.snapshot()['label'], 'Metal can')
        self.assertFalse(self.bridge.snapshot()['awaiting_clear'])
        self.assertEqual(self.arm.commands, ['PLASTIC'])
        self.arm.finish()
        self.assertTrue(self.result(metal))
        self.arm.finish()
        self.assertEqual(self.arm.commands, ['PLASTIC', 'METAL'])
        self.assertEqual(self.bridge.counts['METAL'], 1)
        self.assertFalse(self.result(metal))

    def test_physical_mode_still_requires_explicit_start_and_clear(self):
        arm = FakeArm()
        bridge = Bridge(arm, simulation=False, clock=lambda: self.now)
        bridge.camera_frame((b'frame', 'image/jpeg'))
        bridge.home()
        self.assertFalse(bridge.enabled)
        bridge.set_sorting(True)
        ticket = bridge.begin_prediction()
        self.assertTrue(bridge.accept_prediction(ticket, prediction()))
        arm.finish()
        self.now += .1
        bridge.camera_frame((b'next', 'image/jpeg'))
        ticket = bridge.begin_prediction()
        self.assertFalse(bridge.accept_prediction(ticket, prediction(label='Metal can', bin='METAL')))
        self.assertEqual(arm.commands, ['PLASTIC'])

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

    def test_manual_sort_needs_no_camera_or_vision_and_counts_only_once_on_done(self):
        self.bridge.set_sorting(False)
        self.bridge.camera_lost()
        self.bridge.vision_ready = False
        self.assertTrue(self.bridge.manual_sort('PAPER'))
        pending = dict(self.bridge.pending_sort)
        self.assertTrue(pending['id'])
        self.assertEqual(pending['label'], 'Manual')
        self.assertTrue(self.bridge.awaiting_clear)
        self.assertEqual(self.bridge.status, 'Manual sort to PAPER requested.')
        self.assertEqual(self.bridge.counts['PAPER'], 0)
        self.assertEqual(self.bridge.recent_sorts, [])
        self.arm.emit('DONE', 'SORT METAL')
        self.assertEqual(self.bridge.counts['PAPER'], 0)
        self.arm.finish()
        self.arm.emit('DONE', 'SORT PAPER')
        state = self.bridge.snapshot()
        self.assertEqual(state['counts']['PAPER'], 1)
        self.assertEqual(len(state['recent_sorts']), 1)
        self.assertEqual(state['recent_sorts'][0]['id'], pending['id'])
        self.assertEqual(state['recent_sorts'][0]['label'], 'Manual')
        self.assertEqual(state['recent_sorts'][0]['bin'], 'PAPER')
        self.assertTrue(state['recent_sorts'][0]['completed_at'])
        self.assertIsNone(self.bridge.pending_sort)

    def test_rejected_manual_sort_keeps_the_running_sort_countable(self):
        self.bridge.set_sorting(False)
        self.assertTrue(self.bridge.manual_sort('PAPER'))
        running = dict(self.bridge.pending_sort)
        self.assertFalse(self.bridge.manual_sort('METAL'))  # arm is BUSY with PAPER
        self.assertEqual(self.bridge.pending_sort, running)
        self.arm.finish()
        state = self.bridge.snapshot()
        self.assertEqual(state['counts']['PAPER'], 1)
        self.assertEqual(state['counts']['METAL'], 0)
        self.assertEqual([row['id'] for row in state['recent_sorts']], [running['id']])

    def test_manual_sort_done_emits_one_log_row_with_cycle_time(self):
        sort_log = Mock()
        self.bridge.sort_log = sort_log
        self.bridge.set_sorting(False)
        self.assertTrue(self.bridge.manual_sort('PAPER'))
        sort_log.emit.assert_not_called()
        self.now += 2.5
        self.arm.finish()
        self.arm.emit('DONE', 'SORT PAPER')
        sort_log.emit.assert_called_once()
        event = sort_log.emit.call_args.args[0]
        self.assertEqual(event['label'], 'Manual')
        self.assertEqual(event['bin'], 'PAPER')
        self.assertEqual(event['outcome'], 'DONE')
        self.assertEqual(event['cycle_s'], 2.5)
        self.assertIsNone(event['confidence'])
        self.assertEqual(self.bridge.counts['PAPER'], 1)

    def test_stop_during_manual_sort_emits_stopped_without_incrementing_counts(self):
        sort_log = Mock()
        self.bridge.sort_log = sort_log
        self.bridge.set_sorting(False)
        before = dict(self.bridge.counts)
        self.assertTrue(self.bridge.manual_sort('PAPER'))
        self.now += 1.5
        self.assertTrue(self.bridge.stop())
        self.arm.finish()  # a delayed DONE cannot count or log the interrupted sort
        sort_log.emit.assert_called_once()
        event = sort_log.emit.call_args.args[0]
        self.assertEqual(event['label'], 'Manual')
        self.assertEqual(event['bin'], 'PAPER')
        self.assertEqual(event['outcome'], 'STOPPED')
        self.assertEqual(event['cycle_s'], 1.5)
        self.assertIsNone(event['confidence'])
        self.assertEqual(self.bridge.counts, before)
        self.assertEqual(self.bridge.recent_sorts, [])

    def test_manual_sort_waits_until_the_finished_sort_is_counted(self):
        # The real arm reports READY just before its DONE notification reaches the bridge.
        self.bridge.set_sorting(False)
        self.assertTrue(self.bridge.manual_sort('PAPER'))
        finished = dict(self.bridge.pending_sort)
        self.arm.state = 'READY'  # DONE for PAPER is still queued on the arm's notifier
        for second_bin in ('PAPER', 'METAL'):
            with self.subTest(second_bin=second_bin):
                self.assertFalse(self.bridge.manual_sort(second_bin))
                self.assertEqual(self.bridge.rejection_reason(),
                                 'The previous sort is still finishing; try again in a moment.')
                self.assertEqual(self.bridge.pending_sort, finished)
        self.assertEqual(self.arm.commands, ['PAPER'])
        self.arm.emit('DONE', 'SORT PAPER')  # the queued notifications arrive
        self.arm.emit('READY')
        self.assertEqual(self.bridge.counts['PAPER'], 1)
        self.assertIsNone(self.bridge.pending_sort)
        self.assertTrue(self.bridge.manual_sort('METAL'))  # now accepted
        self.assertEqual(self.bridge.counts['METAL'], 0)

    def test_manual_sort_requires_automatic_sorting_to_be_paused(self):
        with self.assertRaisesRegex(ValueError, 'Pause automatic sorting before a manual sort.'):
            self.bridge.manual_sort('PAPER')
        self.assertTrue(self.bridge.enabled)
        self.assertIsNone(self.bridge.pending_sort)
        self.assertEqual(self.arm.commands, [])

    def test_item_sorted_by_hand_is_not_sorted_again_after_start(self):
        # Simulated continuous mode: Pause, manual sort, Start. Classification was paused during the
        # manual sort, so the item still in view must not be sorted (and counted) a second time.
        self.bridge.set_sorting(False)
        self.assertTrue(self.bridge.manual_sort('PAPER'))
        self.arm.finish()
        self.bridge.set_sorting(True)
        self.assertFalse(self.result())
        self.assertEqual(self.bridge.status, 'Already sorted this item. Show a different item.')
        self.assertFalse(self.result())
        self.assertEqual(self.arm.commands, ['PAPER'])
        self.assertEqual((self.bridge.counts['PAPER'], self.bridge.counts['PLASTIC']), (1, 0))
        self.assertTrue(self.result(prediction(label='Metal can', bin='METAL')))
        self.arm.finish()
        self.assertEqual(self.arm.commands, ['PAPER', 'METAL'])

    def test_empty_area_after_manual_sort_lets_the_next_item_sort(self):
        self.bridge.set_sorting(False)
        self.assertTrue(self.bridge.manual_sort('PAPER'))
        self.arm.finish()
        self.bridge.set_sorting(True)
        empty = prediction(label='EMPTY', bin=None, item_present=False)
        self.result(empty)
        self.result(empty)
        self.assertTrue(self.result())
        self.assertEqual(self.arm.commands, ['PAPER', 'PLASTIC'])

    def test_manual_sort_rejection_keeps_arm_state_and_reason(self):
        self.bridge.set_sorting(False)
        for state in ('ERROR', 'BUSY', 'DONE'):
            with self.subTest(state=state):
                self.arm.emit(state, 'original detail')
                self.assertFalse(self.bridge.manual_sort('METAL'))
                self.assertEqual(self.arm.state, state)
                self.assertEqual(self.arm.detail, 'original detail')
                self.assertEqual(self.bridge.status, self.arm.last_rejection)
                self.assertIsNone(self.bridge.pending_sort)
        self.assertEqual(self.arm.commands, [])
        self.assertEqual(sum(self.bridge.counts.values()), 0)
        self.assertEqual(self.bridge.recent_sorts, [])

    def test_manual_sort_rejects_unknown_non_string_and_unavailable_bins(self):
        self.bridge.set_sorting(False)
        self.bridge.allowed_bins = ('PAPER', 'PLASTIC')
        for bin_name in ('COMPOST', 'paper', 'METAL', None, 1, [], {}):
            with self.subTest(bin_name=bin_name), self.assertRaises(ValueError):
                self.bridge.manual_sort(bin_name)
        self.assertIsNone(self.bridge.pending_sort)
        self.assertEqual(self.arm.commands, [])
        self.assertEqual(self.bridge.snapshot()['bins'], ['PAPER', 'PLASTIC'])

    def test_home_status_reports_acceptance_or_rejection(self):
        self.assertTrue(self.bridge.home())
        # Simulated arm: an accepted HOME re-arms automatic sorting (#34).
        self.assertEqual(self.bridge.status, 'Homing requested. Automatic sorting will start when ready.')
        self.assertTrue(self.bridge.enabled)
        self.arm.emit('BUSY')
        self.assertFalse(self.bridge.home())
        self.assertEqual(self.bridge.status, self.arm.last_rejection)
        self.assertEqual(self.arm.state, 'BUSY')

    def test_manual_sort_and_home_rejection_have_plain_fallbacks(self):
        self.bridge.set_sorting(False)
        self.arm.sort = lambda bin_name: False
        self.arm.home = lambda: False
        self.assertFalse(self.bridge.manual_sort('PAPER'))
        self.assertEqual(self.bridge.status, 'Arm rejected the manual sort.')
        self.assertIsNone(self.bridge.pending_sort)
        self.assertFalse(self.bridge.home())
        self.assertEqual(self.bridge.status, 'Arm rejected HOME.')

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
        self.assertEqual(self.bridge.snapshot()['status'], 'Camera feed restored. Automatic sorting ready.')
        self.assertTrue(self.bridge.enabled)
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

    def test_temporary_prediction_error_keeps_simulated_sorting_armed(self):
        ticket = self.bridge.begin_prediction()
        self.bridge.prediction_failed(ticket, 'Gemini temporarily unavailable', retryable=True)
        self.assertTrue(self.bridge.enabled)
        self.assertIn('Retrying automatically', self.bridge.status)
        self.assertFalse(self.bridge.accept_prediction(ticket, prediction()))
        self.assertIsNotNone(self.bridge.begin_prediction())

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
            state = json.load(response)
            self.assertTrue(state['simulation'])
            self.assertEqual(state['bins'], ['PAPER', 'PLASTIC', 'METAL', 'ORGANIC'])
        with self.assertRaises(HTTPError):
            self.request('/../bridge/core.py')

    def test_cross_origin_and_unexpected_host_rejected(self):
        for path in ('/api/home', '/api/sort'):
            for headers in ({'Origin': 'https://other.example'}, {'Host': 'other.example'}):
                with self.assertRaises(HTTPError) as error:
                    self.request(path, {'bin': 'PAPER'}, headers)
                self.assertEqual(error.exception.code, 403)

    def test_manual_sort_accepts_without_camera_or_vision_and_returns_rejection_reason(self):
        self.bridge.stop()
        self.bridge.home()
        self.bridge.set_sorting(False)  # simulated HOME re-arms automatic sorting
        self.bridge.vision_ready = False
        self.bridge.frame = self.bridge.frame_at = None
        try:
            with self.request('/api/sort', {'bin': 'ORGANIC'}) as response:
                self.assertEqual(response.status, 200)
                result = json.load(response)
            self.assertTrue(result['accepted'])
            self.assertEqual(result['state']['arm_state'], 'BUSY')
            self.assertEqual(result['state']['counts']['ORGANIC'], 0)
            with self.assertRaises(HTTPError) as error:
                self.request('/api/sort', {'bin': 'PAPER'})
            self.assertEqual(error.exception.code, 409)
            result = json.load(error.exception)
            self.assertFalse(result['accepted'])
            # Refused by the bridge before the arm is asked: the ORGANIC sort is still pending.
            self.assertEqual(result['error'], 'The previous sort is still finishing; try again in a moment.')
            self.assertEqual(result['state']['status'], result['error'])
            self.assertEqual(result['state']['arm_state'], 'BUSY')
            # The rejected PAPER request must not drop the ORGANIC sort that is still running.
            self.assertEqual(self.bridge.pending_sort['bin'], 'ORGANIC')
        finally:
            self.bridge.stop()
            self.bridge.vision_ready = True
            self.bridge.camera_frame(self.source.capture())

    def test_manual_sort_invalid_body_or_enabled_automatic_sorting_returns_400(self):
        self.bridge.stop()
        self.bridge.home()
        for body in ({}, {'bin': None}, {'bin': 3}, {'bin': []}, {'bin': 'UNKNOWN'}, []):
            with self.subTest(body=body), self.assertRaises(HTTPError) as error:
                self.request('/api/sort', body)
            self.assertEqual(error.exception.code, 400)
            self.assertEqual(set(json.load(error.exception)), {'error'})
        self.bridge.camera_frame(self.source.capture())
        self.bridge.set_sorting(True)
        try:
            with self.assertRaises(HTTPError) as error:
                self.request('/api/sort', {'bin': 'PAPER'})
            self.assertEqual(error.exception.code, 400)
            self.assertEqual(json.load(error.exception),
                             {'error': 'Pause automatic sorting before a manual sort.'})
        finally:
            self.bridge.set_sorting(False)

    def test_home_while_busy_returns_409_with_error(self):
        self.bridge.stop()
        self.bridge.home()
        self.bridge.set_sorting(False)  # simulated HOME re-arms automatic sorting
        self.assertTrue(self.bridge.manual_sort('METAL'))
        try:
            with self.assertRaises(HTTPError) as error:
                self.request('/api/home', {})
            self.assertEqual(error.exception.code, 409)
            result = json.load(error.exception)
            self.assertFalse(result['accepted'])
            self.assertEqual(result['error'], 'Arm is busy; wait before HOME.')
            self.assertEqual(result['state']['status'], result['error'])
            self.assertEqual(result['state']['arm_state'], 'BUSY')
        finally:
            self.bridge.stop()

    def test_mock_scenario_clears_pending_frame(self):
        with self.request('/api/mock', {'scenario': 'paper'}) as response:
            self.assertTrue(json.load(response)['accepted'])
        with self.assertRaises(HTTPError) as error:
            self.request('/api/frame.jpg')
        self.assertEqual(error.exception.code, 503)
        self.bridge.camera_frame(self.source.capture())


class StartupTests(unittest.TestCase):
    def test_arm_startup_errors_close_initial_camera_and_give_next_step(self):
        from arm import PosesError

        for failure, next_step in ((PosesError('Poses not taught yet'), 'python -m arm.teach'),
                                   (ValueError('Set ARM_PORT'), 'ARM_PORT'),
                                   (OSError('Could not open serial port'), 'ARM_PORT'),
                                   (RuntimeError('No calibration file'), 'lerobot-calibrate')):
            with self.subTest(failure=failure):
                stderr = io.StringIO()
                with patch('sys.argv', ['bridge']), patch.dict('os.environ', {}, clear=True), \
                        patch('bridge.__main__.GeminiPredictor'), \
                        patch('bridge.__main__.Camera') as camera, \
                        patch('arm.ArmController.from_config', side_effect=failure), \
                        redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
                    bridge_main()
                self.assertEqual(error.exception.code, 2)
                camera.return_value.close.assert_called_once()
                self.assertIn('Arm startup failed:', stderr.getvalue())
                self.assertIn(next_step, stderr.getvalue())
                self.assertEqual(len(stderr.getvalue().splitlines()), 1)

    def test_server_bind_failure_stops_and_closes_resources(self):
        stderr = io.StringIO()
        arm = Mock(state='ERROR', connected=True)
        with patch('sys.argv', ['bridge', '--mock']), patch.dict('os.environ', {}, clear=True), \
                patch('arm.ArmController.from_config', return_value=arm), \
                patch('bridge.__main__.CameraSourceManager') as camera, \
                patch('bridge.__main__.make_server', side_effect=OSError('Address already in use')), \
                redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
            bridge_main()
        self.assertEqual(error.exception.code, 2)
        arm.stop.assert_called_once()
        arm.close.assert_called_once()
        camera.return_value.close.assert_called_once()
        self.assertIn('4173', stderr.getvalue())
        self.assertIn('--port', stderr.getvalue())
        self.assertEqual(len(stderr.getvalue().splitlines()), 1)


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
        bridge.home()
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
