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
    # Added for #14; ALTER keeps tables created before the column existed working.
    "ALTER TABLE sort_events ADD COLUMN IF NOT EXISTS material TEXT",
)
# Tiger Cloud services include TimescaleDB; on plain Postgres these fail and the table stays ordinary.
HYPERTABLE = "SELECT create_hypertable('sort_events', 'time', if_not_exists => TRUE)"
# Per-minute rollup that Tiger keeps up to date in the background; real-time so new rows show at once.
CONTINUOUS_AGGREGATE = (
    """CREATE MATERIALIZED VIEW IF NOT EXISTS sort_events_per_minute
       WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
       SELECT time_bucket(INTERVAL '1 minute', time) AS bucket, bin, outcome, simulated,
              count(*) AS sorts, sum(cycle_s) AS cycle_total, count(cycle_s) AS cycle_count
       FROM sort_events GROUP BY bucket, bin, outcome, simulated WITH NO DATA""",
    """SELECT add_continuous_aggregate_policy('sort_events_per_minute',
           start_offset => INTERVAL '2 days', end_offset => INTERVAL '1 minute',
           schedule_interval => INTERVAL '1 minute', if_not_exists => TRUE)""",
)
OUTCOMES = ('DONE', 'STOPPED', 'FAULT')
# Window queries: the continuous aggregate first, the raw table if the aggregate is unavailable.
# Every read is filtered to one mode, so simulated sorts never mix with physical drops.
WINDOW_FROM_AGGREGATE = """
    SELECT bucket, bin, outcome, sum(sorts)::int, sum(cycle_total), sum(cycle_count)::int
    FROM sort_events_per_minute
    WHERE bucket >= now() - make_interval(mins => %s) AND simulated = %s
    GROUP BY bucket, bin, outcome ORDER BY bucket"""
WINDOW_FROM_TABLE = """
    SELECT date_trunc('minute', time) AS bucket, bin, outcome, count(*)::int, sum(cycle_s), count(cycle_s)::int
    FROM sort_events WHERE time >= now() - make_interval(mins => %s) AND simulated = %s
    GROUP BY 1, bin, outcome ORDER BY 1"""
TOTALS = "SELECT outcome, count(*)::int FROM sort_events WHERE simulated = %s GROUP BY outcome"
OTHER_MODE = "SELECT count(*)::int FROM sort_events WHERE simulated <> %s"
RECENT = """SELECT time, label, bin, outcome, confidence, cycle_s
            FROM sort_events WHERE simulated = %s ORDER BY time DESC LIMIT %s"""
INSERT = """INSERT INTO sort_events (time, sort_id, label, bin, material, confidence, outcome, cycle_s, simulated, detail)
            VALUES (%(time)s, %(sort_id)s, %(label)s, %(bin)s, %(material)s, %(confidence)s, %(outcome)s,
                    %(cycle_s)s, %(simulated)s, %(detail)s)
            ON CONFLICT (sort_id, time) DO NOTHING"""
FIELDS = ('time', 'sort_id', 'label', 'bin', 'material', 'confidence', 'outcome', 'cycle_s', 'simulated', 'detail')


def describe_error(exc):
    """Safe diagnostic: the exception type and Postgres SQLSTATE only. Never the message, which can
    echo the connection string, host, or user name."""
    code = getattr(exc, 'sqlstate', None)
    return f'{exc.__class__.__name__}, SQLSTATE {code}' if code else exc.__class__.__name__


def _iso(value):
    return value.isoformat() if hasattr(value, 'isoformat') else value


def _num(value):
    return None if value is None else float(value)


