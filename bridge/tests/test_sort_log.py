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
        self.arm.emit('DONE', 'SORT RECYCLE')  # repeated DONE must not log or count twice
        self.assertEqual(len(self.log.events), 1)
        event = self.log.events[0]
        self.assertEqual((event['outcome'], event['label'], event['bin']), ('DONE', 'Plastic bottle', 'RECYCLE'))
        self.assertEqual(event['confidence'], .93)
        self.assertAlmostEqual(event['cycle_s'], 4.5)
        self.assertTrue(event['simulated'])
        self.assertEqual(event['sort_id'], self.bridge.recent_sorts[0]['id'])
        self.assertEqual(self.bridge.counts['RECYCLE'], 1)

    def test_null_confidence_is_kept(self):
        self.sort(prediction(confidence=None))
        self.arm.finish()
        self.assertIsNone(self.log.events[0]['confidence'])

    def test_stop_logs_stopped_and_does_not_count(self):
        self.assertTrue(self.sort())
        self.bridge.stop()
        self.assertEqual([e['outcome'] for e in self.log.events], ['STOPPED'])
        self.assertEqual(self.bridge.counts['RECYCLE'], 0)
        self.assertEqual(self.bridge.recent_sorts, [])

    def test_fault_logs_fault_with_detail_and_does_not_count(self):
        self.assertTrue(self.sort())
        self.arm.emit('ERROR', 'shoulder_lift did not reach its goal')
        self.arm.emit('ERROR', 'shoulder_lift did not reach its goal')  # one terminal event per sort
        self.assertEqual(len(self.log.events), 1)
        self.assertEqual(self.log.events[0]['outcome'], 'FAULT')
        self.assertIn('did not reach', self.log.events[0]['detail'])
        self.assertEqual(self.bridge.counts['RECYCLE'], 0)

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
        self.assertEqual(self.bridge.counts['RECYCLE'], 1)
        self.assertTrue(self.sort(prediction(label='Metal can', bin='TRASH')))
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
    return dict(time=f't{n}', sort_id=str(n), label='Paper sheet', bin='RECYCLE', confidence=None,
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
        for label, bin_name in (('Paper sheet', 'RECYCLE'), ('Metal can', 'TRASH')):
            bridge.camera_frame((label.encode(), 'image/jpeg'))
            self.assertTrue(bridge.accept_prediction(bridge.begin_prediction(),
                                                     prediction(label=label, bin=bin_name)))
            arm.finish()
        self.assertLess(time.monotonic() - start, 0.5)  # outage never slowed sorting
        self.assertEqual(bridge.counts['RECYCLE'] + bridge.counts['TRASH'], 2)
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


class ReadCursor:
    """Answers the three history queries with canned rows; can fail the aggregate query."""
    def __init__(self, conn):
        self.conn, self.rows = conn, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.queries.append((sql, params))
        if 'sort_events_per_minute' in sql and self.conn.no_aggregate:
            raise RuntimeError('relation "sort_events_per_minute" does not exist')
        if 'GROUP BY outcome' in sql:
            self.rows = self.conn.totals
        elif 'simulated <>' in sql:
            self.rows = [(self.conn.other,)]
        elif 'LIMIT' in sql:
            self.rows = self.conn.recent
        else:
            self.rows = self.conn.window

    def fetchall(self):
        return self.rows


class ReadConn:
    def __init__(self, no_aggregate=False):
        from datetime import datetime, timezone
        t = datetime(2026, 11, 8, 1, 30, tzinfo=timezone.utc)
        self.queries, self.closed, self.no_aggregate = [], False, no_aggregate
        self.window = [(t, 'RECYCLE', 'DONE', 2, 30.0, 2), (t, 'RECYCLE', 'STOPPED', 1, None, 0),
                       (t, 'TRASH', 'FAULT', 1, None, 0)]
        self.totals = [('DONE', 5), ('STOPPED', 1), ('FAULT', 1)]
        self.recent = [(t, 'Paper sheet', 'RECYCLE', 'DONE', 0.9, 15.0)]
        self.other = 3

    def cursor(self):
        return ReadCursor(self)

    def close(self):
        self.closed = True


class HistoryTests(unittest.TestCase):
    def test_history_summarizes_aggregate_rows_and_caches(self):
        conns = []
        def connect():
            conns.append(ReadConn())
            return conns[-1]
        now = [0.0]
        log = SortLog(connect=FakeConn, read_connect=connect, clock=lambda: now[0], cache_s=3)
        data = log.history(60, simulated=True)
        self.assertEqual(data['source'], 'continuous_aggregate')
        self.assertTrue(data['ok'] and data['simulated'])
        self.assertEqual((data['mode'], data['other_mode_rows']), ('simulated', 3))
        # Every query is filtered to the requested mode.
        for sql, params in conns[0].queries:
            self.assertIn('simulated', sql)
            self.assertIn(True, params)
        self.assertEqual(data['totals'], {'DONE': 5, 'STOPPED': 1, 'FAULT': 1})
        paper = next(b for b in data['per_bin'] if b['bin'] == 'RECYCLE')
        self.assertEqual((paper['done'], paper['stopped'], paper['avg_cycle_s']), (2, 1, 15.0))
        self.assertEqual(data['per_minute'][0]['done'], 2)
        self.assertEqual(data['per_minute'][0]['fault'], 1)
        self.assertEqual(data['recent'][0]['label'], 'Paper sheet')
        self.assertTrue(data['recent'][0]['time'].startswith('2026-11-08T01:30'))
        self.assertTrue(conns[0].closed)
        log.history(60, simulated=True)
        self.assertEqual(len(conns), 1)  # served from cache
        log.history(60, simulated=False)
        self.assertEqual(len(conns), 2)  # other mode is a separate query, never the cached one
        now[0] = 5.0
        log.history(60, simulated=True)
        self.assertEqual(len(conns), 3)

    def test_physical_mode_is_labelled_physical(self):
        conn = ReadConn()
        data = SortLog(connect=FakeConn, read_connect=lambda: conn).history(60, simulated=False)
        self.assertEqual((data['mode'], data['simulated'], data['recent'][0]['simulated']), ('physical', False, False))
        self.assertTrue(all(False in params for sql, params in conn.queries))

    def test_history_falls_back_to_raw_table(self):
        log = SortLog(connect=FakeConn, read_connect=lambda: ReadConn(no_aggregate=True))
        self.assertEqual(log.history(60, simulated=True)['source'], 'table')

    def test_history_clamps_window(self):
        conn = ReadConn()
        log = SortLog(connect=FakeConn, read_connect=lambda: conn)
        self.assertEqual(log.history(99999, simulated=True)['window_minutes'], 1440)


class DiagnosticsTests(unittest.TestCase):
    SECRET = 'postgres://tsdbadmin:hunter2@abc.tsdb.cloud.timescale.com:35624/tsdb'

    def test_describe_error_never_includes_the_message(self):
        from bridge.sort_log import describe_error

        class OperationalError(Exception):
            sqlstate = '08001'
        text = describe_error(OperationalError(f'connection failed: {self.SECRET}'))
        self.assertEqual(text, 'OperationalError, SQLSTATE 08001')
        self.assertEqual(describe_error(ValueError(self.SECRET)), 'ValueError')

    def test_writer_and_setup_logs_stay_generic(self):
        messages = []
        secret = self.SECRET

        class SetupConn(FakeConn):
            def cursor(self):
                cur = FakeCursor(self)
                def execute(sql, *args):
                    if 'create_hypertable' in sql:
                        raise RuntimeError(f'extension missing on {secret}')
                    self.executed.append(sql)
                cur.execute = execute
                return cur
        attempts = []

        def connect():
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError(f'could not connect: {secret}')
            return SetupConn()
        log = SortLog(connect=connect, log=messages.append)
        log._stop.wait = lambda delay: threading.Event().wait(0.01)
        log.start()
        log.emit(event())
        self.assertTrue(wait_until(lambda: log.written == 1))
        log.close()
        self.assertTrue(messages)
        for message in messages:
            self.assertNotIn('hunter2', message)
            self.assertNotIn('tsdb.cloud', message)
        self.assertNotIn('hunter2', str(log.last_error))


class HistoryEndpointTests(unittest.TestCase):
    def serve(self, sort_log, simulation=True):
        from bridge.server import make_server
        from pathlib import Path
        bridge = Bridge(FakeArm(), simulation=simulation, sort_log=sort_log)
        server = make_server(bridge, Path(__file__).resolve().parents[2] / 'web', 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_port}'

    def get(self, base, path):
        import json
        from urllib.request import urlopen
        with urlopen(base + path, timeout=5) as response:
            return response.status, json.loads(response.read())

    def test_disabled_without_sort_log(self):
        self.assertEqual(self.get(self.serve(None), '/api/sort-history'), (200, {'enabled': False}))

    class Log:
        def emit(self, event):
            return True

        def history(self, minutes, *, simulated):
            self.minutes, self.simulated = minutes, simulated
            return {'enabled': True, 'ok': True, 'window_minutes': minutes}

    def test_returns_history_and_passes_window(self):
        log = self.Log()
        status, data = self.get(self.serve(log), '/api/sort-history?minutes=30')
        self.assertEqual((status, data['ok'], log.minutes), (200, True, 30))

    def test_mode_defaults_to_the_bridge_mode(self):
        mock_log, physical_log = self.Log(), self.Log()
        self.get(self.serve(mock_log, simulation=True), '/api/sort-history')
        self.get(self.serve(physical_log, simulation=False), '/api/sort-history')
        self.assertEqual((mock_log.simulated, physical_log.simulated), (True, False))

    def test_explicit_mode_and_bad_mode(self):
        from urllib.error import HTTPError
        log = self.Log()
        base = self.serve(log, simulation=True)
        self.get(base, '/api/sort-history?mode=physical')
        self.assertFalse(log.simulated)
        with self.assertRaises(HTTPError) as caught:
            self.get(base, '/api/sort-history?mode=both')
        self.assertEqual(caught.exception.code, 400)

    def test_database_error_is_generic(self):
        class Log:
            def emit(self, event):
                return True
            def history(self, minutes, *, simulated):
                raise OSError('could not connect to tsdbadmin@secret-host.example:5432')
        status, data = self.get(self.serve(Log()), '/api/sort-history')
        self.assertEqual((status, data['ok']), (200, False))
        self.assertNotIn('secret-host', json_dumps(data))


def json_dumps(data):
    import json
    return json.dumps(data)


if __name__ == '__main__':
    unittest.main()
