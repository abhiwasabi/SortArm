"""Tiger Data sort log (#47): bridge hook behavior and the non-blocking writer."""
import sys
import threading
import time
import unittest

from bridge.core import Bridge
from bridge.sort_log import SortLog
from bridge.tests.test_bridge import FakeArm, prediction


class RecordingLog:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)
        return True


class BridgeHookTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.arm = FakeArm()
        self.log = RecordingLog()
        self.bridge = Bridge(self.arm, simulation=True, clock=lambda: self.now, sort_log=self.log)
        self.bridge.camera_frame((b'frame', 'image/jpeg'))
        self.bridge.home()

    def sort(self, raw=None):
        self.now += .1
        self.bridge.camera_frame((b'frame', 'image/jpeg'))
        return self.bridge.accept_prediction(self.bridge.begin_prediction(), raw or prediction())

    def test_home_and_idle_errors_log_nothing(self):
        self.arm.emit('ERROR', 'arm check failed: no answer')
        self.bridge.home()
        self.assertEqual(self.log.events, [])

    def test_done_is_logged_once_with_sort_details(self):
        self.assertTrue(self.sort(prediction(confidence=.93)))
        self.now += 4.5
        self.arm.finish()
        self.arm.emit('DONE', 'SORT PLASTIC')  # repeated DONE must not log or count twice
        self.assertEqual(len(self.log.events), 1)
        event = self.log.events[0]
        self.assertEqual((event['outcome'], event['label'], event['bin']), ('DONE', 'Plastic bottle', 'PLASTIC'))
        self.assertEqual(event['confidence'], .93)
        self.assertAlmostEqual(event['cycle_s'], 4.5)
        self.assertTrue(event['simulated'])
        self.assertEqual(event['sort_id'], self.bridge.recent_sorts[0]['id'])
        self.assertEqual(self.bridge.counts['PLASTIC'], 1)

    def test_null_confidence_is_kept(self):
        self.sort(prediction(confidence=None))
        self.arm.finish()
        self.assertIsNone(self.log.events[0]['confidence'])

    def test_stop_logs_stopped_and_does_not_count(self):
        self.assertTrue(self.sort())
        self.bridge.stop()
        self.assertEqual([e['outcome'] for e in self.log.events], ['STOPPED'])
        self.assertEqual(self.bridge.counts['PLASTIC'], 0)
        self.assertEqual(self.bridge.recent_sorts, [])

    def test_fault_logs_fault_with_detail_and_does_not_count(self):
        self.assertTrue(self.sort())
        self.arm.emit('ERROR', 'shoulder_lift did not reach its goal')
        self.arm.emit('ERROR', 'shoulder_lift did not reach its goal')  # one terminal event per sort
        self.assertEqual(len(self.log.events), 1)
        self.assertEqual(self.log.events[0]['outcome'], 'FAULT')
        self.assertIn('did not reach', self.log.events[0]['detail'])
        self.assertEqual(self.bridge.counts['PLASTIC'], 0)

    def test_history_rows_keep_their_existing_fields(self):
        self.sort()
        self.arm.finish()
        self.assertEqual(set(self.bridge.snapshot()['recent_sorts'][0]), {'id', 'label', 'bin', 'completed_at'})

    def test_failing_log_never_affects_sorting(self):
        class Broken:
            def emit(self, event):
                raise RuntimeError('boom')
        self.bridge.sort_log = Broken()
        self.assertTrue(self.sort())
        self.arm.finish()
        self.assertEqual(self.bridge.counts['PLASTIC'], 1)
        self.assertTrue(self.sort(prediction(label='Metal can', bin='METAL')))
        self.bridge.stop()
        self.assertEqual(self.arm.state, 'ERROR')


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, *args):
        self.conn.executed.append(sql)

    def executemany(self, sql, rows):
        self.conn.rows.extend(rows)


class FakeConn:
    def __init__(self):
        self.executed, self.rows, self.closed = [], [], False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


def wait_until(check, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.01)
    return False


def event(n=0):
    return dict(time=f't{n}', sort_id=str(n), label='Paper sheet', bin='PAPER', confidence=None,
                outcome='DONE', cycle_s=1.0, simulated=True, detail=None)


class SortLogTests(unittest.TestCase):
    def test_unset_url_means_no_writer_thread_and_no_import(self):
        imported_before = 'psycopg' in sys.modules
        for env in ({}, {'TIGER_DATABASE_URL': ''}, {'TIGER_DATABASE_URL': '   '}):
            self.assertIsNone(SortLog.from_env(env))
        self.assertFalse(any(t.name == 'sort-log' for t in threading.enumerate()))
        if not imported_before:
            self.assertNotIn('psycopg', sys.modules)

    def test_full_queue_drops_without_blocking(self):
        log = SortLog(connect=FakeConn, maxsize=2)  # writer never started, so nothing drains
        start = time.monotonic()
        results = [log.emit(event(n)) for n in range(5)]
        self.assertLess(time.monotonic() - start, 0.05)
        self.assertEqual(results, [True, True, False, False, False])
        self.assertEqual(log.dropped, 3)

    def test_writes_schema_then_rows(self):
        conn = FakeConn()
        log = SortLog(connect=lambda: conn, log=lambda msg: None)
        log.start()
        log.emit(event(1))
        log.emit(event(2))
        self.assertTrue(wait_until(lambda: len(conn.rows) == 2))
        log.close()
        self.assertTrue(any('CREATE TABLE IF NOT EXISTS sort_events' in sql for sql in conn.executed))
        self.assertTrue(any('create_hypertable' in sql for sql in conn.executed))
        self.assertEqual([row['sort_id'] for row in conn.rows], ['1', '2'])
        self.assertEqual(log.written, 2)
        self.assertTrue(conn.closed)

    def test_database_outage_keeps_bridge_sorting_and_recovers(self):
        conn, attempts = FakeConn(), []

        def connect():
            attempts.append(1)
            if len(attempts) < 3:
                raise OSError('connection refused')
            return conn
        log = SortLog(connect=connect, retry_max_s=0.05, log=lambda msg: None)
        log._stop.wait = lambda delay: threading.Event().wait(min(delay, 0.02))  # fast backoff for the test
        log.start()
        arm = FakeArm()
        bridge = Bridge(arm, simulation=True, clock=time.monotonic, sort_log=log)
        bridge.camera_frame((b'frame', 'image/jpeg'))
        bridge.home()
        start = time.monotonic()
        for label, bin_name in (('Paper sheet', 'PAPER'), ('Metal can', 'METAL')):
            bridge.camera_frame((label.encode(), 'image/jpeg'))
            self.assertTrue(bridge.accept_prediction(bridge.begin_prediction(),
                                                     prediction(label=label, bin=bin_name)))
            arm.finish()
        self.assertLess(time.monotonic() - start, 0.5)  # outage never slowed sorting
        self.assertEqual(bridge.counts['PAPER'] + bridge.counts['METAL'], 2)
        self.assertTrue(wait_until(lambda: len(conn.rows) == 2))
        self.assertIsNotNone(log.last_error)
        log.close()

    def test_close_returns_promptly_during_outage(self):
        def connect():
            raise OSError('down')
        log = SortLog(connect=connect, log=lambda msg: None)
        log.start()
        log.emit(event())
        time.sleep(0.05)
        start = time.monotonic()
        log.close(timeout=2)
        self.assertLess(time.monotonic() - start, 1.0)


if __name__ == '__main__':
    unittest.main()