def summarize(window, totals, recent, *, minutes, source, simulated, other_mode_rows=0):
    """Shape one mode's database rows into the /api/sort-history JSON (plain values only)."""
    per_minute, per_bin = {}, {}
    for bucket, bin_name, outcome, sorts, cycle_total, cycle_count in window:
        key = outcome.lower() if outcome in OUTCOMES else 'fault'
        minute = per_minute.setdefault(_iso(bucket), dict(bucket=_iso(bucket), done=0, stopped=0, fault=0))
        minute[key] += sorts
        row = per_bin.setdefault(bin_name, dict(bin=bin_name, done=0, stopped=0, fault=0,
                                                cycle_total=0.0, cycle_count=0))
        row[key] += sorts
        if outcome == 'DONE' and cycle_count:
            row['cycle_total'] += float(cycle_total or 0)
            row['cycle_count'] += cycle_count
    bins = []
    for row in per_bin.values():
        count = row.pop('cycle_count')
        total = row.pop('cycle_total')
        row['avg_cycle_s'] = round(total / count, 2) if count else None
        bins.append(row)
    all_time = dict.fromkeys(OUTCOMES, 0)
    for outcome, count in totals:
        all_time[outcome if outcome in OUTCOMES else 'FAULT'] += count
    return dict(
        enabled=True, ok=True, source=source, window_minutes=minutes,
        mode='simulated' if simulated else 'physical', simulated=bool(simulated),
        other_mode_rows=other_mode_rows,
        per_minute=sorted(per_minute.values(), key=lambda m: m['bucket']),
        per_bin=sorted(bins, key=lambda b: b['bin']), totals=all_time,
        recent=[dict(time=_iso(t), label=label, bin=bin_name, outcome=outcome,
                     confidence=_num(conf), cycle_s=_num(cycle), simulated=bool(simulated))
                for t, label, bin_name, outcome, conf, cycle in recent])


class SortLog:
    def __init__(self, connect, *, read_connect=None, maxsize=500, batch_size=50, retry_max_s=30.0,
                 log=print, cache_s=3.0, clock=time.monotonic):
        self._connect = connect
        self._read_connect = read_connect or connect
        self._read_lock = threading.Lock()
        self._cache = {}
        self._cache_s = cache_s
        self._clock = clock
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
        # Dashboard reads get a short statement timeout so a slow query cannot pile up HTTP threads.
        log = cls(lambda: psycopg.connect(url, connect_timeout=5, autocommit=True),
                  read_connect=lambda: psycopg.connect(url, connect_timeout=5, autocommit=True,
                                                       options='-c statement_timeout=5000'),
                  **kwargs)
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
                reason = describe_error(exc)
                if reason != self.last_error:
                    self._log(f'Sort log: database write failed ({reason}); retrying.')
                self.last_error = reason
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
                    for statement in CONTINUOUS_AGGREGATE:
                        cur.execute(statement)
                except Exception as exc:
                    self._log(f'Sort log: TimescaleDB features unavailable ({describe_error(exc)}); '
                              'using a plain table.')
            self._conn = conn
        with self._conn.cursor() as cur:
            cur.executemany(INSERT, batch)

    def history(self, minutes=60, recent=10, *, simulated):
        """Dashboard summary for one mode (simulated or physical) read back from Tiger Data. Runs on the
        caller's (HTTP) thread, never the bridge lock; cached briefly so open dashboards share a query."""
        minutes = max(5, min(int(minutes), 1440))
        simulated = bool(simulated)
        key = (minutes, recent, simulated)
        with self._read_lock:
            cached = self._cache.get(key)
            if cached and self._clock() - cached[0] < self._cache_s:
                return cached[1]
            conn = self._read_connect()
            try:
                with conn.cursor() as cur:
                    try:
                        cur.execute(WINDOW_FROM_AGGREGATE, (minutes, simulated))
                        source = 'continuous_aggregate'
                    except Exception:
                        cur.execute(WINDOW_FROM_TABLE, (minutes, simulated))
                        source = 'table'
                    window = cur.fetchall()
                    cur.execute(TOTALS, (simulated,))
                    totals = cur.fetchall()
                    cur.execute(OTHER_MODE, (simulated,))
                    other = cur.fetchall()
                    cur.execute(RECENT, (simulated, recent))
                    rows = cur.fetchall()
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            result = summarize(window, totals, rows, minutes=minutes, source=source, simulated=simulated,
                               other_mode_rows=other[0][0] if other else 0)
            result.update(queued=self._queue.qsize(), dropped=self.dropped, written=self.written)
            self._cache[key] = (self._clock(), result)
            return result

    def _disconnect(self):
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
