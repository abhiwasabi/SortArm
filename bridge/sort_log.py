"""Optional Tiger Data (Postgres/TimescaleDB) log of sort outcomes (issue #47).

Enabled only when TIGER_DATABASE_URL is set. The bridge calls emit(), which only puts the event on a
bounded queue and never blocks, raises, or touches the network. A daemon thread does all database work,
so a slow or unreachable database can never delay HOME, SORT, or STOP.
"""
import os
import queue
import threading
import time

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS sort_events (
        time        TIMESTAMPTZ NOT NULL,
        sort_id     TEXT NOT NULL,
        label       TEXT NOT NULL,
        bin         TEXT NOT NULL,
        confidence  REAL,
        outcome     TEXT NOT NULL,
        cycle_s     REAL,
        simulated   BOOLEAN NOT NULL,
        detail      TEXT
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS sort_events_sort_id_time ON sort_events (sort_id, time)",
)
# Tiger Cloud services include TimescaleDB; on plain Postgres this fails and the table stays ordinary.
HYPERTABLE = "SELECT create_hypertable('sort_events', 'time', if_not_exists => TRUE)"
INSERT = """INSERT INTO sort_events (time, sort_id, label, bin, confidence, outcome, cycle_s, simulated, detail)
            VALUES (%(time)s, %(sort_id)s, %(label)s, %(bin)s, %(confidence)s, %(outcome)s,
                    %(cycle_s)s, %(simulated)s, %(detail)s)
            ON CONFLICT (sort_id, time) DO NOTHING"""
FIELDS = ('time', 'sort_id', 'label', 'bin', 'confidence', 'outcome', 'cycle_s', 'simulated', 'detail')


class SortLog:
    def __init__(self, connect, *, maxsize=500, batch_size=50, retry_max_s=30.0, log=print):
        self._connect = connect
        self._queue = queue.Queue(maxsize=maxsize)
        self._batch_size = batch_size
        self._retry_max_s = retry_max_s
        self._log = log
        self._stop = threading.Event()
        self._thread = None
        self._conn = None
        self.dropped = 0
        self.written = 0
        self.last_error = None

    @classmethod
    def from_env(cls, env=None, **kwargs):
        """A started SortLog when TIGER_DATABASE_URL is set, else None (no import, no thread)."""
        env = os.environ if env is None else env
        url = (env.get('TIGER_DATABASE_URL') or '').strip()
        if not url:
            return None
        try:
            import psycopg
        except ImportError:
            print('TIGER_DATABASE_URL is set but psycopg is not installed; sort logging is off. '
                  'Install bridge/requirements.txt.', flush=True)
            return None
        log = cls(lambda: psycopg.connect(url, connect_timeout=5, autocommit=True), **kwargs)
        log.start()
        return log

    def emit(self, event):
        """Queue one event. Never blocks or raises; drops the event when the queue is full."""
        try:
            self._queue.put_nowait({key: event.get(key) for key in FIELDS})
            return True
        except queue.Full:
            self.dropped += 1
            return False
        except Exception:
            self.dropped += 1
            return False

    def start(self):
        self._thread = threading.Thread(target=self._run, name='sort-log', daemon=True)
        self._thread.start()

    def close(self, timeout=3.0):
        """Ask the writer to flush what it can and stop. Waits at most `timeout` seconds."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._disconnect()

    def _run(self):
        delay = 1.0
        batch = []
        while True:
            if not batch:
                batch = self._take(timeout=0.5)
            if not batch:
                if self._stop.is_set():
                    return
                continue
            try:
                self._write(batch)
                self.written += len(batch)
                batch = []
                delay = 1.0
            except Exception as exc:  # database down, bad URL, network: keep the bridge running
                self._disconnect()
                if str(exc) != str(self.last_error):
                    self._log(f'Sort log: database write failed ({exc.__class__.__name__}: {exc}); retrying.')
                self.last_error = exc
                if self._stop.wait(delay):
                    return  # shutting down during an outage: give up on the unsent batch
                delay = min(delay * 2, self._retry_max_s)

    def _take(self, timeout):
        try:
            batch = [self._queue.get(timeout=timeout)]
        except queue.Empty:
            return []
        while len(batch) < self._batch_size:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def _write(self, batch):
        if self._conn is None:
            conn = self._connect()
            with conn.cursor() as cur:
                for statement in SCHEMA:
                    cur.execute(statement)
                try:
                    cur.execute(HYPERTABLE)
                except Exception as exc:
                    self._log(f'Sort log: create_hypertable unavailable ({exc}); using a plain table.')
            self._conn = conn
        with self._conn.cursor() as cur:
            cur.executemany(INSERT, batch)

    def _disconnect(self):
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
